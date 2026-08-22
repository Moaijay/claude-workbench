# -*- coding: utf-8 -*-
"""回歸測試。改動後執行：python tests\\regression.py

只讀取對話紀錄，絕不寫入；工作台設定檔於測試前備份、結束時還原。
"""
import glob
import importlib.util
import json
import os
import shutil
import sys
import time
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

import appdata      # noqa: E402
import i18n         # noqa: E402

# 介面語言在各模組載入時就決定了，因此必須早於 options 等模組固定下來，
# 中文斷言才不會隨執行這支測試的機器語言而改變。
i18n.set_language("zh-TW")

import config       # noqa: E402
import launch       # noqa: E402
import options      # noqa: E402
import sessions     # noqa: E402

fails = []
passed = [0]

# CI 等乾淨環境沒裝 claude 執行檔；依賴它的檢查明示略過，不假裝通過
HAVE_CLAUDE = bool(shutil.which("claude")) or os.path.exists(
    os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe"))


def check(label, ok, detail=""):
    print(("  [OK] " if ok else "  [!!] ") + label +
          (("  " + str(detail)) if detail else ""))
    if ok:
        passed[0] += 1
    else:
        fails.append(label)


def snapshot_jsonl():
    return {f: (os.stat(f).st_size, int(os.stat(f).st_mtime))
            for f in glob.glob(os.path.join(sessions.PROJECTS, "*", "*.jsonl"))}


def load_settings_module():
    spec = importlib.util.spec_from_file_location(
        "st", os.path.join(HERE, "settings.pyw"))
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    return st


def wait_until(cond, timeout=15.0, root=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if root is not None:
            root.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def main():
    # 測試在設定檔的沙盒副本上進行，與使用中的工作台互不干擾
    import tempfile
    sandbox_dir = tempfile.mkdtemp(prefix="wbtest")
    sandbox = os.path.join(sandbox_dir, "config.json")
    shutil.copyfile(config.PATH, sandbox)
    config.PATH = sandbox
    files_before = snapshot_jsonl()
    cfg_bak = sandbox + ".bak"
    shutil.copyfile(sandbox, cfg_bak)

    st = load_settings_module()
    answers = {"yes": True}
    messagebox.askyesno = lambda *a, **k: answers["yes"]
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None
    opened = []
    # 執行期偵測的測試要用原函式；替身只給介面測試用
    real_running_sessions = launch.running_sessions
    real_running_status = launch.running_status
    launch.launch = lambda cfg, item: opened.append(item["name"])
    launch.running_sessions = lambda: set()
    launch.running_status = lambda: {}

    try:
        # ---------- 資料層 ----------
        print("== 資料層 ==")
        s = sessions.scan(use_cache=False)
        check("掃描", len(s) > 0, "%d 條" % len(s))
        check("金額為正", sum(x["cost"] for x in s) > 0)
        check("有 ctime 欄位", all("ctime" in x for x in s))
        check("無 <synthetic> 未計價",
              not any("<" in m for x in s for m in x.get("unpriced", [])))

        # grep：邊界不重複、轉義形找得到
        tmpdir = os.path.join(sessions.PROJECTS, "zz-test-grep")
        os.makedirs(tmpdir, exist_ok=True)
        tmp = os.path.join(tmpdir,
                           "00000000-0000-0000-0000-00000000test.jsonl")
        try:
            with open(tmp, "wb") as fh:
                fh.write(b"x" * ((1 << 20) - 3))
                fh.write("abc與『D:\\\\測試』路徑".encode("utf-8"))
            hits = sessions.grep("abc")
            key = os.path.basename(tmp)[:-6]
            check("grep 邊界不重複計數", hits.get(key) == 1,
                  str(hits.get(key)))
            hits = sessions.grep("D:\\測試")
            check("grep 找得到含反斜線的字串", hits.get(key) == 1,
                  str(hits.get(key)))
        finally:
            os.remove(tmp)
            os.rmdir(tmpdir)

        # 交接誤報：同資料夾多條鏈
        fake = [
            {"id": "aaa", "cwd": "X", "mtime": 100.0, "ctime": 100.0,
             "handoff": False},
            {"id": "hand", "cwd": "X", "mtime": 900.0, "ctime": 500.0,
             "handoff": True},
        ]
        check("接續警告：交接後仍活躍的鏈不誤報",
              sessions.newer_handoff(fake, "X", 850.0) is None)
        check("接續警告：被交接的鏈仍會警告",
              sessions.newer_handoff(fake, "X", 480.0) is not None)

        # 執行中偵測：官方執行期登錄（用替換前的原函式測）
        import launch as launch_real
        if os.path.isdir(launch_real.SESSIONS_DIR):
            t0 = time.time()
            live = real_running_sessions()
            dt = (time.time() - t0) * 1000
            check("執行中偵測走執行期登錄", isinstance(live, set), type(live))
            check("執行中偵測夠快（<100ms）", dt < 100, "%.1f ms" % dt)
            stale = os.path.join(launch_real.SESSIONS_DIR, "4999999.json")
            reuse = os.path.join(launch_real.SESSIONS_DIR, "4999998.json")
            try:
                with open(stale, "w", encoding="utf-8") as fh:
                    json.dump({"pid": 4999999,
                               "sessionId": "dead0000-dead-dead-dead-deaddeaddead"},
                              fh)
                # PID 重用：活的 PID（本測試行程）配上錯的建立時間
                with open(reuse, "w", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(), "procStart": "1",
                               "sessionId": "reus0000-dead-dead-dead-deaddeaddead"},
                              fh)
                live2 = real_running_sessions()
                check("殘留登錄（行程已死）會被排除",
                      "dead0000-dead-dead-dead-deaddeaddead" not in live2)
                check("殘留登錄（PID 已被重用）會被排除",
                      "reus0000-dead-dead-dead-deaddeaddead" not in live2)
            finally:
                os.remove(stale)
                os.remove(reuse)

            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, os.getpid())
            ft = (ctypes.c_ulonglong * 4)()
            k32.GetProcessTimes(h, ctypes.byref(ft, 0), ctypes.byref(ft, 8),
                                ctypes.byref(ft, 16), ctypes.byref(ft, 24))
            k32.CloseHandle(h)
            check("PID＋建立時間相符＝存活",
                  launch_real._pid_alive(os.getpid(), str(ft[0])))
            check("沒有建立時間時僅驗 PID",
                  launch_real._pid_alive(os.getpid()))

            # running_pids：登錄項目對應到行程 pid
            live_reg = os.path.join(launch_real.SESSIONS_DIR, "4999997.json")
            try:
                with open(live_reg, "w", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(), "procStart": str(ft[0]),
                               "sessionId":
                               "live0000-dead-dead-dead-deaddeaddead"}, fh)
                pids = launch_real.running_pids()
                check("running_pids 對應到行程",
                      pids.get("live0000-dead-dead-dead-deaddeaddead")
                      == os.getpid(), pids.get("live0000-dead-dead-dead-"
                                               "deaddeaddead"))
            finally:
                os.remove(live_reg)

            # 實機：正在跑的對話至少要有一條找得到終端機視窗
            live_now = launch_real.running_pids()
            if live_now:
                n_found = sum(1 for p in set(live_now.values())
                              if launch_real._window_candidates(p))
                check("實機：執行中對話找得到終端機視窗", n_found >= 1,
                      "%d/%d" % (n_found, len(set(live_now.values()))))

        # 脈絡使用率
        tmpdir2 = os.path.join(sessions.PROJECTS, "zz-test-ctx")
        os.makedirs(tmpdir2, exist_ok=True)
        tmp2 = os.path.join(tmpdir2,
                            "00000000-0000-0000-0000-0000000000c1.jsonl")
        try:
            rec = {"type": "assistant", "cwd": "X",
                   "message": {"id": "m1", "model": "claude-opus-5",
                               "usage": {"input_tokens": 1000,
                                         "cache_read_input_tokens": 199000,
                                         "cache_creation_input_tokens": 0,
                                         "output_tokens": 10}}}
            with open(tmp2, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
                fh.write("x" * (25 * 1024) + "\n")
            info = sessions.read_one(tmp2)
            check("脈絡使用率計算", info["ctx"] == 200000 and
                  abs(info["ctx_pct"] - 0.2) < 1e-6,
                  "%d tokens, %.0f%%" % (info["ctx"], info["ctx_pct"] * 100))
        finally:
            os.remove(tmp2)
            os.rmdir(tmpdir2)

        # 搬移對話
        import tempfile
        srcdir = os.path.join(sessions.PROJECTS, "zz-test-move")
        os.makedirs(srcdir, exist_ok=True)
        mid = "00000000-0000-0000-0000-0000000000m1"
        src = os.path.join(srcdir, mid + ".jsonl")
        target = tempfile.mkdtemp(prefix="wbmove")
        destdir = os.path.join(sessions.PROJECTS,
                               sessions.project_dir_name(target))
        try:
            open(src, "w", encoding="utf-8").write("{}\n")
            ok2, err2 = sessions.move_session(mid, target)
            dest = os.path.join(destdir, mid + ".jsonl")
            check("搬移對話成功", ok2 and os.path.exists(dest) and
                  not os.path.exists(src), err2)
            ok3, err3 = sessions.move_session(mid, target)
            check("搬到原地會拒絕", not ok3, err3)
            ok4, err4 = sessions.move_session("no-such-id", target)
            check("搬不存在的對話會拒絕", not ok4, err4)
        finally:
            for f in (src, os.path.join(destdir, mid + ".jsonl")):
                if os.path.exists(f):
                    os.remove(f)
            for d in (srcdir, destdir):
                if os.path.isdir(d):
                    os.rmdir(d)
            os.rmdir(target)

        # 脈絡視窗依模型世代
        check("脈絡視窗：新世代 1M",
              sessions.context_window("claude-opus-5") == 1_000_000 and
              sessions.context_window("claude-sonnet-4-6") == 1_000_000)
        check("脈絡視窗：舊世代 200K",
              sessions.context_window("claude-opus-4-5") == 200_000 and
              sessions.context_window("claude-sonnet-4-20250514") == 200_000 and
              sessions.context_window("claude-3-5-sonnet-20241022") == 200_000)
        check("脈絡視窗：Haiku 200K",
              sessions.context_window("claude-haiku-4-5") == 200_000)

        # 預覽：檔尾訊息抽取
        pvdir = os.path.join(sessions.PROJECTS, "zz-test-preview")
        os.makedirs(pvdir, exist_ok=True)
        pv = os.path.join(pvdir, "00000000-0000-0000-0000-0000000000p1.jsonl")
        try:
            recs = [
                {"type": "system", "content": "x"},
                {"type": "summary", "summary": "s"},
                {"type": "user", "timestamp": "2026-08-20T04:00:00.000Z",
                 "message": {"role": "user", "content": "第一句"}},
                {"type": "assistant", "timestamp": "2026-08-20T04:00:05.000Z",
                 "message": {"role": "assistant", "content": [
                     {"type": "thinking", "thinking": "思考不顯示"},
                     {"type": "text", "text": "回答一"}]}},
                {"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "tool_use", "name": "Bash"}]}},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "content": "工具輸出"}]}},
                {"type": "user", "message": {"role": "user",
                 "content": "<system-reminder>雜訊</system-reminder>"}},
                {"type": "user", "message": {"role": "user",
                 "content": "Caveat: 雜訊"}},
                {"type": "user", "timestamp": "2026-08-20T04:01:00.000Z",
                 "message": {"role": "user", "content": [
                     {"type": "text", "text": "第二句"}]}},
            ]
            with open(pv, "w", encoding="utf-8") as fh:
                fh.write("x" * (600 * 1024) + "\n")   # 撐出超過檔尾讀取範圍
                for r in recs:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            msgs = sessions.tail_messages(pv)
            check("預覽：只留實際往來、跳過工具與系統雜訊",
                  [m["role"] for m in msgs] == ["user", "assistant", "user"],
                  [m["role"] for m in msgs])
            check("預覽：內容與順序",
                  [m["text"] for m in msgs] == ["第一句", "回答一", "第二句"],
                  [m["text"][:10] for m in msgs])
            check("預覽：limit 取最後幾則",
                  [m["text"] for m in sessions.tail_messages(pv, limit=1)]
                  == ["第二句"])
            check("預覽：時戳轉本地時間",
                  bool(sessions.when_iso(msgs[0]["ts"])), msgs[0]["ts"])
            with open(pv, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "custom-title",
                                     "customTitle": "標題甲"},
                                    ensure_ascii=False) + "\n")
                fh.write(json.dumps({"type": "ai-title",
                                     "aiTitle": "自動乙"},
                                    ensure_ascii=False) + "\n")
            check("quick_title：手動命名壓過較新的自動標題",
                  sessions.quick_title(pv) == "標題甲",
                  sessions.quick_title(pv))
        finally:
            os.remove(pv)
            os.rmdir(pvdir)

        # 標題掃描不受檔頭窗口限制：長對話的改名記錄在檔尾也要抓到
        ttdir = os.path.join(sessions.PROJECTS, "zz-test-title")
        os.makedirs(ttdir, exist_ok=True)
        tt = os.path.join(ttdir, "00000000-0000-0000-0000-0000000000t1.jsonl")
        try:
            with open(tt, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "ai-title", "cwd": "X",
                                     "aiTitle": "遠古自動標題"},
                                    ensure_ascii=False) + "\n")
                fh.write(json.dumps({"type": "user", "message": {
                    "role": "user", "content": "第一句"}},
                    ensure_ascii=False) + "\n")
                # 內文含 "customTitle" 字樣的誘餌，不得被誤認成改名記錄
                fh.write(json.dumps({"type": "user", "message": {
                    "role": "user",
                    "content": 'we discussed "customTitle" and "aiTitle"'}},
                    ensure_ascii=False) + "\n")
                fh.write("x" * (5 * 1024 * 1024) + "\n")   # 超出檔頭窗口
                fh.write(json.dumps({"type": "custom-title",
                                     "customTitle": "改名一"},
                                    ensure_ascii=False) + "\n")
                fh.write(json.dumps({"type": "custom-title",
                                     "customTitle": "改名二"},
                                    ensure_ascii=False) + "\n")
            ti = sessions.read_one(tt)
            check("檔尾改名記錄抓得到且最後一筆為準",
                  ti["custom_title"] == "改名二" and
                  sessions.label(ti) == "改名二",
                  (ti["custom_title"], ti["ai_title"]))
            check("內文誘餌不影響標題", ti["ai_title"] == "遠古自動標題",
                  ti["ai_title"])
        finally:
            os.remove(tt)
            os.rmdir(ttdir)

        # ---------- 子代理紀錄 ----------
        subproj = os.path.join(sessions.PROJECTS, "zz-test-sub")
        sid_b = "00000000-0000-0000-0000-0000000000b1"
        sid_empty = "00000000-0000-0000-0000-0000000000b2"
        main_f = os.path.join(subproj, sid_b + ".jsonl")
        sub_dir = os.path.join(subproj, sid_b, "subagents", "workflows",
                               "wf_x")
        sub_f = os.path.join(sub_dir, "agent-test1.jsonl")
        empty_f = os.path.join(subproj, sid_empty + ".jsonl")
        target2 = os.path.join(sandbox_dir, "movetarget2")
        dest_proj = os.path.join(sessions.PROJECTS,
                                 sessions.project_dir_name(target2))
        os.makedirs(sub_dir, exist_ok=True)
        os.makedirs(target2, exist_ok=True)
        try:
            rec = {"type": "assistant", "cwd": "X", "message": {
                "id": "mb1", "model": "claude-opus-5",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}}
            with open(main_f, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")            # $5
            rec["message"]["id"] = "ms1"
            rec["message"]["usage"]["input_tokens"] = 2_000_000   # $10
            with open(sub_f, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
                fh.write("NEEDLE-zz-sub-search\n")
            with open(empty_f, "w", encoding="utf-8") as fh:
                fh.write('{"type":"user"}\n')

            check("parent_of 解析母對話", sessions.parent_of(sub_f) == sid_b)
            s2 = {x["id"]: x for x in sessions.scan()}
            check("小檔但有計費回覆會列出", sid_b in s2)
            check("子代理費用歸母對話",
                  sid_b in s2 and abs(s2[sid_b]["cost"] - 15.0) < 0.01,
                  s2.get(sid_b, {}).get("cost"))
            check("無計費回覆的空檔不列出", sid_empty not in s2)
            s3 = {x["id"]: x for x in sessions.scan()}
            check("重掃不重複累計子代理費用",
                  sid_b in s3 and abs(s3[sid_b]["cost"] - 15.0) < 0.01,
                  s3.get(sid_b, {}).get("cost"))
            hits2 = sessions.grep("NEEDLE-zz-sub-search")
            check("全文搜尋涵蓋子代理且歸母對話", hits2.get(sid_b) == 1,
                  hits2)
            sig1 = sessions.signature()
            with open(sub_f, "a", encoding="utf-8") as fh:
                fh.write("x\n")
            check("子代理變動會改變 signature",
                  sessions.signature() != sig1)

            ok5, err5 = sessions.move_session(sid_b, target2)
            moved_sub = os.path.join(dest_proj, sid_b, "subagents",
                                     "workflows", "wf_x", "agent-test1.jsonl")
            check("搬移連子代理目錄一起", ok5 and os.path.exists(moved_sub),
                  err5)
        finally:
            for p in (main_f, empty_f, sub_f,
                      os.path.join(dest_proj, sid_b + ".jsonl"),
                      os.path.join(dest_proj, sid_b, "subagents",
                                   "workflows", "wf_x", "agent-test1.jsonl")):
                if os.path.exists(p):
                    os.remove(p)
            for base in (subproj, dest_proj):
                for d in (os.path.join(base, sid_b, "subagents", "workflows",
                                       "wf_x"),
                          os.path.join(base, sid_b, "subagents", "workflows"),
                          os.path.join(base, sid_b, "subagents"),
                          os.path.join(base, sid_b),
                          base):
                    if os.path.isdir(d):
                        try:
                            os.rmdir(d)
                        except OSError:
                            pass

        # 執行中狀態為字典（用替換前的原函式測）
        st_map = real_running_status()
        check("running_status 回傳字典", isinstance(st_map, dict),
              type(st_map))
        if st_map:
            # 官方狀態值會長新的（已見過 busy/idle/shell），只驗型別不驗清單；
            # 介面把非 busy 一律視為開著，不依賴完整枚舉
            check("狀態值為非空字串",
                  all(isinstance(v, str) and v for v in st_map.values()),
                  set(st_map.values()))

        # 版本比較
        import version
        check("版本比較：新舊分得開",
              version.parse("1.10.0") > version.parse("1.9.9") and
              version.parse("v1.2.0") == version.parse("1.2.0") and
              version.parse("1.2") < version.parse("1.2.1"))
        check("版本比較：怪字串不炸",
              isinstance(version.parse("weird"), tuple))
        import i18n as _i
        check("檢查更新的翻譯齊全",
              all(k in _i.EN for k in ("檢查更新", "檢查更新失敗",
                                       "已是最新版本（v%s）")))
        check("交接與掃描字串的翻譯齊全",
              all(k in _i.EN for k in
                  ("　（已交接）", "開啟進行中，稍候",
                   "啟動清單已改指到接續：%s", "掃描失敗，沿用上次結果（%s）",
                   "「%s」已在某個視窗中開啟，但找不到那個視窗；請於工作列尋找。")))

        # 快取位置
        check("掃描快取在資料目錄",
              sessions.CACHE.startswith(appdata.data_dir()))
        check("安裝目錄沒有快取檔",
              not os.path.exists(os.path.join(HERE, "scan-cache.json")))

        # ---------- schema 抽取 ----------
        import claudeschema
        if HAVE_CLAUDE:
            sch = claudeschema.load()
            check("schema 抽取健全",
                  bool(sch.get("ok")) and
                  len(sch.get("keys") or {}) >= claudeschema.MIN_KEYS,
                  "%d 鍵" % len(sch.get("keys") or {}))
            check("哨兵鍵在",
                  sum(1 for k in claudeschema.SENTINELS
                      if k in (sch.get("keys") or {})) >= 2)
            check("enum 只綁定設定鍵",
                  all(k in sch["keys"] for k in sch.get("enums") or {}) and
                  len(sch.get("enums") or {}) >= 20,
                  "%d 組" % len(sch.get("enums") or {}))
            check("effortLevel 枚舉含 xhigh",
                  "xhigh" in (sch.get("enums") or {}).get("effortLevel", []))
        else:
            print("  [--] 未安裝 claude，略過 schema 抽取檢查")
        check("_sane 擋空結果", not claudeschema._sane({}))
        old_cache, old_scan = claudeschema.CACHE, claudeschema._scan
        try:
            claudeschema.CACHE = os.path.join(sandbox_dir, "schema-test.json")
            good = {"sig": "old", "version": "9.9.9",
                    "keys": {"model": "d", "effortLevel": "d"},
                    "enums": {}, "ok": True}
            with open(claudeschema.CACHE, "w", encoding="utf-8") as fh:
                json.dump(good, fh)
            claudeschema._scan = lambda p: ({}, {})
            blob = claudeschema.load(force=True)
            check("抽取失敗回落上次結果",
                  bool(blob.get("stale")) and
                  blob.get("keys", {}).get("model") == "d")
            with open(claudeschema.CACHE, encoding="utf-8") as fh:
                after = json.load(fh)
            check("抽取失敗不覆寫快取",
                  after.get("ok") is True and "stale" not in after)
        finally:
            claudeschema.CACHE, claudeschema._scan = old_cache, old_scan

        # ---------- 設定檔保護 ----------
        print("== 設定檔保護 ==")
        with open(config.PATH, "w", encoding="utf-8") as fh:
            fh.write("{ 這不是 JSON")
        cfg = config.load()
        check("損壞時回報 LOAD_ERROR", bool(config.LOAD_ERROR))
        corrupt = glob.glob(config.PATH + ".corrupt.*")
        check("壞檔已改名保留", len(corrupt) >= 1)
        check("壞檔不再擋路", not os.path.exists(config.PATH) or
              json.load(open(config.PATH, encoding="utf-8")) is not None)
        for f in corrupt:
            os.remove(f)
        shutil.copyfile(cfg_bak, config.PATH)
        cfg = config.load()
        check("還原後讀取正常", not config.LOAD_ERROR and cfg["items"])

        config.save(cfg)
        baks = glob.glob(os.path.join(appdata.backups_dir(), "config.json.*"))
        check("儲存前有輪替備份", len(baks) >= 1, "%d 份" % len(baks))

        # ---------- 介面 ----------
        print("== 介面 ==")
        st.make_dpi_aware()
        root = tk.Tk()
        st.setup_theme(root)
        root.geometry("1320x820+80+80")
        app = st.App(root)
        root.update()
        ok = wait_until(lambda: app.sessions, root=root)
        check("背景掃描完成", ok, "%d 條" % len(app.sessions))

        check("多選模式", str(app.tree.cget("selectmode")) == "extended")
        check("右鍵選單存在",
              hasattr(app, "tree_menu") and hasattr(app, "picked_menu"))

        # 交接改指：鏈上追到最新
        real_app_sessions = app.sessions
        app.sessions = [
            {"id": "a1", "cwd": "X", "mtime": 100.0, "ctime": 50.0,
             "handoff": False, "cost": 0.0, "ctx": 0, "ctx_pct": 0.0,
             "custom_title": "同名", "ai_title": "", "first": ""},
            {"id": "h1", "cwd": "X", "mtime": 500.0, "ctime": 150.0,
             "handoff": True, "cost": 0.0, "ctx": 0, "ctx_pct": 0.0,
             "custom_title": "同名", "ai_title": "", "first": ""},
            {"id": "h2", "cwd": "X", "mtime": 900.0, "ctime": 550.0,
             "handoff": True, "cost": 0.0, "ctx": 0, "ctx_pct": 0.0,
             "custom_title": "同名", "ai_title": "", "first": ""},
        ]
        try:
            ho = app._handoff_target("a1")
            check("交接追到鏈上最新", bool(ho) and ho["id"] == "h2",
                  ho and ho["id"])
            check("沒被交接的不改指", app._handoff_target("h2") is None)
            check("不明對話不改指", app._handoff_target("zzz") is None)

            # 啟動清單自動跟鏈，不再逐次詢問；排隊改名一併跟過去
            real_items2 = app.items
            real_pending2 = dict(app.pending_renames)
            app.items = [{"name": "跟鏈", "cwd": "X", "session": "a1"}]
            app.pending_renames = {"a1": "新名"}
            moved = app._auto_repoint()
            check("啟動清單自動跟到鏈上最新",
                  app.items[0]["session"] == "h2" and moved == ["跟鏈"],
                  app.items[0]["session"])
            check("排隊改名跟著交接走",
                  app.pending_renames.get("h2") == "新名" and
                  "a1" not in app.pending_renames,
                  dict(app.pending_renames))
            saved2 = json.load(open(config.PATH, encoding="utf-8"))
            check("自動改指寫入設定檔",
                  saved2.get("items", [{}])[0].get("session") == "h2")
            app.items = real_items2
            app.pending_renames = real_pending2
            cfg_r = config.load()
            cfg_r["items"] = real_items2
            cfg_r["pending_renames"] = real_pending2
            config.save(cfg_r)

            # 左側清單：被交接的舊對話預設隱藏；開關打開才顯示（灰＋後綴）
            ro_was = bool(app.recent_only.get())
            app.recent_only.set(False)
            app.v_handed.set(False)
            app.fill_tree()
            rows0 = [r for pnode in app.tree.get_children()
                     for r in app.tree.get_children(pnode)]
            check("已交接的預設隱藏、接續仍在",
                  "a1" not in rows0 and "h1" not in rows0 and "h2" in rows0,
                  rows0)
            app.v_handed.set(True)
            app.fill_tree()
            texts = {r: app.tree.item(r, "text")
                     for pnode in app.tree.get_children()
                     for r in app.tree.get_children(pnode)}
            check("開關打開後顯示且標（已交接）、接續不標",
                  "（已交接）" in texts.get("a1", "") and
                  "（已交接）" in texts.get("h1", "") and
                  "（已交接）" not in texts.get("h2", ""),
                  {k: v for k, v in texts.items() if k in ("a1", "h1", "h2")})
            check("（已交接）帶灰色標籤",
                  "superseded" in app.tree.item("a1", "tags"))
            # 全文搜尋命中時，隱藏中的舊對話仍要出現（歷史只存在舊檔）
            app.v_handed.set(False)
            app.hits = {"a1": 2}
            app.fill_tree()
            rows1 = [r for pnode in app.tree.get_children()
                     for r in app.tree.get_children(pnode)]
            check("搜尋命中的已交接對話仍顯示", "a1" in rows1, rows1)
            app.hits = None
            app.recent_only.set(ro_was)
        finally:
            app.sessions = real_app_sessions
            app.fill_tree()
        check("四條測試對話已封存", len(app.archived) >= 4,
              str(len(app.archived)))

        rows = [c for p in app.tree.get_children()
                for c in app.tree.get_children(p)]
        check("已封存的預設不顯示",
              not any(r in app.archived for r in rows))

        # 排序
        app.set_sort("cost")
        root.update()
        top_folder = app.tree.get_children()[0]
        first_row = app.tree.get_children(top_folder)[0]
        idx = sessions.by_id(app.sessions)
        max_cost = max(x["cost"] for x in app.sessions
                       if x["id"] not in app.archived)
        check("點金額排序後最貴的在最上面",
              abs(idx[first_row]["cost"] - max_cost) < 0.01,
              sessions.money(idx[first_row]["cost"]))
        app.set_sort("mtime")
        root.update()

        # 批次開啟走背景執行緒
        in_list = {i["session"] for i in app.items}
        idx_all = sessions.by_id(app.sessions)
        free = [r for r in [c for p in app.tree.get_children()
                            for c in app.tree.get_children(p)]
                if r not in in_list and
                os.path.isdir(idx_all.get(r, {}).get("cwd", ""))][:3]
        app.tree.selection_set(free)
        opened.clear()
        answers["yes"] = True
        app.open_many([app.item_for(x) for x in free])
        ok = wait_until(lambda: len(opened) == 3, root=root)
        check("批次開啟（背景執行緒）", ok, "開了 %d 個" % len(opened))
        ok = wait_until(lambda: all(x.lower() in app.running for x in free),
                        timeout=5, root=root)
        check("開啟後記為執行中", ok)
        app.running = set()

        # 右鍵選單以名字記索引，加項目不會讓 entryconfigure 指錯位置
        check("右鍵選單索引對得上標籤",
              app.tree_menu.entrycget(app._tm_idx["preview"],
                                      "label") == i18n.tr("預覽") and
              app.tree_menu.entrycget(app._tm_idx["archive"], "label")
              in (i18n.tr("封存"), i18n.tr("取消封存")) and
              app.picked_menu.entrycget(app._pm_idx["remove"],
                                        "label") == i18n.tr("移除"))
        check("左側 Delete 鍵綁定封存切換",
              bool(app.tree.bind("<Delete>")))

        # 已開啟者帶到前景，不重開
        real_focus = launch.focus_session
        focused_calls = []
        launch.focus_session = (lambda sid, hints=():
                                focused_calls.append(sid) or True)
        try:
            rows_all = [c for pnode in app.tree.get_children()
                        for c in app.tree.get_children(pnode)]
            target_sid = rows_all[0]
            app.running = {target_sid.lower()}
            app.running_status = {target_sid.lower(): "idle"}
            opened.clear()
            app.open_many([app.item_for(target_sid)], confirm=False)
            ok = wait_until(lambda: focused_calls, timeout=5, root=root)
            check("已開啟者帶到前景且不重開",
                  ok and opened == [] and focused_calls == [target_sid],
                  (opened, focused_calls))
        finally:
            launch.focus_session = real_focus
            app.running = set()
            app.running_status = {}

        # 視窗候選：血緣圈選（用本行程的 Tk 視窗仿真終端機類別）
        import ctypes
        u32 = ctypes.windll.user32
        w1 = tk.Toplevel(root)
        w1.title("wbtest-視窗甲-93d1")
        w2 = tk.Toplevel(root)
        w2.title("wbtest-視窗乙-93d1")
        root.update()
        hw = u32.GetAncestor(w1.winfo_id(), 2)      # GA_ROOT
        clsbuf = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hw, clsbuf, 64)
        cands = launch._window_candidates(os.getpid(),
                                          classes=(clsbuf.value,))
        titles = {t for _, t in cands}
        check("視窗候選涵蓋本行程視窗",
              {"wbtest-視窗甲-93d1", "wbtest-視窗乙-93d1"} <= titles,
              sorted(titles))
        w1.destroy()
        w2.destroy()
        root.focus_force()      # 還焦點；後面的鍵盤事件測試需要
        root.update()

        # 預覽視窗
        pvdir2 = os.path.join(sessions.PROJECTS, "zz-test-pvui")
        os.makedirs(pvdir2, exist_ok=True)
        pv2 = os.path.join(pvdir2,
                           "00000000-0000-0000-0000-0000000000p2.jsonl")
        fake_info = {"id": "00000000-0000-0000-0000-0000000000p2",
                     "path": pv2, "cwd": "X", "mtime": time.time(),
                     "cost": 1.23, "custom_title": "預覽測試", "ai_title": "",
                     "first": "", "ctx": 0, "ctx_pct": 0.0}
        try:
            with open(pv2, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "message": {
                    "role": "user", "content": "預覽測試訊息"}},
                    ensure_ascii=False) + "\n")
            app.sessions = app.sessions + [fake_info]
            app.show_preview(fake_info["id"])
            ok = wait_until(lambda: getattr(app, "_preview", None) is not None
                            and app._preview.winfo_exists(), root=root)
            texts = []

            def walk_text(w):
                for c in w.winfo_children():
                    if isinstance(c, tk.Text):
                        texts.append(c.get("1.0", "end"))
                    walk_text(c)
            if ok:
                walk_text(app._preview)
            check("預覽視窗顯示檔尾訊息",
                  ok and any("預覽測試訊息" in t for t in texts),
                  (texts[0][:60] if texts else ""))
            if ok:
                app._preview.destroy()
        finally:
            app.sessions = [s for s in app.sessions if s is not fake_info]
            os.remove(pv2)
            os.rmdir(pvdir2)
            app.fill_tree()     # 清掉背景刷新可能畫上的假對話列
        root.focus_force()      # 還焦點；後面的鍵盤事件測試需要
        root.update()

        # 未命名新增對話
        dlg = st.NewSessionDialog(root, config.load(),
                                  os.path.expanduser("~"))
        root.update()
        dlg.name.set("")
        dlg.ok()
        check("未命名建立回傳 new 標記",
              dlg.result is not None and dlg.result.get("new") is True and
              len(dlg.result.get("session", "")) == 36,
              str(dlg.result and dlg.result.get("name")))
        import launch as L
        args = L.claude_args(config.load(), dlg.result)
        check("未命名以 --session-id 開啟", args[0] == "--session-id", args[:2])
        args2 = L.claude_args(config.load(),
                              {"session": "x" * 36, "name": "n", "cwd": "C:\\"})
        check("既有對話仍以 --resume 開啟", args2[0] == "--resume", args2[:2])
        args3 = L.claude_args(config.load(),
                              {"session": "x" * 36, "name": "n", "cwd": "C:\\",
                               "model": "opus", "effort": "xhigh"})
        check("模型與 effort 旗標帶入指令",
              "--model" in args3 and "opus" in args3 and
              "--effort" in args3 and "xhigh" in args3, args3)
        check("未指定時不帶模型與 effort 旗標",
              "--model" not in args2 and "--effort" not in args2)

        # ultracode：以 --settings 疊加檔開啟（session 級，官方管道）
        args4 = L.claude_args(config.load(),
                              {"session": "x" * 36, "name": "n", "cwd": "C:\\",
                               "ultracode": True})
        ov = L.ultracode_overlay()
        check("ultracode 以 --settings 疊加檔帶入",
              "--settings" in args4 and ov in args4, args4[-2:])
        with open(ov, encoding="utf-8") as fh:
            check("疊加檔內容正確",
                  json.load(fh) == {"ultracode": True})
        check("未勾選時不帶 --settings", "--settings" not in args3)
        check("引號：含空白的參數兩種殼都包好",
              L._q("C:\\a b\\c.json", "powershell") == "'C:\\a b\\c.json'" and
              L._q("C:\\a b\\c.json", "cmd") == '"C:\\a b\\c.json"' and
              L._q("plain", "powershell") == "plain")

        # 新增對話：模型與 effort 選擇進入結果；預設＝空字串（不帶旗標）
        dlgm = st.NewSessionDialog(root, config.load(),
                                   os.path.expanduser("~"))
        root.update()
        dlgm.name.set("")
        dlgm.model.set("opus")
        dlgm.effort.set("xhigh")
        dlgm.ok()
        check("新增對話帶模型與 effort",
              dlgm.result is not None and
              dlgm.result.get("model") == "opus" and
              dlgm.result.get("effort") == "xhigh",
              dlgm.result and (dlgm.result.get("model"),
                               dlgm.result.get("effort")))
        check("沿用預設＝不帶旗標",
              dlg.result.get("model") == "" and dlg.result.get("effort") == "")
        check("effort 清單含 CLI 全部等級",
              set(("low", "medium", "high", "xhigh", "max"))
              <= set(st._effort_levels()), st._effort_levels())

        # ultracode 勾選：進結果、並鎖住 effort 下拉
        dlgu = st.NewSessionDialog(root, config.load(),
                                   os.path.expanduser("~"))
        root.update()
        dlgu.ultracode.set(True)
        dlgu._ultracode_toggled()
        eff_state = str(dlgu.effort_box.cget("state"))
        dlgu.name.set("")
        dlgu.ok()
        check("ultracode 勾選進結果且鎖住 effort",
              dlgu.result is not None and
              dlgu.result.get("ultracode") is True and
              dlgu.result.get("effort") == "" and eff_state == "disabled",
              (dlgu.result and dlgu.result.get("ultracode"), eff_state))
        check("未勾選 ultracode ＝ False",
              dlg.result.get("ultracode") is False)
        root.focus_force()
        root.update()

        # 按「建立」即開視窗：命名建立完成後直接 launch，不必再按立即啟動
        real_items = app.items
        app.items = []
        opened.clear()
        app._new_session_done({"name": "建立即開", "cwd": os.path.expanduser("~"),
                               "session": "f" * 36, "model": "",
                               "effort": ""}, config.load())
        check("命名建立後直接開啟視窗",
              opened == ["建立即開"] and len(app.items) == 1 and
              ("f" * 36).lower() in app.running, opened)
        app.items = real_items
        app.running = set()
        app._just_launched.pop("f" * 36, None)
        app.fill_picked()
        root.focus_force()      # 還焦點；後面的鍵盤事件測試需要
        root.update()

        # 狀態燈：busy 顯示為橘色標籤
        # 先同步重畫再取列：清單可能還留著前面測試期間背景刷新畫上的列，
        # 與現在的 app.sessions 不一致，取到的列會在下次重畫時消失
        app.fill_tree()
        app.running_status = {r.lower(): "busy" for r in
                              [c for pnode in app.tree.get_children()
                               for c in app.tree.get_children(pnode)][:1]}
        app.running = set(app.running_status)
        app.fill_tree()
        # 不在這裡 root.update()：泵事件會讓背景輪詢結果蓋掉手動設的狀態
        # （fill_tree 是同步的，讀 tag 不需要泵）
        busy_row = next(iter(app.running_status))
        found_tags = ()
        for pnode in app.tree.get_children():
            for c in app.tree.get_children(pnode):
                if c.lower() == busy_row:
                    found_tags = app.tree.item(c, "tags")
        check("busy 狀態帶橘色標籤", "busy" in found_tags, found_tags)
        app.running_status = {}
        app.running = set()

        # 脈絡欄開關
        check("脈絡開關在工作台選項中",
              any(k == "ui.show_context" for _, rows in options.WORKBENCH_SPEC
                  for k, *_ in rows))
        app.v_ctx.set(False)
        app.apply_columns()
        check("關閉脈絡欄後不顯示",
              "ctx" not in app.tree.cget("displaycolumns"),
              app.tree.cget("displaycolumns"))
        app.v_ctx.set(True)
        app.apply_columns()
        check("開啟脈絡欄後顯示",
              "ctx" in app.tree.cget("displaycolumns"))

        # 就地重新命名（右側）
        app.items = [{"name": "old", "cwd": "C:\\", "session": "s" * 36}]
        app.fill_picked()
        root.update()
        app.picked.selection_set(["0"])
        pushed = []
        launch.rename_session = lambda cfg, item, name: pushed.append(name) or (True, None)
        app.rename_item()
        root.update()
        editors = [w for w in app.picked.winfo_children()
                   if isinstance(w, ttk.Entry)]
        check("F2 出現就地編輯框", len(editors) == 1)
        if editors:
            ed = editors[0]
            ed.delete(0, "end")
            ed.insert(0, "newname")
            ed.event_generate("<Return>")
            root.update()
            ok5 = wait_until(lambda: pushed == ["newname"], timeout=5,
                             root=root)
            check("確認後更新本地名稱", app.items[0]["name"] == "newname",
                  app.items[0]["name"])
            check("未開啟的對話自動同步 Claude 端", ok5, pushed)

        # 開啟中的對話：不立即同步，改記入待同步佇列
        app.running = {"s" * 36}
        app.running_status = {"s" * 36: "idle"}
        # 讓背景輪詢也看到「開啟中」，避免它提前把佇列沖掉
        launch.running_status = lambda: {"s" * 36: "idle"}
        app.fill_picked()
        root.update()
        app.picked.selection_set(["0"])
        pushed.clear()
        app.rename_item()
        root.update()
        editors = [w for w in app.picked.winfo_children()
                   if isinstance(w, ttk.Entry)]
        if editors:
            editors[0].delete(0, "end")
            editors[0].insert(0, "another")
            editors[0].event_generate("<Return>")
            root.update()
        time.sleep(0.3)
        root.update()
        check("開啟中改名不立即同步",
              app.items[0]["name"] == "another" and pushed == [], pushed)
        check("改名進入待同步佇列",
              app.pending_renames.get("s" * 36) == "another",
              dict(app.pending_renames))
        saved_cfg = json.load(open(config.PATH, encoding="utf-8"))
        check("佇列寫入設定檔",
              saved_cfg.get("pending_renames", {}).get("s" * 36) == "another")

        # 對話關閉後由輪詢補同步；只發一次
        launch.running_status = lambda: {}
        app.running = set()
        app.running_status = {}
        pushed.clear()
        app._flush_pending_renames(set())
        ok6 = wait_until(lambda: pushed == ["another"], timeout=5, root=root)
        app._flush_pending_renames(set())
        root.update()
        check("對話關閉後自動補發 /rename 且只發一次",
              ok6 and pushed == ["another"] and not app.pending_renames,
              (pushed, dict(app.pending_renames)))

        # 左側：開啟中可就地改名，進佇列並立即顯示新名稱
        rows_now = [c for pnode in app.tree.get_children()
                    for c in app.tree.get_children(pnode)]
        if rows_now:
            target_row = rows_now[0]
            app.running = {target_row.lower()}
            app.running_status = {target_row.lower(): "idle"}
            launch.running_status = lambda: {target_row.lower(): "idle"}
            root.focus_force()
            root.update()
            app.tree.selection_set([target_row])
            app.tree.focus(target_row)
            app.rename_session_title()
            root.update()
            editors2 = [w for w in app.tree.winfo_children()
                        if isinstance(w, ttk.Entry)]
            check("左側開啟中可就地改名", len(editors2) == 1)
            if editors2:
                editors2[0].delete(0, "end")
                editors2[0].insert(0, "佇列新名")
                editors2[0].event_generate("<Return>")
                root.update()
                shown_now = app.tree.item(target_row, "text")
                check("左側開啟中改名進佇列且清單即時顯示",
                      app.pending_renames.get(target_row) == "佇列新名" and
                      "佇列新名" in shown_now,
                      (app.pending_renames.get(target_row), shown_now))
            app.pending_renames.pop(target_row, None)
            app._save_pending()
            launch.running_status = lambda: {}
            app.running = set()
            app.running_status = {}
            app.fill_tree()

        # 改名同步中擋開啟
        app._rename_guard = {"s" * 36}
        opened.clear()
        app.open_many([{"name": "x", "cwd": os.path.expanduser("~"),
                        "session": "s" * 36}])
        root.update()
        time.sleep(0.2)
        root.update()
        check("改名同步中拒絕開啟", opened == [],
              app.status.cget("text"))
        app._rename_guard = set()

        # 編輯中延後刷新
        app._editing = True
        app._refresh_pending = False
        app.refresh_views()
        check("編輯中刷新被延後", app._refresh_pending is True)
        app._editing = False
        app._refresh_pending = False

        # 多筆移除需確認
        app.items = [{"name": "a", "cwd": "C:\\", "session": "1"},
                     {"name": "b", "cwd": "C:\\", "session": "2"}]
        app.fill_picked()
        root.update()
        app.picked.selection_set(["0", "1"])
        answers["yes"] = False
        app.remove_item()
        check("多筆移除回答否時不動作", len(app.items) == 2)
        answers["yes"] = True
        app.remove_item()
        check("多筆移除回答是時執行", len(app.items) == 0)

        # 復原測試資料
        real = json.load(open(cfg_bak, encoding="utf-8"))
        app.items = list(real["items"])
        app.archived = set(real.get("archived", []))
        app.save_archived()
        root.destroy()

        # ---------- 設定編輯器 ----------
        print("== 設定編輯器 ==")
        cs_bak = options.CLAUDE_SETTINGS + ".testbak"
        shutil.copyfile(options.CLAUDE_SETTINGS, cs_bak)
        try:
            root = tk.Tk()
            root.withdraw()
            if HAVE_CLAUDE:
                dlg = options.open_claude_settings(root)
                root.update()
                check("設定項目數", len(dlg.vars) >= 30, len(dlg.vars))
                check("ultracode 存在", "ultracode" in dlg.vars)
                editable = []

                def walk(w):
                    for c in w.winfo_children():
                        if isinstance(c, ttk.Combobox) and \
                                str(c.cget("state")) != "readonly":
                            editable.append(c)
                        walk(c)
                walk(dlg)
                check("所有下拉唯讀", not editable)
                dlg.destroy()
            else:
                print("  [--] 未安裝 claude，略過 Claude 設定編輯器檢查")

            # 工作台選項：金額開關
            d2 = options.open_workbench_options(root, config)
            root.update()
            check("金額開關在工作台選項中", "ui.show_cost" in d2.vars)
            d2.vars["ui.show_cost"].set("關閉")
            d2.save()
            saved = json.load(open(config.PATH, encoding="utf-8"))
            check("金額開關寫入 ui.show_cost",
                  saved.get("ui", {}).get("show_cost") is False)
            d2.vars["ui.show_cost"].set("開啟")
            d2.save()
            d2.destroy()
            root.destroy()
        finally:
            shutil.move(cs_bak, options.CLAUDE_SETTINGS)

        # ---------- 英文介面 ----------
        print("== 英文介面 ==")
        check("語言選項在工作台選項中",
              any(k == "language" for _, rows in options.WORKBENCH_SPEC
                  for k, *_ in rows))
        try:
            i18n.set_language("en")
            check("英文查得到翻譯",
                  i18n.tr("已在啟動清單中") == "Already in the launch list",
                  i18n.tr("已在啟動清單中"))
            check("查不到就回傳中文原文",
                  i18n.tr("這句沒有翻譯") == "這句沒有翻譯")
            check("同字不同義分得開",
                  i18n.tr("開啟", "bool") == "On" and i18n.tr("開啟") == "Open",
                  (i18n.tr("開啟", "bool"), i18n.tr("開啟")))
            root = tk.Tk()
            root.withdraw()
            en_app = st.App(root)
            root.update()
            head = str(en_app.tree.heading("cost")["text"])
            check("英文介面的標題與按鈕為英文",
                  en_app.arch_btn.cget("text") == "Archive" and
                  head.startswith("Cost"),
                  (en_app.arch_btn.cget("text"), head))
            root.destroy()
        finally:
            i18n.set_language("zh-TW")
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    # 不變量：工作台絕不刪除或截短任何紀錄檔。
    # 正在進行中的 Claude 對話會持續追加自己的紀錄，屬正常外部寫入。
    files_after = snapshot_jsonl()
    gone = [f for f in files_before if f not in files_after]
    shrunk = [f for f, (sz, _) in files_before.items()
              if f in files_after and files_after[f][0] < sz]
    check("★ 紀錄檔無刪除", not gone, [os.path.basename(f) for f in gone[:3]])
    check("★ 紀錄檔無截短", not shrunk,
          [os.path.basename(f) for f in shrunk[:3]])

    print()
    print("全部通過（%d 項）" % passed[0]
          if not fails else "沒過（%d 過／%d 失敗）：%s"
          % (passed[0], len(fails), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
