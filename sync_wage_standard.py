"""
sync_wage_standard.py — 1-工资标准 同步逻辑

业务逻辑：
- 每个员工一行（匹配键=工号），有变化就更新
- 数据来源：HR系统「全部薪资档案」
- 字段分4部分：
    1. 人员信息：工号、姓名、部门、职务、币种（取最新记录）
    2. 当前工资：该员工生效日期最晚的记录
    3. 试用期工资：变动原因=「入职定薪」的记录
    4. 转正后工资：变动原因=「转正调薪」的记录
- 固定工资：薪资包含「日薪」→ 实习生日薪×22
- 绩效标准：月度绩效基数优先，否则季度÷3
- 奖金基数：月度奖金基数优先，否则季度÷3
"""
import asyncio
from typing import Optional
from collections import defaultdict

from hr_client import HRClient
from feishu_client import FeishuClient, _extract_text
from config import FEISHU_TABLE1_ID, FEISHU_FIELDS_TABLE1
from logger import get_logger

log = get_logger("sync_wage_std")

F = FEISHU_FIELDS_TABLE1


# ──────────────────────────────────────────────
# 工具函数（与 sync_table3 相同）
# ──────────────────────────────────────────────

def _to_number(val) -> Optional[float]:
    if val is None or val == "" or val == "--":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    return str(date_str).replace("/", "-")[:10]


def _calc_fixed_salary(rec: dict) -> Optional[float]:
    """固定工资：薪资包含「日薪」→ 实习生日薪×22，否则取基本工资"""
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
# 分组与分类
# ──────────────────────────────────────────────

def _group_and_classify(raw_records: list[dict]) -> dict[str, dict]:
    """
    按工号分组，对每个员工提取三类记录：
    返回: {
        "008001": {
            "current":   {...},   # 生效日期最晚的记录（当前工资）
            "probation": {...},   # 变动原因=入职定薪（试用期），可能为 None
            "confirmed": {...},   # 变动原因=转正调薪（转正后），可能为 None
        }, ...
    }
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in raw_records:
        emp_no = str(rec.get("工号", "")).strip()
        if emp_no:
            groups[emp_no].append(rec)

    result = {}
    for emp_no, recs in groups.items():
        # 按生效日期降序，第一条就是最新
        recs.sort(key=lambda r: _normalize_date(r.get("生效日期", "")), reverse=True)
        current = recs[0]

        probation = None
        confirmed = None
        for r in recs:
            reason = str(r.get("变动原因", "") or "").strip()
            if reason == "入职定薪" and probation is None:
                probation = r
            elif reason == "转正调薪" and confirmed is None:
                confirmed = r

        result[emp_no] = {
            "current": current,
            "probation": probation,
            "confirmed": confirmed,
        }
    return result


# ──────────────────────────────────────────────
# 构建飞书记录
# ──────────────────────────────────────────────

def _build_feishu_record(data: dict) -> dict:
    """
    把一个员工的 current / probation / confirmed 合并为飞书字段。
    """
    cur = data["current"]
    pro = data.get("probation")
    con = data.get("confirmed")

    record = {}

    # ── 人员信息（取当前记录）──
    def _text(val) -> Optional[str]:
        v = str(val or "").strip()
        return v if v else None

    for key, raw_key in [
        ("工号", "工号"), ("姓名", "员工"), ("部门", "部门"),
        ("职务", "职务"), ("币种", "offer币种"),
    ]:
        v = _text(cur.get(raw_key, ""))
        if v:
            record[F[key]] = v

    # ── 当前工资 ──
    cur_pkg = _text(cur.get("薪资包", ""))
    if cur_pkg:
        record[F["当前薪资包"]] = cur_pkg

    cur_fixed = _calc_fixed_salary(cur)
    if cur_fixed is not None:
        record[F["当前固定工资"]] = cur_fixed

    cur_perf = _calc_perf(cur)
    if cur_perf is not None:
        record[F["当前绩效标准"]] = cur_perf

    cur_bonus = _calc_bonus(cur)
    if cur_bonus is not None:
        record[F["当前奖金基数"]] = cur_bonus

    # ── 试用期工资（变动原因=入职定薪）──
    if pro:
        pro_pkg = _text(pro.get("薪资包", ""))
        if pro_pkg:
            record[F["试用期薪资包"]] = pro_pkg

        pro_fixed = _calc_fixed_salary(pro)
        if pro_fixed is not None:
            record[F["试用期固定工资"]] = pro_fixed

        pro_bonus = _calc_bonus(pro)
        if pro_bonus is not None:
            record[F["试用期奖金基数"]] = pro_bonus

    # ── 转正后工资（变动原因=转正调薪）──
    if con:
        con_pkg = _text(con.get("薪资包", ""))
        if con_pkg:
            record[F["转正后薪资包"]] = con_pkg

        con_fixed = _calc_fixed_salary(con)
        if con_fixed is not None:
            record[F["转正固定工资"]] = con_fixed

        con_perf = _calc_perf(con)
        if con_perf is not None:
            record[F["转正绩效标准"]] = con_perf

        con_bonus = _calc_bonus(con)
        if con_bonus is not None:
            record[F["转正奖金基数"]] = con_bonus

    return record


# ──────────────────────────────────────────────
# 变更检测
# ──────────────────────────────────────────────

def _has_changed(new_fields: dict, existing_fields: dict) -> bool:
    """逐字段对比，任何一个有差异就算变更"""
    for key, val in new_fields.items():
        existing_val = existing_fields.get(key)
        # 统一为字符串比较（数值去小数点尾0）
        new_s = _fmt(val)
        old_s = _fmt(_extract_text(existing_val) if existing_val is not None else "")
        if new_s != old_s:
            return True
    return False


def _fmt(val) -> str:
    """格式化值为可比较的字符串"""
    if val is None:
        return ""
    if isinstance(val, float):
        return str(int(val)) if val == int(val) else str(val)
    return str(val).strip()


# ──────────────────────────────────────────────
# 主同步函数
# ──────────────────────────────────────────────

async def sync_wage_standard():
    log.info("=" * 50)
    log.info("开始同步 [1-工资标准]")
    log.info("=" * 50)

    hr_client = HRClient()
    feishu_client = FeishuClient()

    try:
        # ── Step 1: 从 HR 抓取全部薪资档案原始数据 ──
        await hr_client.start()
        if not await hr_client.login():
            raise Exception("HR 系统登录失败")
        await hr_client._navigate_to_salary_page()
        await asyncio.sleep(2)

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
            log.info(f"第 {page_num} 页：本页 {len(page_records)} 行，"
                     f"累计 {len(all_raw)} 行")

            has_next = await hr_client._click_next_and_wait(first_emp)
            if not has_next:
                break
            page_num += 1
            if page_num > 30:
                break

        if not all_raw:
            raise Exception("HR 原始数据为空")

        log.info(f"HR 原始数据共 {len(all_raw)} 行")

        # ── Step 2: 按员工分组分类 ──
        classified = _group_and_classify(all_raw)
        log.info(f"共 {len(classified)} 名员工")

        for emp_no, data in classified.items():
            name = data["current"].get("员工", "")
            pro = "有" if data["probation"] else "无"
            con = "有" if data["confirmed"] else "无"
            log.debug(f"  {name}({emp_no}): 入职定薪={pro}, 转正调薪={con}")

        # ── Step 3: 读取飞书已有记录 ──
        existing_map = feishu_client.get_records_by_emp_no(
            FEISHU_TABLE1_ID, F["工号"]
        )

        # ── Step 4: 逐员工比对，新增或更新 ──
        new_records = []
        update_count = 0
        skip_count = 0
        new_count = 0

        for emp_no, data in classified.items():
            name = data["current"].get("员工", "")
            new_fields = _build_feishu_record(data)

            if emp_no in existing_map:
                existing = existing_map[emp_no]
                if _has_changed(new_fields, existing["fields"]):
                    record_id = existing["record_id"]
                    ok = feishu_client.update_record(
                        FEISHU_TABLE1_ID, record_id, new_fields
                    )
                    if ok:
                        log.info(f"更新: {name}({emp_no})")
                        update_count += 1
                    else:
                        log.error(f"更新失败: {name}({emp_no})")
                else:
                    log.debug(f"无变化: {name}({emp_no})，跳过")
                    skip_count += 1
            else:
                log.info(f"新增: {name}({emp_no})")
                new_records.append(new_fields)
                new_count += 1

        # ── Step 5: 批量新增 ──
        if new_records:
            log.info(f"准备向飞书写入 {len(new_records)} 条新记录...")
            created = feishu_client.batch_create_records(
                FEISHU_TABLE1_ID, new_records
            )
            log.info(f"飞书新增完成: 成功 {created} 条")

        # ── 汇总 ──
        log.info("=" * 50)
        log.info(f"同步完成 [1-工资标准]")
        log.info(f"  员工总数:   {len(classified)}")
        log.info(f"  新增:       {new_count} 条")
        log.info(f"  更新:       {update_count} 条")
        log.info(f"  无变化跳过: {skip_count} 条")
        log.info("=" * 50)

        return {"new": new_count, "update": update_count, "skip": skip_count}

    except Exception as e:
        log.error(f"同步失败: {e}", exc_info=True)
        raise

    finally:
        await hr_client.close()


if __name__ == "__main__":
    asyncio.run(sync_wage_standard())
