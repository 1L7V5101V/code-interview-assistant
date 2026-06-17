"""
Windows 原生热键监听模块（RegisterHotKey）
作为 keyboard 库的备用方案，在虚拟机/全屏等环境下更可靠
"""
import sys
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QByteArray

# Windows 常量
WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 虚拟键码映射（常见键）
VK_MAP = {
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45, 'f': 0x46,
    'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A, 'k': 0x4B, 'l': 0x4C,
    'm': 0x4D, 'n': 0x4E, 'o': 0x4F, 'p': 0x50, 'q': 0x51, 'r': 0x52,
    's': 0x53, 't': 0x54, 'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58,
    'y': 0x59, 'z': 0x5A,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
    'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79,
    'f11': 0x7A, 'f12': 0x7B,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'return': 0x0D, 'enter': 0x0D, 'space': 0x20, 'tab': 0x09,
    'escape': 0x1B, 'esc': 0x1B, 'backspace': 0x08, 'delete': 0x2E,
    'insert': 0x2D, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
    'print': 0x2C, 'scroll': 0x91, 'pause': 0x13,
    'numlock': 0x90, 'capslock': 0x14,
    'add': 0x6B, 'subtract': 0x6D, 'multiply': 0x6A, 'divide': 0x6F,
    'decimal': 0x6E, 'separator': 0x6C,
    'oem_1': 0xBA,      # ;:
    'oem_plus': 0xBB,   # =+
    'oem_comma': 0xBC,  # ,<
    'oem_minus': 0xBD,  # -_
    'oem_period': 0xBE, # .>
    'oem_2': 0xBF,      # /?
    'oem_3': 0xC0,      # `~
    'oem_4': 0xDB,      # [{
    'oem_5': 0xDC,      # \|
    'oem_6': 0xDD,      # ]}
    'oem_7': 0xDE,      # '"
    'oem_8': 0xDF,
    'oem_102': 0xE2,
}

# 特殊字符映射到虚拟键码
CHAR_VK_MAP = {
    '\\': 0xDC,  # oem_5
    '[': 0xDB,   # oem_4
    ']': 0xDD,   # oem_6
    '-': 0xBD,   # oem_minus
    '=': 0xBB,   # oem_plus
    ',': 0xBC,   # oem_comma
    '.': 0xBE,   # oem_period
    '/': 0xBF,   # oem_2
    ';': 0xBA,   # oem_1
    "'": 0xDE,   # oem_7
    '`': 0xC0,   # oem_3
}


def _parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """
    将 hotkey 字符串（如 "ctrl+b"）解析为 (modifiers, vk_code)
    返回 (0, 0) 表示无法解析
    """
    parts = hotkey_str.lower().split('+')
    modifiers = 0
    vk = 0

    for part in parts:
        part = part.strip()
        if part == 'ctrl' or part == 'control':
            modifiers |= MOD_CONTROL
        elif part == 'alt':
            modifiers |= MOD_ALT
        elif part == 'shift':
            modifiers |= MOD_SHIFT
        elif part == 'win' or part == 'windows' or part == 'command':
            modifiers |= MOD_WIN
        else:
            # 尝试获取虚拟键码
            if part in VK_MAP:
                vk = VK_MAP[part]
            elif part in CHAR_VK_MAP:
                vk = CHAR_VK_MAP[part]
            elif len(part) == 1 and part.isalpha():
                vk = ord(part.upper())
            elif len(part) == 1 and part.isdigit():
                vk = ord(part)
            else:
                # 无法识别的键
                return 0, 0

    return modifiers, vk


class NativeHotkeyFilter(QAbstractNativeEventFilter):
    """
    Windows 原生热键事件过滤器
    使用 RegisterHotKey 注册系统级热键，通过 QAbstractNativeEventFilter 拦截 WM_HOTKEY
    """

    def __init__(self):
        super().__init__()
        self.hotkeys = {}   # hotkey_id -> callback
        self.next_id = 1
        self._registered_ids = set()

    def register(self, hotkey_str: str, callback) -> bool:
        """注册一个热键，返回是否成功"""
        if sys.platform != "win32":
            return False

        modifiers, vk = _parse_hotkey(hotkey_str)
        if vk == 0:
            return False

        hotkey_id = self.next_id
        self.next_id += 1

        # 添加 MOD_NOREPEAT 防止按键重复触发
        mods = modifiers | MOD_NOREPEAT

        result = ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods, vk)
        if result:
            self.hotkeys[hotkey_id] = callback
            self._registered_ids.add(hotkey_id)
            return True
        else:
            # 注册失败（可能键已被占用）
            return False

    def unregister(self, hotkey_id: int):
        """注销单个热键"""
        if sys.platform != "win32":
            return
        if hotkey_id in self._registered_ids:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
            self._registered_ids.discard(hotkey_id)
            self.hotkeys.pop(hotkey_id, None)

    def unregister_all(self):
        """注销所有热键"""
        if sys.platform != "win32":
            return
        for hid in list(self._registered_ids):
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
        self.hotkeys.clear()

    def nativeEventFilter(self, eventType, message):
        """拦截 Windows 原生消息 (PyQt6)"""
        if sys.platform != "win32":
            return False, 0

        # PyQt6 在 Windows 上 eventType 是 QByteArray，message 是 sip.voidptr
        try:
            if eventType != QByteArray(b"windows_generic_MSG"):
                return False, 0
        except Exception:
            return False, 0

        try:
            # 在 PyQt6 中 message 是 voidptr，可直接 cast 为 MSG 指针
            msg_ptr = ctypes.cast(message, ctypes.POINTER(wintypes.MSG))
            if msg_ptr:
                msg = msg_ptr.contents
                if msg.message == WM_HOTKEY:
                    hotkey_id = msg.wParam
                    if hotkey_id in self.hotkeys:
                        callback = self.hotkeys[hotkey_id]
                        callback()
                        return True, 1
        except Exception:
            pass
        return False, 0
