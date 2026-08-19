# -*- coding: utf-8 -*-
"""錯誤記錄與例外攔截。

pythonw 底下 stderr 是黑洞：未攔截的例外會無聲消失，介面「點了沒反應」而無從診斷。
這裡把 sys.excepthook 與 Tk 的 callback 例外都導進輪替記錄檔，並盡力跳一個錯誤視窗。
"""
import logging
import logging.handlers
import sys
import traceback

import appdata

_logger = None
_notifying = False


def get():
    global _logger
    if _logger is None:
        _logger = logging.getLogger("workbench")
        _logger.setLevel(logging.INFO)
        h = logging.handlers.RotatingFileHandler(
            appdata.log_path(), maxBytes=512 * 1024, backupCount=2,
            encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        _logger.addHandler(h)
    return _logger


def _notify(text):
    """跳錯誤視窗；再失敗就放棄，避免遞迴。"""
    global _notifying
    if _notifying:
        return
    _notifying = True
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk._default_root
        owned = False
        if root is None:
            root = tk.Tk()
            root.withdraw()
            owned = True
        messagebox.showerror(
            "Claude 工作台",
            "發生未預期的錯誤\n\n%s\n\n詳細內容：%s"
            % (text, appdata.log_path()))
        if owned:
            root.destroy()
    except Exception:
        pass
    finally:
        _notifying = False


def _handle(kind, exc_type, exc, tb):
    try:
        get().error("%s: %s", kind,
                    "".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass
    _notify("%s: %s" % (exc_type.__name__, exc))


def install(root=None, name=""):
    """攔截主執行緒、背景執行緒與 Tk callback 的例外。"""
    if name:
        try:
            get().info("start %s", name)
        except Exception:
            pass
    sys.excepthook = lambda t, e, tb: _handle("uncaught", t, e, tb)
    try:
        import threading
        threading.excepthook = lambda a: _handle(
            "thread", a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass
    if root is not None:
        root.report_callback_exception = \
            lambda t, e, tb: _handle("tk", t, e, tb)
