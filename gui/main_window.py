"""
主窗口模块 -- 半透明浮窗界面（统一迭代轮架构 + 双栏联动）
"""

import sys
import os
import threading
import concurrent.futures
import json
import re
from PIL import Image
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton, QFrame, QScrollArea,
    QMessageBox, QApplication, QSystemTrayIcon, QMenu,
    QTabBar, QLineEdit, QStackedWidget, QSplitter
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer, QMetaObject, pyqtSlot
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor

# 确保项目根目录在 sys.path
_ROOT = os.path.dirname(os.path.abspath(os.path.join(os.path.abspath(__file__), "..")))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import core.task_manager as task_manager
import core.screenshot as screenshot
import core.llm_client as llm_client
from core.hotkey_listener import HotkeyListener
from core import window_pinner
from core import vmware_overlay
from core.op_log import op_logger
from gui.settings_dialog import SettingsDialog
from gui.screenshot_tray import ScreenshotTray


# ─────────────────────────────────────────────
#  跨线程信号桥接
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
    trigger_new_task = pyqtSignal()
    trigger_debug_fix = pyqtSignal()
    trigger_toggle_mode = pyqtSignal()


bridge = _Bridge()


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────
def _hk(action: str) -> str:
    raw = config.get_hotkey(action)
    return "+".join(p.capitalize() for p in raw.split("+"))


def _md_to_html(text: str) -> str:
    """简易 Markdown → HTML 转换（支持标题、列表、粗体、代码块、换行等）"""
    if not text:
        return ""
    html = text
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = re.sub(
        r'```(\w*)\n?(.*?)```',
        r'<pre style="background:#f4f4f4;padding:8px;border-radius:4px;color:#333;'
        r'font-family:Consolas,monospace;font-size:10px;white-space:pre-wrap;">\2</pre>',
        html, flags=re.DOTALL
    )
    html = re.sub(
        r'`(.*?)`',
        r'<code style="background:#f0f0f0;padding:1px 4px;border-radius:2px;'
        r'font-family:Consolas;font-size:10px;">\1</code>',
        html
    )
    html = re.sub(r'### (.+)$', r'<h4 style="color:#555;margin:4px 0 2px;">\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'## (.+)$', r'<h3 style="color:#444;margin:6px 0 3px;">\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'# (.+)$', r'<h2 style="color:#333;margin:8px 0 4px;">\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^- (.+)$', r'<li style="margin-left:16px;color:#444;">\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'^\d+\. (.+)$', r'<li style="margin-left:16px;color:#444;">\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#333;">\1</b>', html)
    html = html.replace("\n\n", "<br><br>").replace("\n", "<br>")
    return html


# ─────────────────────────────────────────────
#  主窗口
# ─────────────────────────────────────────────
class TranslucentWindow(QMainWindow):

    MOVE_STEP = 20

    def __init__(self):
        super().__init__()
        # ── 核心状态 ──
        self.tasks: list[dict] = []
        self.current_task_index: int = -1
        self.current_mode: str = "coding"
        self.current_sub_mode: str = "leetcode"
        self.current_round_index: int = 0   # 指向 history_rounds 的当前视图索引
        self.is_clickthrough: bool = False
        self._drag_pos: QPoint | None = None
        self._pending_screenshot: Image.Image | None = None
        # ── 截图暂存区（多张） ──
        self._staged_screenshots: list[Image.Image] = []  # 由 ScreenshotTray 托管，此处仅备份引用
        # ── AI 处理状态（取消机制） ──
        self._ai_busy: bool = False
        self._ai_token: int = 0  # 每次 AI 操作启动或取消时递增，用于丢弃过期结果

        self.hotkey_listener = HotkeyListener()
        self._init_window()
        self._init_ui()
        self._init_tray()
        self._connect_bridge()
        self._register_hotkeys()
        self._load_tasks()

    # ─────────────── 窗口属性 ───────────────
    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(config.load_config().get("window_opacity", 0.92))
        cfg = config.load_config()
        self.setGeometry(cfg.get("window_pos_x", 80), cfg.get("window_pos_y", 80), 480, 800)
        self.setWindowTitle("手撕代码助手")

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        window_pinner.start_pinner(hwnd)
        vmware_overlay.start_overlay_monitor(hwnd)

    def hideEvent(self, event):
        window_pinner.stop_pinner()
        vmware_overlay.stop_overlay_monitor()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(event, 'type') and event.type() == event.Type.ActivationChange:
            if not self.isActiveWindow() and self.isVisible():
                window_pinner.wake_pinner()

    # ─────────────── UI 构建 ───────────────
    def _init_ui(self):
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(4)

        # ── 标题栏 ──
        title_bar = QHBoxLayout()
        lbl_title = QLabel("  手撕代码助手")
        lbl_title.setStyleSheet("color:#333; font-weight:bold; font-size:13px;")
        title_bar.addWidget(lbl_title)

        self.btn_mode_coding, self.btn_mode_qa = self._mode_toggle_pair()
        title_bar.addWidget(self.btn_mode_coding)
        title_bar.addWidget(self.btn_mode_qa)

        title_bar.addStretch()

        for icon, tip, slot in [
            ("[日志]", "查看操作日志", self._open_log),
            ("[设置]", "设置", self._open_settings),
            ("[-]", "最小化", self.showMinimized),
            ("[X]", "退出", self._on_exit)
        ]:
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

        # ── 题目导航栏 ──
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(4)

        self.task_tabs = QTabBar()
        self.task_tabs.setExpanding(False)
        self.task_tabs.setTabsClosable(True)
        self.task_tabs.setStyleSheet("""
            QTabBar::tab {
                background: rgba(0,0,0,0.04); color: #555;
                border: 1px solid rgba(0,0,0,0.08); border-radius: 4px;
                padding: 3px 10px; font-size: 10px; margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: rgba(60,100,200,0.15); color: #24a;
            }
            QTabBar::close-button { image: none; width: 0; }
        """)
        self.task_tabs.currentChanged.connect(self._on_tab_changed)
        self.task_tabs.tabCloseRequested.connect(self._on_tab_close)
        nav_bar.addWidget(self.task_tabs, stretch=1)

        self.btn_new_task = self._small_btn("+ 新建", "#1a7a42")
        self.btn_new_task.setToolTip("创建新题目")
        self.btn_new_task.clicked.connect(self._new_task)
        nav_bar.addWidget(self.btn_new_task)

        self.btn_clear_all = self._small_btn("清除", "#c0392b")
        self.btn_clear_all.setToolTip("清除所有题目记录")
        self.btn_clear_all.clicked.connect(self._clear_all)
        nav_bar.addWidget(self.btn_clear_all)

        root.addLayout(nav_bar)

        # ── 状态标签 + 取消按钮 ──
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.lbl_status = QLabel("暂无题目，点击 [+ 新建] 开始")
        self.lbl_status.setStyleSheet(
            "color:#2a6; font-size:10px; padding:2px 6px;"
            "background:rgba(34,170,100,0.08); border-radius:4px;"
        )
        self.lbl_status.setWordWrap(True)
        status_row.addWidget(self.lbl_status, stretch=1)

        self.btn_cancel_ai = QPushButton("⏹ 取消")
        self.btn_cancel_ai.setFixedHeight(22)
        self.btn_cancel_ai.setFixedWidth(56)
        self.btn_cancel_ai.setStyleSheet("""
            QPushButton {
                background: rgba(192,57,43,0.12); color: #c0392b;
                border: 1.5px solid rgba(192,57,43,0.35); border-radius: 4px;
                font-size: 10px; padding: 2px 6px; font-weight: bold;
            }
            QPushButton:hover { background: rgba(192,57,43,0.22); }
        """)
        self.btn_cancel_ai.clicked.connect(self._cancel_ai)
        self.btn_cancel_ai.setVisible(False)
        status_row.addWidget(self.btn_cancel_ai)
        root.addLayout(status_row)

        # ── 主内容区 ──
        self.content_stack = QStackedWidget()
        self._init_coding_panel()
        self._init_qa_panel()
        self.content_stack.setCurrentIndex(0)
        root.addWidget(self.content_stack, stretch=10)

        # ── 快捷键速查栏 ──
        hk_bar = self._build_shortcut_bar()
        root.addLayout(hk_bar)

        central.setStyleSheet("""
            QWidget#central {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(180, 180, 180, 0.5);
                border-radius: 14px;
            }
        """)

        self._apply_mode_ui()
        self._apply_sub_mode_ui()

    def _build_shortcut_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        lbl = QLabel(f"[F1]截图入队  [F2]区域入队  [F3]上一题  [F4]下一题  [F5]穿透  [F6]模式")
        lbl.setStyleSheet("color:#aaa; font-size:8px; padding:2px 0;")
        bar.addWidget(lbl)
        return bar

    # ═══════════════════════════════════════
    #  面板初始化
    # ═══════════════════════════════════════

    def _init_coding_panel(self):
        """Coding 模式面板：题目内容 + 解题思路 + 核心代码（含版本导航）+ 统一回复框 + 对话输入"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        # === 操作按钮行 ===
        btn_row = QHBoxLayout()
        self.btn_screenshot = self._func_btn(f"全屏截图 [{_hk('screenshot')}]", "#1a7a42")
        self.btn_region = self._func_btn(f"区域截图 [{_hk('screenshot_region')}]", "#1565c0")
        self.btn_fix = self._func_btn("纠错截图", "#8a6a10")
        self.btn_screenshot.clicked.connect(self._do_screenshot)
        self.btn_region.clicked.connect(self._do_screenshot_region)
        self.btn_fix.clicked.connect(self._do_fix_screenshot)
        btn_row.addWidget(self.btn_screenshot)
        btn_row.addWidget(self.btn_region)
        btn_row.addWidget(self.btn_fix)
        layout.addLayout(btn_row)

        # === 截图暂存托盘 ===
        self.screenshot_tray = ScreenshotTray()
        self.screenshot_tray.set_on_changed(self._on_staged_changed)
        layout.addWidget(self.screenshot_tray)

        # === 提交分析按钮行 ===
        submit_row = QHBoxLayout()
        submit_row.setSpacing(4)
        self.btn_submit_screenshots = self._func_btn("🚀 提交分析", "#1a4fa0")
        self.btn_submit_screenshots.setToolTip("将暂存区所有截图一起提交给 AI 分析")
        self.btn_submit_screenshots.setEnabled(False)
        self.btn_submit_screenshots.clicked.connect(self._do_submit_screenshots)
        submit_row.addWidget(self.btn_submit_screenshots)

        self.lbl_staged_count = QLabel("暂存 0 张")
        self.lbl_staged_count.setStyleSheet("color:#888; font-size:9px;")
        submit_row.addWidget(self.lbl_staged_count)
        submit_row.addStretch()
        layout.addLayout(submit_row)

        # === ACM / LeetCode 双按钮 ===
        sub_row = QHBoxLayout()
        sub_row.setSpacing(4)
        sub_label = QLabel("代码风格")
        sub_label.setStyleSheet("color:#888; font-size:9px;")
        sub_row.addWidget(sub_label)
        self.btn_sub_leetcode = QPushButton("LC")
        self.btn_sub_acm = QPushButton("ACM")
        for b in (self.btn_sub_leetcode, self.btn_sub_acm):
            b.setFixedHeight(22)
            b.setFixedWidth(44)
        self.btn_sub_leetcode.clicked.connect(lambda: self._set_sub_mode("leetcode"))
        self.btn_sub_acm.clicked.connect(lambda: self._set_sub_mode("acm"))
        sub_row.addWidget(self.btn_sub_leetcode)
        sub_row.addWidget(self.btn_sub_acm)
        sub_row.addStretch()
        layout.addLayout(sub_row)

        # === 题目内容区 ===
        layout.addWidget(self._section_label("题目内容"))
        self.txt_question_content = QTextEdit()
        self.txt_question_content.setReadOnly(True)
        self.txt_question_content.setFont(QFont("Microsoft YaHei", 9))
        self.txt_question_content.setStyleSheet(self._textarea_style("#f9f9f9", "#444"))
        self.txt_question_content.setMaximumHeight(80)
        layout.addWidget(self.txt_question_content)

        # === 解题思路区 ===
        layout.addWidget(self._section_label("解题思路"))
        self.txt_thinking_process = QTextEdit()
        self.txt_thinking_process.setReadOnly(True)
        self.txt_thinking_process.setFont(QFont("Microsoft YaHei", 9))
        self.txt_thinking_process.setStyleSheet(
            self._textarea_style("#fff", "#333") +
            "QTextEdit{border-left:3px solid #24a;}"
        )
        self.txt_thinking_process.setMinimumHeight(80)
        layout.addWidget(self.txt_thinking_process, stretch=2)

        # === 核心代码区（版本导航 + 复制按钮） ===
        code_header = QHBoxLayout()
        code_header.setSpacing(4)
        code_header.addWidget(self._section_label("核心代码"))

        self.lbl_language_badge = QLabel("")
        self.lbl_language_badge.setStyleSheet(
            "color:#1a6fc4;font-size:9px;background:rgba(26,111,196,0.08);"
            "border-radius:3px;padding:1px 5px;"
        )
        code_header.addWidget(self.lbl_language_badge)

        self.btn_round_prev = self._small_btn("< 上一步", "#666")
        self.btn_round_prev.setFixedWidth(60)
        self.btn_round_prev.clicked.connect(self._round_prev)
        code_header.addWidget(self.btn_round_prev)

        self.lbl_round_index = QLabel("第 1 轮")
        self.lbl_round_index.setStyleSheet("color:#888;font-size:10px;")
        code_header.addWidget(self.lbl_round_index)

        self.btn_round_next = self._small_btn("下一步 >", "#666")
        self.btn_round_next.setFixedWidth(60)
        self.btn_round_next.clicked.connect(self._round_next)
        code_header.addWidget(self.btn_round_next)

        code_header.addStretch()

        self.btn_copy_code = self._small_btn("复制代码", "#1a6fc4")
        self.btn_copy_code.setToolTip("复制当前版本完整代码到剪贴板")
        self.btn_copy_code.clicked.connect(self._copy_code)
        code_header.addWidget(self.btn_copy_code)

        layout.addLayout(code_header)

        self.txt_code = QTextEdit()
        self.txt_code.setReadOnly(True)
        self.txt_code.setFont(QFont("Consolas", 10))
        self.txt_code.setStyleSheet(
            self._textarea_style("#fff", "#222") +
            "QTextEdit{border-left:3px solid #2a6;}"
        )
        self.txt_code.setMinimumHeight(100)
        layout.addWidget(self.txt_code, stretch=3)

        # === 一键纠错按钮 ===
        debug_bar = QHBoxLayout()
        debug_bar.setSpacing(4)
        self.btn_debug = self._func_btn("一键纠错", "#c0392b")
        self.btn_debug.setToolTip("对当前代码进行纠错分析")
        self.btn_debug.clicked.connect(self._do_debug)
        debug_bar.addWidget(self.btn_debug)
        debug_bar.addStretch()
        layout.addLayout(debug_bar)

        # === 统一回复框（替代原纠错分析框 + 对话显示框） ===
        layout.addWidget(self._section_label("AI 回复"))
        self.txt_unified_response = QTextEdit()
        self.txt_unified_response.setReadOnly(True)
        self.txt_unified_response.setFont(QFont("Microsoft YaHei", 9))
        self.txt_unified_response.setStyleSheet(
            self._textarea_style("#fff8f0", "#555") +
            "QTextEdit{border-left:3px solid #c0392b;}"
        )
        self.txt_unified_response.setMinimumHeight(80)
        self.txt_unified_response.setMaximumHeight(200)
        layout.addWidget(self.txt_unified_response)

        # === 对话输入 ===
        layout.addWidget(self._section_label("追问 / 对话"))
        chat_input_row = QHBoxLayout()
        chat_input_row.setSpacing(4)
        self.txt_chat_input = QLineEdit()
        self.txt_chat_input.setPlaceholderText("输入问题，回车发送...")
        self.txt_chat_input.setStyleSheet("""
            QLineEdit {
                background: #fff; color: #333; border: 1px solid rgba(0,0,0,0.1);
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
        """)
        self.txt_chat_input.returnPressed.connect(self._do_chat_send)
        chat_input_row.addWidget(self.txt_chat_input, stretch=1)

        self.btn_chat_send = QPushButton("发送")
        self.btn_chat_send.setFixedWidth(50)
        self.btn_chat_send.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.88); color: #24a;
                border: 2px solid rgba(60,100,200,0.33); border-radius: 4px;
                font-size: 11px; padding: 4px 0; font-weight: bold;
            }
            QPushButton:hover { background: rgba(60,100,200,0.10); }
        """)
        self.btn_chat_send.clicked.connect(self._do_chat_send)
        chat_input_row.addWidget(self.btn_chat_send)
        layout.addLayout(chat_input_row)

        self.content_stack.addWidget(panel)  # index 0

    def _init_qa_panel(self):
        """QA 模式面板：截图 + 统一回复框 + 追问输入"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        btn_row = QHBoxLayout()
        self.btn_screenshot_qa = self._func_btn(f"全屏截图 [{_hk('screenshot')}]", "#1a7a42")
        self.btn_region_qa = self._func_btn(f"区域截图 [{_hk('screenshot_region')}]", "#1565c0")
        self.btn_screenshot_qa.clicked.connect(self._do_screenshot)
        self.btn_region_qa.clicked.connect(self._do_screenshot_region)
        btn_row.addWidget(self.btn_screenshot_qa)
        btn_row.addWidget(self.btn_region_qa)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # === QA 截图暂存托盘（与 Coding 面板共享同一个托盘实例引用） ===
        self.screenshot_tray_qa = ScreenshotTray()
        self.screenshot_tray_qa.set_on_changed(self._on_staged_changed_qa)
        layout.addWidget(self.screenshot_tray_qa)

        # === QA 提交按钮行 ===
        qa_submit_row = QHBoxLayout()
        qa_submit_row.setSpacing(4)
        self.btn_submit_screenshots_qa = self._func_btn("🚀 提交分析", "#1a4fa0")
        self.btn_submit_screenshots_qa.setToolTip("将暂存区截图提交给 AI 回答")
        self.btn_submit_screenshots_qa.setEnabled(False)
        self.btn_submit_screenshots_qa.clicked.connect(self._do_submit_screenshots_qa)
        qa_submit_row.addWidget(self.btn_submit_screenshots_qa)

        self.lbl_staged_count_qa = QLabel("暂存 0 张")
        self.lbl_staged_count_qa.setStyleSheet("color:#888; font-size:9px;")
        qa_submit_row.addWidget(self.lbl_staged_count_qa)
        qa_submit_row.addStretch()
        layout.addLayout(qa_submit_row)

        # 版本导航（QA 模式下也有，但代码栏显示"本轮无代码"）
        qa_code_header = QHBoxLayout()
        qa_code_header.setSpacing(4)
        qa_code_header.addWidget(self._section_label("代码"))

        self.btn_qa_round_prev = self._small_btn("< 上一步", "#666")
        self.btn_qa_round_prev.setFixedWidth(60)
        self.btn_qa_round_prev.clicked.connect(self._round_prev)
        qa_code_header.addWidget(self.btn_qa_round_prev)

        self.lbl_qa_round_index = QLabel("第 1 轮")
        self.lbl_qa_round_index.setStyleSheet("color:#888;font-size:10px;")
        qa_code_header.addWidget(self.lbl_qa_round_index)

        self.btn_qa_round_next = self._small_btn("下一步 >", "#666")
        self.btn_qa_round_next.setFixedWidth(60)
        self.btn_qa_round_next.clicked.connect(self._round_next)
        qa_code_header.addWidget(self.btn_qa_round_next)

        qa_code_header.addStretch()
        layout.addLayout(qa_code_header)

        # 代码展示（QA 模式下通常为空，但保留以显示可能的代码返回）
        self.txt_qa_code = QTextEdit()
        self.txt_qa_code.setReadOnly(True)
        self.txt_qa_code.setFont(QFont("Consolas", 10))
        self.txt_qa_code.setStyleSheet(
            self._textarea_style("#fff", "#222") +
            "QTextEdit{border-left:3px solid #2a6;}"
        )
        self.txt_qa_code.setMinimumHeight(60)
        self.txt_qa_code.setMaximumHeight(120)
        layout.addWidget(self.txt_qa_code, stretch=1)

        # 统一回复框
        layout.addWidget(self._section_label("AI 回复"))
        self.txt_qa_response = QTextEdit()
        self.txt_qa_response.setReadOnly(True)
        self.txt_qa_response.setFont(QFont("Microsoft YaHei", 10))
        self.txt_qa_response.setStyleSheet(
            self._textarea_style("#fff", "#333") +
            "QTextEdit{border-left:3px solid #c0392b;}"
        )
        self.txt_qa_response.setMinimumHeight(120)
        layout.addWidget(self.txt_qa_response, stretch=4)

        # 追问输入
        layout.addWidget(self._section_label("追问"))
        qa_input_row = QHBoxLayout()
        qa_input_row.setSpacing(4)
        self.txt_qa_chat_input = QLineEdit()
        self.txt_qa_chat_input.setPlaceholderText("基于截图提问，回车发送...")
        self.txt_qa_chat_input.setStyleSheet("""
            QLineEdit {
                background: #fff; color: #333; border: 1px solid rgba(0,0,0,0.1);
                border-radius: 4px; padding: 4px 8px; font-size: 11px;
            }
        """)
        self.txt_qa_chat_input.returnPressed.connect(self._do_qa_chat_send)
        qa_input_row.addWidget(self.txt_qa_chat_input, stretch=1)

        self.btn_qa_chat_send = QPushButton("发送")
        self.btn_qa_chat_send.setFixedWidth(50)
        self.btn_qa_chat_send.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.88); color: #24a;
                border: 2px solid rgba(60,100,200,0.33); border-radius: 4px;
                font-size: 11px; padding: 4px 0; font-weight: bold;
            }
            QPushButton:hover { background: rgba(60,100,200,0.10); }
        """)
        self.btn_qa_chat_send.clicked.connect(self._do_qa_chat_send)
        qa_input_row.addWidget(self.btn_qa_chat_send)
        layout.addLayout(qa_input_row)

        self.content_stack.addWidget(panel)  # index 1

    # ═══════════════════════════════════════
    #  模式切换
    # ═══════════════════════════════════════

    def _mode_toggle_pair(self) -> tuple:
        btn_c = QPushButton("代码")
        btn_c.setFixedHeight(24)
        btn_c.setToolTip("切换到代码模式")
        btn_c.clicked.connect(lambda: self._switch_mode("coding"))
        btn_q = QPushButton("问答")
        btn_q.setFixedHeight(24)
        btn_q.setToolTip("切换到问答模式")
        btn_q.clicked.connect(lambda: self._switch_mode("qa"))
        return btn_c, btn_q

    def _switch_mode(self, mode: str):
        if self.current_mode == mode:
            return
        self.current_mode = mode
        self._apply_mode_ui()
        if self.current_task_index >= 0:
            task = self.tasks[self.current_task_index]
            task_manager.update_task(task["id"], mode=self.current_mode)
            task["mode"] = self.current_mode

    def _toggle_mode(self):
        self._switch_mode("qa" if self.current_mode == "coding" else "coding")

    def _apply_mode_ui(self):
        if self.current_mode == "coding":
            self.content_stack.setCurrentIndex(0)
            self._set_toggle_style(self.btn_mode_coding, True)
            self._set_toggle_style(self.btn_mode_qa, False)
            self.btn_sub_leetcode.setVisible(True)
            self.btn_sub_acm.setVisible(True)
        else:
            self.content_stack.setCurrentIndex(1)
            self._set_toggle_style(self.btn_mode_coding, False)
            self._set_toggle_style(self.btn_mode_qa, True)
            self.btn_sub_leetcode.setVisible(False)
            self.btn_sub_acm.setVisible(False)
        # 刷新当前题目的显示（切换面板时需要重新渲染当前轮）
        if self.current_task_index >= 0:
            self._display_current()

    def _set_sub_mode(self, mode: str):
        self.current_sub_mode = mode
        self._apply_sub_mode_ui()
        if self.current_task_index >= 0:
            task = self.tasks[self.current_task_index]
            task_manager.update_task(task["id"], sub_mode=mode)
            task["sub_mode"] = mode

    def _apply_sub_mode_ui(self):
        is_leetcode = self.current_sub_mode == "leetcode"
        self._set_toggle_style(self.btn_sub_leetcode, is_leetcode)
        self._set_toggle_style(self.btn_sub_acm, not is_leetcode)

    def _set_toggle_style(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.88); color: #1a6fc4;
                    border: 2px solid rgba(26,111,196,0.35); border-radius: 4px;
                    font-size: 10px; padding: 2px 8px; font-weight: bold;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #999;
                    border: 1px solid rgba(150,150,150,0.35); border-radius: 4px;
                    font-size: 10px; padding: 2px 8px;
                }
            """)

    # ═══════════════════════════════════════
    #  题目导航
    # ═══════════════════════════════════════

    def _new_task(self):
        label = f"题目 {task_manager.get_task_count() + 1}"
        lang = config.get_language()
        tid = task_manager.create_task(label=label, language=lang,
                                       mode=self.current_mode,
                                       sub_mode=self.current_sub_mode)
        self._load_tasks()
        for i, t in enumerate(self.tasks):
            if t["id"] == tid:
                self.current_task_index = i
                self.task_tabs.setCurrentIndex(i)
                break
        self._display_current()

    def _clear_all(self):
        count = len(self.tasks)
        if count == 0:
            return
        reply = QMessageBox.question(
            self, "确认清除",
            f"将删除全部 {count} 道题目的记录（含所有迭代历史）。\n此操作不可撤销，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            task_manager.delete_all_tasks()
            self._load_tasks()
            self.current_task_index = -1
            self._display_current()

    def _on_tab_changed(self, index: int):
        if index >= 0 and index < len(self.tasks):
            self.current_task_index = index
            task = self.tasks[index]
            saved_mode = task.get("mode", "coding")
            if self.current_mode != saved_mode:
                self.current_mode = saved_mode
                self._apply_mode_ui()
            saved_sub = task.get("sub_mode", "leetcode")
            if self.current_sub_mode != saved_sub:
                self.current_sub_mode = saved_sub
                self._apply_sub_mode_ui()
            self._display_current()

    def _on_tab_close(self, index: int):
        if 0 <= index < len(self.tasks):
            task = self.tasks[index]
            reply = QMessageBox.question(
                self, "确认删除",
                f"删除「{task.get('label', '未命名')}」及其所有迭代历史？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                task_manager.delete_task(task["id"])
                self._load_tasks()
                new_idx = min(index, len(self.tasks) - 1)
                if new_idx >= 0:
                    self.current_task_index = new_idx
                    self.task_tabs.setCurrentIndex(new_idx)
                else:
                    self.current_task_index = -1
                self._display_current()

    def _prev_question(self):
        if self.current_task_index > 0:
            self.current_task_index -= 1
            self.task_tabs.setCurrentIndex(self.current_task_index)
            self._display_current()

    def _next_question(self):
        if self.current_task_index < len(self.tasks) - 1:
            self.current_task_index += 1
            self.task_tabs.setCurrentIndex(self.current_task_index)
            self._display_current()

    # ═══════════════════════════════════════
    #  截图与 AI 调用（统一推送迭代轮）
    # ═══════════════════════════════════════

    def _do_screenshot(self):
        if not self._check_api_key():
            return
        self._set_status("正在截图...")
        QApplication.processEvents()
        img = screenshot.screenshot_full()
        self._pending_screenshot = img
        self._add_to_tray(img)

    def _do_screenshot_region(self):
        if not self._check_api_key():
            return
        self._was_visible_before_region = self.isVisible()
        self.hide()

        from gui.region_selector import RegionSelector
        self._selector = RegionSelector()

        def _restore():
            if self._was_visible_before_region:
                self.show()
                self.raise_()

        def on_captured(img):
            try:
                _restore()
                self._pending_screenshot = img
                self._add_to_tray(img)
            except Exception as e:
                self._set_status(f"截图回调异常: {e}")
                import traceback; traceback.print_exc()
            finally:
                QTimer.singleShot(100, lambda: setattr(self, '_selector', None))

        def on_cancelled():
            QTimer.singleShot(100, lambda: setattr(self, '_selector', None))
            _restore()
            self._set_status("区域截图已取消")

        self._selector.set_captured_callback(on_captured)
        self._selector.set_cancelled_callback(on_cancelled)
        self._selector.show()

    def _add_to_tray(self, img: Image.Image):
        """将截图添加到当前活跃面板的暂存托盘"""
        if self.current_mode == "qa":
            ok = self.screenshot_tray_qa.add_screenshot(img)
        else:
            ok = self.screenshot_tray.add_screenshot(img)
        if ok:
            count = self.screenshot_tray_qa.count() if self.current_mode == "qa" else self.screenshot_tray.count()
            self._set_status(f"截图已暂存（共 {count} 张），点击「🚀 提交分析」发送给 AI")
        else:
            self._set_status("截图暂存区已满（最多 8 张），请先提交或删除后再添加")

    def _on_staged_changed(self, imgs: list[Image.Image]):
        """Coding 面板暂存区变化回调"""
        self._staged_screenshots = imgs
        count = len(imgs)
        self.lbl_staged_count.setText(f"暂存 {count} 张")
        self.btn_submit_screenshots.setEnabled(count > 0)

    def _on_staged_changed_qa(self, imgs: list[Image.Image]):
        """QA 面板暂存区变化回调"""
        count = len(imgs)
        self.lbl_staged_count_qa.setText(f"暂存 {count} 张")
        self.btn_submit_screenshots_qa.setEnabled(count > 0)

    def _do_submit_screenshots(self):
        """Coding 面板：提交暂存区所有截图（每次提交自动新建题目）"""
        imgs = self.screenshot_tray.get_all()
        if not imgs:
            self._set_status("暂存区无截图，请先截图")
            return
        self._new_task()  # 每次提交自动新建题目
        self._set_status(f"正在提交 {len(imgs)} 张截图给 AI 分析...")
        QApplication.processEvents()
        # 清空托盘（提交后清空）
        self.screenshot_tray.clear()
        self._call_llm_solve_multi(imgs)

    def _do_submit_screenshots_qa(self):
        """QA 面板：提交暂存区所有截图（每次提交自动新建题目）"""
        imgs = self.screenshot_tray_qa.get_all()
        if not imgs:
            self._set_status("暂存区无截图，请先截图")
            return
        self._new_task()  # 每次提交自动新建题目
        self._set_status(f"正在提交 {len(imgs)} 张截图给 AI 分析...")
        QApplication.processEvents()
        self.screenshot_tray_qa.clear()
        self._call_llm_qa_multi(imgs)

    def _call_llm_solve(self, img: Image.Image):
        """根据 current_mode 分发到不同流程，成功后推送 initial 轮（单图，向后兼容）"""
        self._call_llm_solve_multi([img])

    def _call_llm_solve_multi(self, imgs: list[Image.Image]):
        """根据 current_mode 分发到多图流程"""
        if self.current_mode == "qa":
            self._call_llm_qa_multi(imgs)
        else:
            self._call_llm_coding_multi(imgs)

    def _call_llm_coding(self, img: Image.Image):
        """代码模式：单图入口（向后兼容，转发到多图版本）"""
        self._call_llm_coding_multi([img])

    def _call_llm_coding_multi(self, imgs: list[Image.Image]):
        """代码模式：多张截图 → LLM JSON → 推送 initial 轮到 history_rounds"""
        self._set_status(f"AI 正在分析题目（{len(imgs)} 张截图）...")
        QApplication.processEvents()

        # ★ 在主线程预取数据
        task_id = self.tasks[self.current_task_index]["id"]
        sub_mode = self.current_sub_mode
        self._ai_token += 1
        token = self._ai_token
        self._set_ai_busy(True)

        def worker():
            # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
            _EXECUTOR_TIMEOUT = 120
            print(f"[WORKER] _call_llm_coding_multi 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
            op_logger.log({
                "op": "截图解题",
                "model": config.load_config().get("vision_model", "?"),
                "img_info": f"{len(imgs)}张  尺寸={', '.join(f'{i.width}×{i.height}' for i in imgs)}",
                "status": "开始",
            })
            try:
                language = config.get_language()
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _fut = _ex.submit(
                    llm_client.solve_with_multiple_screenshots,
                    imgs, language, sub_mode
                )
                try:
                    result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                    _ex.shutdown(wait=False)  # 不等待线程结束
                    raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                _ex.shutdown(wait=False)
                print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                # 用第一张图作为代表性截图存档；多图时一并存入 user_screenshots
                b64_list = [llm_client._image_to_base64(img) for img in imgs]
                b64_primary = b64_list[0]

                # 提取字段
                if "raw" in result:
                    code, solution = llm_client.parse_code_from_response(result["raw"])
                    title = ""
                    content = ""
                    thinking = solution
                    ai_code = code
                else:
                    title = result.get("title", "")
                    content = result.get("content", "")
                    thinking = result.get("thinking_process", "")
                    ai_code = result.get("code", "")

                # ★ 调度到主线程做 DB 写入和 UI 更新
                def do_save():
                    if token != self._ai_token:
                        return  # 已取消或已被新操作取代
                    self._set_ai_busy(False)
                    try:
                        task_manager.update_task(task_id,
                            screenshot_b64=b64_primary,
                            title=title,
                            content=content,
                            thinking_process=thinking,
                            initial_code=ai_code,
                            programming_language=result.get("programming_language", language) if "raw" not in result else language,
                            language=language,
                            model=config.load_config().get("vision_model", "")
                        )
                        task_manager.push_history_round(task_id, {
                            "round_type": "initial",
                            "user_input_text": "",
                            "user_screenshots": b64_list,
                            "ai_analysis": thinking,
                            "ai_code": ai_code,
                        })
                        self._after_llm_success()
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        self._after_llm_error(str(e))

                self._pending_worker_fn = do_save


                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                import traceback; traceback.print_exc()
                def do_error():
                    if token != self._ai_token:
                        return
                    self._set_ai_busy(False)
                    self._after_llm_error(str(e))
                self._pending_worker_fn = do_error

                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    def _call_llm_qa(self, img: Image.Image):
        """QA 模式：单图入口（向后兼容）"""
        self._call_llm_qa_multi([img])

    def _call_llm_qa_multi(self, imgs: list[Image.Image]):
        """QA 模式：多张截图 → LLM JSON → 推送 initial 轮"""
        self._set_status(f"AI 正在分析图片（{len(imgs)} 张截图）...")
        QApplication.processEvents()

        # ★ 在主线程预取数据
        task_id = self.tasks[self.current_task_index]["id"]
        self._ai_token += 1
        token = self._ai_token
        self._set_ai_busy(True)

        def worker():
            # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
            _EXECUTOR_TIMEOUT = 120
            print(f"[WORKER] _call_llm_qa_multi 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
            op_logger.log({
                "op": "QA截图解题",
                "model": config.load_config().get("vision_model", "?"),
                "img_info": f"{len(imgs)}张  尺寸={', '.join(f'{i.width}×{i.height}' for i in imgs)}",
                "status": "开始",
            })
            try:
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _fut = _ex.submit(
                    llm_client.qa_solve_with_multiple_screenshots,
                    imgs
                )
                try:
                    result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                    _ex.shutdown(wait=False)
                    raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                _ex.shutdown(wait=False)
                print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                b64_list = [llm_client._image_to_base64(img) for img in imgs]
                b64_primary = b64_list[0]

                if "raw" in result:
                    ai_analysis = result["raw"]
                    ai_code = ""
                else:
                    ai_analysis = result.get("thinking_process", "") or result.get("content", "")
                    ai_code = result.get("code", "")

                # ★ 调度到主线程做 DB 写入和 UI 更新
                def do_save():
                    if token != self._ai_token:
                        return  # 已取消或已被新操作取代
                    self._set_ai_busy(False)
                    try:
                        task_manager.update_task(task_id,
                            screenshot_b64=b64_primary,
                            title=result.get("title", "") if "raw" not in result else "",
                            content=result.get("content", "") if "raw" not in result else "",
                            thinking_process=ai_analysis,
                            initial_code=ai_code,
                            programming_language=result.get("programming_language", "") if "raw" not in result else "",
                            model=config.load_config().get("vision_model", "")
                        )
                        task_manager.push_history_round(task_id, {
                            "round_type": "initial",
                            "user_input_text": "",
                            "user_screenshots": b64_list,
                            "ai_analysis": ai_analysis,
                            "ai_code": ai_code,
                        })
                        self._after_llm_success()
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        self._after_llm_error(str(e))

                self._pending_worker_fn = do_save


                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                import traceback; traceback.print_exc()
                def do_error():
                    if token != self._ai_token:
                        return
                    self._set_ai_busy(False)
                    self._after_llm_error(str(e))
                self._pending_worker_fn = do_error

                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    def _do_fix_screenshot(self):
        """纠错模式截图（区域截图）：截图 + 当前代码 → AI debug → 推送 debug 轮"""
        if not self._check_api_key():
            return
        if self.current_task_index < 0:
            self._new_task()
        self._was_visible_before_region = self.isVisible()
        self.hide()

        from gui.region_selector import RegionSelector
        self._selector = RegionSelector()

        def _restore():
            if self._was_visible_before_region:
                self.show()
                self.raise_()

        def on_captured(img):
            try:
                _restore()
                self._pending_screenshot = img
                self._set_status("区域截图完成，AI 正在分析代码错误...")
                QApplication.processEvents()

                task_idx = self.current_task_index
                # ★ 在主线程预取数据，避免工作线程访问 UI 状态
                task_id = self.tasks[task_idx]["id"]
                current_code = self._get_current_code()
                self._ai_token += 1
                token = self._ai_token
                self._set_ai_busy(True)

                def worker():
                    # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
                    _EXECUTOR_TIMEOUT = 120
                    print(f"[WORKER] _do_fix_screenshot 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
                    try:
                        language = config.get_language()
                        b64 = llm_client._image_to_base64(img)
                        _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                        _fut = _ex.submit(
                            llm_client.debug_code,
                            b64, current_code, language
                        )
                        try:
                            result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                        except concurrent.futures.TimeoutError:
                            print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                            _ex.shutdown(wait=False)
                            raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                        _ex.shutdown(wait=False)
                        print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                        # ← 结果处理
                        if "raw" in result:
                            # JSON 解析失败，把 raw 作为分析，尝试提取代码
                            ai_analysis = result["raw"]
                            ai_code = ""
                            # 尝试从 raw 中提取代码块
                            code_blocks = re.findall(r"```[\w]*\n(.*?)```", ai_analysis, re.DOTALL)
                            if code_blocks:
                                ai_code = "\n\n".join(b.strip() for b in code_blocks)
                        else:
                            ai_analysis = ""
                            ea = result.get("error_analysis", "")
                            mods = result.get("modifications", "")
                            parts = []
                            if ea:
                                parts.append(f"【错误分析】\n{ea}")
                            if mods:
                                parts.append(f"【修改说明】\n{mods}")
                            ai_analysis = "\n\n".join(parts) if parts else "代码未见明显错误。"
                            ai_code = result.get("fixed_code", "")

                        # ★ 调度到主线程做 DB 写入和 UI 更新
                        def do_save():
                            if token != self._ai_token:
                                return  # 已取消或已被新操作取代
                            self._set_ai_busy(False)
                            try:
                                # 如果 debug 返回了新代码，更新 initial_code
                                if ai_code:
                                    task_manager.update_task(task_id, initial_code=ai_code)

                                # 推送 debug 轮
                                task_manager.push_history_round(task_id, {
                                    "round_type": "debug",
                                    "user_input_text": "",
                                    "user_screenshots": [b64],
                                    "ai_analysis": ai_analysis,
                                    "ai_code": ai_code,
                                })

                                self._after_debug_success()
                            except Exception as e:
                                import traceback; traceback.print_exc()
                                self._after_debug_error(str(e))

                        self._pending_worker_fn = do_save


                        QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        def do_error():
                            if token != self._ai_token:
                                return
                            self._set_ai_busy(False)
                            self._after_debug_error(str(e))
                        self._pending_worker_fn = do_error

                        QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

                threading.Thread(target=worker, daemon=True).start()
            except Exception as e:
                self._set_status(f"截图回调异常: {e}")
                import traceback; traceback.print_exc()
            finally:
                QTimer.singleShot(100, lambda: setattr(self, '_selector', None))

        def on_cancelled():
            QTimer.singleShot(100, lambda: setattr(self, '_selector', None))
            _restore()
            self._set_status("区域截图已取消")

        self._selector.set_captured_callback(on_captured)
        self._selector.set_cancelled_callback(on_cancelled)
        self._selector.show()

    def _do_debug(self):
        """一键纠错（用已保存的截图）"""
        if self.current_task_index < 0:
            self._set_status("请先截图解题再进行纠错")
            return
        task = self.tasks[self.current_task_index]
        if not task.get("screenshot_b64"):
            self._set_status("该题目还没有初始化，请先截图解题")
            return

        current_code = self._get_current_code()
        if not current_code:
            self._set_status("当前无代码可纠错")
            return

        self._set_status("AI 正在分析代码错误...")
        self.btn_debug.setEnabled(False)
        QApplication.processEvents()

        # ★ 在主线程预取数据，避免工作线程访问 UI 状态
        task_idx = self.current_task_index
        task_id = task["id"]
        screenshot_b64 = task["screenshot_b64"]
        language = task.get("language", "python")
        self._ai_token += 1
        token = self._ai_token
        self._set_ai_busy(True)

        def worker():
            # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
            _EXECUTOR_TIMEOUT = 120
            print(f"[WORKER] _do_debug 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
            try:
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _fut = _ex.submit(
                    llm_client.debug_code,
                    screenshot_b64, current_code, language
                )
                try:
                    result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                    _ex.shutdown(wait=False)
                    raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                _ex.shutdown(wait=False)
                print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                if "raw" in result:
                    ai_analysis = result["raw"]
                    ai_code = ""
                    code_blocks = re.findall(r"```[\w]*\n(.*?)```", ai_analysis, re.DOTALL)
                    if code_blocks:
                        ai_code = "\n\n".join(b.strip() for b in code_blocks)
                else:
                    ea = result.get("error_analysis", "")
                    mods = result.get("modifications", "")
                    parts = []
                    if ea:
                        parts.append(f"【错误分析】\n{ea}")
                    if mods:
                        parts.append(f"【修改说明】\n{mods}")
                    ai_analysis = "\n\n".join(parts) if parts else "代码未见明显错误。"
                    ai_code = result.get("fixed_code", "")

                # ★ 调度到主线程做 DB 写入和 UI 更新
                def do_save():
                    if token != self._ai_token:
                        return  # 已取消或已被新操作取代
                    self._set_ai_busy(False)
                    try:
                        if ai_code:
                            task_manager.update_task(task_id, initial_code=ai_code)

                        task_manager.push_history_round(task_id, {
                            "round_type": "debug",
                            "user_input_text": "",
                            "user_screenshots": [],
                            "ai_analysis": ai_analysis,
                            "ai_code": ai_code,
                        })

                        self._after_debug_success()
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        self._after_debug_error(str(e))

                self._pending_worker_fn = do_save


                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                import traceback; traceback.print_exc()
                def do_error():
                    if token != self._ai_token:
                        return
                    self._set_ai_busy(False)
                    self._after_debug_error(str(e))
                self._pending_worker_fn = do_error

                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════
    #  对话 / 追问（统一推送 chat/qa 轮）
    # ═══════════════════════════════════════

    def _do_chat_send(self):
        """代码模式：对话输入 → LLM → 推送 chat 轮"""
        text = self.txt_chat_input.text().strip()
        if not text:
            return
        task = self.tasks[self.current_task_index] if self.current_task_index >= 0 else None
        if not task or not task.get("screenshot_b64"):
            self._set_status("请先截图解题再开始对话")
            return
        self.txt_chat_input.clear()
        self._set_status("AI 思考中...")
        QApplication.processEvents()

        # ★ 在主线程预取数据，避免工作线程访问 UI 状态
        task_id = task["id"]
        screenshot_b64 = task["screenshot_b64"]
        current_code = self._get_current_code()
        language = task.get("language", "python")
        # 构建对话历史（最近 6 轮，跳过 initial）
        _hist = []
        _rounds = task.get("history_rounds", [])
        _recent = [r for r in _rounds if r.get("round_type") != "initial"]
        if len(_recent) > 6:
            _recent = _recent[-6:]
        for _r in _recent:
            _u = _r.get("user_input_text", "").strip()
            _a = _r.get("ai_analysis", "").strip()
            if _u:
                _hist.append({"role": "user", "content": _u})
            if _a:
                _hist.append({"role": "assistant", "content": _a})
        self._ai_token += 1
        token = self._ai_token
        self._set_ai_busy(True)

        def worker():
            # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
            _EXECUTOR_TIMEOUT = 120
            print(f"[WORKER] _do_chat_send 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
            try:
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _fut = _ex.submit(
                    llm_client.chat_with_context,
                    screenshot_b64, current_code, text, language, _hist
                )
                try:
                    result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                    _ex.shutdown(wait=False)
                    raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                _ex.shutdown(wait=False)
                print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                # result: {"ai_analysis": "...", "ai_code": "..."}
                ai_analysis = result.get("ai_analysis", "")
                ai_code = result.get("ai_code", "")

                # ★ 调度到主线程做 DB 写入和 UI 更新
                def do_save():
                    if token != self._ai_token:
                        return  # 已取消或已被新操作取代
                    self._set_ai_busy(False)
                    try:
                        # 如果返回了新代码，更新
                        if ai_code:
                            task_manager.update_task(task_id, initial_code=ai_code)

                        # 推送 chat 轮
                        task_manager.push_history_round(task_id, {
                            "round_type": "chat",
                            "user_input_text": text,
                            "user_screenshots": [],
                            "ai_analysis": ai_analysis,
                            "ai_code": ai_code,
                        })

                        self._after_chat_success()
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        self._after_chat_error(str(e))

                self._pending_worker_fn = do_save


                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                import traceback; traceback.print_exc()
                def do_error():
                    if token != self._ai_token:
                        return
                    self._set_ai_busy(False)
                    self._after_chat_error(str(e))
                self._pending_worker_fn = do_error

                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    def _do_qa_chat_send(self):
        """QA 模式：追问输入 → LLM → 推送 qa 轮"""
        text = self.txt_qa_chat_input.text().strip()
        if not text:
            return
        task = self.tasks[self.current_task_index] if self.current_task_index >= 0 else None
        if not task or not task.get("screenshot_b64"):
            self._set_status("请先截图再开始对话")
            return
        self.txt_qa_chat_input.clear()
        self._set_status("AI 思考中...")
        QApplication.processEvents()

        # ★ 在主线程预取数据，避免工作线程访问 UI 状态
        task_id = task["id"]
        screenshot_b64 = task["screenshot_b64"]
        # 构建对话历史（最近 6 轮，跳过 initial）
        _hist = []
        _rounds = task.get("history_rounds", [])
        _recent = [r for r in _rounds if r.get("round_type") != "initial"]
        if len(_recent) > 6:
            _recent = _recent[-6:]
        for _r in _recent:
            _u = _r.get("user_input_text", "").strip()
            _a = _r.get("ai_analysis", "").strip()
            if _u:
                _hist.append({"role": "user", "content": _u})
            if _a:
                _hist.append({"role": "assistant", "content": _a})
        self._ai_token += 1
        token = self._ai_token
        self._set_ai_busy(True)

        def worker():
            # ── ThreadPoolExecutor 超时兜底（不用 with 块，避免 __exit__ 阻塞） ──
            _EXECUTOR_TIMEOUT = 120
            print(f"[WORKER] _do_qa_chat_send 开始，超时={_EXECUTOR_TIMEOUT}s", flush=True)
            try:
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _fut = _ex.submit(
                    llm_client.qa_chat,
                    screenshot_b64, text, _hist
                )
                try:
                    result = _fut.result(timeout=_EXECUTOR_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    print(f"[TIMEOUT] LLM 调用超时（{_EXECUTOR_TIMEOUT}s），强制取消", flush=True)
                    _ex.shutdown(wait=False)
                    raise TimeoutError(f"AI 分析超时（{_EXECUTOR_TIMEOUT}s），已自动取消")
                _ex.shutdown(wait=False)
                print("[WORKER] LLM 返回成功，开始处理结果", flush=True)

                # result: {"ai_analysis": "...", "ai_code": ""}
                ai_analysis = result.get("ai_analysis", "")

                # ★ 调度到主线程做 DB 写入和 UI 更新
                def do_save():
                    if token != self._ai_token:
                        return  # 已取消或已被新操作取代
                    self._set_ai_busy(False)
                    try:
                        # 推送 qa 轮
                        task_manager.push_history_round(task_id, {
                            "round_type": "qa",
                            "user_input_text": text,
                            "user_screenshots": [],
                            "ai_analysis": ai_analysis,
                            "ai_code": "",
                        })

                        self._after_chat_success()
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        self._after_chat_error(str(e))

                self._pending_worker_fn = do_save


                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)
            except Exception as e:
                import traceback; traceback.print_exc()
                def do_error():
                    if token != self._ai_token:
                        return
                    self._set_ai_busy(False)
                    self._after_chat_error(str(e))
                self._pending_worker_fn = do_error

                QMetaObject.invokeMethod(self, "_run_pending_worker_fn", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════
    #  迭代轮版本导航
    # ═══════════════════════════════════════

    def _copy_code(self):
        """复制当前视图显示的完整代码到剪贴板"""
        code = self._get_current_code()
        if code:
            QApplication.clipboard().setText(code)
            self._set_status("代码已复制到剪贴板")
        else:
            self._set_status("本轮无代码可复制")

    def _round_prev(self):
        """上一步：current_round_index -= 1"""
        if self.current_task_index < 0:
            return
        task = self.tasks[self.current_task_index]
        rounds = task.get("history_rounds", [])
        if self.current_round_index > 0:
            self.current_round_index -= 1
            self._display_round()
            self._sync_round_index_to_other_panel()

    def _round_next(self):
        """下一步：current_round_index += 1"""
        if self.current_task_index < 0:
            return
        task = self.tasks[self.current_task_index]
        rounds = task.get("history_rounds", [])
        if self.current_round_index < len(rounds) - 1:
            self.current_round_index += 1
            self._display_round()
            self._sync_round_index_to_other_panel()

    def _sync_round_index_to_other_panel(self):
        """同步更新 QA 面板上的版本导航标签（如果两个面板都显示同一个 index）"""
        task = self.tasks[self.current_task_index] if self.current_task_index >= 0 else None
        if not task:
            return
        rounds = task.get("history_rounds", [])
        total = len(rounds)
        if total == 0:
            return
        idx = self.current_round_index
        label = f"第 {idx+1}/{total} 轮"
        rtype = rounds[idx].get("round_type", "") if idx < total else ""
        if rtype:
            label += f" ({rtype})"

        # 更新 Coding 面板标签
        self.lbl_round_index.setText(label)
        self.btn_round_prev.setEnabled(idx > 0)
        self.btn_round_next.setEnabled(idx < total - 1)

        # 更新 QA 面板标签
        if hasattr(self, 'lbl_qa_round_index'):
            self.lbl_qa_round_index.setText(label)
            self.btn_qa_round_prev.setEnabled(idx > 0)
            self.btn_qa_round_next.setEnabled(idx < total - 1)

    def _display_round(self):
        """根据 current_round_index 渲染代码栏 + 统一回复框"""
        if self.current_task_index < 0:
            return
        task = self.tasks[self.current_task_index]
        rounds = task.get("history_rounds", [])
        total = len(rounds)

        if total == 0:
            # 无历史轮次，显示 task 初始字段
            self.txt_code.setPlainText(task.get("initial_code", ""))
            # initial 轮的分析在"解题思路"区展示，回复栏留空
            self.txt_unified_response.setPlainText("")
            if hasattr(self, 'txt_qa_code'):
                self.txt_qa_code.setPlainText("")
            if hasattr(self, 'txt_qa_response'):
                self.txt_qa_response.setPlainText("")
            return

        idx = self.current_round_index
        if idx < 0 or idx >= total:
            idx = total - 1
            self.current_round_index = idx

        r = rounds[idx]
        rtype = r.get("round_type", "")
        ai_code = r.get("ai_code", "")
        ai_analysis = r.get("ai_analysis", "")

        # ── Coding 面板 ──
        if ai_code:
            self.txt_code.setPlainText(ai_code)
        else:
            self.txt_code.setPlainText("【本轮无代码变更】")

        # 回复栏策略：
        #   - initial 轮：分析已在"解题思路"区展示，回复栏留空
        #   - debug / chat / qa 轮：纯文本显示 AI 回复（不用 Markdown 渲染）
        if rtype == "initial":
            self.txt_unified_response.setPlainText("")
        else:
            self.txt_unified_response.setPlainText(ai_analysis if ai_analysis else "")

        # ── QA 面板 ──
        if hasattr(self, 'txt_qa_code'):
            if ai_code:
                self.txt_qa_code.setPlainText(ai_code)
            else:
                self.txt_qa_code.setPlainText("【本轮无代码变更】")
        if hasattr(self, 'txt_qa_response'):
            if rtype == "initial":
                self.txt_qa_response.setPlainText("")
            else:
                self.txt_qa_response.setPlainText(ai_analysis if ai_analysis else "")

        # ── 版本标签 ──
        self._sync_round_index_to_other_panel()

    def _get_current_code(self) -> str:
        """获取当前视图显示的代码（版本感知）"""
        if self.current_task_index < 0:
            return ""
        task = self.tasks[self.current_task_index]
        rounds = task.get("history_rounds", [])
        total = len(rounds)

        if total == 0 or self.current_round_index < 0:
            return task.get("initial_code", "")
        idx = self.current_round_index
        if idx >= total:
            idx = total - 1
        r = rounds[idx]
        return r.get("ai_code", "") or task.get("initial_code", "")

    # ═══════════════════════════════════════
    #  UI 刷新
    # ═══════════════════════════════════════

    def _display_current(self):
        if self.current_task_index < 0 or not self.tasks:
            self._clear_panels()
            self._set_status("暂无题目，点击 [+ 新建] 开始")
            return

        task = self.tasks[self.current_task_index]

        # 确保 history_rounds 已加载
        if "history_rounds" not in task or not task["history_rounds"]:
            # 尝试从数据库重新加载（可能刚迁移完）
            task["history_rounds"] = task_manager.get_history_rounds(task["id"])
            # 如果仍为空且有 initial_code，自动生成一个 initial 轮
            if not task["history_rounds"] and task.get("initial_code"):
                task_manager.push_history_round(task["id"], {
                    "round_type": "initial",
                    "user_input_text": "",
                    "user_screenshots": [task.get("screenshot_b64", "")],
                    "ai_analysis": task.get("thinking_process", ""),
                    "ai_code": task.get("initial_code", ""),
                })
                task["history_rounds"] = task_manager.get_history_rounds(task["id"])

        rounds = task.get("history_rounds", [])
        # current_round_index 指向最新轮
        if rounds:
            self.current_round_index = len(rounds) - 1
        else:
            self.current_round_index = 0

        # 渲染题目内容 + 语言标签
        content = task.get("content", "")
        if not content:
            content = task.get("title", "")
        self.txt_question_content.setPlainText(content or "(无题目内容)")

        thinking = task.get("thinking_process", "") or task.get("initial_solution", "")
        self.txt_thinking_process.setHtml(
            _md_to_html(thinking) if thinking else "<i style='color:#999'>暂无分析</i>"
        )

        prog_lang = task.get("programming_language", task.get("language", ""))
        self.lbl_language_badge.setText(prog_lang if prog_lang else "")

        # 渲染当前轮
        self._display_round()

        # 状态栏
        idx = self.current_task_index + 1
        total = len(self.tasks)
        label = task.get("label", "未命名")
        mode_label = "代码模式" if task.get("mode", "coding") == "coding" else "问答模式"
        sub_label = ""
        if task.get("mode", "coding") == "coding":
            sub_label = " | ACM" if task.get("sub_mode") == "acm" else " | LC"
        self._set_status(f"[{idx}/{total}] {label} | {mode_label}{sub_label} | {prog_lang}")

    def _clear_panels(self):
        self.txt_question_content.clear()
        self.txt_thinking_process.clear()
        self.txt_code.setPlainText("")
        self.txt_unified_response.clear()
        if hasattr(self, 'txt_qa_code'):
            self.txt_qa_code.clear()
        if hasattr(self, 'txt_qa_response'):
            self.txt_qa_response.clear()
        self.lbl_round_index.setText("第 1 轮")
        self.lbl_language_badge.setText("")
        self.btn_round_prev.setEnabled(False)
        self.btn_round_next.setEnabled(False)

    def _reload_current_task(self):
        if self.current_task_index < 0:
            return
        task = self.tasks[self.current_task_index]
        full = task_manager.get_full_task(task["id"])
        if full:
            # 保留内存中的 history_rounds（已从 DB 加载）
            full["history_rounds"] = task_manager.get_history_rounds(task["id"])
            self.tasks[self.current_task_index] = full

    # ═══════════════════════════════════════
    #  LLM 回调（跨线程）
    # ═══════════════════════════════════════

    @pyqtSlot()
    def _run_pending_worker_fn(self):
        """执行由 worker 线程通过 QMetaObject.invokeMethod 调度的回调函数。
        
        PyQt6 中 QTimer.singleShot(0, callback) 在非主线程（threading.Thread）
        中创建的定时器永远不会触发，因为该线程没有运行 Qt 事件循环。
        改用 QMetaObject.invokeMethod 配合 self._pending_worker_fn 属性实现跨线程调度。
        """
        fn = getattr(self, '_pending_worker_fn', None)
        if fn:
            self._pending_worker_fn = None
            fn()

    def _after_llm_success(self):
        QMetaObject.invokeMethod(self, "_on_llm_done", Qt.ConnectionType.QueuedConnection)

    def _after_llm_error(self, msg: str):
        self._llm_error_msg = msg
        QMetaObject.invokeMethod(self, "_on_llm_error", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _on_llm_done(self):
        self._reload_current_task()
        self._display_current()
        self._set_status("分析完成！")

    @pyqtSlot()
    def _on_llm_error(self):
        msg = getattr(self, "_llm_error_msg", "未知错误")
        self._set_status("调用失败")
        QMessageBox.warning(self, "LLM 调用失败",
            f"{msg}\n\n可能原因：\n"
            "1. 模型不支持图片（需 vision 模型，如 gpt-4o）\n"
            "2. API Key / Endpoint 有误\n"
            "3. 网络超时或余额不足\n\n请点击 [设置] 按钮检查配置。"
        )

    def _after_debug_success(self):
        QMetaObject.invokeMethod(self, "_on_debug_done", Qt.ConnectionType.QueuedConnection)

    def _after_debug_error(self, msg: str):
        self._debug_error_msg = msg
        QMetaObject.invokeMethod(self, "_on_debug_error", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _on_debug_done(self):
        self.btn_debug.setEnabled(True)
        self._reload_current_task()
        self._display_current()
        self._set_status("纠错完成！")

    @pyqtSlot()
    def _on_debug_error(self):
        self.btn_debug.setEnabled(True)
        msg = getattr(self, "_debug_error_msg", "未知错误")
        self._set_status("纠错失败")
        QMessageBox.warning(self, "纠错失败", msg)

    def _after_chat_success(self):
        QMetaObject.invokeMethod(self, "_on_chat_done", Qt.ConnectionType.QueuedConnection)

    def _after_chat_error(self, msg: str):
        self._chat_error_msg = msg
        QMetaObject.invokeMethod(self, "_on_chat_error", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _on_chat_done(self):
        self._reload_current_task()
        self._display_current()
        self._set_status("对话回复已就绪")

    @pyqtSlot()
    def _on_chat_error(self):
        msg = getattr(self, "_chat_error_msg", "未知错误")
        self._set_status("对话失败")
        QMessageBox.warning(self, "对话失败", msg)

    # ═══════════════════════════════════════
    #  状态管理
    # ═══════════════════════════════════════

    def _load_tasks(self):
        """从数据库加载所有题目，并触发旧数据迁移"""
        self.tasks = task_manager.get_all_full_tasks()

        # 对每道题触发迁移（如果 history_rounds 为空且有旧数据）
        for t in self.tasks:
            rounds = t.get("history_rounds", [])
            if not rounds:
                # 尝试迁移
                migrated = task_manager.migrate_to_history_rounds(t["id"])
                if migrated:
                    # 重新加载
                    t["history_rounds"] = task_manager.get_history_rounds(t["id"])

        self.task_tabs.blockSignals(True)
        while self.task_tabs.count() > 0:
            self.task_tabs.removeTab(0)

        for t in self.tasks:
            label = t.get("label", "未命名")
            mode_icon = "[Q]" if t.get("mode") == "qa" else "[C]"
            self.task_tabs.addTab(f"{mode_icon} {label}")

        if self.current_task_index >= 0 and self.current_task_index < len(self.tasks):
            self.task_tabs.setCurrentIndex(self.current_task_index)
        elif self.tasks:
            self.current_task_index = len(self.tasks) - 1
            self.task_tabs.setCurrentIndex(self.current_task_index)
        else:
            self.current_task_index = -1

        self.task_tabs.blockSignals(False)
        self.btn_clear_all.setEnabled(len(self.tasks) > 0)
        self._display_current()

    def _set_status(self, text: str):
        self.lbl_status.setText("  " + text)

    def _set_ai_busy(self, busy: bool):
        """设置 AI 处理状态：控制取消按钮显隐 + 提交按钮可用性"""
        self._ai_busy = busy
        self.btn_cancel_ai.setVisible(busy)
        # AI 处理中禁用提交按钮，防止重复提交
        if hasattr(self, 'btn_submit_screenshots'):
            self.btn_submit_screenshots.setEnabled(
                not busy and self.screenshot_tray.count() > 0
            )
        if hasattr(self, 'btn_submit_screenshots_qa'):
            self.btn_submit_screenshots_qa.setEnabled(
                not busy and self.screenshot_tray_qa.count() > 0
            )

    def _cancel_ai(self):
        """取消正在进行的 AI 处理（通过 token 机制丢弃过期结果）"""
        if not self._ai_busy:
            return
        self._ai_token += 1
        self._set_ai_busy(False)
        # 恢复可能被禁用的按钮
        if hasattr(self, 'btn_debug'):
            self.btn_debug.setEnabled(True)
        self._set_status("已取消 AI 处理")

    def _check_api_key(self) -> bool:
        if not config.load_config().get("api_key", "").strip():
            QMessageBox.warning(self, "未配置 API",
                "请先点击右上角 [设置] 按钮，填写 API Endpoint 和 API Key")
            self._open_settings()
            return False
        return True

    # ═══════════════════════════════════════
    #  快捷键 & 桥接
    # ═══════════════════════════════════════

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
        bridge.trigger_new_task.connect(self._new_task)
        bridge.trigger_debug_fix.connect(self._do_debug)
        bridge.trigger_toggle_mode.connect(self._toggle_mode)

    def _register_hotkeys(self):
        self.hotkey_listener.register("toggle_window",       lambda: bridge.trigger_toggle_window.emit())
        self.hotkey_listener.register("screenshot",          lambda: bridge.trigger_screenshot.emit())
        self.hotkey_listener.register("screenshot_region",   lambda: bridge.trigger_screenshot_region.emit())
        self.hotkey_listener.register("prev_question",       lambda: bridge.trigger_prev.emit())
        self.hotkey_listener.register("next_question",       lambda: bridge.trigger_next.emit())
        self.hotkey_listener.register("toggle_clickthrough", lambda: bridge.trigger_clickthrough.emit())
        self.hotkey_listener.register("move_left",           lambda: bridge.trigger_move_left.emit())
        self.hotkey_listener.register("move_right",          lambda: bridge.trigger_move_right.emit())
        self.hotkey_listener.register("move_up",             lambda: bridge.trigger_move_up.emit())
        self.hotkey_listener.register("move_down",           lambda: bridge.trigger_move_down.emit())
        self.hotkey_listener.register("new_task",            lambda: bridge.trigger_new_task.emit())
        self.hotkey_listener.register("debug_fix",           lambda: bridge.trigger_debug_fix.emit())
        self.hotkey_listener.register("toggle_mode",         lambda: bridge.trigger_toggle_mode.emit())
        self.hotkey_listener.start()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            hwnd = int(self.winId())
            window_pinner.update_hwnd(hwnd)
            vmware_overlay.update_overlay_hwnd(hwnd)
            self.activateWindow()

    def _toggle_clickthrough(self):
        self.is_clickthrough = not self.is_clickthrough
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self.is_clickthrough)
        self.show()
        hwnd = int(self.winId())
        window_pinner.update_hwnd(hwnd)
        vmware_overlay.update_overlay_hwnd(hwnd)
        tip = "点击穿透 ON (再按切回)" if self.is_clickthrough else "点击穿透 OFF"
        self._set_status(tip)

    def _move_window(self, dx: int, dy: int):
        self.move(self.x() + dx, self.y() + dy)

    # ═══════════════════════════════════════
    #  设置
    # ═══════════════════════════════════════

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.hotkey_listener.refresh()
            self.setWindowOpacity(config.load_config().get("window_opacity", 0.92))
            self._refresh_hotkey_labels()

    def _open_log(self):
        """打开设置对话框并自动切到日志标签页"""
        dlg = SettingsDialog(self)
        # 切到日志 tab（最后一个 tab）
        tab_count = dlg.tabs.count()
        for i in range(tab_count):
            if dlg.tabs.tabText(i) == "日志":
                dlg.tabs.setCurrentIndex(i)
                break
        dlg.exec()

    def _refresh_hotkey_labels(self):
        hk_ss = _hk("screenshot")
        hk_region = _hk("screenshot_region")
        self.btn_screenshot.setText(f"全屏截图 [{hk_ss}]")
        self.btn_region.setText(f"区域截图 [{hk_region}]")
        if hasattr(self, 'btn_screenshot_qa'):
            self.btn_screenshot_qa.setText(f"全屏截图 [{hk_ss}]")
        if hasattr(self, 'btn_region_qa'):
            self.btn_region_qa.setText(f"区域截图 [{hk_region}]")

    # ═══════════════════════════════════════
    #  窗口拖拽
    # ═══════════════════════════════════════

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

    # ═══════════════════════════════════════
    #  托盘
    # ═══════════════════════════════════════

    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("手撕代码助手")
        icon = self._create_tray_icon_pixmap()
        self.tray_icon.setIcon(QIcon(icon))
        menu = QMenu()
        menu.addAction("显示 / 隐藏窗口", self._toggle_window)
        menu.addAction("切换点击穿透", self._toggle_clickthrough)
        menu.addSeparator()
        menu.addAction("新建题目", self._new_task)
        menu.addAction("切换模式", self._toggle_mode)
        menu.addSeparator()
        menu.addAction("设置", self._open_settings)
        menu.addSeparator()
        menu.addAction("退出", self._on_exit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _create_tray_icon_pixmap(self) -> QPixmap:
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
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_window()

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

    # ═══════════════════════════════════════
    #  辅助 UI 组件
    # ═══════════════════════════════════════

    def _small_btn(self, text: str, accent: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.88); color: {accent};
                border: 1.5px solid {accent}44; border-radius: 4px;
                font-size: 10px; padding: 2px 8px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {accent}14; }}
        """)
        return b

    def _func_btn(self, text: str, accent: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.92); color: {accent};
                border: 2px solid {accent}55; border-radius: 6px;
                padding: 4px 8px; font-size: 10px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {accent}18; }}
            QPushButton:disabled {{ background: rgba(220,220,220,0.5); color:#aaa; border-color:#ccc; }}
        """)
        return b

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(f"  {text}")
        lbl.setStyleSheet("color:#555; font-size:10px; font-weight:bold;")
        return lbl

    def _textarea_style(self, bg: str, fg: str) -> str:
        return f"""
            QTextEdit {{
                background: {bg}; color: {fg};
                border: 1px solid rgba(0,0,0,0.08); border-radius: 6px;
                padding: 4px 6px; font-size: 10px;
            }}
            QTextEdit:read-only {{ background: {bg}; }}
        """
