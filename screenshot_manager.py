"""
截图管理工具
负责清理过期截图
"""
import os
import time
from config import SCREENSHOT_DIR, SCREENSHOT_RETENTION_DAYS
from logger import get_logger

log = get_logger("screenshot_manager")

def cleanup_screenshots():
    """清理 screenshots 文件夹中过期的截图文件"""
    log.info(f"开始清理过期截图（保留最近 {SCREENSHOT_RETENTION_DAYS} 天）...")
    
    if not os.path.exists(SCREENSHOT_DIR):
        log.warning(f"截图目录 {SCREENSHOT_DIR} 不存在，跳过清理")
        return

    now = time.time()
    retention_seconds = SCREENSHOT_RETENTION_DAYS * 24 * 60 * 60
    
    count = 0
    # 遍历 screenshots 及其子目录
    for root, dirs, files in os.walk(SCREENSHOT_DIR):
        for name in files:
            if not name.lower().endswith(('.png', '.jpg', '.jpeg')):
                continue
                
            file_path = os.path.join(root, name)
            try:
                # 获取文件最后修改时间
                mtime = os.path.getmtime(file_path)
                if now - mtime > retention_seconds:
                    os.remove(file_path)
                    log.info(f"已删除过期截图: {file_path}")
                    count += 1
            except Exception as e:
                log.error(f"清理文件 {file_path} 时出错: {e}")

    log.info(f"清理完成，共删除 {count} 个过期截图文件")

if __name__ == "__main__":
    cleanup_screenshots()
