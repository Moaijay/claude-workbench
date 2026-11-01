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
# 2.1.3xx 起多數改成延遲宣告：viewMode:()=>U(["default",…]).optional()…
LAZY = rb"(?:\(\)\s*=>\s*)?"
SCHEMA_RE = re.compile(
    rb"([A-Za-z][A-Za-z0-9]{2,40})\s*:\s*" + LAZY + rb"[A-Za-z0-9_$]{1,8}\("
    rb"[^;{}]{0,600}?\.describe\((['\"])(.{1,900}?)\2\s*\)",
    re.DOTALL)

# 枚舉：Nr(["a","b","c"])、U(["a","b"])，或指向變數 U(aJt)、聯集的第一項
# Ue([U(R1e),…])。只在 SCHEMA_RE 命中的宣告開頭比對——全檔掃描會綁到
# 同名的無關陣列，產生看似合理但無效的選項值。
_HEAD = (rb"^[A-Za-z][A-Za-z0-9]{2,40}\s*:\s*" + LAZY
         + rb"[A-Za-z0-9_$]{1,8}\(\s*")
ENUM_RE = re.compile(_HEAD + rb"\[([^\]]{1,300})\]")
REF_RE = re.compile(_HEAD + rb"(?:\[\s*[A-Za-z0-9_$]{1,8}\(\s*)?"
                    rb"([A-Za-z_$][A-Za-z0-9_$]{0,7})\s*\)")
_ITEM_STR = re.compile(r'^"([^"\\]{1,40})"$')
_ITEM_SPREAD = re.compile(r"^\.\.\.([A-Za-z_$][A-Za-z0-9_$]{0,7})$")


def _array_values(raw, defs):
    """陣列字面值的內容 → 字串清單。

    只接受字串與 ...變數 兩種項目，變數從 defs（名稱 → 字串清單或 None）
    查；出現其他東西（呼叫、物件、數字）表示這不是單純的枚舉，回 None。
    """
    vals = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        m = _ITEM_STR.match(part)
        if m:
            vals.append(m.group(1))
            continue
        m = _ITEM_SPREAD.match(part)
        sub = defs.get(m.group(1)) if m else None
        if sub is None:
            return None
        vals.extend(sub)
    return vals or None


def _spreads(raw):
    return [m.group(1) for m in (_ITEM_SPREAD.match(p.strip())
                                 for p in raw.split(",")) if m]


def _find_defs(names, blob):
    """一次掃描找出 names 裡每個變數的陣列定義：{名稱: 原始內容}。

    全檔只有唯一一處定義的才收；同名定義不只一處就無法確定是哪個。
    """
    # 壓縮過的程式碼寫成 name=[…]，不帶空白；用 bytes.find 比正規式快得多
    ident = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                b"0123456789_$.")
    out = {}
    for name in names:
        needle = name.encode("ascii", "ignore") + b"=["
        hits, pos = [], blob.find(needle)
        while pos != -1 and len(hits) < 2:
            if pos == 0 or blob[pos - 1] not in ident:
                start = pos + len(needle)
                end = blob.find(b"]", start, start + 401)
                if end != -1:
                    hits.append(blob[start:end].decode("utf-8", "replace"))
            pos = blob.find(needle, pos + 1)
        if len(hits) == 1:
            out[name] = hits[0]
    return out


def _resolve_all(names, blob, depth=2):
    """變數名 → 字串清單；展開 ...變數 最多 depth 層，查不到的為 None。"""
    raws, todo = {}, set(names)
    for _ in range(depth + 1):
        if not todo:
            break
        got = _find_defs(todo, blob)
        raws.update(got)
        todo = {s for raw in got.values() for s in _spreads(raw)} - set(raws)
    out = {}
    # 由內往外解：被展開的先解，最多跑 depth+1 輪
    for _ in range(depth + 2):
        for name, raw in raws.items():
            if out.get(name) is None:
                out[name] = _array_values(raw, out)
    return out

# 抽取結果的健全檢查：一定存在的鍵抓不到、或總數過低＝打包格式變了
# 導致抽取失敗，不是這個版本真的沒有這些設定。
SENTINELS = ("model", "effortLevel", "env", "theme")
MIN_KEYS = 100


def _sane(keys):
    return (len(keys) >= MIN_KEYS
            and sum(1 for k in SENTINELS if k in keys) >= 2)


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
    # 每個鍵依出現順序記下枚舉的候選宣告：("vals", 清單)、("ref", 變數名)
    # 或 ("lit", 含 ...變數 的陣列內容)；最後取第一個解得出值的
    cands = {}
    for m in SCHEMA_RE.finditer(blob):
        key = m.group(1).decode("ascii", "ignore")
        desc = m.group(3).decode("utf-8", "replace")
        desc = " ".join(desc.replace("\\n", " ").split())
        # 同一個鍵可能出現多次，取最長的說明
        if len(desc) > len(keys.get(key, "")):
            keys[key] = desc
        head = m.group(0)
        em = ENUM_RE.match(head)
        if em:
            raw = em.group(1).decode("utf-8", "replace")
            if _spreads(raw):
                cands.setdefault(key, []).append(("lit", raw))
                continue
            vals = _array_values(raw, {})
            if vals:
                cands.setdefault(key, []).append(("vals", vals))
                continue
            # 陣列裡不是字串（如聯集 Ue([U(R1e),…])）：改看第一項指向的變數
        rm = REF_RE.match(head)
        if rm:
            cands.setdefault(key, []).append(
                ("ref", rm.group(1).decode("ascii", "ignore")))

    # 指向變數的枚舉最後一起查，整個執行檔只多掃一兩趟
    need = set()
    for lst in cands.values():
        for kind, v in lst:
            if kind == "ref":
                need.add(v)
            elif kind == "lit":
                need.update(_spreads(v))
    defs = _resolve_all(need, blob)

    enums = {}
    for key, lst in cands.items():
        for kind, v in lst:
            if kind == "vals":
                vals = v
            elif kind == "ref":
                vals = defs.get(v)
            else:
                vals = _array_values(v, defs)
            if vals:
                enums[key] = vals
                break
    return keys, enums


def load(claude=None, force=False):
    """回傳 {"version", "keys": {鍵: 說明}, "enums": {鍵: [值]}}，結果會快取。

    抽取結果沒通過健全檢查時不覆寫快取，改回傳上一次成功的結果並標
    "stale"；沒有可用的舊結果才回空。
    """
    exe = claude or claude_path()
    try:
        st = os.stat(exe)
        # 結尾是抽取邏輯的版本；改了抽取方式要跟著改，讓舊快取作廢
        sig = "%s|%d|%d|e3" % (exe, st.st_size, int(st.st_mtime))
    except OSError:
        sig = None

    old = None
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = None
    if old is not None and not (old.get("ok") and old.get("keys")):
        old = None
    if not force and sig and old and old.get("sig") == sig:
        return old

    keys, enums = {}, {}
    if sig:
        try:
            keys, enums = _scan(exe)
        except Exception:
            keys, enums = {}, {}

    if _sane(keys):
        blob = {"sig": sig, "version": installed_version(exe),
                "keys": keys, "enums": enums, "ok": True}
        try:
            with open(CACHE, "w", encoding="utf-8") as fh:
                json.dump(blob, fh, ensure_ascii=False)
        except OSError:
            pass
        return blob
    if old:
        stale = dict(old)
        stale["stale"] = True
        return stale
    return {"version": "", "keys": {}, "enums": {}, "ok": False}


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
