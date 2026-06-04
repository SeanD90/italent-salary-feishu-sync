"""
sync_table1.py — 2-工资数据 同步逻辑

同步规则：
- 每天 22:32 运行
- 以「工号」为匹配键，读取 HR 系统最新薪资数据
- 与飞书表中该员工「最新一条记录」对比以下 5 个字段：
    薪资包、固定工资、绩效标准、奖金基数、生效日期
- 任意一个字段有变化 → 在飞书新增一条记录（原记录保留不动）
- 无变化 → 跳过

程序写入的字段（共10个）：
    工号、姓名、部门、职务、币种、薪资包、固定工资、绩效标准、奖金基数、生效日期

飞书自动计算（程序不干预）：
    本人、一级部门、二级部门、汇率
"""
import asyncio
from datetime import datetime
from typing import Optional
from hr_client import HRClient
from feishu_client import FeishuClient
from config import FEISHU_TABLE2_ID, FEISHU_FIELDS, COMPARE_FIELDS
from logger import get_logger

log = get_logger("sync_table1")


def _date_to_timestamp(date_str: str) -> Optional[int]:
    """把日期字符串转为飞书需要的 Unix 时间戳（毫秒）"""
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


def _build_feishu_record(hr_data: dict) -> dict:
    """把 HR 数据转换为飞书字段格式，空值不写入"""
    F = FEISHU_FIELDS
    record = {}

    # 文本字段
    for key, hr_key in [
        ("工号", "emp_no"), ("姓名", "name"), ("部门", "dept"),
        ("职务", "position"), ("币种", "currency"), ("薪资包", "薪资包"),
        ("变动原因", "变动原因"),
    ]:
        val = str(hr_data.get(hr_key, "") or "").strip()
        if val:
            record[F[key]] = val

    # 数字字段
    for key, hr_key in [
        ("固定工资", "固定工资"), ("绩效标准", "绩效标准"), ("奖金基数", "奖金基数"),
    ]:
        val = _to_number(hr_data.get(hr_key))
        if val is not None:
            record[F[key]] = val

    # 日期字段：转为 Unix 时间戳（毫秒）
    ts = _date_to_timestamp(str(hr_data.get("生效日期", "") or ""))
    if ts:
        record[F["生效日期"]] = ts

    return record


def _to_number(val) -> Optional[float]:
    """尝试把字符串转为数字，失败返回 None"""
    if val is None or val == "" or val == "--":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _has_changed(hr_data: dict, feishu_record: dict) -> bool:
    """
    对比 5 个关键字段，判断是否有变更
    只要有任意一个字段的值不同，就认为有变更
    """
    F = FEISHU_FIELDS
    field_map = {
        "薪资包":   F["薪资包"],
        "固定工资": F["固定工资"],
        "绩效标准": F["绩效标准"],
        "奖金基数": F["奖金基数"],
        "生效日期": F["生效日期"],
    }

    for field_key in COMPARE_FIELDS:
        feishu_field = field_map[field_key]

        hr_val     = str(hr_data.get(field_key, "") or "").strip()
        feishu_val = str(feishu_record.get(feishu_field, "") or "").strip()

        # 数值字段：去掉小数点后无意义的0再比较（如 12000.0 == 12000）
        if field_key in ("固定工资", "绩效标准", "奖金基数"):
            try:
                hr_val     = str(float(hr_val))     if hr_val     else ""
                feishu_val = str(float(feishu_val)) if feishu_val else ""
            except (ValueError, TypeError):
                pass

        if hr_val != feishu_val:
            log.debug(f"字段「{field_key}」有变更: HR={hr_val!r} vs 飞书={feishu_val!r}")
            return True

    return False


async def sync_table1():
    """
    主同步函数：
    1. 从 HR 系统拉取最新薪资数据
    2. 从飞书读取现有记录（每人取最新一条）
    3. 对比 5 个字段，有变更则新增记录到飞书
    """
    log.info("=" * 50)
    log.info("开始同步 [2-工资数据]")
    log.info("=" * 50)

    hr_client     = HRClient()
    feishu_client = FeishuClient()

    try:
        # ── Step 1: 启动浏览器，从 HR 系统获取数据 ──
        await hr_client.start()
        hr_records = await hr_client.fetch_all_salary_data()

        if not hr_records:
            log.error("HR 系统未返回任何数据，退出")
            return

        log.info(f"HR 系统共获取 {len(hr_records)} 条员工薪资记录")

        # ── Step 2: 从飞书读取已有的 (工号+生效日期) 集合 ──
        existing_keys = feishu_client.get_existing_emp_date_keys(FEISHU_TABLE2_ID)

        # ── Step 3: 逐条比对，(工号+生效日期) 不存在则新增 ──
        new_records   = []
        skip_count    = 0
        error_count   = 0
        new_count     = 0

        for hr_data in hr_records:
            emp_no = str(hr_data.get("emp_no", "")).strip()
            name   = str(hr_data.get("name", "")).strip()

            if not emp_no:
                log.warning(f"跳过无工号记录: {name}")
                error_count += 1
                continue

            # 归一化生效日期为 YYYY-MM-DD
            eff_date = str(hr_data.get("生效日期", "") or "").replace("/", "-")[:10]
            key = (emp_no, eff_date)

            if key in existing_keys:
                # 该 工号+生效日期 已在飞书 → 跳过，不覆盖
                log.debug(f"已存在: {name}({emp_no}) {eff_date}，跳过")
                skip_count += 1
            else:
                # 新记录（新员工 或 员工的新薪资历史）→ 新增
                log.info(f"新增: {name}({emp_no}) 生效日期 {eff_date}")
                new_records.append(_build_feishu_record(hr_data))
                new_count += 1
                existing_keys.add(key)  # 防止本批次内重复

        # ── Step 4: 批量写入飞书 ──
        if new_records:
            log.info(f"准备向飞书写入 {len(new_records)} 条新记录...")
            created = feishu_client.batch_create_records(FEISHU_TABLE2_ID, new_records)
            log.info(f"飞书写入完成: 成功 {created} 条")
        else:
            log.info("本次无任何变更，飞书无需更新")

        # ── 汇总 ──
        log.info("=" * 50)
        log.info(f"同步完成 [2-工资数据]")
        log.info(f"  HR 数据总计: {len(hr_records)} 人")
        log.info(f"  新增记录:   {new_count} 条")
        log.info(f"  已存在跳过: {skip_count} 条")
        log.info(f"  错误跳过:   {error_count} 条")
        log.info("=" * 50)

        return {"new": new_count, "skip": skip_count}

    except Exception as e:
        log.error(f"同步失败: {e}", exc_info=True)
        raise

    finally:
        await hr_client.close()


# ── 单独运行此文件时直接执行（用于测试）──
if __name__ == "__main__":
    asyncio.run(sync_table1())
