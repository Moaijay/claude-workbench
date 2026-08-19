# -*- coding: utf-8 -*-
"""同一時間只准跑一個實例。

以 Windows 具名互斥鎖判斷，不使用鎖檔：行程結束時由作業系統釋放，
不會殘留無效的鎖。
"""
import ctypes
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_held = []          # 保留控制代碼，行程存活期間鎖才有效


def acquire(name):
    """取得指定名稱的鎖。True 表示為第一個實例，False 表示已有實例在執行。"""
    handle = kernel32.CreateMutexW(None, False, "Local\\" + name)
    if not handle:
        return True                      # 建立失敗時不阻擋執行
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _held.append(handle)
    return True


def find_window(title):
    """依視窗標題尋找既有視窗。"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def focus(hwnd):
    """將視窗帶到前景。跨行程需先連結對方的輸入佇列才會生效。"""
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    mine = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other) and bool(user32.AttachThreadInput(mine, other, True))
    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))
    if attached:
        user32.AttachThreadInput(mine, other, False)
    if not ok:
        user32.FlashWindow(hwnd, True)   # 無法取得前景時改為閃爍工作列
    return ok


def single_instance(name, window_title=None):
    """True 表示可繼續執行；False 表示已有實例在執行，並已將其帶到前景。"""
    if acquire(name):
        return True
    if window_title:
        focus(find_window(window_title))
    return False
