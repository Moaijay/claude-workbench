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
CACHE_VERSION = 5           # 格式改了就把舊快取作廢

# 脈絡視窗大小（token）。目前世代預設 1M；Haiku 與 4.6 之前的舊世代為 200K。
CONTEXT_WINDOW = {
    "claude-haiku-4-5": 200_000,
}
LEGACY_200K = ("claude-3-", "claude-haiku-", "claude-opus-4-1",
               "claude-opus-4-2025", "claude-opus-4-5",
               "claude-sonnet-4-2025", "claude-sonnet-4-5")
DEFAULT_WINDOW = 1_000_000


def context_window(model):
    if model in CONTEXT_WINDOW:
        return CONTEXT_WINDOW[model]
    if any(model.startswith(p) for p in LEGACY_200K):
        return 200_000
    return DEFAULT_WINDOW

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


# 子代理與 workflow 的紀錄在 <專案>/<母對話id>/subagents/ 底下，
# 獨立計費、不寫回母檔
SUB_GLOB = os.path.join(PROJECTS, "*", "*", "subagents", "**", "*.jsonl")


def project_dir_name(path):
    """資料夾路徑 → ~/.claude/projects 底下的目錄名（非英數一律換成連字號）。"""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def parent_of(sub_path):
    """子代理紀錄檔路徑 → 母對話 id；不是子代理路徑時回空字串。"""
    parts = os.path.relpath(sub_path, PROJECTS).split(os.sep)
    return parts[1] if len(parts) >= 4 and parts[2] == "subagents" else ""


def _first_text(msg):
    c = msg.get("content", "")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c
                     if isinstance(p, dict) and p.get("type") == "text")
    return (c or "").strip()


def _msg_text(msg):
    """訊息的純文字內容（保留換行）；工具呼叫、圖片等區塊略過。"""
    c = msg.get("content", "")
    if isinstance(c, list):
        c = "\n".join(p.get("text", "") for p in c
                      if isinstance(p, dict) and p.get("type") == "text")
    return (c or "").strip()


TAIL_BYTES = 512 * 1024     # 預覽只讀檔尾這麼多；一則訊息極少超過


def tail_messages(path, limit=8, max_bytes=TAIL_BYTES):
    """檔尾最後幾則實際對話訊息，舊到新：[{"role", "text", "ts"}]。

    只看 user／assistant 記錄的文字區塊；工具結果、系統提醒、
    Caveat 之類的自動內容不算訊息。
    """
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()               # 丟掉被切斷的半行
        data = fh.read()

    out = []
    for line in reversed(data.splitlines()):
        if len(out) >= limit:
            break
        if b'"type"' not in line:
            continue
        try:
            o = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        t = o.get("type")
        if t not in ("user", "assistant"):
            continue
        text = _msg_text(o.get("message") or {})
        if not text:
            continue
        if t == "user" and (text.startswith("<") or text.startswith("Caveat:")):
            continue
        out.append({"role": t, "text": text[:2000],
                    "ts": o.get("timestamp", "") or ""})
    out.reverse()
    return out


def quick_title(path):
    """只讀檔頭與檔尾找對話標題，供不做完整掃描的呼叫端（如啟動器）。

    改名是往檔尾追加 custom-title 記錄，所以檔尾的最新；檔頭作後備。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)
            fh.seek(max(0, size - 64 * 1024))
            tail = fh.read()
    except OSError:
        return ""
    # 手動命名永遠壓過自動標題（與 label() 一致），不論哪筆較新
    custom = ai = ""
    for blob in (tail, head):
        for line in reversed(blob.splitlines()):
            if b'"customTitle"' not in line and b'"aiTitle"' not in line:
                continue
            try:
                o = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            custom = custom or o.get("customTitle") or ""
            ai = ai or o.get("aiTitle") or ""
            if custom:
                return custom
    return ai


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

            # 標題不受檔頭窗口限制：改名記錄往檔尾追加，長對話的改名
            # 全落在窗口外。整檔都看、最後一筆為準（反正整檔本來就要讀）。
            if '"customTitle"' in line or '"aiTitle"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    o = None
                if o:
                    t = o.get("type")
                    if t == "custom-title" and o.get("customTitle"):
                        info["custom_title"] = o["customTitle"]
                    elif t == "ai-title" and o.get("aiTitle"):
                        info["ai_title"] = o["aiTitle"]

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
            if t == "user" and not info["first"]:
                c = _first_text(o.get("message", {}))
                if not c or c.startswith("<") or c.startswith("Caveat:"):
                    continue
                info["first"] = " ".join(c.split())[:120]
                info["handoff"] = info["first"].startswith(
                    "This session is being continued")
            if info["cwd"] and info["first"]:
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
            info["ctx_pct"] = used / context_window(model)
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
    """回傳所有對話，依最後使用時間新到舊排序。

    金額含掛在該對話底下的子代理與 workflow 紀錄。小於 MIN_SIZE 的主檔
    多半是沒有實際內容的空對話，但只要有計費回覆仍會列出。
    """
    cache = {}
    if use_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("version") == CACHE_VERSION:
                cache = blob.get("items", {})
        except Exception:
            cache = {}

    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    sub_files = glob.glob(SUB_GLOB, recursive=True)
    total = len(files) + len(sub_files)
    out, fresh = [], {}

    def read_cached(f, n):
        st = os.stat(f)
        hit = cache.get(f)
        if hit and hit.get("size") == st.st_size and \
                abs(hit.get("mtime", 0) - st.st_mtime) < 1:
            return hit
        if progress:
            progress(n + 1, total)
        return read_one(f)

    for n, f in enumerate(files):
        try:
            info = read_cached(f, n)
        except OSError:
            continue
        fresh[f] = info
        if info["size"] >= MIN_SIZE or info["cost"] > 0:
            # 快取存母檔本身的花費；顯示用的副本才加上子代理
            out.append(dict(info))

    sub_cost, sub_unpriced = {}, {}
    for n, f in enumerate(sub_files):
        try:
            info = read_cached(f, len(files) + n)
        except OSError:
            continue
        fresh[f] = info
        pid = parent_of(f)
        if not pid:
            continue
        sub_cost[pid] = sub_cost.get(pid, 0.0) + info["cost"]
        for m in info.get("unpriced") or []:
            sub_unpriced.setdefault(pid, set()).add(m)
    for info in out:
        pid = info["id"]
        if pid in sub_cost:
            info["cost"] += sub_cost[pid]
        if pid in sub_unpriced:
            info["unpriced"] = sorted(set(info.get("unpriced") or [])
                                      | sub_unpriced[pid])

    try:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump({"version": CACHE_VERSION, "items": fresh}, fh,
                      ensure_ascii=False)
    except OSError:
        pass

    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def signature():
    """所有紀錄檔（含子代理）的 (路徑, 大小, 修改時間) 摘要，供偵測變動。"""
    out = []
    for f in (glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
              + glob.glob(SUB_GLOB, recursive=True)):
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
    # 該對話的子代理紀錄在同層的 <id>/ 目錄，要跟著搬，否則會變成孤兒
    src_sub = os.path.join(os.path.dirname(src), session_id)
    dest_sub = os.path.join(dest_dir, session_id)
    if os.path.isdir(src_sub) and os.path.exists(dest_sub):
        return False, tr("目標已存在同名紀錄")
    try:
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(src, dest)
        if os.path.isdir(src_sub):
            try:
                os.replace(src_sub, dest_sub)
            except OSError as e:
                os.replace(dest, src)
                return False, str(e)
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

    files = (glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
             + glob.glob(SUB_GLOB, recursive=True))
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
            # 子代理的命中歸給母對話
            sid = parent_of(f) or os.path.basename(f)[:-6]
            found[sid] = found.get(sid, 0) + hits
            if len(found) >= limit:
                break
    return found


def when(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def when_iso(ts):
    """紀錄檔的 ISO-8601 UTC 時戳 → 本地時間顯示；解析失敗回空字串。"""
    try:
        import calendar
        t = time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        return when(calendar.timegm(t))
    except (ValueError, TypeError):
        return ""


def money(v):
    if not v:
        return ""
    if v >= 1000:
        return "${:,.0f}".format(v)
    if v >= 10:
        return "$%.0f" % v
    return "$%.2f" % v
