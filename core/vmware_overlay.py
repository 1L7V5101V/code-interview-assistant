"""
VMware 全屏窗口置顶终极方案
组合方案三（设置 Owner 窗口）+ 方案四（监听窗口事件）

技术方案：
1. 检测 vmware-kvm.exe 窗口
2. 将助手窗口的 Owner 设为 VMware 窗口（SetWindowLongPtr GWLP_HWNDPARENT）
   - Owner 窗口不会被裁剪，但会跟随 Owner 的 Z 顺序
   - 这比 Parent 关系更合适，Overlay 不会被 VMware 窗口裁剪
3. 使用 SetWinEventHook 监听系统前台窗口变化和 Z 顺序变化
   - 不需要 DLL 注入，纯 Python + ctypes 实现
   - 当 VMware 全屏激活时，立即重新置顶
4. 持续监控 VMware 状态，动态维护 Owner 关系
5. 与 window_pinner 协同工作（pinner 负责高频置顶，overlay 负责 Owner 关系）

注意：
- Owner 关系用 GWLP_HWNDPARENT（值为 -8），不是 GWL_STYLE
- 设置 Owner 后，Overlay 窗口会跟随 VMware 窗口的 Z 顺序
- 当 VMware 退出全屏时，需要解除 Owner 关系，恢复普通置顶
"""

import sys
import ctypes
import ctypes.wintypes as wintypes
import threading
import time

if sys.platform != "win32":
    def start_overlay_monitor(overlay_hwnd):
        pass
    def stop_overlay_monitor():
        pass
    def is_vmware_fullscreen():
        return False
    exit()

# ─── Windows 常量 ────────────────────────────────────────────────────────
GWLP_HWNDPARENT = -8
HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010

# SetWinEventHook 事件常量
EVENT_SYSTEM_FOREGROUND   = 0x0003
EVENT_OBJECT_ZORDERCHANGED = 0x801D

WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

# OpenProcess 权限
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ            = 0x0010

# ─── 全局状态 ────────────────────────────────────────────────────────────
_overlay_hwnd = None
_vmware_hwnd = None
_vmware_pid = None
_running = False
_monitor_thread = None
_hook_handle = None
_hook_handle2 = None
_lock = threading.Lock()

# User32 / Kernel32 函数
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# 回调函数引用（防止 GC）
_foreground_hook_cb = None
_object_hook_cb = None


def _get_process_name(pid):
    """
    通过 PID 获取进程名（纯 ctypes，无 psutil 依赖）
    返回进程名字符串，失败返回 ''
    """
    try:
        hProcess = _kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
            False,  # bInheritHandle
            pid
        )
        if not hProcess:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            # QueryFullProcessImageNameW
            if _kernel32.QueryFullProcessImageNameW(hProcess, 0, buf, ctypes.byref(size)):
                path = buf.value
                # 提取文件名（如 vmware-kvm.exe）
                return path.split('\\')[-1].lower()
            return ''
        finally:
            _kernel32.CloseHandle(hProcess)
    except Exception:
        return ''


def _find_vmware_kvm_window():
    """
    找到 vmware-kvm.exe 的顶层窗口句柄
    返回 (hwnd, pid) 或 (None, None)
    """
    result = []

    def enum_callback(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        try:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = _get_process_name(pid.value)
            if 'vmware-kvm' in name:
                result.append((hwnd, pid.value))
        except Exception:
            pass
        return True

    _user32.EnumWindows(
        ctypes.CFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)(enum_callback),
        None
    )

    if not result:
        return None, None

    # 如果找到多个，选择尺寸最大的（通常是全屏窗口）
    best = result[0]
    try:
        for hwnd, pid in result:
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            best_rect = wintypes.RECT()
            _user32.GetWindowRect(best[0], ctypes.byref(best_rect))
            best_area = (best_rect.right - best_rect.left) * (best_rect.bottom - best_rect.top)
            if area > best_area:
                best = (hwnd, pid)
    except Exception:
        pass

    return best[0], best[1]


def _set_owner(overlay_hwnd, owner_hwnd):
    """
    将 overlay_hwnd 的 Owner 设置为 owner_hwnd
    返回 True/False
    """
    try:
        # GWLP_HWNDPARENT = -8，设置 Owner 窗口
        # Owner 不是 Parent：Owner 窗口销毁时会连带销毁 Owned 窗口
        # Owned 窗口始终显示在 Owner 窗口的上方
        old_owner = _user32.SetWindowLongPtrW(
            overlay_hwnd, GWLP_HWNDPARENT, owner_hwnd
        )
        # SetWindowLongPtrW 返回之前的 Owner（或 0 表示无 Owner）
        # 即使返回 0 也不一定是错误（之前可能没有 Owner）
        return True
    except Exception:
        return False


def _clear_owner(overlay_hwnd):
    """清除 Owner 关系（设为 0 = 无 Owner）"""
    try:
        _user32.SetWindowLongPtrW(overlay_hwnd, GWLP_HWNDPARENT, 0)
        return True
    except Exception:
        return False


def _is_vmware_foreground():
    """检查前台窗口是否属于 vmware-kvm.exe"""
    try:
        foreground_hwnd = _user32.GetForegroundWindow()
        if not foreground_hwnd:
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(pid))
        name = _get_process_name(pid.value)
        return 'vmware-kvm' in name
    except Exception:
        return False


def _win_event_callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
    """
    SetWinEventHook 回调函数
    当前台窗口变化或 Z 顺序变化时，检查是否需要重新置顶
    """
    global _overlay_hwnd, _vmware_hwnd, _vmware_pid

    if _overlay_hwnd is None or not _running:
        return

    try:
        if event == EVENT_SYSTEM_FOREGROUND:
            # 不依赖 _vmware_hwnd，直接检查前台窗口是否是 VMware
            if hwnd:
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                name = _get_process_name(pid.value)
                if 'vmware-kvm' in name:
                    # 是 VMware 窗口，更新全局状态并设置 Owner
                    if hwnd != _vmware_hwnd:
                        _vmware_hwnd = hwnd
                        _vmware_pid = pid.value
                        _set_owner(_overlay_hwnd, hwnd)
                    _force_topmost(_overlay_hwnd)
                else:
                    # 前台窗口不是 VMware，但 VMware 可能仍然在全屏运行
                    # 保持 Owner 关系，只做置顶
                    if _vmware_hwnd:
                        _force_topmost(_overlay_hwnd)
        elif event == EVENT_OBJECT_ZORDERCHANGED:
            if hwnd and hwnd == _vmware_hwnd:
                _force_topmost(_overlay_hwnd)
    except Exception:
        pass


def _force_topmost(hwnd):
    """强制置顶（单次，不阻塞）"""
    try:
        if not _user32.IsWindow(hwnd):
            return
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
    except Exception:
        pass


def _monitor_loop():
    """
    监控线程主循环：
    1. 检测 VMware 全屏状态变化
    2. 动态维护 Owner 关系
    3. 通过 SetWinEventHook 监听系统事件
    """
    global _overlay_hwnd, _vmware_hwnd, _vmware_pid, _running
    global _hook_handle, _hook_handle2
    global _foreground_hook_cb, _object_hook_cb

    # 设置线程优先级
    try:
        _kernel32.SetThreadPriority(
            _kernel32.GetCurrentThread(),
            1  # THREAD_PRIORITY_ABOVE_NORMAL
        )
    except Exception:
        pass

    # ── 安装 SetWinEventHook ────────────────────────────────────────────
    # 回调函数类型
    WinEventProc = ctypes.CFUNCTYPE(
        None,
        wintypes.HANDLE,   # hWinEventHook
        wintypes.DWORD,    # event
        wintypes.HWND,     # hwnd
        wintypes.LONG,     # idObject
        wintypes.LONG,     # idChild
        wintypes.DWORD,    # idEventThread
        wintypes.DWORD,    # dwmsEventTime
    )

    # 前台窗口变化 hook
    _foreground_hook_cb = WinEventProc(_win_event_callback)
    _hook_handle = _user32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        0,  # 不需要 DLL
        _foreground_hook_cb,
        0,  # 所有进程
        0,  # 所有线程
        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
    )

    # Z 顺序变化 hook
    _object_hook_cb = WinEventProc(_win_event_callback)
    _hook_handle2 = _user32.SetWinEventHook(
        EVENT_OBJECT_ZORDERCHANGED,
        EVENT_OBJECT_ZORDERCHANGED,
        0,
        _object_hook_cb,
        0,
        0,
        WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
    )

    last_vmware_state = False
    check_counter = 0

    while _running:
        try:
            overlay = _overlay_hwnd
            if overlay is None or not _user32.IsWindow(overlay):
                break

            # 每 ~500ms 检测一次 VMware 状态
            check_counter += 1
            if check_counter >= 5:  # 5 * 100ms = 500ms
                check_counter = 0

                vmware_hwnd, vmware_pid = _find_vmware_kvm_window()
                vmware_active = vmware_hwnd is not None

                if vmware_active and not last_vmware_state:
                    # VMware 刚刚启动 / 进入全屏
                    _vmware_hwnd = vmware_hwnd
                    _vmware_pid = vmware_pid
                    _set_owner(overlay, vmware_hwnd)
                    _force_topmost(overlay)
                elif not vmware_active and last_vmware_state:
                    # VMware 退出全屏
                    _vmware_hwnd = None
                    _vmware_pid = None
                    _clear_owner(overlay)
                    _force_topmost(overlay)
                elif vmware_active and last_vmware_state:
                    # VMware 持续全屏，确保 Owner 关系仍然正确
                    current_owner = _user32.GetWindowLongPtrW(overlay, GWLP_HWNDPARENT)
                    if current_owner != vmware_hwnd:
                        _set_owner(overlay, vmware_hwnd)
                    _force_topmost(overlay)

                last_vmware_state = vmware_active

            # 处理 Windows 消息队列（SetWinEventHook 需要消息泵）
            msg = wintypes.MSG()
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):  # PM_REMOVE
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

        except Exception:
            pass

        time.sleep(0.1)

    # 清理 Hook
    try:
        if _hook_handle:
            _user32.UnhookWinEvent(_hook_handle)
        if _hook_handle2:
            _user32.UnhookWinEvent(_hook_handle2)
    except Exception:
        pass

    with _lock:
        _running = False


def start_overlay_monitor(overlay_hwnd):
    """
    启动 VMware 全屏置顶监控
    overlay_hwnd: 助手窗口的句柄（int）
    """
    global _overlay_hwnd, _running, _monitor_thread

    if sys.platform != "win32":
        return

    stop_overlay_monitor()

    with _lock:
        _overlay_hwnd = overlay_hwnd
        _running = True

    _monitor_thread = threading.Thread(
        target=_monitor_loop, daemon=True, name="VMwareOverlay"
    )
    _monitor_thread.start()


def stop_overlay_monitor():
    """停止 VMware 全屏置顶监控"""
    global _running, _monitor_thread, _overlay_hwnd

    with _lock:
        _running = False

    if _monitor_thread and _monitor_thread.is_alive():
        _monitor_thread.join(timeout=2.0)

    # 清除 Owner 关系
    if _overlay_hwnd:
        _clear_owner(_overlay_hwnd)

    _monitor_thread = None


def update_overlay_hwnd(hwnd):
    """
    更新 Overlay 窗口句柄（窗口重建后调用，如 setWindowFlag 后 show）
    """
    global _overlay_hwnd
    with _lock:
        _overlay_hwnd = hwnd
    # 如果正在运行，立即重新置顶一次
    if _running:
        _force_topmost(hwnd)


def is_vmware_fullscreen():
    """检测 VMware 是否处于全屏状态（前台且窗口尺寸大）"""
    try:
        foreground_hwnd = _user32.GetForegroundWindow()
        if not foreground_hwnd:
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(pid))
        name = _get_process_name(pid.value)
        if 'vmware-kvm' not in name:
            return False
        # 检查窗口是否全屏（尺寸大于等于 1920x1080）
        rect = wintypes.RECT()
        _user32.GetWindowRect(foreground_hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        return width >= 1920 and height >= 1080
    except Exception:
        return False
