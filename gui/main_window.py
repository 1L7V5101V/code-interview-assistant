"""
主窗口模块 -- 半透明浮窗界面（白色主题）
"""

import sys
import os
import threading
import ctypes
from PIL import Image
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton, QFrame,
    QMessageBox, QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor

# 确保项目根目录在 sys.path
_ROOT = os.path.dirname(os.path.abspath(os.path.join(os.path.abspath(__file__), "..")))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import core.history_manager as history_manager
import core.screenshot as screenshot
import core.llm_client as llm_client
from core.hotkey_listener import HotkeyListener
from core import window_pinner
from core import vmware_overlay
from gui.settings_dialog import SettingsDialog


# ─────────────────────────────────────────────
#  用于跨线程发送信号（全局快捷键在后台线程触发）
# ─────────────────────────────────────────────
class _Bridge(QObject):
    trigger_screenshot = pyqtSignal()
    trigger_screenshot_region = pyqtSignal()
    trigger_toggle_window = pyqtSignal()
    trigger_prev = pyqtSignal()
    trigger_next = pyqtSignal()
    trigger_clickthrough = pyqtSignal()
    trigger_move_left = pyqtSignal()
    trigger_move_right = pyqtSignal()
    trigger_move_up = pyqtSignal()
    trigger_move_down = pyqtSignal()

bridge = _Bridge()


# ─────────────────────────────────────────────
#  快捷键标签辅助
# ─────────────────────────────────────────────
def _hk(action: str) -> str:
    """把 config 里的快捷键格式化为更友好的大写显示，如 ctrl+h -> Ctrl+H"""
    raw = config.get_hotkey(action)
    return "+".join(p.capitalize() for p in raw.split("+"))


# ─────────────────────────────────────────────
#  主窗口
# ─────────────────────────────────────────────
class TranslucentWindow(QMainWindow):

    MOVE_STEP = 20  # 方向键每次移动像素

    def __init__(self):
        super().__init__()
        self.current_index = -1
        self.history_list = []
        self.is_clickthrough = False
        self._drag_pos = None

        self.hotkey_listener = HotkeyListener()
        self._init_window()
        self._init_ui()
        self._init_tray()
        self._connect_bridge()
        self._register_hotkeys()
        self._load_history()

        # 不再使用 Qt 定时器，改用 window_pinner 独立高优先级线程置顶
        # 窗口首次显示时会在 showEvent 中启动 pinner

    # ── 窗口显示/隐藏事件 ──
    def showEvent(self, event):
        """窗口显示时启动强力置顶线程 + VMware 全屏监控"""
        super().showEvent(event)
        hwnd = int(self.winId())
        window_pinner.start_pinner(hwnd)
        vmware_overlay.start_overlay_monitor(hwnd)

    def hideEvent(self, event):
        """窗口隐藏时停止强力置顶线程 + VMware 全屏监控"""
        window_pinner.stop_pinner()
        vmware_overlay.stop_overlay_monitor()
        super().hideEvent(event)

    def changeEvent(self, event):
        """窗口激活状态变化时，立即重新置顶"""
        super().changeEvent(event)
        if hasattr(event, 'type') and event.type() == event.Type.ActivationChange:
            if not self.isActiveWindow() and self.isVisible():
                # 窗口被停用时（如用户点击了其他窗口），立即唤醒置顶
                window_pinner.wake_pinner()
                # 注意：不在这里 stop/start overlay monitor，
                # 因为 stop 会清除 Owner 关系，导致 VMware 全屏时助手被覆盖。
                # overlay monitor 本身会持续运行并维护 Owner 关系。

    # ─────────────── 窗口属性 ───────────────
    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(config.load_config().get("window_opacity", 0.92))
        cfg = config.load_config()
        self.setGeometry(cfg.get("window_pos_x", 80), cfg.get("window_pos_y", 80), 440, 680)
        self.setWindowTitle("手撕代码助手")

    # ─────────────── UI ───────────────
    def _init_ui(self):
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── 标题栏 ──────────────────────
        title_bar = QHBoxLayout()
        lbl_title = QLabel("  手撕代码助手")
        lbl_title.setStyleSheet("color:#333; font-weight:bold; font-size:13px;")
        title_bar.addWidget(lbl_title)
        title_bar.addStretch()

        for icon, tip, slot in [("[设置]", "设置", self._open_settings),
                                  ("[-]", "最小化", self.showMinimized),
                                  ("[X]", "退出", self._on_exit)]:
            b = QPushButton(icon)
            b.setFixedSize(36, 26)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            b.setStyleSheet("""
                QPushButton{background:transparent;color:#888;border:none;font-size:11px;}
                QPushButton:hover{background:rgba(0,0,0,0.06);border-radius:4px;}
            """)
            title_bar.addWidget(b)
        root.addLayout(title_bar)

        # ── 状态标签 ────────────────────
        self.lbl_status = QLabel("暂无题目")
        self.lbl_status.setStyleSheet(
            "color:#2a6; font-size:11px; padding:2px 4px;"
            "background:rgba(34,170,100,0.08); border-radius:4px;"
        )
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        # ── 代码区 ──────────────────────
        lbl_code = QLabel("  代码答案")
        lbl_code.setStyleSheet("color:#555;font-size:11px;")
        root.addWidget(lbl_code)

        self.txt_code = QTextEdit()
        self.txt_code.setReadOnly(True)
        self.txt_code.setFont(QFont("Consolas", 10))
        self.txt_code.setStyleSheet(self._textarea_style("#fff", "#222"))
        self.txt_code.setMinimumHeight(200)
        root.addWidget(self.txt_code, stretch=3)

        # ── 思路区 ──────────────────────
        lbl_sol = QLabel("  解题思路")
        lbl_sol.setStyleSheet("color:#555;font-size:11px;")
        root.addWidget(lbl_sol)

        self.txt_solution = QTextEdit()
        self.txt_solution.setReadOnly(True)
        self.txt_solution.setFont(QFont("Microsoft YaHei", 10))
        self.txt_solution.setStyleSheet(self._textarea_style("#fff", "#333"))
        self.txt_solution.setMinimumHeight(130)
        root.addWidget(self.txt_solution, stretch=2)

        # ── 导航按钮 ────────────────────
        nav = QHBoxLayout()
        hk_prev = _hk("prev_question")
        hk_next = _hk("next_question")
        self.btn_prev = self._nav_btn(f"< 上一题 [{hk_prev}]")
        self.btn_next = self._nav_btn(f"下一题 [{hk_next}] >")
        self.btn_prev.clicked.connect(self._prev_question)
        self.btn_next.clicked.connect(self._next_question)
        nav.addWidget(self.btn_prev)
        nav.addStretch()
        nav.addWidget(self.btn_next)
        root.addLayout(nav)

        # ── 功能按钮 ────────────────────
        hk_ss      = _hk("screenshot")
        hk_region  = _hk("screenshot_region")
        hk_fix     = "Ctrl+H 同截图键"

        func = QHBoxLayout()
        self.btn_screenshot = self._func_btn(f"全屏截图 [{hk_ss}]", "#1a7a42")
        self.btn_region      = self._func_btn(f"区域截图 [{hk_region}]", "#1565c0")
        self.btn_fix        = self._func_btn("纠错模式", "#8a6a10")
        self.btn_screenshot.clicked.connect(self._do_screenshot)
        self.btn_region.clicked.connect(self._do_screenshot_region)
        self.btn_fix.clicked.connect(self._do_fix)
        func.addWidget(self.btn_screenshot)
        func.addWidget(self.btn_region)
        func.addWidget(self.btn_fix)
        root.addLayout(func)

        # ── 快捷键速查栏 ─────────────────
        hk_bar = QFrame()
        hk_bar.setStyleSheet("QFrame{background:rgba(0,0,0,0.04);border-radius:6px;}")
        hk_layout = QVBoxLayout(hk_bar)
        hk_layout.setContentsMargins(8, 5, 8, 5)
        hk_layout.setSpacing(2)

        lbl_hk_title = QLabel("快捷键")
        lbl_hk_title.setStyleSheet("color:#555;font-size:10px;font-weight:bold;")
        hk_layout.addWidget(lbl_hk_title)

        hotkey_rows = [
            ("toggle_window",      "显示/隐藏窗口"),
            ("screenshot",         "全屏截图解题"),
            ("screenshot_region",  "区域截图解题"),
            ("prev_question",      "上一题"),
            ("next_question",      "下一题"),
            ("toggle_clickthrough","切换点击穿透"),
            ("move_left",          "窗口左移"),
            ("move_right",         "窗口右移"),
            ("move_up",            "窗口上移"),
            ("move_down",          "窗口下移"),
        ]
        grid = QHBoxLayout()
        col1 = QVBoxLayout()
        col2 = QVBoxLayout()
        for i, (action, desc) in enumerate(hotkey_rows):
            key_lbl = QLabel(_hk(action))
            key_lbl.setStyleSheet(
                "color:#c60; font-size:10px; font-family:Consolas;"
                "background:rgba(200,100,0,0.07); border-radius:3px; padding:0 4px;"
            )
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color:#666; font-size:10px;")
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(key_lbl)
            row.addWidget(desc_lbl)
            row.addStretch()
            target = col1 if i < 5 else col2
            target_w = QWidget()
            target_w.setLayout(row)
            target.addWidget(target_w)
        col1.addStretch()
        col2.addStretch()
        grid.addLayout(col1)
        grid.addLayout(col2)
        hk_layout.addLayout(grid)
        root.addWidget(hk_bar)

        # ── 主容器样式 ───────────────────
        central.setStyleSheet("""
            QWidget#central {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(180, 180, 180, 0.5);
                border-radius: 14px;
            }
        """)

    def _textarea_style(self, bg: str, fg: str) -> str:
        return f"""
            QTextEdit {{
                background: {bg};
                color: {fg};
                border: 1px solid rgba(180,180,180,0.4);
                border-radius: 6px;
                padding: 6px;
            }}
            QScrollBar:vertical {{
                background: rgba(220,220,220,0.6); width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(160,160,160,0.5); border-radius: 3px;
            }}
        """

    def _nav_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet("""
            QPushButton {
                background: rgba(60,100,200,0.12); color: #336;
                border: 1px solid rgba(60,100,200,0.2); border-radius: 7px;
                padding: 5px 14px; font-size: 11px; text-align:center;
            }
            QPushButton:hover { background: rgba(60,100,200,0.22); }
            QPushButton:disabled { background: rgba(200,200,200,0.3); color:#aaa; border-color:#ddd; }
        """)
        return b

    def _func_btn(self, text: str, bg_color: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {bg_color}22; color: {bg_color};
                border: 1px solid {bg_color}44; border-radius: 7px;
                padding: 6px 10px; font-size: 11px; text-align:center;
            }}
            QPushButton:hover {{ background: {bg_color}33; }}
        """)
        return b

    # ─────────────── 桥接信号（跨线程） ───────────────
    def _connect_bridge(self):
        bridge.trigger_screenshot.connect(self._do_screenshot)
        bridge.trigger_screenshot_region.connect(self._do_screenshot_region)
        bridge.trigger_toggle_window.connect(self._toggle_window)
        bridge.trigger_prev.connect(self._prev_question)
        bridge.trigger_next.connect(self._next_question)
        bridge.trigger_clickthrough.connect(self._toggle_clickthrough)
        bridge.trigger_move_left.connect(lambda: self._move_window(-self.MOVE_STEP, 0))
        bridge.trigger_move_right.connect(lambda: self._move_window(self.MOVE_STEP, 0))
        bridge.trigger_move_up.connect(lambda: self._move_window(0, -self.MOVE_STEP))
        bridge.trigger_move_down.connect(lambda: self._move_window(0, self.MOVE_STEP))

    # ─────────────── 全局快捷键注册 ───────────────
    def _register_hotkeys(self):
        self.hotkey_listener.register("toggle_window",       lambda: bridge.trigger_toggle_window.emit())
        self.hotkey_listener.register("screenshot",          lambda: bridge.trigger_screenshot.emit())
        self.hotkey_listener.register("screenshot_region",   lambda: bridge.trigger_screenshot_region.emit())
        self.hotkey_listener.register("prev_question",      lambda: bridge.trigger_prev.emit())
        self.hotkey_listener.register("next_question",      lambda: bridge.trigger_next.emit())
        self.hotkey_listener.register("toggle_clickthrough",lambda: bridge.trigger_clickthrough.emit())
        self.hotkey_listener.register("move_left",          lambda: bridge.trigger_move_left.emit())
        self.hotkey_listener.register("move_right",         lambda: bridge.trigger_move_right.emit())
        self.hotkey_listener.register("move_up",            lambda: bridge.trigger_move_up.emit())
        self.hotkey_listener.register("move_down",          lambda: bridge.trigger_move_down.emit())
        self.hotkey_listener.start()

    # ─────────────── 功能回调 ───────────────
    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            # 窗口显示后刷新 pinner 和 overlay 的窗口句柄（Qt 可能重建了原生窗口）
            hwnd = int(self.winId())
            window_pinner.update_hwnd(hwnd)
            vmware_overlay.update_overlay_hwnd(hwnd)
            self.activateWindow()

    def _toggle_clickthrough(self):
        self.is_clickthrough = not self.is_clickthrough
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self.is_clickthrough)
        self.show()
        # 点击穿透切换后刷新 pinner 和 overlay 的窗口句柄
        hwnd = int(self.winId())
        window_pinner.update_hwnd(hwnd)
        vmware_overlay.update_overlay_hwnd(hwnd)
        tip = "点击穿透 ON (再按切回)" if self.is_clickthrough else "点击穿透 OFF"
        self._set_status(tip)

    def _move_window(self, dx: int, dy: int):
        """按方向键移动窗口"""
        self.move(self.x() + dx, self.y() + dy)

    def _check_api_key(self) -> bool:
        if not config.load_config().get("api_key", "").strip():
            QMessageBox.warning(self, "未配置 API",
                "请先点击右上角 [设置] 按钮，填写 API Endpoint 和 API Key")
            self._open_settings()
            return False
        return True

    # ── 全屏截图 ──
    def _do_screenshot(self):
        if not self._check_api_key():
            return
        self._set_status("正在截图，请稍候...")
        QApplication.processEvents()
        img = screenshot.screenshot_full()
        self._call_llm(img, mode="solve")

    # ── 区域截图 ──
    def _do_screenshot_region(self):
        if not self._check_api_key():
            return
        # 先隐藏主窗口，避免遮挡选区
        self._was_visible_before_region = self.isVisible()
        self.hide()

        from gui.region_selector import RegionSelector
        self._selector = RegionSelector()

        def _restore_window():
            """恢复主窗口显示（showEvent 会自动启动 pinner 置顶）"""
            if self._was_visible_before_region:
                self.show()
                self.raise_()

        def on_captured(img):
            try:
                _restore_window()
                self._set_status("区域截图完成，AI 分析中...")
                self._call_llm(img, mode="solve")
            except Exception as e:
                self._set_status(f"区域截图回调异常: {e}")
                import traceback; traceback.print_exc()
            finally:
                # 延迟清理引用，避免在 Qt 事件处理中立即销毁对象
                QTimer.singleShot(100, lambda: setattr(self, '_selector', None))

        def on_cancelled():
            QTimer.singleShot(100, lambda: setattr(self, '_selector', None))
            _restore_window()
            self._set_status("区域截图已取消")

        self._selector.set_captured_callback(on_captured)
        self._selector.set_cancelled_callback(on_cancelled)
        self._selector.show()

    # ── 纠错 ──
    def _do_fix(self):
        if not self._check_api_key():
            return
        self._set_status("全屏截图中（纠错模式）...")
        QApplication.processEvents()
        img = screenshot.screenshot_full()
        self._call_llm(img, mode="fix")

    # ── 统一 LLM 调用（在线程中执行，避免 UI 卡死） ──
    def _call_llm(self, img, mode: str = "solve"):
        self._set_status("AI 正在分析...")
        QApplication.processEvents()

        def worker():
            try:
                language = config.get_language()
                if mode == "fix":
                    raw = llm_client.fix_code_with_screenshot(img, language)
                    q_tag = "[纠错]"
                else:
                    raw = llm_client.solve_with_screenshot(img, language)
                    q_tag = ""

                code, solution = llm_client.parse_code_from_response(raw)
                history_manager.add_entry(
                    question_text=q_tag,
                    code=code,
                    solution=solution,
                    language=language,
                    model=config.load_config().get("vision_model", "")
                )
                # 回到主线程刷新 UI
                self._after_llm_success()
            except Exception as e:
                self._after_llm_error(str(e))

        self._pending_worker = threading.Thread(target=worker, daemon=True)
        self._pending_worker.start()

    def _after_llm_success(self):
        from PyQt6.QtCore import QMetaObject, Qt as QtNS
        QMetaObject.invokeMethod(self, "_on_llm_done", QtNS.ConnectionType.QueuedConnection)

    def _after_llm_error(self, msg: str):
        from PyQt6.QtCore import QMetaObject, Qt as QtNS
        self._llm_error_msg = msg
        QMetaObject.invokeMethod(self, "_on_llm_error", QtNS.ConnectionType.QueuedConnection)

    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot()
    def _on_llm_done(self):
        self._load_history()
        self.current_index = len(self.history_list) - 1
        self._display_current()

    @pyqtSlot()
    def _on_llm_error(self):
        msg = getattr(self, "_llm_error_msg", "未知错误")
        self._set_status("调用失败")
        QMessageBox.warning(self, "LLM 调用失败",
            f"{msg}\n\n"
            "可能原因：\n"
            "1. 模型不支持图片（需 vision 模型，如 gpt-4o）\n"
            "2. API Key / Endpoint 有误\n"
            "3. 网络超时或余额不足\n\n"
            "请点击 [设置] 按钮检查配置。"
        )

    # ─────────────── 导航 ───────────────
    def _prev_question(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._display_current()

    def _next_question(self):
        if self.current_index < len(self.history_list) - 1:
            self.current_index += 1
            self._display_current()

    def _display_current(self):
        if not self.history_list or self.current_index < 0:
            return
        entry = self.history_list[self.current_index]
        self.txt_code.setPlainText(entry.get("code") or "")
        self.txt_solution.setPlainText(entry.get("solution") or "")
        total = len(self.history_list)
        idx   = self.current_index + 1
        lang  = entry.get("language", "")
        tag   = entry.get("question_text", "")
        self._set_status(f"题目 #{entry['id']}  [{idx}/{total}]  [{lang}]  {tag}")
        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < total - 1)

    def _load_history(self):
        self.history_list = history_manager.get_all_asc()
        if self.history_list:
            self.current_index = len(self.history_list) - 1
            self._display_current()
        else:
            self.current_index = -1
            self.txt_code.clear()
            self.txt_solution.clear()
            self._set_status("暂无题目，按快捷键或点按钮截图")

    def _set_status(self, text: str):
        self.lbl_status.setText("  " + text)

    # ─────────────── 设置 ───────────────
    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.hotkey_listener.refresh()
            self.setWindowOpacity(config.load_config().get("window_opacity", 0.92))
            self._refresh_hotkey_labels()

    def _refresh_hotkey_labels(self):
        hk_ss     = _hk("screenshot")
        hk_region = _hk("screenshot_region")
        hk_prev   = _hk("prev_question")
        hk_next   = _hk("next_question")
        self.btn_screenshot.setText(f"全屏截图 [{hk_ss}]")
        self.btn_region.setText(f"区域截图 [{hk_region}]")
        self.btn_prev.setText(f"< 上一题 [{hk_prev}]")
        self.btn_next.setText(f"下一题 [{hk_next}] >")

    # ─────────────── 窗口拖拽 ───────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self._drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        cfg = config.load_config()
        cfg["window_pos_x"] = self.x()
        cfg["window_pos_y"] = self.y()
        config.save_config(cfg)

    # ─────────────── 托盘图标 ───────────────
    def _init_tray(self):
        """初始化系统托盘图标，作为快捷键失效时的备用操作入口"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("手撕代码助手")

        # 创建一个简单的图标
        icon = self._create_tray_icon_pixmap()
        self.tray_icon.setIcon(QIcon(icon))

        # 托盘菜单
        menu = QMenu()
        menu.addAction("显示 / 隐藏窗口", self._toggle_window)
        menu.addAction("切换点击穿透", self._toggle_clickthrough)
        menu.addSeparator()
        menu.addAction("设置", self._open_settings)
        menu.addSeparator()
        menu.addAction("退出", self._on_exit)
        self.tray_icon.setContextMenu(menu)

        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _create_tray_icon_pixmap(self) -> QPixmap:
        """生成一个简易的托盘图标（绿底白字 C）"""
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor("#2a6"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#fff"))
        painter.setFont(QFont("Consolas", 32))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "C")
        painter.end()
        return pixmap

    def _tray_activated(self, reason):
        """双击托盘图标切换窗口显示"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_window()

    # ─────────────── 退出 ───────────────
    def _on_exit(self):
        window_pinner.stop_pinner()
        self.hotkey_listener.stop()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        window_pinner.stop_pinner()
        self.hotkey_listener.stop()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        event.accept()
