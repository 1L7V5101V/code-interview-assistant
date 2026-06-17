"""
全局快捷键监听模块
使用 keyboard 库注册系统级热键，同时以 Windows RegisterHotKey 作为备用方案
"""

import sys
import keyboard
import config

from core.native_hotkey import NativeHotkeyFilter


class HotkeyListener:
    """
    全局快捷键管理器
    优先使用 keyboard 库注册热键，在 Windows 上同时注册原生热键作为备用
    """

    def __init__(self):
        self.callbacks = {}
        self.registered = False
        self._native_filter = None

    def register(self, action: str, callback):
        """
        注册一个快捷键动作
        action: toggle_window / screenshot / prev_question / next_question / toggle_clickthrough
        callback: 触发时调用的函数
        """
        self.callbacks[action] = callback

    def start(self):
        """开始监听所有已注册的快捷键"""
        if self.registered:
            return
        self._register_all()
        self.registered = True

    def stop(self):
        """停止监听，卸载所有热键"""
        keyboard.unhook_all()
        if self._native_filter is not None:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._native_filter.unregister_all()
            self._native_filter = None
        self.registered = False

    def _register_all(self):
        """根据 config.json 注册所有热键"""
        hotkey_map = {
            "toggle_window": self.callbacks.get("toggle_window"),
            "screenshot": self.callbacks.get("screenshot"),
            "prev_question": self.callbacks.get("prev_question"),
            "next_question": self.callbacks.get("next_question"),
            "toggle_clickthrough": self.callbacks.get("toggle_clickthrough"),
            "move_left": self.callbacks.get("move_left"),
            "move_right": self.callbacks.get("move_right"),
            "move_up": self.callbacks.get("move_up"),
            "move_down": self.callbacks.get("move_down"),
        }

        # 1. 注册 keyboard 库热键
        for action, callback in hotkey_map.items():
            if callback is None:
                continue
            hk = config.get_hotkey(action)
            try:
                # 用 lambda 包装，避免 keyboard 库传参导致报错
                keyboard.add_hotkey(hk, lambda cb=callback: cb(), suppress=False)
            except Exception:
                # 热键冲突或无效，忽略
                pass

        # 2. 在 Windows 上同时注册原生热键作为备用（虚拟机/全屏环境下更可靠）
        if sys.platform == "win32":
            self._native_filter = NativeHotkeyFilter()
            for action, callback in hotkey_map.items():
                if callback is None:
                    continue
                hk = config.get_hotkey(action)
                self._native_filter.register(hk, callback)
            # 安装原生事件过滤器到 Qt 应用
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._native_filter)

    def refresh(self):
        """重新加载配置并注册热键（用户修改设置后调用）"""
        keyboard.unhook_all()
        if self._native_filter is not None:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._native_filter.unregister_all()
            self._native_filter = None
        self.registered = False
        self._register_all()
        self.registered = True
