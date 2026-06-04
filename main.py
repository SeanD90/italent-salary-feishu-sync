"""
main.py — 定时调度入口
每天 22:32 自动执行同步任务，完成后发送飞书通知
"""
import asyncio
import schedule
import time
from datetime import datetime
from config import SYNC_TIME, FEISHU_NOTIFY_USER_IDS
from logger import get_logger
from sync_table1 import sync_table1
from sync_table3 import sync_table3
from sync_wage_standard import sync_wage_standard
from feishu_client import FeishuClient
from screenshot_manager import cleanup_screenshots

log = get_logger("main")


def _run_one(name: str, coro_func):
    """执行单个同步任务，返回 (结果dict, 错误信息)"""
    try:
        result = asyncio.run(coro_func())
        return result, None
    except Exception as e:
        log.error(f"{name} 同步失败: {e}", exc_info=True)
        return None, str(e)


def _build_notification(results: dict) -> tuple[str, list[str]]:
    """
    构建通知标题和正文。
    results: {"表1": (result_dict, error), "表2": ..., "表3": ...}
    """
    title = "薪酬数据多维表格同步完成"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"同步时间：{now}", ""]

    for table_name, (result, error) in results.items():
        if error:
            lines.append(f"❌ {table_name}：同步失败 — {error[:80]}")
        elif result:
            parts = []
            if result.get("new", 0) > 0:
                parts.append(f"新增 {result['new']} 条")
            if result.get("update", 0) > 0:
                parts.append(f"更新 {result['update']} 条")
            if result.get("skip", 0) > 0:
                parts.append(f"跳过 {result['skip']} 条")
            summary = "，".join(parts) if parts else "无变更"
            lines.append(f"✅ {table_name}：{summary}")
        else:
            lines.append(f"❌ {table_name}：未知错误")

    lines.append("")
    lines.append("如有异常请查看日志文件 logs/sync.log")

    return title, lines


def run_sync():
    """同步任务入口"""
    log.info(f"定时任务触发，开始执行同步...")

    results = {}

    # 依次执行三个表的同步
    r1, e1 = _run_one("1-工资标准", sync_wage_standard)
    results["1-工资标准"] = (r1, e1)

    r2, e2 = _run_one("2-工资数据", sync_table1)
    results["2-工资数据"] = (r2, e2)

    r3, e3 = _run_one("3-调薪记录表", sync_table3)
    results["3-调薪记录表"] = (r3, e3)

    # 清理截图
    cleanup_screenshots()

    # 发送飞书通知
    try:
        title, lines = _build_notification(results)
        feishu = FeishuClient()
        feishu.send_notification(FEISHU_NOTIFY_USER_IDS, title, lines)
    except Exception as e:
        log.error(f"发送通知失败: {e}", exc_info=True)


def main():
    log.info(f"薪酬同步服务启动，计划每天 {SYNC_TIME} 执行")

    # 注册定时任务
    schedule.every().day.at(SYNC_TIME).do(run_sync)

    # 额外注册一个每周清理任务（周一凌晨 3 点）
    schedule.every().monday.at("03:00").do(cleanup_screenshots)

    # 启动时立即执行一次（测试用，验证完成后注释掉这两行）
    # log.info("立即执行一次（测试）...")
    # run_sync()

    # 主循环
    while True:
        schedule.run_pending()
        time.sleep(30)  # 每 30 秒检查一次


if __name__ == "__main__":
    main()
