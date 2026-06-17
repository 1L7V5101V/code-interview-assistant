"""
截图模块
支持全屏截图和区域截图，返回 PIL Image 对象
"""

import io
import base64
from PIL import Image
import pyautogui


def screenshot_full() -> Image.Image:
    """全屏截图，返回 PIL Image"""
    return pyautogui.screenshot()


def screenshot_region(x: int, y: int, width: int, height: int) -> Image.Image:
    """区域截图，返回 PIL Image"""
    return pyautogui.screenshot(region=(x, y, width, height))


def image_to_base64(img: Image.Image, format: str = "PNG") -> str:
    """将 PIL Image 转换为 base64 字符串"""
    buf = io.BytesIO()
    img.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def save_screenshot(img: Image.Image, path: str) -> str:
    """保存截图到文件，返回文件路径"""
    img.save(path)
    return path


class RegionSelector:
    """
    区域截图选择器（简化版）
    实际使用时，通过键盘快捷键触发，然后用 PyQt 画框选界面
    这里只提供工具函数，UI 部分在 main_window.py 中实现
    """
    pass
