"""
设置弹窗
配置 API Endpoint、API Key、模型、编程语言、快捷键
白色主题
"""

import sys
import os
import json
import base64
import io
from PIL import Image
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QFormLayout, QMessageBox, QTabWidget, QWidget, QTextEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
import config
from core.op_log import op_logger, format_log_text


LANGUAGES = ["python", "java", "cpp", "c", "go", "javascript", "typescript", "rust", "swift", "kotlin"]
HOTKEY_ACTIONS = {
    "toggle_window": "显示/隐藏窗口",
    "screenshot": "截图解题",
    "screenshot_region": "区域截图",
    "prev_question": "上一题",
    "next_question": "下一题",
    "toggle_clickthrough": "切换点击穿透",
    "move_left": "窗口左移",
    "move_right": "窗口右移",
    "move_up": "窗口上移",
    "move_down": "窗口下移",
    "new_task": "新建题目",
    "debug_fix": "一键纠错",
    "toggle_mode": "切换代码/问答模式",
}


# ─────────────────────────────────────────────
#  用 raw HTTP 测试 API，绕过 openai SDK 版本差异
# ─────────────────────────────────────────────
def _raw_chat_completion(endpoint: str, api_key: str, model: str,
                          messages: list, max_tokens: int = 10,
                          image_b64: str = None) -> str:
    """
    用 raw HTTP POST 调用 OpenAI 兼容 API，
    返回 response JSON 中的 content 字符串。
    """
    import urllib.request
    import ssl

    # 构建 URL
    if endpoint.rstrip("/").endswith("/v1"):
        url = endpoint.rstrip("/") + "/chat/completions"
    else:
        url = endpoint.rstrip("/") + "/v1/chat/completions"

    # 构建请求体
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    # 忽略 SSL 验证（本地服务不需要）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        # 尝试解析为 JSON 错误
        try:
            err_json = json.loads(error_body)
            raise RuntimeError(json.dumps(err_json, ensure_ascii=False))
        except (json.JSONDecodeError, RuntimeError):
            raise RuntimeError(f"HTTP {e.code}: {error_body[:300]}")
    # 提取 content
    content = result["choices"][0]["message"]["content"]
    return content


def _raw_vision_completion(endpoint: str, api_key: str, model: str) -> str:
    """
    用 raw HTTP POST 测试视觉模型（发送 1x1 像素 PNG）
    """
    import urllib.request
    import ssl

    if endpoint.rstrip("/").endswith("/v1"):
        url = endpoint.rstrip("/") + "/chat/completions"
    else:
        url = endpoint.rstrip("/") + "/v1/chat/completions"

    # 生成 1x1 像素测试图
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    body = {
        "model": model,
        "max_tokens": 10,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(error_body)
            raise RuntimeError(json.dumps(err_json, ensure_ascii=False))
        except (json.JSONDecodeError, RuntimeError):
            raise RuntimeError(f"HTTP {e.code}: {error_body[:300]}")
    content = result["choices"][0]["message"]["content"]
    return content


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)
        self.setStyleSheet("""
            QDialog {
                background: rgba(255, 255, 255, 0.97);
                border-radius: 10px;
                border: 1px solid rgba(180,180,180,0.5);
            }
            QLabel { color: #333; font-size: 12px; }
            QLineEdit {
                background: #fff;
                color: #222;
                border: 1px solid rgba(160,160,160,0.5);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border: 1px solid rgba(60,100,200,0.6); }
            QComboBox {
                background: #fff;
                color: #222;
                border: 1px solid rgba(160,160,160,0.5);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QComboBox QAbstractItemView {
                background: #fff;
                color: #222;
                selection-background-color: rgba(60, 100, 200, 0.15);
            }
            QPushButton {
                background: rgba(60, 100, 200, 0.12);
                color: #336;
                border: 1px solid rgba(60,100,200,0.25);
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover { background: rgba(60, 100, 200, 0.22); }
            QPushButton#btn_test_text { background: rgba(40,160,80,0.12); color: #264; border-color: rgba(40,160,80,0.3); }
            QPushButton#btn_test_text:hover { background: rgba(40,160,80,0.22); }
            QPushButton#btn_test_vision { background: rgba(200,120,0,0.12); color: #a50; border-color: rgba(200,120,0,0.3); }
            QPushButton#btn_test_vision:hover { background: rgba(200,120,0,0.22); }
        """)

        self.cfg = config.load_config()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid rgba(180,180,180,0.4); border-radius: 6px; background: #fff; }
            QTabBar::tab {
                background: rgba(240,240,240,0.8); color: #666;
                padding: 6px 16px; border-radius: 4px;
            }
            QTabBar::tab:selected { background: #fff; color: #333; border: 1px solid rgba(180,180,180,0.4); }
        """)

        # ── API 设置 Tab ──
        tab_api = QWidget()
        form_api = QFormLayout(tab_api)
        form_api.setSpacing(8)

        self.txt_endpoint = QLineEdit(self.cfg.get("api_endpoint", ""))
        self.txt_endpoint.setPlaceholderText("http://localhost:3000")
        form_api.addRow("API Endpoint:", self.txt_endpoint)

        self.txt_api_key = QLineEdit(self.cfg.get("api_key", ""))
        self.txt_api_key.setPlaceholderText("sk-...")
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        form_api.addRow("API Key:", self.txt_api_key)

        self.txt_model = QLineEdit(self.cfg.get("model", ""))
        self.txt_model.setPlaceholderText("qwen-plus")
        form_api.addRow("文本模型:", self.txt_model)

        h_test_text = QHBoxLayout()
        h_test_text.addWidget(QLabel(""))
        self.btn_test_text = QPushButton("测试文本模型连接")
        self.btn_test_text.setObjectName("btn_test_text")
        self.btn_test_text.clicked.connect(self._test_text_model)
        h_test_text.addWidget(self.btn_test_text)
        h_test_text.addStretch()
        form_api.addRow(h_test_text)

        self.txt_vision_model = QLineEdit(self.cfg.get("vision_model", ""))
        self.txt_vision_model.setPlaceholderText("qwen-vl-plus")
        form_api.addRow("视觉模型:", self.txt_vision_model)

        h_test_vision = QHBoxLayout()
        h_test_vision.addWidget(QLabel(""))
        self.btn_test_vision = QPushButton("测试视觉模型连接")
        self.btn_test_vision.setObjectName("btn_test_vision")
        self.btn_test_vision.clicked.connect(self._test_vision_model)
        h_test_vision.addWidget(self.btn_test_vision)
        h_test_vision.addStretch()
        form_api.addRow(h_test_vision)

        tip_label = QLabel("提示：填好后分别点击两个测试按钮验证连接")
        tip_label.setStyleSheet("color:#999; font-size:10px; padding:4px 0;")
        form_api.addRow(tip_label)

        self.tabs.addTab(tab_api, "API 配置")

        # ── 常规设置 Tab ──
        tab_gen = QWidget()
        form_gen = QFormLayout(tab_gen)
        form_gen.setSpacing(8)

        self.cmb_language = QComboBox()
        self.cmb_language.addItems(LANGUAGES)
        cur_lang = self.cfg.get("language", "python")
        idx = self.cmb_language.findText(cur_lang)
        if idx >= 0:
            self.cmb_language.setCurrentIndex(idx)
        form_gen.addRow("编程语言:", self.cmb_language)

        self.txt_opacity = QLineEdit(str(self.cfg.get("window_opacity", 0.92)))
        self.txt_opacity.setPlaceholderText("0.1 ~ 1.0")
        form_gen.addRow("窗口透明度:", self.txt_opacity)

        self.tabs.addTab(tab_gen, "常规")

        # ── 快捷键设置 Tab ──
        tab_hk = QWidget()
        form_hk = QFormLayout(tab_hk)
        form_hk.setSpacing(8)

        self.hotkey_edits = {}
        hotkeys = self.cfg.get("hotkeys", {})
        for action, label in HOTKEY_ACTIONS.items():
            edit = QLineEdit(hotkeys.get(action, ""))
            edit.setPlaceholderText("如 ctrl+h")
            form_hk.addRow(f"{label}:", edit)
            self.hotkey_edits[action] = edit

        self.tabs.addTab(tab_hk, "快捷键")

        # ── 提示词设置 Tab ──
        tab_prompt = QWidget()
        layout_prompt = QVBoxLayout(tab_prompt)
        layout_prompt.setSpacing(8)

        # 选择器 + 重置按钮
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("编辑提示词："))
        self.cmb_prompt = QComboBox()
        self.cmb_prompt.setMinimumWidth(180)
        self.prompt_keys = list(config.PROMPT_LABELS.keys())
        for key in self.prompt_keys:
            self.cmb_prompt.addItem(config.PROMPT_LABELS[key])
        self.cmb_prompt.currentIndexChanged.connect(self._on_prompt_selected)
        selector_row.addWidget(self.cmb_prompt, stretch=1)

        self.btn_prompt_reset = QPushButton("重置此项为默认")
        self.btn_prompt_reset.clicked.connect(self._reset_current_prompt)
        selector_row.addWidget(self.btn_prompt_reset)

        self.btn_prompt_reset_all = QPushButton("重置全部")
        self.btn_prompt_reset_all.clicked.connect(self._reset_all_prompts)
        selector_row.addWidget(self.btn_prompt_reset_all)
        layout_prompt.addLayout(selector_row)

        # 提示词编辑框
        self.txt_prompt = QTextEdit()
        self.txt_prompt.setFont(QFont("Microsoft YaHei", 10))
        self.txt_prompt.setStyleSheet("""
            QTextEdit {
                background: #fff; color: #222;
                border: 1px solid rgba(160,160,160,0.5);
                border-radius: 4px; padding: 8px;
            }
            QTextEdit:focus { border: 1px solid rgba(60,100,200,0.6); }
        """)
        self.txt_prompt.setMinimumHeight(200)
        layout_prompt.addWidget(self.txt_prompt)

        # 加载默认选中的提示词
        self._prompt_values = {}  # 缓存当前编辑值
        self._on_prompt_selected(0)

        self.tabs.addTab(tab_prompt, "提示词")

        # ── 日志 Tab ──
        tab_log = QWidget()
        layout_log = QVBoxLayout(tab_log)
        layout_log.setSpacing(8)

        log_btn_row = QHBoxLayout()
        self.btn_refresh_log = QPushButton("刷新日志")
        self.btn_refresh_log.clicked.connect(self._refresh_log)
        log_btn_row.addWidget(self.btn_refresh_log)

        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.setStyleSheet("QPushButton { background: rgba(200,60,60,0.12); color: #a33; border-color: rgba(200,60,60,0.3); } QPushButton:hover { background: rgba(200,60,60,0.22); }")
        self.btn_clear_log.clicked.connect(self._clear_log)
        log_btn_row.addWidget(self.btn_clear_log)

        self.lbl_log_count = QLabel(f"共 {op_logger.count()} 条")
        self.lbl_log_count.setStyleSheet("color:#999; font-size:10px;")
        log_btn_row.addWidget(self.lbl_log_count)
        log_btn_row.addStretch()
        layout_log.addLayout(log_btn_row)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 9))
        self.txt_log.setStyleSheet("""
            QTextEdit {
                background: #1e1e1e; color: #d4d4d4;
                border: 1px solid rgba(160,160,160,0.5);
                border-radius: 4px; padding: 8px;
                font-size: 12px;
            }
        """)
        self.txt_log.setMinimumHeight(300)
        layout_log.addWidget(self.txt_log)

        # 首次加载日志
        self._refresh_log()

        self.tabs.addTab(tab_log, "日志")

        layout.addWidget(self.tabs)

        # ── 底部按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)

        layout.addLayout(btn_layout)

    def _test_text_model(self):
        endpoint = self.txt_endpoint.text().strip()
        api_key = self.txt_api_key.text().strip()
        model = self.txt_model.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先填写 API Key")
            return
        if not model:
            QMessageBox.warning(self, "提示", "请先填写文本模型名称")
            return
        try:
            content = _raw_chat_completion(
                endpoint, api_key, model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            QMessageBox.information(self, "成功", f"文本模型 连接测试成功！\n\n模型返回：{content[:50]}")
        except Exception as e:
            self._show_test_error("文本模型", e)

    def _test_vision_model(self):
        endpoint = self.txt_endpoint.text().strip()
        api_key = self.txt_api_key.text().strip()
        model = self.txt_vision_model.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先填写 API Key")
            return
        if not model:
            QMessageBox.warning(self, "提示", "请先填写视觉模型名称")
            return
        try:
            content = _raw_vision_completion(endpoint, api_key, model)
            QMessageBox.information(self, "成功", f"视觉模型 连接测试成功！\n\n模型返回：{content[:50]}")
        except Exception as e:
            self._show_test_error("视觉模型", e)

    def _show_test_error(self, label: str, e: Exception):
        err_msg = str(e)
        detail = ""
        if "url error" in err_msg.lower() or "bad_response_status_code" in err_msg.lower():
            detail = (
                f"\n\n可能原因：\n"
                f"1. One API 渠道中【{label}】对应的模型名填写有误\n"
                f"2. One API 渠道未启用或余额不足\n"
                f"3. 模型不支持该功能（如文本模型不支持图片输入）\n\n"
                f"请登录 One API 管理页面确认渠道配置。\n"
                f"原始错误：{err_msg[:400]}"
            )
        elif "<!doctype" in err_msg.lower() or "<html" in err_msg.lower():
            detail = "\n\nAPI 返回了 HTML 页面。请检查 Endpoint 地址是否正确。"
        else:
            detail = f"\n\n原始错误：{err_msg[:400]}"
        QMessageBox.warning(self, "失败", f"{label} 连接测试失败：{detail}")

    def _save(self):
        # 保存当前正在编辑的提示词
        self._save_current_prompt_value()

        hotkeys = {}
        for action, edit in self.hotkey_edits.items():
            val = edit.text().strip()
            if val:
                hotkeys[action] = val

        self.cfg["api_endpoint"] = self.txt_endpoint.text().strip()
        self.cfg["api_key"] = self.txt_api_key.text().strip()
        self.cfg["model"] = self.txt_model.text().strip()
        self.cfg["vision_model"] = self.txt_vision_model.text().strip()
        self.cfg["language"] = self.cmb_language.currentText()
        try:
            opacity = float(self.txt_opacity.text().strip())
            if 0.1 <= opacity <= 1.0:
                self.cfg["window_opacity"] = opacity
        except ValueError:
            pass
        if hotkeys:
            self.cfg["hotkeys"] = hotkeys

        # 保存提示词（只保存与默认值不同的）
        prompts = {}
        for key in self._prompt_values:
            val = self._prompt_values[key]
            default = config.DEFAULT_PROMPTS.get(key, "")
            if val.strip() != default.strip():
                prompts[key] = val
        self.cfg["prompts"] = prompts

        config.save_config(self.cfg)
        self.accept()

    # ── 提示词编辑 ──────────────────────────

    def _on_prompt_selected(self, index: int):
        """切换提示词选择 — 先保存当前值，再加载新值"""
        if not hasattr(self, "txt_prompt") or not hasattr(self, "_prompt_values"):
            return
        # 保存当前编辑中的值
        self._save_current_prompt_value()
        # 加载新选中的提示词
        key = self.prompt_keys[index]
        if key not in self._prompt_values:
            self._prompt_values[key] = config.get_prompt(key)
        self.txt_prompt.setPlainText(self._prompt_values[key])

    def _save_current_prompt_value(self):
        """把当前文本框内容存入缓存"""
        idx = self.cmb_prompt.currentIndex()
        if idx < 0:
            return
        key = self.prompt_keys[idx]
        self._prompt_values[key] = self.txt_prompt.toPlainText()

    def _reset_current_prompt(self):
        """重置当前选中的提示词为默认值"""
        idx = self.cmb_prompt.currentIndex()
        if idx < 0:
            return
        key = self.prompt_keys[idx]
        default = config.DEFAULT_PROMPTS.get(key, "")
        self._prompt_values[key] = default
        self.txt_prompt.setPlainText(default)

    def _reset_all_prompts(self):
        """重置全部提示词为默认值"""
        reply = QMessageBox.question(
            self, "确认", "将全部提示词重置为默认值？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for key in self.prompt_keys:
            self._prompt_values[key] = config.DEFAULT_PROMPTS.get(key, "")
        self._on_prompt_selected(self.cmb_prompt.currentIndex())

    # ── 日志操作 ──────────────────────────

    def _refresh_log(self):
        """刷新日志面板内容"""
        entries = op_logger.get_all()
        self.lbl_log_count.setText(f"共 {op_logger.count()} 条")
        text = format_log_text(entries)
        self.txt_log.setPlainText(text)
        # 滚动到顶部（最新条目在 reversed 列表头部）
        from PyQt6.QtGui import QTextCursor
        self.txt_log.moveCursor(QTextCursor.MoveOperation.Start)

    def _clear_log(self):
        """清空日志"""
        reply = QMessageBox.question(
            self, "确认", "清空所有日志记录？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        op_logger.clear()
        self._refresh_log()
