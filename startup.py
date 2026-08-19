# -*- coding: utf-8 -*-
"""開機自動啟動：寫在 HKCU 的 Run 機碼。

只動目前使用者的機碼（不需要管理員權限），值名稱固定，開關就是新增／刪除那個值。
"""
import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClaudeWorkbench"
HERE = os.path.dirname(os.path.abspath(__file__))


def _pythonw():
    """用 pythonw.exe 才不會跳出黑色主控台。"""
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    if exe.lower().endswith("pythonw.exe"):
        return exe
    # 被打包成 exe 或找不到時，退回目前的執行檔
    return exe


def command():
    return '"%s" "%s"' % (_pythonw(), os.path.join(HERE, "launch.pyw"))


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def current_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, VALUE_NAME)[0]
    except OSError:
        return ""


def set_enabled(on):
    """回傳 (成功, 訊息)。開啟時順便把指令更新成目前的路徑。"""
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as e:
        return False, str(e)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("目前狀態：", "已開啟" if is_enabled() else "未開啟")
    print("登錄的指令：", current_value() or "(無)")
    print("會寫入的指令：", command())
