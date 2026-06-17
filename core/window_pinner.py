"""
Windows 窗口强力置顶模块
使用独立高优先级线程，以极高频率（~500fps）执行置顶，
确保窗口始终在所有 TOPMOST 窗口（包括 VMware 全屏）的最前面。

技术方案（参考 Bongo Cat 等桌面挂件）：
1. 独立 Python 线程（THREAD_PRIORITY_HIGHEST）以 ~2ms 间隔循环置顶
2. 直接设置 WS_EX_TOPMOST 窗口扩展样式（比 SetWindowPos 更持久）
3. 每次循环执行"先移出 TOPMOST 组 → 再重新加入"，确保排在最前面
4. 检查窗口句柄有效性，窗口销毁时自动停止
"""

import sys
import ctypes
import threading
import time

if sys.platform != "win32":
    def start_pinner(hwnd):
        pass
    def stop_pinner():
        pass
    def wake_pinner():
        pass
    def update_hwnd(hwnd):
        pass
    exit()

# ─── Windows 常量 ─────────────────────────────────────────────────────────
GWL_EXSTYLE   = -20
WS_EX_TOPMOST  = 0x00000008
HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# ─── 全局状态 ─────────────────────────────────────────────────────────────
_pinned_hwnd = None
_running = False
_thread = None
_lock = threading.Lock()


def _pinner_loop():
    """独立线程：以极高频率强制置顶"""
    global _pinned_hwnd, _running

    # 设置线程优先级为 THREAD_PRIORITY_HIGHEST
    try:
        _kernel32.SetThreadPriority(
            _kernel32.GetCurrentThread(),
            2   # THREAD_PRIORITY_HIGHEST
        )
    except Exception:
        pass

    style_check_counter = 0

    while True:
        with _lock:
            running = _running
            hwnd = _pinned_hwnd

        if not running or hwnd is None:
            break

        try:
            # 检查窗口是否仍然存在
            if not _user32.IsWindow(hwnd):
                break

            # 核心：先移出 TOPMOST 组，再重新加入
            # 这确保我们成为 TOPMOST 组内最新的窗口，排在最前面
            _user32.SetWindowPos(
                hwnd, HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
            _user32.SetWindowPos(
                hwnd, HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )

            # 每 ~200ms 检查一次 WS_EX_TOPMOST 扩展样式是否被外部清除
            style_check_counter += 1
            if style_check_counter >= 100:   # 100 * 2ms = 200ms
                style_check_counter = 0
                try:
                    ex_style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                    if not (ex_style & WS_EX_TOPMOST):
                        _user32.SetWindowLongPtrW(
                            hwnd, GWL_EXSTYLE,
                            ex_style | WS_EX_TOPMOST
                        )
                except Exception:
                    pass

        except Exception:
            pass

        # ~2ms 间隔（~500fps），几乎无间隙
        time.sleep(0.002)

    with _lock:
        _running = False


def start_pinner(hwnd):
    """启动强力置顶，hwnd 为窗口句柄（int）"""
    global _pinned_hwnd, _running, _thread

    if sys.platform != "win32":
        return

    stop_pinner()  # 先停止已有的

    with _lock:
        _pinned_hwnd = hwnd
        _running = True

    # 先设置 WS_EX_TOPMOST 扩展样式（持久化，比 SetWindowPos 更可靠）
    try:
        ex_style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongPtrW(
            hwnd, GWL_EXSTYLE,
            ex_style | WS_EX_TOPMOST
        )
    except Exception:
        pass

    # 启动独立高优先级线程
    _thread = threading.Thread(target=_pinner_loop, daemon=True, name="WindowPinner")
    _thread.start()


def stop_pinner():
    """停止强力置顶"""
    global _running, _thread
    with _lock:
        _running = False
    if _thread is not None:
        _thread.join(timeout=1.0)
        _thread = None


def wake_pinner():
    """立即触发一次置顶（窗口显示、切换点击穿透等操作时调用）"""
    with _lock:
        hwnd = _pinned_hwnd
    if hwnd is None:
        return
    try:
        if not _user32.IsWindow(hwnd):
            return
        _user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )
        _user32.SetWindowPos(
            hwnd, HWND_TOPMOST,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )
    except Exception:
        pass


def update_hwnd(hwnd):
    """更新窗口句柄（窗口重建后调用，如 setWindowFlag 后 show）"""
    global _pinned_hwnd
    with _lock:
        _pinned_hwnd = hwnd
    wake_pinner()
