# -*- coding: utf-8 -*-
"""從安裝的 claude 執行檔讀出它認得的設定鍵、可選值與說明。

設定結構以 zod schema 的形式編在執行檔裡，形如
``key:validator(...).optional().describe("...")``，枚舉則是 ``Nr([...])``。
掃描結果依執行檔的大小與修改時間快取。

用意是讓可選值跟著實際安裝的版本走，避免硬編清單在 Claude Code 更新後過時。
"""
import json
import os
import re
import subprocess
import sys

import appdata

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = appdata.cache_path("schema-cache.json")
CREATE_NO_WINDOW = 0x08000000

# autoCompactEnabled:jt().optional().describe("Automatically compact …")
# viewMode:Nr(["default","verbose"]).optional().catch(void 0).describe("…")
SCHEMA_RE = re.compile(
    rb"([A-Za-z][A-Za-z0-9]{2,40})\s*:\s*[A-Za-z0-9_$]{1,8}\("
    rb"[^;{}]{0,600}?\.describe\((['\"])(.{1,900}?)\2\s*\)",
    re.DOTALL)

# 枚舉：Nr(["a","b","c"])
ENUM_RE = re.compile(
    rb"([A-Za-z][A-Za-z0-9]{2,40})\s*:\s*[A-Za-z0-9_$]{1,8}\(\s*\[([^\]]{1,300})\]")


def claude_path(cfg=None):
    if cfg and cfg.get("claude"):
        return cfg["claude"]
    import config
    return config.find_claude()


def installed_version(claude=None):
    """回傳版本字串（如 "2.1.235"）；取不到時回空字串。"""
    exe = claude or claude_path()
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=15, encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return ""
    m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m else ""


def _scan(path):
    with open(path, "rb") as fh:
        blob = fh.read()

    keys = {}
    for m in SCHEMA_RE.finditer(blob):
        key = m.group(1).decode("ascii", "ignore")
        desc = m.group(3).decode("utf-8", "replace")
        desc = " ".join(desc.replace("\\n", " ").split())
        # 同一個鍵可能出現多次，取最長的說明
        if len(desc) > len(keys.get(key, "")):
            keys[key] = desc

    enums = {}
    for m in ENUM_RE.finditer(blob):
        key = m.group(1).decode("ascii", "ignore")
        raw = m.group(2).decode("utf-8", "replace")
        vals = re.findall(r"[\"']([^\"']{1,40})[\"']", raw)
        if vals and key not in enums:
            enums[key] = vals
    return keys, enums


def load(claude=None, force=False):
    """回傳 {"version", "keys": {鍵: 說明}, "enums": {鍵: [值]}}，結果會快取。"""
    exe = claude or claude_path()
    try:
        st = os.stat(exe)
        sig = "%s|%d|%d" % (exe, st.st_size, int(st.st_mtime))
    except OSError:
        return {"version": "", "keys": {}, "enums": {}, "ok": False}

    if not force and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("sig") == sig:
                return blob
        except Exception:
            pass

    keys, enums = _scan(exe)
    blob = {"sig": sig, "version": installed_version(exe),
            "keys": keys, "enums": enums, "ok": bool(keys)}
    try:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, ensure_ascii=False)
    except OSError:
        pass
    return blob


def drift(spec_keys, schema=None):
    """比對指定的鍵與這個版本實際認得的鍵。

    回傳 (此版本沒有的, 此版本有但未列出的)。掃不到 schema 時回 ([], [])。
    """
    schema = schema or load()
    known = schema.get("keys") or {}
    if not known:
        return [], []
    top = set()
    for k in spec_keys:
        top.add(k.split(".")[0])
    gone = sorted(k for k in top if k not in known)
    extra = sorted(k for k in known if k not in top)
    return gone, extra


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import time
    t = time.time()
    s = load(force=True)
    print("掃描耗時 %.1f 秒" % (time.time() - t))
    print("Claude 版本：%s" % (s["version"] or "(問不到)"))
    print("認得的設定鍵：%d 個，其中 %d 個有枚舉值"
          % (len(s["keys"]), len(s["enums"])))
    print()
    for k in ("ultracode", "effortLevel", "workflowSizeGuideline",
              "showThinkingSummaries", "viewMode", "autoUploadSessions"):
        print("  %-24s %s" % (k, (s["keys"].get(k) or "(沒有)")[:96]))
        if k in s["enums"]:
            print("  %-24s 可選：%s" % ("", s["enums"][k]))
