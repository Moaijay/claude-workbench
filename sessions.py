# -*- coding: utf-8 -*-
"""掃描 ~/.claude/projects 底下所有 Claude Code 對話紀錄，並估算每條的花費。"""
import glob
import json
import os
import re
import time

import appdata
from i18n import tr

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
CACHE = appdata.cache_path("scan-cache.json")
CACHE_VERSION = 4           # 格式改了就把舊快取作廢

# 脈絡視窗大小（token）。目前世代預設 1M，Haiku 200K。
CONTEXT_WINDOW = {
    "claude-haiku-4-5": 200_000,
}
DEFAULT_WINDOW = 1_000_000

MIN_SIZE = 20 * 1024        # 小於此大小視為沒有實際內容的空對話
META_LINES = 3000           # 找開頭訊息／標題最多看幾行
META_BYTES = 4 * 1024 * 1024

# 每百萬 token 的（輸入, 輸出）美元。取自 Anthropic 公開定價。
PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_PRICE = (5.0, 25.0)
CACHE_READ_RATE = 0.1       # 快取讀取＝輸入價的 0.1 倍
CACHE_WRITE_5M = 1.25       # 5 分鐘快取寫入＝1.25 倍
CACHE_WRITE_1H = 2.0        # 1 小時快取寫入＝2 倍
FAST_RATE = 2.0             # fast mode＝標準費率的 2 倍（usage.speed == "fast"）
WEB_SEARCH_USD = 0.01       # 每次 web search（$10／1000 次）


def project_dir_name(path):
    """資料夾路徑 → ~/.claude/projects 底下的目錄名（非英數一律換成連字號）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _first_text(msg):
    c = msg.get("content", "")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c
                     if isinstance(p, dict) and p.get("type") == "text")
    return (c or "").strip()


def is_real_model(model):
    """<synthetic> 之類為內部標記，不是實際模型。"""
    return bool(model) and not model.startswith("<")


def _usage_cost(usage, model):
    if not is_real_model(model):
        return 0.0
    pin, pout = PRICING.get(model, DEFAULT_PRICE)
    if usage.get("speed") == "fast":
        pin, pout = pin * FAST_RATE, pout * FAST_RATE
    created = usage.get("cache_creation") or {}
    w1h = created.get("ephemeral_1h_input_tokens", 0)
    w5m = created.get("ephemeral_5m_input_tokens")
    if w5m is None:
        w5m = max(0, usage.get("cache_creation_input_tokens", 0) - w1h)
    tools = usage.get("server_tool_use") or {}
    searches = tools.get("web_search_requests", 0) or 0
    return (usage.get("input_tokens", 0) * pin
            + w5m * pin * CACHE_WRITE_5M
            + w1h * pin * CACHE_WRITE_1H
            + usage.get("cache_read_input_tokens", 0) * pin * CACHE_READ_RATE
            + usage.get("output_tokens", 0) * pout) / 1_000_000 \
        + searches * WEB_SEARCH_USD


def read_one(path):
    """讀出單一對話的摘要資訊與累計花費。

    計算花費需掃完整個檔：同一則回覆在串流過程會寫入多次，以 message id 去重取最後一筆。
    """
    st = os.stat(path)
    info = {
        "id": os.path.basename(path)[:-6],
        "path": path,
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,   # Windows：檔案建立時間
        "size": st.st_size,
        "cwd": "",
        "custom_title": "",
        "ai_title": "",
        "first": "",
        "handoff": False,
        "cost": 0.0,
        "turns": 0,
    }
    meta_done = False
    read_bytes = 0
    usage_by_id = {}
    order = []

    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            read_bytes += len(line)

            if '"usage"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    o = None
                if o:
                    m = o.get("message")
                    if isinstance(m, dict) and isinstance(m.get("usage"), dict):
                        mid = m.get("id") or ("~%d" % i)
                        if mid not in usage_by_id:
                            order.append(mid)
                        usage_by_id[mid] = (m["usage"], m.get("model"))

            if meta_done:
                continue
            if i > META_LINES or read_bytes > META_BYTES:
                meta_done = True
                continue
            if '"cwd"' not in line and '"type"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not info["cwd"]:
                info["cwd"] = o.get("cwd", "") or ""
            t = o.get("type")
            if t == "custom-title":
                info["custom_title"] = o.get("customTitle", "") or ""
            elif t == "ai-title":
                info["ai_title"] = o.get("aiTitle", "") or ""
            elif t == "user" and not info["first"]:
                c = _first_text(o.get("message", {}))
                if not c or c.startswith("<") or c.startswith("Caveat:"):
                    continue
                info["first"] = " ".join(c.split())[:120]
                info["handoff"] = info["first"].startswith(
                    "This session is being continued")
            if info["cwd"] and info["first"] and info["custom_title"]:
                meta_done = True

    info["cost"] = sum(_usage_cost(*usage_by_id[k]) for k in order)
    info["turns"] = len(order)
    # 記錄尚無費率資料的模型，供介面標示
    info["unpriced"] = sorted({m for _, m in usage_by_id.values()
                               if is_real_model(m) and m not in PRICING})
    # 脈絡使用率：最後一則回覆的 prompt 大小 ÷ 該模型的脈絡視窗
    info["ctx"] = 0
    info["ctx_pct"] = 0.0
    for k in reversed(order):
        u, model = usage_by_id[k]
        if not is_real_model(model):
            continue
        used = (u.get("input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0))
        if used:
            info["ctx"] = used
            info["ctx_pct"] = used / CONTEXT_WINDOW.get(model, DEFAULT_WINDOW)
        break
    return info


def label(info):
    """這條對話在清單上顯示的名字。"""
    for key in ("custom_title", "ai_title"):
        if info.get(key):
            return info[key]
    f = info.get("first", "")
    return f[:60] if f else tr("(無標題)")


def scan(use_cache=True, progress=None):
    """回傳所有對話，依最後使用時間新到舊排序。"""
    cache = {}
    if use_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("version") == CACHE_VERSION:
                cache = blob.get("items", {})
        except Exception:
            cache = {}

    files = [f for f in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
             if os.path.getsize(f) >= MIN_SIZE]
    out, fresh = [], {}
    for n, f in enumerate(files):
        st = os.stat(f)
        hit = cache.get(f)
        if hit and hit.get("size") == st.st_size and \
                abs(hit.get("mtime", 0) - st.st_mtime) < 1:
            info = hit
        else:
            if progress:
                progress(n + 1, len(files))
            try:
                info = read_one(f)
            except OSError:
                continue
        fresh[f] = info
        out.append(info)

    try:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump({"version": CACHE_VERSION, "items": fresh}, fh,
                      ensure_ascii=False)
    except OSError:
        pass

    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def signature():
    """所有紀錄檔的 (路徑, 大小, 修改時間) 摘要，供偵測變動。"""
    out = []
    for f in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            st = os.stat(f)
        except OSError:
            continue
        out.append((f, st.st_size, int(st.st_mtime)))
    return hash(tuple(sorted(out)))


def by_id(sessions):
    return {s["id"]: s for s in sessions}


HANDOFF_MARGIN = 300        # 秒；容許壓縮交接前後的寫入時間差


def newer_handoff(sessions, cwd, mtime):
    """同一個資料夾裡，是否有接續「這條」對話的較新交接。

    同資料夾可能有多條互不相關的對話鏈：若這條在交接檔建立之後仍持續活動，
    該交接屬於別條鏈，不視為取代。
    """
    for s in sessions:
        if s["cwd"] != cwd or not s["handoff"] or s["mtime"] <= mtime:
            continue
        born = s.get("ctime") or s["mtime"]
        if mtime <= born + HANDOFF_MARGIN:
            return s
    return None


def find_file(session_id):
    """在所有專案目錄裡找這條對話的紀錄檔。"""
    hits = glob.glob(os.path.join(PROJECTS, "*", session_id + ".jsonl"))
    return hits[0] if hits else None


def move_session(session_id, new_cwd):
    """把對話的紀錄檔搬到另一個專案資料夾。回傳 (成功, 錯誤訊息)。

    搬移後該對話會出現在目標資料夾的 claude -r 選單，於該處續接。
    同一磁碟內的更名為原子操作，內容不變。
    """
    src = find_file(session_id)
    if not src:
        return False, tr("找不到對話紀錄")
    if not os.path.isdir(new_cwd):
        return False, tr("目標資料夾不存在：%s") % new_cwd
    dest_dir = os.path.join(PROJECTS, project_dir_name(new_cwd))
    dest = os.path.join(dest_dir, session_id + ".jsonl")
    if os.path.normcase(dest) == os.path.normcase(src):
        return False, tr("已在該資料夾")
    if os.path.exists(dest):
        return False, tr("目標已存在同名紀錄")
    try:
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(src, dest)
    except OSError as e:
        return False, str(e)
    return True, None


def grep(needle, limit=200, progress=None):
    """在所有對話的全文裡找字串，回傳 {session_id: 命中次數}。

    以位元組比對搜尋，不做 JSON 解析。紀錄檔內容是 JSON 轉義形，
    因此同時比對原形與轉義形（含 \\ 或 " 的字串只會以轉義形出現）。
    """
    found = {}
    if not needle:
        return found
    pats = {needle.encode("utf-8")}
    escaped = json.dumps(needle, ensure_ascii=False)[1:-1]
    pats.add(escaped.encode("utf-8"))
    pats = sorted(pats)

    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    for n, f in enumerate(files):
        if progress:
            progress(n + 1, len(files))
        hits = 0
        tails = {p: b"" for p in pats}
        try:
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    for p in pats:
                        buf = tails[p] + chunk
                        hits += buf.count(p)
                        # 保留 len-1 個位元組接下一塊；保留整個 pattern 會重複計數
                        tails[p] = buf[-(len(p) - 1):] if len(p) > 1 else b""
        except OSError:
            continue
        if hits:
            found[os.path.basename(f)[:-6]] = hits
            if len(found) >= limit:
                break
    return found


def when(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def money(v):
    if not v:
        return ""
    if v >= 1000:
        return "${:,.0f}".format(v)
    if v >= 10:
        return "$%.0f" % v
    return "$%.2f" % v
