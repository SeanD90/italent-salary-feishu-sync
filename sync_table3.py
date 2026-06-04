"""
sync_table3.py — 3-调薪记录表 同步逻辑

业务逻辑：
- 数据来源：HR「全部薪资档案」（与表2相同）
- 对同一员工的多条记录，按生效日期排序后，相邻两条两两配对：
    早的一条 → 【调前】各字段
    晚的一条 → 【调后】各字段
  3条及以上记录会产生多行（N条记录产生N-1行）
- 只有1条记录的员工跳过（无调薪历史）
- 增量匹配键：工号 + 【调前】生效日期 + 【调后】生效日期
- 只增不改，永不覆盖已有数据

字段映射：
    飞书字段            HR来源
    工号                工号
    姓名                员工
    部门                部门
    职务                职务
    币种                offer币种
    变动原因            调后记录的变动原因
    【调前】薪资包      调前记录的薪资包
    【调前】固定工资    调前记录的基本工资（日薪包×22）
    【调前】绩效标准    调前记录的月度绩效基数（空则季度÷3）
    【调前】奖金基数    调前记录的月度奖金基数（空则季度÷3）
    【调后】薪资包      调后记录的薪资包
    【调后】固定工资    调后记录的基本工资（日薪包×22）
    【调后】绩效标准    调后记录的月度绩效基数（空则季度÷3）
    【调后】奖金基数    调后记录的月度奖金基数（空则季度÷3）
    生效日期            调后记录的生效日期（Unix毫秒时间戳）
"""
import asyncio
from datetime import datetime
from typing import Optional
from collections import defaultdict

from hr_client import HRClient
from feishu_client import FeishuClient
from config import FEISHU_TABLE3_ID, FEISHU_FIELDS_TABLE3
from logger import get_logger

log = get_logger("sync_table3")

F = FEISHU_FIELDS_TABLE3


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _to_number(val) -> Optional[float]:
    """字符串转数字，失败返回 None"""
    if val is None or val == "" or val == "--":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _date_to_timestamp(date_str: str) -> Optional[int]:
    """日期字符串 → 飞书 Unix 时间戳（毫秒）"""
    if not date_str or date_str in ("--", ""):
        return None
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%Y年%m月%d日"]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    log.warning(f"无法解析日期: {date_str}")
    return None


def _normalize_date(date_str: str) -> str:
    """归一化日期为 YYYY-MM-DD，用于排序和匹配键"""
    if not date_str:
        return ""
    return str(date_str).replace("/", "-")[:10]


def _calc_fixed_salary(rec: dict) -> Optional[float]:
    """
    固定工资计算：
    - 薪资包含「日薪」→ 实习生日薪 × 22
    - 否则取基本工资
    """
    salary_pkg = rec.get("薪资包", "") or ""
    daily = _to_number(rec.get("实习生日薪"))
    if "日薪" in salary_pkg and daily is not None:
        return round(daily * 22, 2)
    return _to_number(rec.get("基本工资"))


def _calc_perf(rec: dict) -> Optional[float]:
    """绩效标准：月度绩效基数优先，否则季度÷3"""
    v = _to_number(rec.get("月度绩效基数"))
    if v is None:
        q = _to_number(rec.get("季度绩效基数"))
        if q is not None:
            return round(q / 3, 2)
    return v


def _calc_bonus(rec: dict) -> Optional[float]:
    """奖金基数：月度奖金基数优先，否则季度÷3"""
    v = _to_number(rec.get("月度奖金基数"))
    if v is None:
        q = _to_number(rec.get("季度奖金基数"))
        if q is not None:
            return round(q / 3, 2)
    return v


# ──────────────────────────────────────────────
# 数据分组与配对
# ──────────────────────────────────────────────

def _group_by_employee(hr_records: list[dict]) -> dict[str, list[dict]]:
    """
    按工号分组，每组内按生效日期升序排列。
    hr_records 是 HRClient._map_to_fields 输出的格式（已含 emp_no、生效日期等）。
    注意：这里直接用 HRClient 的原始行数据（未经 _map_to_fields），
    需要字段名与 HR 表头一致（工号、员工、部门……）。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in hr_records:
        emp_no = str(rec.get("工号", "") or rec.get("emp_no", "")).strip()
        if not emp_no:
            continue
        groups[emp_no].append(rec)

    # 每组内按生效日期升序
    for emp_no in groups:
        groups[emp_no].sort(key=lambda r: _normalize_date(
            r.get("生效日期", "") or r.get("生效日期", "")
        ))
    return groups


def _make_pairs(group: list[dict]) -> list[tuple[dict, dict]]:
    """
    将同一员工的记录列表两两配对（相邻）：
    [A, B, C] → [(A,B), (B,C)]
    """
    return [(group[i], group[i + 1]) for i in range(len(group) - 1)]


# ──────────────────────────────────────────────
# 飞书记录构建
# ──────────────────────────────────────────────

def _build_feishu_record(before: dict, after: dict) -> dict:
    """
    把「调前」和「调后」两条 HR 原始行合并为一条飞书记录。
    before / after 的字段名与 HR 表头一致。
    """
    record = {}

    # ── 基础信息（取调后记录，数据更新）──
    def _text(val: str) -> Optional[str]:
        v = str(val or "").strip()
        return v if v else None

    for feishu_key, raw_key, source in [
        ("工号",     "工号",     after),
        ("姓名",     "员工",     after),
        ("部门",     "部门",     after),
        ("职务",     "职务",     after),
        ("币种",     "offer币种", after),
        ("变动原因", "变动原因", after),   # 变动原因取调后
    ]:
        v = _text(source.get(raw_key, ""))
        if v:
            record[F[feishu_key]] = v

    # ── 【调前】字段 ──
    before_pkg = str(before.get("薪资包", "") or "").strip()
    before_fixed = _calc_fixed_salary(before)
    before_perf  = _calc_perf(before)
    before_bonus = _calc_bonus(before)

    if before_pkg:
        record[F["调前薪资包"]] = before_pkg
    if before_fixed is not None:
        record[F["调前固定工资"]] = before_fixed
    if before_perf is not None:
        record[F["调前绩效标准"]] = before_perf
    if before_bonus is not None:
        record[F["调前奖金基数"]] = before_bonus

    # ── 【调后】字段 ──
    after_pkg = str(after.get("薪资包", "") or "").strip()
    after_fixed = _calc_fixed_salary(after)
    after_perf  = _calc_perf(after)
    after_bonus = _calc_bonus(after)

    if after_pkg:
        record[F["调后薪资包"]] = after_pkg
    if after_fixed is not None:
        record[F["调后固定工资"]] = after_fixed
    if after_perf is not None:
        record[F["调后绩效标准"]] = after_perf
    if after_bonus is not None:
        record[F["调后奖金基数"]] = after_bonus

    # ── 生效日期：取调后，转时间戳 ──
    ts = _date_to_timestamp(str(after.get("生效日期", "") or ""))
    if ts:
        record[F["生效日期"]] = ts

    return record


# ──────────────────────────────────────────────
# 飞书已有记录读取（三键去重）
# ──────────────────────────────────────────────

def _get_existing_keys(feishu_client: FeishuClient) -> set:
    """
    返回飞书表3已有记录的 (工号, 调前生效日期, 调后生效日期) 集合。
    调前/调后生效日期从飞书记录里读不出来（飞书只存了调后生效日期），
    所以这里用「工号 + 【调后】生效日期YYYY-MM-DD」作为唯一键。

    注意：一个员工同一天只能有一条调薪记录，所以这个键足够唯一。
    """
    from feishu_client import _extract_text, _normalize_date as _nd
    all_records = feishu_client.get_all_records(FEISHU_TABLE3_ID)
    keys = set()
    for rec in all_records:
        fields = rec.get("fields", {})
        emp_no    = _extract_text(fields.get(F["工号"], ""))
        eff_date  = _nd(fields.get(F["生效日期"], ""))
        if emp_no:
            keys.add((emp_no, eff_date))
    log.info(f"飞书表3已有 {len(keys)} 条 (工号+调后生效日期) 记录")
    return keys


# ──────────────────────────────────────────────
# 主同步函数
# ──────────────────────────────────────────────

async def sync_table3():
    log.info("=" * 50)
    log.info("开始同步 [3-调薪记录表]")
    log.info("=" * 50)

    hr_client     = HRClient()
    feishu_client = FeishuClient()

    try:
        # ── Step 1: 从 HR 拉取全部薪资档案原始数据 ──
        # HRClient.fetch_all_salary_data() 返回的是已经过 _map_to_fields 的数据，
        # 字段名已转换（emp_no、name……）。
        # 但表3需要原始表头字段名（工号、员工、基本工资……）来做计算。
        # 所以这里调用内部方法获取原始行数据。
        await hr_client.start()
        if not await hr_client.login():
            raise Exception("HR 系统登录失败")
        await hr_client._navigate_to_salary_page()
        await asyncio.sleep(2)

        # 翻页读取所有原始行
        all_raw = []
        page_num = 1
        while True:
            log.info(f"── 读取第 {page_num} 页 ──")
            await asyncio.sleep(1.5)
            await hr_client._expand_table()
            page_records = await hr_client._read_current_page_stable()

            if not page_records:
                log.warning(f"第 {page_num} 页无数据")
                break

            first_emp = page_records[0].get("工号")
            all_raw.extend(page_records)
            log.info(f"第 {page_num} 页：本页 {len(page_records)} 行，累计 {len(all_raw)} 行")

            has_next = await hr_client._click_next_and_wait(first_emp)
            if not has_next:
                break
            page_num += 1
            if page_num > 30:
                break

        if not all_raw:
            raise Exception("HR 原始数据为空")

        log.info(f"HR 原始数据共 {len(all_raw)} 行")

        # ── Step 2: 按员工分组，生成调薪配对 ──
        groups = _group_by_employee(all_raw)
        log.info(f"共 {len(groups)} 名员工")

        all_pairs = []
        skip_single = 0
        for emp_no, recs in groups.items():
            if len(recs) < 2:
                log.debug(f"员工 {emp_no} 只有1条记录，跳过")
                skip_single += 1
                continue
            pairs = _make_pairs(recs)
            log.info(f"员工 {emp_no}({recs[0].get('员工','')}): "
                     f"{len(recs)} 条记录 → {len(pairs)} 对调薪")
            all_pairs.extend([(emp_no, b, a) for b, a in pairs])

        log.info(f"共生成 {len(all_pairs)} 对调薪记录（{skip_single} 名员工因只有1条记录跳过）")

        # ── Step 3: 读取飞书已有记录 ──
        existing_keys = _get_existing_keys(feishu_client)

        # ── Step 4: 逐对比对，新记录写入飞书 ──
        new_records = []
        skip_count  = 0
        new_count   = 0

        for emp_no, before, after in all_pairs:
            after_date = _normalize_date(after.get("生效日期", ""))
            key = (emp_no, after_date)

            if key in existing_keys:
                log.debug(f"已存在: {emp_no} 调后={after_date}，跳过")
                skip_count += 1
            else:
                name = after.get("员工", "")
                before_date = _normalize_date(before.get("生效日期", ""))
                log.info(f"新增: {name}({emp_no}) "
                         f"调前={before_date} → 调后={after_date} "
                         f"变动原因={after.get('变动原因', '')}")
                new_records.append(_build_feishu_record(before, after))
                new_count += 1
                existing_keys.add(key)  # 防止本批次内重复

        # ── Step 5: 批量写入飞书 ──
        if new_records:
            log.info(f"准备向飞书写入 {len(new_records)} 条新记录...")
            created = feishu_client.batch_create_records(FEISHU_TABLE3_ID, new_records)
            log.info(f"飞书写入完成: 成功 {created} 条")
        else:
            log.info("本次无任何变更，飞书无需更新")

        # ── 汇总 ──
        log.info("=" * 50)
        log.info(f"同步完成 [3-调薪记录表]")
        log.info(f"  HR 原始行数:   {len(all_raw)}")
        log.info(f"  参与配对员工:  {len(groups) - skip_single} 名")
        log.info(f"  单条记录跳过:  {skip_single} 名")
        log.info(f"  生成调薪对数:  {len(all_pairs)}")
        log.info(f"  新增记录:      {new_count} 条")
        log.info(f"  已存在跳过:    {skip_count} 条")
        log.info("=" * 50)

        return {"new": new_count, "skip": skip_count}

    except Exception as e:
        log.error(f"同步失败: {e}", exc_info=True)
        raise

    finally:
        await hr_client.close()


# ── 单独运行测试 ──
if __name__ == "__main__":
    asyncio.run(sync_table3())
