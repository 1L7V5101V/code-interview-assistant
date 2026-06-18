"""
手撕代码助手 - 入口文件
"""

import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 初始化数据库
import core.task_manager as task_manager

task_manager.init_db()

# 迁移旧数据（如果有 history.db）
task_manager.migrate_from_old_history()

# 检查 API 配置
import config

cfg = config.load_config()
if not cfg.get("api_key"):
    print("[提示] 未配置 API Key，启动后点击右上角齿轮按钮进行设置")


def main():
    from PyQt6.QtWidgets import QApplication
    from gui.main_window import TranslucentWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 隐藏控制台（Windows）
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0
            )
        except Exception:
            pass

    win = TranslucentWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
