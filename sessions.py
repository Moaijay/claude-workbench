# -*- coding: utf-8 -*-
"""掃描 ~/.claude/projects 底下所有 Claude Code 對話紀錄，並估算每條的花費。"""
import glob
import json
import os
import re
import shutil
import time

import appdata
from i18n import tr

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
CACHE = appdata.cache_path("scan-cache.json")
CACHE_VERSION = 10          # 格式改了就把舊快取作廢

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _handoff_source(text, own_id):
    """交接摘要裡明指的前身對話 id；找不到回空字串。

    摘要末尾固定附上前一份逐字稿的路徑（…<id>.jsonl），優先取它；
    沒有路徑時退而取內文第一個非自身的 uuid。
    """
    m = re.search(r"transcript\s+at:\s*\S*?(%s)\.jsonl" % UUID_RE.pattern,
                  text)
    if m:
        return m.group(1).lower()
    for u in UUID_RE.findall(text):
        if u.lower() != own_id.lower():
            return u.lower()
    return ""

# 脈絡視窗大小（token）。目前世代預設 1M；Haiku 與 4.6 之前的舊世代為 200K。
CONTEXT_WINDOW = {
    "claude-haiku-4-5": 200_000,
}
# Haiku 5.5 起也是 1M，所以只列 Haiku 4 以前的前綴
LEGACY_200K = ("claude-3-", "claude-haiku-4-", "claude-opus-4-1",
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

# 每百萬 token 的（輸入, 輸出）美元。取自 Anthropic 公開定價（2026-11-01 對過）。
PRICING = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    # Sonnet 5 上市價 $2/$10 已成為正式價（原定 2026-09-01 調回 $3/$15 取消）
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    # Haiku 5.5 分兩段計價：prompt 在 10 萬 token 以內為此價，超過見 LONG_PROMPT
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
# prompt（輸入＋快取讀寫）超過門檻時整則改用另一組價：{模型: (門檻, (輸入, 輸出))}
LONG_PROMPT = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
}
DEFAULT_PRICE = (5.0, 25.0)
CACHE_READ_RATE = 0.1       # 快取讀取＝輸入價的 0.1 倍

# 有些模型以帶日期的完整 id 記錄（如 claude-haiku-4-5-20251001），
# 或帶 [1m] 之類的脈絡標記；查價時折回主名，免得同一顆模型因 id
# 寫法不同被當成無費率
_DATED_RE = re.compile(r"(-20\d{6})?(\[[^\]]*\])?$")


def _price_key(model):
    """模型 id 對應費率表的鍵；帶日期或 [1m] 字尾的折回主名，查無回 None。"""
    if model in PRICING:
        return model
    base = _DATED_RE.sub("", model or "")
    return base if base in PRICING else None


# 快取讀取不是 0.1 倍的例外：
#   Fable/Mythos 5.1 為 $0.25/M（0.025 倍）；Opus 5.5 為 $0.20/M（0.05 倍）
CACHE_READ_OVERRIDE = {
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
    "claude-opus-5-5": 0.05,
}
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
    key = _price_key(model)
    pin, pout = PRICING[key] if key else DEFAULT_PRICE
    model = key or model
    if model in LONG_PROMPT:
        limit, long_price = LONG_PROMPT[model]
        prompt = (usage.get("input_tokens", 0)
                  + usage.get("cache_read_input_tokens", 0)
                  + usage.get("cache_creation_input_tokens", 0))
        if prompt > limit:
            pin, pout = long_price
    if usage.get("speed") == "fast":
        pin, pout = pin * FAST_RATE, pout * FAST_RATE
    created = usage.get("cache_creation") or {}
    w1h = created.get("ephemeral_1h_input_tokens", 0)
    w5m = created.get("ephemeral_5m_input_tokens")
    if w5m is None:
        w5m = max(0, usage.get("cache_creation_input_tokens", 0) - w1h)
    tools = usage.get("server_tool_use") or {}
    searches = tools.get("web_search_requests", 0) or 0
    cr = CACHE_READ_OVERRIDE.get(model, CACHE_READ_RATE)
    return (usage.get("input_tokens", 0) * pin
            + w5m * pin * CACHE_WRITE_5M
            + w1h * pin * CACHE_WRITE_1H
            + usage.get("cache_read_input_tokens", 0) * pin * cr
            + usage.get("output_tokens", 0) * pout) / 1_000_000 \
        + searches * WEB_SEARCH_USD


def _iso_ms(ts):
    """紀錄檔的 ISO-8601 UTC 時戳 → 毫秒；解析失敗回 None。"""
    if not ts or len(ts) < 19:
        return None
    try:
        import calendar
        sec = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None
    frac = 0
    if len(ts) > 20 and ts[19] == ".":
        digits = "".join(ch for ch in ts[20:23] if ch.isdigit())
        frac = int(digits.ljust(3, "0")) if digits else 0
    return sec * 1000 + frac


def read_one(path):
    """讀出單一對話的摘要資訊與累計花費。

    計算花費需掃完整個檔：同一則回覆在串流過程會寫入多次，以 message id 去重取最後一筆。

    新版 Claude Code 在每次執行結束時往檔尾寫一筆 cost-state，記下它自己
    算的累計花費（從 startTime 起、跨多次續接累加），其中包含不寫進逐字稿
    的背景呼叫。info["official"] 記下最後一筆，以及逐字稿裡被它涵蓋的那段
    的估算值，由 scan() 換算成較準的總額。
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
        "handoff_from": "",     # 交接摘要明指的前身對話 id
        "cost": 0.0,
        "turns": 0,
        "first_ms": None,       # 第一／最後一則計費回覆的時間（毫秒）
        "last_ms": None,
        "official": None,
    }
    meta_done = False
    read_bytes = 0
    usage_by_id = {}
    where_by_id = {}            # message id → (行號, 時間毫秒)
    order = []
    cost_state = None           # (行號, 紀錄)
    relocated = ""

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
                        where_by_id[mid] = (i, _iso_ms(o.get("timestamp")))

            if '"cost-state"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    o = None
                if o and o.get("type") == "cost-state"                         and isinstance(o.get("totalCostUSD"), (int, float))                         and isinstance(o.get("startTime"), (int, float)):
                    cost_state = (i, o)

            # 對話換了資料夾（Claude Code 在對話中 cd、或本工具「移至其他
            # 資料夾」）時往檔尾追加 relocated，之後以它為準、最後一筆為準。
            # 檔頭的 cwd 仍是原本的位置。
            if '"relocatedCwd"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    o = None
                if o and o.get("type") == "relocated" and \
                        isinstance(o.get("relocatedCwd"), str) and \
                        o["relocatedCwd"]:
                    relocated = o["relocatedCwd"]

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
                if info["handoff"]:
                    info["handoff_from"] = _handoff_source(c, info["id"])
            if info["cwd"] and info["first"]:
                meta_done = True

    if relocated:
        info["cwd"] = relocated
    info["cost"] = sum(_usage_cost(*usage_by_id[k]) for k in order)
    info["turns"] = len(order)
    stamps = [ms for _, ms in where_by_id.values() if ms is not None]
    if stamps:
        info["first_ms"], info["last_ms"] = min(stamps), max(stamps)
    if cost_state and not cost_state[1].get("hasUnknownModelCost"):
        # 被這筆 cost-state 涵蓋的回覆：在它之前寫入、且不早於 startTime
        at, o = cost_state
        start = o["startTime"]
        covered, end = 0.0, None
        for k in order:
            line_no, ms = where_by_id[k]
            if line_no > at or (ms is not None and ms < start):
                continue
            covered += _usage_cost(*usage_by_id[k])
            if ms is not None and (end is None or ms > end):
                end = ms
        info["official"] = {"usd": float(o["totalCostUSD"]), "start": start,
                            "end": end if end is not None else start,
                            "covered": covered}
    # 記錄尚無費率資料的模型，供介面標示
    info["unpriced"] = sorted({m for _, m in usage_by_id.values()
                               if is_real_model(m) and _price_key(m) is None})
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

    sub_cost, sub_unpriced, subs = {}, {}, {}
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
        subs.setdefault(pid, []).append(info)
        for m in info.get("unpriced") or []:
            sub_unpriced.setdefault(pid, set()).add(m)
    for info in out:
        pid = info["id"]
        if pid in sub_cost:
            info["cost"] += sub_cost[pid]
        if pid in sub_unpriced:
            info["unpriced"] = sorted(set(info.get("unpriced") or [])
                                      | sub_unpriced[pid])
        info["cost"] = with_official(info, subs.get(pid, ()))

    try:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump({"version": CACHE_VERSION, "items": fresh}, fh,
                      ensure_ascii=False)
    except OSError:
        pass

    # 同一條對話可能同時在兩個專案目錄（例如手動複製到新位置、舊的還留著）：
    # 只列最近寫入的那份，與 find_file 取的是同一份
    newest = {}
    for info in out:
        k = info["id"].lower()
        if k not in newest or (info["mtime"], info["size"]) >                 (newest[k]["mtime"], newest[k]["size"]):
            newest[k] = info
    out = list(newest.values())
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


# 子代理在母對話某次執行的期間內開始，就算進那次的 cost-state；
# 容許母檔最後一則回覆之後再晚這麼久才開始（毫秒）
SUB_GRACE_MS = 2 * 60 * 1000


def with_official(info, subs=()):
    """母對話（已含子代理）的金額，併入 Claude Code 自己記下的花費。

    cost-state 涵蓋的那段改用官方數字，其餘仍是估算。官方數字比同段估算
    低時（例如該次執行還沒結束、紀錄還沒寫）保留估算——只會往上修正，
    不會因為官方紀錄不完整而少算。
    """
    off = info.get("official")
    if not off:
        return info["cost"]
    covered = off["covered"]
    for s in subs:
        first = s.get("first_ms")
        if first is not None and                 off["start"] <= first <= off["end"] + SUB_GRACE_MS:
            covered += s["cost"]
    return info["cost"] - covered + max(off["usd"], covered)


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


def handoff_map(sessions):
    """{被交接的舊對話 id: 接續它的那條}。

    只認交接摘要裡明指的前身 id（摘要末尾固定附上前一份逐字稿的路徑）。
    不用「同資料夾＋時間相近」推測——那會把交接前恰好停用的無關對話
    全部誤判成已交接。配不到明確前身就不判定：寧可多顯示，絕不誤藏。
    """
    by = {s["id"]: s for s in sessions}
    out = {}
    for s in sessions:
        src = (s.get("handoff_from") or "").lower()
        if src and src != s["id"].lower() and src in by:
            out[src] = s
    return out


def find_file(session_id):
    """在所有專案目錄裡找這條對話的紀錄檔。"""
    hits = glob.glob(os.path.join(PROJECTS, "*", session_id + ".jsonl"))
    if len(hits) > 1:
        # 有好幾份時取最近寫入的（續接時 Claude Code 寫的是那份）
        def key(h):
            try:
                st = os.stat(h)
                return (st.st_mtime, st.st_size)
            except OSError:
                return (0, 0)
        hits.sort(key=key, reverse=True)
    return hits[0] if hits else None


def _stamp_relocated(path, session_id, new_cwd):
    """在紀錄檔尾追加 relocated 記錄（與 Claude Code 自己搬移時寫的同格式）。

    沒有它，紀錄檔頭的 cwd 仍是舊位置：本工具會把對話列在舊資料夾，
    Claude Code 的 /resume 選單也會因「記錄的資料夾不同」把它濾掉
    （中文資料夾名換成專案目錄名時常撞名）。
    """
    rec = json.dumps({"type": "relocated", "sessionId": session_id,
                      "relocatedCwd": new_cwd},
                     ensure_ascii=False, separators=(",", ":"))
    with open(path, "rb+") as fh:
        fh.seek(0, os.SEEK_END)
        lead = b""
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                lead = b"\n"
            fh.seek(0, os.SEEK_END)
        fh.write(lead + rec.encode("utf-8") + b"\n")


def same_dir(a, b):
    return os.path.normcase(os.path.normpath(a)) == \
        os.path.normcase(os.path.normpath(b))


def move_session(session_id, new_cwd, old_cwd=None):
    """把對話改到另一個資料夾續接。回傳 (成功, 錯誤訊息)。

    紀錄檔（與子代理目錄）搬到目標資料夾對應的專案目錄，並記下新位置，
    之後該對話會出現在目標資料夾的 claude -r 選單，於該處續接。
    同一磁碟內的更名為原子操作，原有內容不變。不同資料夾可能對應同一個
    專案目錄（非英數字一律變成連字號），這時只記下新位置、不必搬檔。
    old_cwd 是對話目前記錄的資料夾，與目標相同時回報「已在該資料夾」。
    """
    src = find_file(session_id)
    if not src:
        return False, tr("找不到對話紀錄")
    if not os.path.isdir(new_cwd):
        return False, tr("目標資料夾不存在：%s") % new_cwd
    if old_cwd is not None and same_dir(old_cwd, new_cwd):
        return False, tr("已在該資料夾")
    dest_dir = os.path.join(PROJECTS, project_dir_name(new_cwd))
    dest = os.path.join(dest_dir, session_id + ".jsonl")
    if os.path.normcase(dest) == os.path.normcase(src):
        if old_cwd is None:
            return False, tr("已在該資料夾")
        try:
            _stamp_relocated(src, session_id, new_cwd)
        except OSError as e:
            return False, str(e)
        return True, None
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
    try:
        _stamp_relocated(dest, session_id, new_cwd)
    except OSError:
        pass                # 已搬過去，只差顯示位置
    return True, None


def copy_memory(old_cwd, new_cwd):
    """把舊資料夾的專案記憶複製到新資料夾。回傳是否有複製。

    Claude Code 的專案記憶放在 ~/.claude/projects/<專案目錄>/memory，
    資料夾搬走後新位置對應另一個專案目錄，記憶就讀不到了。只在新位置
    還沒有任何記憶時複製（不覆蓋）；舊的留著，因為撞名時可能是別的
    資料夾在用。
    """
    src = os.path.join(PROJECTS, project_dir_name(old_cwd), "memory")
    dst = os.path.join(PROJECTS, project_dir_name(new_cwd), "memory")
    if os.path.normcase(src) == os.path.normcase(dst):
        return False
    try:
        if not os.path.isdir(src) or not os.listdir(src):
            return False
        if os.path.isdir(dst) and os.listdir(dst):
            return False
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    except OSError:
        return False


# 找搬走的資料夾時不往裡面找的目錄
_SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata",
              "appdata", "recovery", "system volume information",
              "node_modules", "__pycache__", "site-packages", "venv"}
FIND_DEPTH = 5              # 從磁碟根目錄往下找幾層


def _name_fits(name, want):
    """資料夾名是否就是 want，或 want 後面接 -、_、空白、( 的改名（日記 → 日記-diary）。"""
    n, w = name.lower(), want.lower()
    return n == w or (len(n) > len(w) and n.startswith(w)
                      and n[len(w)] in "-_ (（")


def _walk_dirs(root, wanted, depth, out):
    """在 root 底下 depth 層內找名字符合 wanted（小寫）任一者的資料夾。"""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        low = e.name.lower()
        for w in wanted:
            if _name_fits(low, w):
                out.setdefault(w, []).append(e.path)
        if depth > 1 and low not in _SKIP_DIRS and \
                not low.startswith((".", "$")):
            _walk_dirs(e.path, wanted, depth - 1, out)


def _descend(base, parts):
    """從 base 依序往下走 parts；某段不在時，接受唯一一個改名過的同名資料夾。"""
    cur = base
    for part in parts:
        nxt = os.path.join(cur, part)
        if os.path.isdir(nxt):
            cur = nxt
            continue
        try:
            kids = [e.path for e in os.scandir(cur)
                    if e.is_dir(follow_symlinks=False)
                    and _name_fits(e.name, part)]
        except OSError:
            return None
        if len(kids) != 1:
            return None
        cur = kids[0]
    return os.path.normpath(cur)


def missing_tail(path):
    """不存在的資料夾 → (磁碟, 從第一個不存在的那一段起的各段)；存在則 None。"""
    path = os.path.normpath(path)
    drive, rest = os.path.splitdrive(path)
    parts = [x for x in rest.split(os.sep) if x]
    if not drive or not parts:
        return None
    k = 0
    while k < len(parts) and \
            os.path.isdir(os.path.join(drive + os.sep, *parts[:k + 1])):
        k += 1
    if k == len(parts):
        return None
    return drive.upper(), parts[k:]


# 路徑裡有這些名字的位置多半是備份或待刪的副本，排在後面、不預設勾選
_DOUBTFUL = ("node_modules", "待刪", "回收", "trash", "backup", "備份", "副本")


def doubtful(path):
    return any(w in part.lower() for part in path.split(os.sep)
               for w in _DOUBTFUL)


def likely(old, cands):
    """找到的新位置是否有把握到可以預設勾選：唯一一個、同一顆磁碟、不像副本。"""
    return len(cands) == 1 and not doubtful(cands[0]) and \
        os.path.splitdrive(cands[0])[0].upper() == \
        os.path.splitdrive(old)[0].upper()


def find_moved(paths, drives=(), roots=None):
    """不存在的資料夾 → 現在可能的位置（清單，可能為空，較可能的在前）。

    從第一個不存在的那一段起比對（例如 D:\\日記\\Entries 找「日記\\Entries」），
    在同一顆磁碟找同名資料夾（也接受「日記-diary」這種加了字尾的改名）、
    底下也有同樣子路徑者；同一顆找不到再找 drives 裡的其他磁碟。
    roots 可指定某顆磁碟從哪裡找起（{"C:": 資料夾}，測試用），預設從根目錄。
    """
    roots = roots or {}
    tails = {}
    for p in paths:
        t = missing_tail(p)
        if t:
            tails[os.path.normpath(p)] = t
    if not tails:
        return {}
    walked = {}                 # 磁碟 → {小寫名稱: [路徑]}
    names = {t[1][0].lower() for t in tails.values()}

    def lookup(drive, name):
        if drive not in walked:
            hits = {}
            root = roots.get(drive, drive + os.sep)
            if os.path.isdir(root):
                _walk_dirs(root, names, FIND_DEPTH, hits)
            walked[drive] = hits
        return walked[drive].get(name.lower(), [])

    found = {}
    others = sorted({d.upper().rstrip(":\\/") + ":" for d in drives if d})
    for p, (drive, tail) in tails.items():
        cands = []
        order = [drive] + [x for x in others if x != drive]
        for d in order:
            for base in lookup(d, tail[0]):
                cand = _descend(base, tail[1:])
                if cand and not same_dir(cand, p):
                    cands.append(cand)
            if cands:
                break
        if not cands and len(tail) > 1:
            cands = _find_inside(p, tail, lookup, order)
        found[p] = sorted(set(cands), key=lambda c: (
            doubtful(c), not os.path.basename(c).lower()
            == tail[-1].lower(), c.count(os.sep), c))
    return found


def _find_inside(p, tail, lookup, drives):
    """中間某層也改了名（例如 教授論文 → 論文\\東大教授論文）時的退路。

    最上面那段找得到（整個資料夾搬走了），就在它的新位置裡找最後一段的
    同名資料夾；只有唯一一個才算，避免 src、test 這種常見名字配錯。
    """
    for d in drives:
        tops = [b for b in lookup(d, tail[0]) if os.path.isdir(b)]
        if len(tops) != 1:
            continue
        hits = {}
        _walk_dirs(tops[0], {tail[-1].lower()}, FIND_DEPTH + 1, hits)
        got = [os.path.normpath(h) for h in hits.get(tail[-1].lower(), [])
               if not same_dir(h, p)]
        return got if len(got) == 1 else []
    return []


# 新版 Claude Code 在紀錄檔裡另存送給模型的內容：每次請求的副本
# （{"type":"api-request…} 開頭）與系統提示＋工具說明的快照（prompt_snapshot
# 附件）。搜尋要跳過這兩種，否則系統提示裡的字會讓每一條新對話都「命中」；
# 對話本身的內容在 user／assistant 記錄裡另有一份。
REQUEST_PREFIX = b'{"type":"api-request'
SNAPSHOT_MARK = b'"attachment":{"type":"prompt_snapshot"'
HEAD_BYTES = 256            # 判斷記錄種類只看行首這麼多


def _skip_line(head):
    return head.startswith(REQUEST_PREFIX) or SNAPSHOT_MARK in head


def _count_lines(block, pats, begin=0, end=None):
    """block[begin:end]（完整的行）裡 pats 出現的總次數，跳過 Claude Code
    自存副本的行。

    逐個命中位置回頭看它所在那行的行首，同一行的命中共用判斷結果；
    沒有命中的區塊不必切行，也不另外複製，維持位元組比對的速度。
    """
    if end is None:
        end = len(block)
    n = 0
    for p in pats:
        i = block.find(p, begin, end)
        line_end, skip = -1, False
        while i != -1:
            if i > line_end:
                start = max(begin, block.rfind(b"\n", begin, i) + 1)
                line_end = block.find(b"\n", i, end)
                if line_end == -1:
                    line_end = end
                skip = _skip_line(block[start:start + HEAD_BYTES])
            if not skip:
                n += 1
            i = block.find(p, i + len(p), end)
    return n


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
        pending = []            # 上一塊留下、還沒遇到換行的半行
        try:
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        if pending:
                            hits += _count_lines(b"".join(pending), pats)
                        break
                    # 以完整的行為單位計數。跨塊的那一行另外接起來算，
                    # 整塊本身不複製
                    first = chunk.find(b"\n")
                    if first == -1:
                        pending.append(chunk)
                        continue
                    if pending:
                        pending.append(chunk[:first + 1])
                        hits += _count_lines(b"".join(pending), pats)
                    else:
                        hits += _count_lines(chunk, pats, 0, first + 1)
                    last = chunk.rfind(b"\n") + 1
                    hits += _count_lines(chunk, pats, first + 1, last)
                    pending = [chunk[last:]] if last < len(chunk) else []
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
