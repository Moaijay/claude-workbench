# -*- coding: utf-8 -*-
"""Claude 工作台的設定檔存取。"""
import json
import os
import shutil
import time

import appdata

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "config.json")

# 上一次 load() 遇到損壞設定檔時的說明；正常為空字串
LOAD_ERROR = ""

DEFAULT = {
    "claude": "",
    "shell": "powershell",   # 或 "cmd"
    # 介面語言：auto 跟隨 Windows 介面語言，或指定 zh-TW／en；重啟生效
    "language": "auto",
    "stagger_ms": 700,
    "keep_open": True,
    "skip_running": True,    # 已開啟的對話不重複開啟
    # 新增對話時預設帶入的資料夾；留空代表家目錄
    "default_cwd": "",
    # 每個視窗的權限模式；空字串代表沿用 settings.json 的全域設定
    # 可用：bypassPermissions / acceptEdits / auto / plan / manual / dontAsk
    "permission_mode": "bypassPermissions",
    "items": [],
    # 已封存的對話 id；只影響清單顯示，不動對話紀錄
    "archived": [],
    # 開啟中改名的待同步佇列 {對話 id: 新名稱}；對話關閉後自動補發 /rename
    "pending_renames": {},
}


def find_claude():
    p = shutil.which("claude")
    if p:
        return p
    guess = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
    return guess if os.path.exists(guess) else "claude"


def new_session_cwd(cfg=None):
    """新增對話時預設帶入的資料夾。

    預設為家目錄，與在終端機直接執行 claude 的起始位置一致。
    """
    cfg = cfg or load()
    want = (cfg.get("default_cwd") or "").strip()
    if want and os.path.isdir(want):
        return want
    return os.path.expanduser("~")


def load():
    """讀取設定。設定檔損壞時改名保留、回報 LOAD_ERROR，絕不讓後續儲存覆寫壞檔。"""
    global LOAD_ERROR
    LOAD_ERROR = ""
    cfg = dict(DEFAULT)
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("top-level is not an object")
            cfg.update(data)
        except Exception as e:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            quarantine = "%s.corrupt.%s" % (PATH, stamp)
            try:
                os.replace(PATH, quarantine)
            except OSError:
                quarantine = PATH
            from i18n import tr
            LOAD_ERROR = (tr("設定檔損壞（%s）\n已保留於 %s\n目前載入預設值")
                          % (e, quarantine))
            try:
                import applog
                applog.get().error("config corrupt: %s -> %s", e, quarantine)
            except Exception:
                pass
    # 設定檔可能來自其他機器，路徑不存在時重新尋找
    claude = cfg.get("claude") or ""
    if not claude or (os.path.isabs(claude) and not os.path.exists(claude)):
        cfg["claude"] = find_claude()
    return cfg


def save(cfg):
    appdata.backup(PATH, "config.json", keep=10)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
