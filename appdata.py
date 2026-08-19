# -*- coding: utf-8 -*-
"""應用程式資料目錄與備份輪替。

快取、記錄檔、備份放在使用者資料目錄而非安裝目錄：
安裝目錄可能是唯讀（快取寫不進去會導致每次重掃），備份也需要固定的家與數量上限。
"""
import glob
import os
import shutil
import time

APP_NAME = "ClaudeWorkbench"


def data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def backups_dir():
    d = os.path.join(data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(name):
    return os.path.join(data_dir(), name)


def log_path():
    return os.path.join(data_dir(), "workbench.log")


def backup(path, prefix, keep=10):
    """把檔案複製一份帶時間戳的備份，並把同字首的舊備份修剪到 keep 份。"""
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backups_dir(), "%s.%s" % (prefix, stamp))
    try:
        shutil.copyfile(path, dest)
    except OSError:
        return None
    olds = sorted(glob.glob(os.path.join(backups_dir(), prefix + ".*")))
    for f in olds[:-keep]:
        try:
            os.remove(f)
        except OSError:
            pass
    return dest
