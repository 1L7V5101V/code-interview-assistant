"""
区域截图选择器
全屏半透明覆盖层，拖拽选择区域
截图时主窗口自动隐藏，右下角显示提示
"""

import sys
import os
from io import BytesIO

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics
from PIL import Image


class RegionSelector(QWidget):
    """
    全屏半透明覆盖层，用于框选截图区域。
    使用方式：
        selector = RegionSelector()
        selector.set_captured_callback(on_captured)   # 回调接收 PIL Image
        selector.set_cancelled_callback(on_cancelled) # 取消时回调
        selector.show()
    """

    def __init__(self):
        super().__init__()
        self.start_pos = None
        self.end_pos = None
        self.is_drawing = False
        self._captured_callback = None
        self._cancelled_callback = None

        # 全屏覆盖，置顶，无边框，不在任务栏显示
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        # 透明背景：允许绘制半透明/透明像素
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 禁用鼠标事件穿透，确保点击被覆盖层捕获，不会跳到虚拟机
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        # 铺满所有屏幕的联合区域
        screens = QApplication.primaryScreen().virtualSiblings()
        total_rect = QRect()
        for screen in screens:
            total_rect = total_rect.united(screen.geometry())
        self.setGeometry(total_rect)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ─── 回调设置 ───
    def set_captured_callback(self, callback):
        """设置截图完成后的回调，回调接收 PIL Image 对象"""
        self._captured_callback = callback

    def set_cancelled_callback(self, callback):
        """设置取消截图时的回调"""
        self._cancelled_callback = callback

    # ─── 显示 / 隐藏事件 ───
    def showEvent(self, event):
        super().showEvent(event)
        # Tool 窗口在 Windows 上 show() 后立即 activateWindow() 可能时机不对，
        # 导致焦点无法获取、Esc 键盘事件失效。用 singleShot 延迟执行。
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._do_activate)

    def _do_activate(self):
        self.raise_()
        self.activateWindow()
        self.setFocus()

    # ─── 绘制 ───
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 关键：WA_TranslucentBackground 的透明区域在 Windows 上会导致鼠标事件穿透。
        # 绘制一个几乎不可见的全屏背景（alpha=1），确保覆盖层整个区域都能捕获鼠标事件。
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

        if self.is_drawing and self.start_pos and self.end_pos:
            rect = self._get_rect()

            # 1 像素粗细的边框（青色，醒目）
            painter.setPen(QPen(QColor(0, 200, 255), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # 四角小标记（帮助定位）
            cross_len = 8
            painter.setPen(QPen(QColor(0, 200, 255), 2))
            # 左上
            painter.drawLine(rect.left(), rect.top() + cross_len, rect.left(), rect.top())
            painter.drawLine(rect.left(), rect.top(), rect.left() + cross_len, rect.top())
            # 右上
            painter.drawLine(rect.right() - cross_len, rect.top(), rect.right(), rect.top())
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.top() + cross_len)
            # 左下
            painter.drawLine(rect.left(), rect.bottom() - cross_len, rect.left(), rect.bottom())
            painter.drawLine(rect.left(), rect.bottom(), rect.left() + cross_len, rect.bottom())
            # 右下
            painter.drawLine(rect.right() - cross_len, rect.bottom(), rect.right(), rect.bottom())
            painter.drawLine(rect.right(), rect.bottom() - cross_len, rect.right(), rect.bottom())

            # 选区尺寸提示（显示在选区上方）
            size_text = f"{rect.width()} x {rect.height()}"
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            text_y = rect.y() - 8
            if text_y < 12:
                text_y = rect.bottom() + 16
            # 文字背景
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(size_text) + 8
            text_bg = QRect(rect.x(), text_y - 12, tw, 18)
            painter.fillRect(text_bg, QColor(0, 0, 0, 180))
            painter.drawText(text_bg, Qt.AlignmentFlag.AlignCenter, size_text)

        # ── 右下角提示 ──
        tip = "截图中… 拖拽框选，右键 / Esc 取消"
        painter.setFont(QFont("Microsoft YaHei", 9))
        fm = QFontMetrics(painter.font())
        tw = fm.horizontalAdvance(tip) + 20
        tip_rect = QRect(self.width() - tw - 12, self.height() - 32, tw, 26)
        # 右下角提示背景
        painter.fillRect(tip_rect, QColor(20, 20, 20, 230))
        painter.drawRect(tip_rect)
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(tip_rect, Qt.AlignmentFlag.AlignCenter, tip)

    # ─── 鼠标事件 ───
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.is_drawing = True
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drawing:
            self.is_drawing = False
            self.end_pos = event.pos()
            rect = self._get_rect()
            if rect.width() > 5 and rect.height() > 5:
                self._capture_rect(rect)
            else:
                self._cancel()
            self.close()

    # ─── 键盘事件 ───
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    # ─── 取消 ───
    def _cancel(self):
        self.is_drawing = False
        if self._cancelled_callback:
            self._cancelled_callback()
        self.close()

    # ─── 截图指定区域 ───
    def _capture_rect(self, rect: QRect):
        """将 QRect 区域截图为 PIL Image 并触发回调"""
        # 将局部坐标转换为全局屏幕坐标
        global_top_left = self.mapToGlobal(rect.topLeft())
        x = global_top_left.x()
        y = global_top_left.y()
        w = rect.width()
        h = rect.height()

        try:
            # 使用 pyautogui 截图，更可靠
            import core.screenshot as screenshot
            img = screenshot.screenshot_region(x, y, w, h)

            if self._captured_callback:
                self._captured_callback(img)
        except Exception as e:
            # 截图失败，调用取消回调
            if self._cancelled_callback:
                self._cancelled_callback()

    # ─── 工具 ───
    def _get_rect(self) -> QRect:
        if not self.start_pos or not self.end_pos:
            return QRect()
        x1 = min(self.start_pos.x(), self.end_pos.x())
        y1 = min(self.start_pos.y(), self.end_pos.y())
        x2 = max(self.start_pos.x(), self.end_pos.x())
        y2 = max(self.start_pos.y(), self.end_pos.y())
        return QRect(x1, y1, x2 - x1, y2 - y1)
