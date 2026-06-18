"""
截图暂存托盘组件

功能：
- 水平滚动展示已暂存的截图缩略图卡片（最多 8 张）
- 每张卡片带 ✕ 删除按钮
- 显示当前暂存数量 badge
- 暴露 add_screenshot / remove_screenshot / clear / get_all 接口
- 发出 on_changed 信号通知外部暂存列表变化
"""

from __future__ import annotations

from io import BytesIO
from typing import Callable

from PIL import Image
from PyQt6.QtCore import Qt, QBuffer, QByteArray, QSize
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy
)

_MAX_SCREENSHOTS = 8
_THUMB_W = 64
_THUMB_H = 42


def _pil_to_pixmap(img: Image.Image, w: int = _THUMB_W, h: int = _THUMB_H) -> QPixmap:
    """将 PIL Image 转为指定大小的 QPixmap 缩略图"""
    thumb = img.copy()
    thumb.thumbnail((w * 2, h * 2), Image.LANCZOS)

    # 转 RGBA 再编码 PNG
    if thumb.mode not in ("RGB", "RGBA"):
        thumb = thumb.convert("RGB")
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    tmp_bytes = BytesIO()
    thumb.save(tmp_bytes, format="PNG")
    buf.write(tmp_bytes.getvalue())
    buf.close()

    px = QPixmap()
    px.loadFromData(ba, "PNG")
    return px.scaled(w, h,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


class _ThumbCard(QFrame):
    """单张截图卡片：缩略图 + 序号 + 删除按钮"""

    def __init__(self, index: int, img: Image.Image, on_delete: Callable[[int], None]):
        super().__init__()
        self._index = index
        self._on_delete = on_delete

        self.setFixedWidth(_THUMB_W + 10)
        self.setStyleSheet("""
            QFrame {
                background: rgba(240,248,255,0.95);
                border: 1px solid rgba(100,150,220,0.35);
                border-radius: 5px;
                margin: 1px;
            }
        """)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(3, 3, 3, 2)
        vbox.setSpacing(1)

        # 缩略图
        self.lbl_thumb = QLabel()
        self.lbl_thumb.setFixedSize(_THUMB_W, _THUMB_H)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setStyleSheet("border:none; background:transparent;")
        px = _pil_to_pixmap(img)
        self.lbl_thumb.setPixmap(px)
        vbox.addWidget(self.lbl_thumb)

        # 底部：序号 + 删除按钮
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(2)

        lbl_no = QLabel(f"#{index + 1}")
        lbl_no.setStyleSheet("color:#888; font-size:8px; border:none; background:transparent;")
        footer.addWidget(lbl_no)

        footer.addStretch()

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(16, 14)
        btn_del.setStyleSheet("""
            QPushButton {
                background: rgba(200,60,60,0.15); color: #c33;
                border: 1px solid rgba(200,60,60,0.3); border-radius: 3px;
                font-size: 8px; padding: 0;
            }
            QPushButton:hover { background: rgba(200,60,60,0.35); }
        """)
        btn_del.clicked.connect(self._delete)
        footer.addWidget(btn_del)

        vbox.addLayout(footer)

    def _delete(self):
        self._on_delete(self._index)


class ScreenshotTray(QWidget):
    """
    截图暂存托盘主组件。

    使用示例：
        tray = ScreenshotTray()
        tray.set_on_changed(lambda imgs: print(len(imgs)))
        tray.add_screenshot(pil_img)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._screenshots: list[Image.Image] = []
        self._on_changed: Callable[[list[Image.Image]], None] | None = None

        self._build_ui()
        self._refresh()

    # ─── 公开接口 ───

    def set_on_changed(self, cb: Callable[[list[Image.Image]], None]):
        """截图列表变化时回调，参数为当前全部截图列表"""
        self._on_changed = cb

    def add_screenshot(self, img: Image.Image) -> bool:
        """添加一张截图到暂存区，超过上限返回 False"""
        if len(self._screenshots) >= _MAX_SCREENSHOTS:
            return False
        self._screenshots.append(img)
        self._refresh()
        if self._on_changed:
            self._on_changed(list(self._screenshots))
        return True

    def remove_screenshot(self, index: int):
        """删除指定索引的截图"""
        if 0 <= index < len(self._screenshots):
            self._screenshots.pop(index)
            self._refresh()
            if self._on_changed:
                self._on_changed(list(self._screenshots))

    def clear(self):
        """清空所有暂存截图"""
        self._screenshots.clear()
        self._refresh()
        if self._on_changed:
            self._on_changed([])

    def get_all(self) -> list[Image.Image]:
        """返回当前暂存的所有截图（副本列表）"""
        return list(self._screenshots)

    def count(self) -> int:
        return len(self._screenshots)

    def is_empty(self) -> bool:
        return len(self._screenshots) == 0

    # ─── UI 构建 ───

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 0)
        root.setSpacing(2)

        # ── 标题行（折叠标题 + 数量 badge + 清空按钮）──
        header = QHBoxLayout()
        header.setSpacing(4)

        self.lbl_title = QLabel("  📎 截图暂存区")
        self.lbl_title.setStyleSheet("color:#555; font-size:10px; font-weight:bold;")
        header.addWidget(self.lbl_title)

        self.lbl_badge = QLabel("0")
        self.lbl_badge.setFixedSize(18, 18)
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_badge.setStyleSheet("""
            QLabel {
                background: #e74c3c; color: #fff;
                border-radius: 9px; font-size: 9px; font-weight: bold;
            }
        """)
        header.addWidget(self.lbl_badge)

        header.addStretch()

        self.lbl_hint = QLabel(f"最多 {_MAX_SCREENSHOTS} 张，点击「提交分析」批量发送给 AI")
        self.lbl_hint.setStyleSheet("color:#aaa; font-size:8px;")
        header.addWidget(self.lbl_hint)

        self.btn_clear_all = QPushButton("清空")
        self.btn_clear_all.setFixedSize(36, 18)
        self.btn_clear_all.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.88); color: #c33;
                border: 1px solid rgba(200,60,60,0.3); border-radius: 3px;
                font-size: 9px; padding: 0;
            }
            QPushButton:hover { background: rgba(200,60,60,0.12); }
        """)
        self.btn_clear_all.clicked.connect(self.clear)
        header.addWidget(self.btn_clear_all)

        root.addLayout(header)

        # ── 水平滚动区（放缩略图卡片）──
        self.scroll_area = QScrollArea()
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFixedHeight(_THUMB_H + 36)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: rgba(245,248,255,0.7);
                border: 1px solid rgba(100,150,220,0.2);
                border-radius: 5px;
            }
            QScrollBar:horizontal {
                height: 6px; background: transparent;
            }
            QScrollBar::handle:horizontal {
                background: rgba(100,150,220,0.4);
                border-radius: 3px; min-width: 20px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
        """)

        self.cards_widget = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(4, 2, 4, 2)
        self.cards_layout.setSpacing(4)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # 占位提示（空时显示）
        self.lbl_empty = QLabel("暂无截图，点击上方截图按钮开始添加")
        self.lbl_empty.setStyleSheet("color:#bbb; font-size:9px;")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cards_layout.addWidget(self.lbl_empty)

        self.scroll_area.setWidget(self.cards_widget)
        root.addWidget(self.scroll_area)

    # ─── 内部刷新 ───

    def _refresh(self):
        """重建卡片区"""
        # 移除所有子组件
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        count = len(self._screenshots)
        self.lbl_badge.setText(str(count))

        # badge 颜色：空=灰色，有=红色
        if count == 0:
            self.lbl_badge.setStyleSheet("""
                QLabel {
                    background: #bbb; color: #fff;
                    border-radius: 9px; font-size: 9px; font-weight: bold;
                }
            """)
            self.lbl_empty = QLabel("暂无截图，点击上方截图按钮开始添加")
            self.lbl_empty.setStyleSheet("color:#bbb; font-size:9px;")
            self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(self.lbl_empty)
            self.lbl_hint.setStyleSheet("color:#bbb; font-size:8px;")
        else:
            self.lbl_badge.setStyleSheet("""
                QLabel {
                    background: #e74c3c; color: #fff;
                    border-radius: 9px; font-size: 9px; font-weight: bold;
                }
            """)
            self.lbl_hint.setStyleSheet("color:#888; font-size:8px;")
            for i, img in enumerate(self._screenshots):
                card = _ThumbCard(i, img, self._on_delete_card)
                self.cards_layout.addWidget(card)

            # 如果未满，显示一个"+"占位提示
            if count < _MAX_SCREENSHOTS:
                lbl_more = QLabel(f"+更多\n({count}/{_MAX_SCREENSHOTS})")
                lbl_more.setFixedSize(48, _THUMB_H + 10)
                lbl_more.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl_more.setStyleSheet("""
                    QLabel {
                        color: #aaa; font-size: 8px;
                        border: 1px dashed rgba(150,150,200,0.4);
                        border-radius: 4px;
                        background: rgba(240,240,255,0.5);
                    }
                """)
                self.cards_layout.addWidget(lbl_more)

        self.cards_layout.addStretch()
        self.btn_clear_all.setEnabled(count > 0)

    def _on_delete_card(self, index: int):
        self.remove_screenshot(index)
