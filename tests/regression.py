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
    real_background = launch.background_sessions
    launch.launch = lambda cfg, item, args=None: opened.append(item["name"])
    launch.running_sessions = lambda: set()
    launch.running_status = lambda: {}
    launch.background_sessions = lambda: {}

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
            # Claude Code 自存的請求副本與系統提示快照不算命中；
            # 跨好幾塊的長行、一塊裡多行都要算對
            with open(tmp, "wb") as fh:
                fh.write(b'{"type":"api-request-blob","message":"NEEDLE-q"}\n')
                fh.write(b'{"parentUuid":"x","isSidechain":false,'
                         b'"attachment":{"type":"prompt_snapshot",'
                         b'"systemPrompt":"NEEDLE-q"}}\n')
                fh.write(b'{"type":"user","message":"NEEDLE-q NEEDLE-q"}\n')
                fh.write(b'{"type":"api-request","p":"'
                         + b"y" * (3 << 20) + b'NEEDLE-q"}\n')
                fh.write(b'{"type":"assistant","m":"'
                         + b"z" * ((2 << 20) + 7) + b'NEEDLE-q"}\n')
                fh.write(b'{"type":"user","message":"NEEDLE-q"}')
            hits = sessions.grep("NEEDLE-q")
            check("grep 跳過請求副本與系統提示快照、長行照算",
                  hits.get(key) == 4, str(hits.get(key)))
        finally:
            os.remove(tmp)
            os.rmdir(tmpdir)

        # 交接判定：只認摘要明指的前身，不再用同資料夾＋時間推測
        fake = [
            {"id": "aaa", "cwd": "X", "handoff": False, "handoff_from": ""},
            {"id": "hand", "cwd": "X", "handoff": True,
             "handoff_from": "aaa"},
            # 交接前恰好停用的無關對話——時間啟發式時代的誤判受害者
            {"id": "quiet", "cwd": "X", "handoff": False, "handoff_from": ""},
        ]
        hm = sessions.handoff_map(fake)
        check("交接只認明指的前身",
              hm.get("aaa", {}).get("id") == "hand", hm.keys())
        check("同資料夾的無關舊對話不誤判", "quiet" not in hm and len(hm) == 1)
        check("前身不存在時不建立連結",
              not sessions.handoff_map(
                  [{"id": "h", "handoff": True, "handoff_from": "gone"}]))
        # read_one 從交接摘要抽出前身 id
        hodir = os.path.join(sessions.PROJECTS, "zz-test-hofrom")
        os.makedirs(hodir, exist_ok=True)
        old_id = "00000000-0000-0000-0000-0000000000d1"
        hof = os.path.join(hodir, "00000000-0000-0000-0000-0000000000d2.jsonl")
        try:
            summary = ("This session is being continued from a previous "
                       "conversation that ran out of context. read the full "
                       "transcript at: C:\\x\\" + old_id + ".jsonl")
            with open(hof, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "cwd": "X", "message": {
                    "role": "user", "content": summary}},
                    ensure_ascii=False) + "\n")
            hi = sessions.read_one(hof)
            check("交接摘要抽出前身 id",
                  hi["handoff"] and hi["handoff_from"] == old_id,
                  hi["handoff_from"])
        finally:
            os.remove(hof)
            os.rmdir(hodir)

        # 執行中偵測：官方執行期登錄（用替換前的原函式測）
        import launch as launch_real
        if os.path.isdir(launch_real.SESSIONS_DIR):
            t0 = time.time()
            live = set(real_running_status())
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
                live2 = set(real_running_status())
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

            # 背景對話的行程也會登錄（kind 為 bg），但沒有視窗，不拿去找視窗
            bg_reg = os.path.join(launch_real.SESSIONS_DIR, "4999995.json")
            try:
                with open(bg_reg, "w", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(), "procStart": str(ft[0]),
                               "kind": "bg", "sessionId":
                               "bgrg0000-dead-dead-dead-deaddeaddead"}, fh)
                check("背景對話的登錄不拿去找視窗",
                      "bgrg0000-dead-dead-dead-deaddeaddead"
                      not in launch_real.running_pids() and
                      "bgrg0000-dead-dead-dead-deaddeaddead"
                      in real_running_status())
            finally:
                os.remove(bg_reg)

            # 背景執行（claude --bg）：背景服務的 roster.json＋jobs 狀態
            old_roster, old_jobs = launch_real.DAEMON_ROSTER, \
                launch_real.JOBS_DIR
            bgdir = tempfile.mkdtemp(prefix="wbbg")
            try:
                launch_real.DAEMON_ROSTER = os.path.join(bgdir, "roster.json")
                launch_real.JOBS_DIR = os.path.join(bgdir, "jobs")
                os.makedirs(os.path.join(launch_real.JOBS_DIR, "bb000001"))
                with open(os.path.join(launch_real.JOBS_DIR, "bb000001",
                                       "state.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"state": "working",
                               "detail": "  正在跑\n測試  "}, fh)
                with open(launch_real.DAEMON_ROSTER, "w",
                          encoding="utf-8") as fh:
                    json.dump({"workers": {
                        "bb000001": {"pid": os.getpid(),
                                     "procStart": str(ft[0]),
                                     "cliVersion": "2.1.300",
                                     "sessionId":
                                     "BB000001-dead-dead-dead-deaddeaddead"},
                        "bb000002": {"pid": 4999996,
                                     "sessionId":
                                     "bb000002-dead-dead-dead-deaddeaddead"},
                        "bb000003": {"pid": os.getpid(), "procStart": "1",
                                     "sessionId":
                                     "bb000003-dead-dead-dead-deaddeaddead"},
                    }}, fh)
                bg = real_background()
                got = bg.get("bb000001-dead-dead-dead-deaddeaddead") or {}
                check("背景對話：讀得到且 id 轉小寫",
                      got.get("short") == "bb000001", bg)
                check("背景對話：working 視為執行中",
                      got.get("status") == "busy", got)
                check("背景對話：行程已死或 PID 被重用的排除",
                      len(bg) == 1, sorted(bg))
                check("背景對話：帶出進度與版本",
                      got.get("detail") == "正在跑 測試" and
                      got.get("version") == "2.1.300" and
                      got.get("state") == "working", got)
                check("背景對話：舊版的挑得出來、版本不明不算",
                      launch_real.outdated_background(bg, "2.1.316")
                      == ["bb000001-dead-dead-dead-deaddeaddead"] and
                      launch_real.outdated_background(bg, "2.1.300") == []
                      and launch_real.outdated_background(bg, "") == [])
                launch.background_sessions = real_background
                try:
                    st_all = real_running_status()
                finally:
                    launch.background_sessions = lambda: {}
                check("執行中偵測含背景對話",
                      st_all.get("bb000001-dead-dead-dead-deaddeaddead")
                      == "busy")
                with open(launch_real.DAEMON_ROSTER, "w",
                          encoding="utf-8") as fh:
                    fh.write("{壞掉")
                check("roster 壞掉時回空、不拋例外", real_background() == {})
                os.remove(launch_real.DAEMON_ROSTER)
                check("沒有背景服務時回空", real_background() == {})
            finally:
                launch_real.DAEMON_ROSTER, launch_real.JOBS_DIR = \
                    old_roster, old_jobs
                shutil.rmtree(bgdir, ignore_errors=True)

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

        # 資料夾搬家：記下新位置、撞名的專案目錄、找新位置、複製記憶

        base = tempfile.mkdtemp(prefix="wbreloc")
        made_proj = []
        try:
            old_dir = os.path.join(base, "甲乙")      # 已不存在的舊位置
            new_dir = os.path.join(base, "分類", "甲乙")
            same_dir = os.path.join(base, "丙丁")     # 專案目錄名與舊位置相同
            os.makedirs(os.path.join(new_dir, "子"))
            os.makedirs(same_dir)
            rid = "00000000-0000-0000-0000-0000000000r1"
            old_proj = os.path.join(sessions.PROJECTS,
                                    sessions.project_dir_name(old_dir))
            check("中文資料夾名換成專案目錄名會撞名",
                  sessions.project_dir_name(old_dir)
                  == sessions.project_dir_name(same_dir))
            if os.path.exists(old_proj):
                raise RuntimeError("測試用專案目錄已存在：" + old_proj)
            os.makedirs(old_proj)
            made_proj.append(old_proj)
            rf = os.path.join(old_proj, rid + ".jsonl")
            with open(rf, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "cwd": old_dir,
                                     "message": {"content": "hi"}},
                                    ensure_ascii=False))     # 沒有結尾換行
            check("搬家前以檔頭的資料夾為準",
                  sessions.read_one(rf)["cwd"] == old_dir)

            ok6, err6 = sessions.move_session(rid, same_dir, old_dir)
            check("撞名的資料夾：不搬檔、只記下新位置",
                  ok6 and os.path.exists(rf) and
                  sessions.read_one(rf)["cwd"] == same_dir, err6)
            lines = open(rf, encoding="utf-8").read().splitlines()
            rec = json.loads(lines[-1])
            check("新位置記錄與 Claude Code 同格式、補上換行",
                  len(lines) == 2 and rec == {"type": "relocated",
                                              "sessionId": rid,
                                              "relocatedCwd": same_dir},
                  lines[-1])
            ok7, err7 = sessions.move_session(rid, same_dir, same_dir)
            check("已在該資料夾會拒絕", not ok7, err7)

            new_proj = os.path.join(sessions.PROJECTS,
                                    sessions.project_dir_name(new_dir))
            if not os.path.exists(new_proj):
                made_proj.append(new_proj)
            ok8, err8 = sessions.move_session(rid, new_dir, same_dir)
            moved_f = os.path.join(new_proj, rid + ".jsonl")
            check("搬到別的專案目錄並記下新位置",
                  ok8 and os.path.exists(moved_f) and not os.path.exists(rf)
                  and sessions.read_one(moved_f)["cwd"] == new_dir, err8)

            # 同一條對話被複製到兩個專案目錄：只列一次、取最近寫入的那份
            dup_proj = os.path.join(sessions.PROJECTS, "zz-test-dup")
            os.makedirs(dup_proj, exist_ok=True)
            made_proj.append(dup_proj)
            with open(moved_f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {
                    "id": "mr1", "model": "claude-opus-5",
                    "usage": {"input_tokens": 10, "output_tokens": 1}}})
                    + "\n")
            dup_f = os.path.join(dup_proj, rid + ".jsonl")
            shutil.copy(moved_f, dup_f)
            old_t = time.time() - 3600
            os.utime(dup_f, (old_t, old_t))
            listed = [x for x in sessions.scan() if x["id"] == rid]
            check("同一條對話有兩份：只列一次、取最近的",
                  len(listed) == 1 and
                  os.path.normcase(listed[0]["path"])
                  == os.path.normcase(moved_f) and
                  os.path.normcase(sessions.find_file(rid))
                  == os.path.normcase(moved_f),
                  [x["path"] for x in listed])

            found = sessions.find_moved(
                [old_dir, os.path.join(old_dir, "子"),
                 os.path.join(base, "不存在"), new_dir],
                roots={os.path.splitdrive(base)[0].upper(): base})
            check("找到搬走的資料夾與其子資料夾",
                  found.get(old_dir) == [new_dir] and
                  found.get(os.path.join(old_dir, "子"))
                  == [os.path.join(new_dir, "子")], found)
            check("找不到的回空清單、還在的不列",
                  found.get(os.path.join(base, "不存在")) == [] and
                  new_dir not in found, found)
            os.makedirs(os.path.join(base, "分類", "甲乙", "改名後", "深"))
            got = sessions.find_moved(
                [os.path.join(old_dir, "舊名", "深")],
                roots={os.path.splitdrive(base)[0].upper(): base})
            os.makedirs(os.path.join(base, "生活", "日記-diary", "Entries"))
            os.makedirs(os.path.join(base, "生活", "作業-hw"))
            os.makedirs(os.path.join(base, "生活", "作業-冷啟動"))
            os.makedirs(os.path.join(base, "封存", "待刪", "網頁"))
            os.makedirs(os.path.join(base, "賺錢", "網頁"))
            r2 = sessions.find_moved(
                [os.path.join(base, "日記", "Entries"),
                 os.path.join(base, "作業"), os.path.join(base, "網頁")],
                roots={os.path.splitdrive(base)[0].upper(): base})
            diary = os.path.join(base, "生活", "日記-diary", "Entries")
            check("改名加了字尾（日記 → 日記-diary）也找得到",
                  r2.get(os.path.join(base, "日記", "Entries")) == [diary]
                  and sessions.likely(os.path.join(base, "日記"), [diary]),
                  r2)
            check("同名不只一個、或像待刪副本的：不算有把握，副本排後面",
                  not sessions.likely(base, r2.get(os.path.join(base, "作業")))
                  and r2.get(os.path.join(base, "網頁"))[0]
                  == os.path.join(base, "賺錢", "網頁") and
                  not sessions.likely(
                      base, [os.path.join(base, "封存", "待刪", "網頁")]), r2)
            check("中間那層改了名也找得到最後一層",
                  list(got.values()) == [[os.path.join(new_dir, "改名後",
                                                       "深")]], got)

            mem = os.path.join(old_proj, "memory")
            os.makedirs(mem)
            open(os.path.join(mem, "MEMORY.md"), "w").write("x")
            check("專案記憶複製到新位置",
                  sessions.copy_memory(old_dir, new_dir) and
                  os.path.exists(os.path.join(new_proj, "memory",
                                              "MEMORY.md")) and
                  os.path.exists(os.path.join(mem, "MEMORY.md")))
            check("新位置已有記憶就不覆蓋",
                  not sessions.copy_memory(old_dir, new_dir))
        finally:
            for d in made_proj:
                shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(base, ignore_errors=True)

        # 費率（2026-09-21 依官方價目表對過）
        check("Fable 5.1 有費率資料",
              "claude-fable-5-1" in sessions.PRICING and
              sessions.PRICING["claude-fable-5-1"] == (10.0, 50.0))
        check("Sonnet 5 費率為正式價 2/10",
              abs(sessions._usage_cost({"input_tokens": 1_000_000,
                                        "output_tokens": 1_000_000},
                                       "claude-sonnet-5") - 12.0) < 1e-6)
        check("Fable 5.1 快取讀取 0.025 倍",
              abs(sessions._usage_cost(
                  {"cache_read_input_tokens": 1_000_000},
                  "claude-fable-5-1") - 0.25) < 1e-6)
        check("帶日期字尾的模型 id 折回主名查價",
              sessions._price_key("claude-haiku-4-5-20251001")
              == "claude-haiku-4-5" and
              abs(sessions._usage_cost({"input_tokens": 1_000_000,
                                        "output_tokens": 1_000_000},
                                       "claude-haiku-4-5-20251001")
                  - 6.0) < 1e-6 and
              sessions._price_key("claude-nonexistent-9-20991231") is None)
        check("其他模型快取讀取仍 0.1 倍",
              abs(sessions._usage_cost(
                  {"cache_read_input_tokens": 1_000_000},
                  "claude-opus-5") - 0.5) < 1e-6 and
              abs(sessions._usage_cost(
                  {"cache_read_input_tokens": 1_000_000},
                  "claude-fable-5") - 1.0) < 1e-6)

        # 脈絡視窗依模型世代
        check("脈絡視窗：新世代 1M",
              sessions.context_window("claude-opus-5") == 1_000_000 and
              sessions.context_window("claude-fable-5-1") == 1_000_000 and
              sessions.context_window("claude-sonnet-4-6") == 1_000_000)
        check("脈絡視窗：舊世代 200K",
              sessions.context_window("claude-opus-4-5") == 200_000 and
              sessions.context_window("claude-sonnet-4-20250514") == 200_000 and
              sessions.context_window("claude-3-5-sonnet-20241022") == 200_000)
        check("脈絡視窗：Haiku 200K",
              sessions.context_window("claude-haiku-4-5") == 200_000)
        check("脈絡視窗：Haiku 5.5 為 1M",
              sessions.context_window("claude-haiku-5-5") == 1_000_000)

        # 費率：5.5 世代（2026-11-01 依官方價目表對過）
        def c(model, **u):
            return sessions._usage_cost(u, model)
        check("Opus 5.5 費率 4/20",
              abs(c("claude-opus-5-5", input_tokens=1_000_000,
                    output_tokens=1_000_000) - 24.0) < 1e-6)
        check("Opus 5.5 快取讀取 $0.20（0.05 倍）",
              abs(c("claude-opus-5-5",
                    cache_read_input_tokens=1_000_000) - 0.20) < 1e-6)
        check("Sonnet 5.5 費率 2/10、快取讀取 0.1 倍",
              abs(c("claude-sonnet-5-5", input_tokens=1_000_000,
                    output_tokens=1_000_000) - 12.0) < 1e-6 and
              abs(c("claude-sonnet-5-5",
                    cache_read_input_tokens=1_000_000) - 0.20) < 1e-6)
        check("Haiku 5.5 短 prompt 0.10/0.50",
              abs(c("claude-haiku-5-5", input_tokens=100_000,
                    output_tokens=1_000_000) - 0.51) < 1e-6)
        check("Haiku 5.5 prompt 超過 10 萬改用 0.50/2.50",
              abs(c("claude-haiku-5-5", input_tokens=1000,
                    cache_read_input_tokens=100_000,
                    output_tokens=1_000_000)
                  - (1000 * 0.5 + 100_000 * 0.05 + 1_000_000 * 2.5) / 1e6)
              < 1e-9)
        check("帶 [1m] 的模型 id 折回主名查價",
              sessions._price_key("claude-opus-5[1m]") == "claude-opus-5" and
              sessions._price_key("claude-opus-5-5[1m]") == "claude-opus-5-5")

        # 官方 cost-state：涵蓋的那段改用 Claude Code 自己記的金額
        csdir = os.path.join(sessions.PROJECTS, "zz-test-coststate")
        os.makedirs(csdir, exist_ok=True)
        csf = os.path.join(csdir, "00000000-0000-0000-0000-0000000000s1.jsonl")

        def asst(mid, ts, out_tokens):
            return {"type": "assistant", "timestamp": ts, "cwd": "X",
                    "message": {"id": mid, "model": "claude-opus-5-5",
                                "usage": {"output_tokens": out_tokens}}}

        start = sessions._iso_ms("2026-10-01T00:00:00.000Z")

        def write_cs(usd, unknown=False):
            recs = [asst("m0", "2026-09-30T23:00:00.000Z", 1_000_000),  # $20 前
                    asst("m1", "2026-10-01T01:00:00.000Z", 1_000_000),  # $20 中
                    {"type": "cost-state", "startTime": start,
                     "totalCostUSD": usd, "hasUnknownModelCost": unknown},
                    asst("m2", "2026-10-02T01:00:00.000Z", 1_000_000)]  # $20 後
            with open(csf, "w", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r) + "\n")
            return sessions.read_one(csf)

        try:
            check("ISO 時戳轉毫秒含小數",
                  sessions._iso_ms("2026-10-01T00:00:00.250Z") - start == 250
                  and sessions._iso_ms("壞") is None)
            ci = write_cs(30.0)
            off = ci.get("official") or {}
            check("cost-state 只涵蓋 startTime 之後、紀錄之前的回覆",
                  abs(off.get("covered", 0) - 20.0) < 1e-6 and
                  abs(ci["cost"] - 60.0) < 1e-6, off)
            check("官方數字較高時改用官方（前段＋官方＋後段）",
                  abs(sessions.with_official(ci) - 70.0) < 1e-6,
                  sessions.with_official(ci))
            # 母檔已含子代理：60 + 5（期間內）+ 7（之後的另一次執行）
            sub = {"cost": 5.0, "first_ms": start + 3600_000 + 1000}
            sub_late = {"cost": 7.0,
                        "first_ms": start + 86400_000 + 3600_000}
            got = sessions.with_official(dict(ci, cost=72.0), [sub, sub_late])
            check("該次執行期間的子代理算進官方涵蓋範圍",
                  abs(got - 77.0) < 1e-6, got)
            ci = write_cs(5.0)
            check("官方數字較低時保留估算（只往上修正）",
                  abs(sessions.with_official(ci) - 60.0) < 1e-6)
            ci = write_cs(99.0, unknown=True)
            check("官方標示有未知模型時不採用",
                  ci.get("official") is None and
                  abs(sessions.with_official(ci) - 60.0) < 1e-6)
        finally:
            os.remove(csf)
            os.rmdir(csdir)

        # 開新視窗前清掉子行程標記（含 2.1.3xx 新增的）
        old_env = dict(os.environ)
        try:
            os.environ.update({"CLAUDE_CODE_MESSAGING_SOCKET": "x",
                               "CLAUDE_CODE_MESSAGING_TOKEN": "x",
                               "CLAUDE_CODE_SESSION_ATTENDED": "1",
                               "AI_AGENT": "claude-code_2-1-316_agent"})
            env = launch.clean_env()
            check("子行程標記全部移除",
                  not any(k in env for k in (
                      "CLAUDE_CODE_MESSAGING_SOCKET",
                      "CLAUDE_CODE_MESSAGING_TOKEN",
                      "CLAUDE_CODE_SESSION_ATTENDED", "AI_AGENT")))
            os.environ["AI_AGENT"] = "my-own-agent"
            check("使用者自己的 AI_AGENT 保留",
                  launch.clean_env().get("AI_AGENT") == "my-own-agent")
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        cmd = launch.build_command({"claude": "claude"},
                                   {"session": "x"}, ["attach", "ab12cd34"])
        check("接上背景對話的指令", cmd[-1].endswith("attach ab12cd34"), cmd)
        calls = []
        real_run = launch._run_claude
        launch._run_claude = (lambda cfg, cwd, args, timeout=180:
                              calls.append((cwd, args)) or (True, ""))
        try:
            it = {"session": "s1", "cwd": "C:\\x", "name": "n",
                  "model": "haiku", "background": True}
            launch.start_background({"permission_mode": ""}, it)
            launch.respawn_background({})
        finally:
            launch._run_claude = real_run
        check("在背景開＝claude --bg --resume，帶上該條的模型",
              calls and calls[0] == ("C:\\x", ["--bg", "--resume", "s1",
                                                "--model", "haiku"]), calls)
        check("背景對話改用新版＝claude respawn --all",
              len(calls) == 2 and calls[1][1] == ["respawn", "--all"], calls)
        fa = launch.fork_args({"permission_mode": ""},
                              {"session": "s1", "cwd": "X", "new": True})
        check("分出一份＝續接原對話加 --fork-session",
              fa == ["--resume", "s1", "--fork-session"], fa)

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
        # 抽取邏輯：用合成的打包程式碼片段測 2.1.3xx 的延遲宣告與變數枚舉
        synth = os.path.join(sandbox_dir, "synth.bin")
        with open(synth, "wb") as fh:
            fh.write(
                b'aZ=["dark","light"],bZ=["auto",...aZ],cZ=["x","y"];'
                b'dup=["p"];dup=["q"];'
                b'o={oldStyle:Nr(["a","b"]).optional().describe("old"),'
                b'lazyEnum:()=>U(["l1","l2"]).optional().describe("lazy"),'
                b'refEnum:()=>U(cZ).optional().describe("ref"),'
                b'unionEnum:()=>Ue([U(bZ),o().startsWith("c:")]).optional()'
                b'.describe("union"),'
                b'dupEnum:()=>U(dup).optional().describe("dup"),'
                b'notEnum:()=>Ue([U(zz),o()]).optional().describe("no"),'
                b'plain:()=>o().optional().describe("plain")};')
        sk, se = claudeschema._scan(synth)
        check("抽取：舊式與延遲宣告的鍵都抓得到",
              all(k in sk for k in ("oldStyle", "lazyEnum", "refEnum",
                                    "unionEnum", "plain")), sorted(sk))
        check("抽取：字面枚舉",
              se.get("oldStyle") == ["a", "b"] and
              se.get("lazyEnum") == ["l1", "l2"], se)
        check("抽取：指向變數與展開的枚舉",
              se.get("refEnum") == ["x", "y"] and
              se.get("unionEnum") == ["auto", "dark", "light"], se)
        check("抽取：定義不唯一或查不到的變數不猜",
              "dupEnum" not in se and "notEnum" not in se, se)
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
        ok = wait_until(lambda: app.sessions, timeout=120, root=root)
        check("背景掃描完成", ok, "%d 條" % len(app.sessions))

        check("多選模式", str(app.tree.cget("selectmode")) == "extended")
        check("右鍵選單存在",
              hasattr(app, "tree_menu") and hasattr(app, "picked_menu"))

        # 交接改指：鏈上追到最新
        real_app_sessions = app.sessions
        app.sessions = [
            {"id": "a1", "cwd": "X", "mtime": 100.0, "ctime": 50.0,
             "handoff": False, "handoff_from": "", "cost": 0.0, "ctx": 0,
             "ctx_pct": 0.0, "custom_title": "同名", "ai_title": "",
             "first": ""},
            {"id": "h1", "cwd": "X", "mtime": 500.0, "ctime": 150.0,
             "handoff": True, "handoff_from": "a1", "cost": 0.0, "ctx": 0,
             "ctx_pct": 0.0, "custom_title": "同名", "ai_title": "",
             "first": ""},
            {"id": "h2", "cwd": "X", "mtime": 900.0, "ctime": 550.0,
             "handoff": True, "handoff_from": "h1", "cost": 0.0, "ctx": 0,
             "ctx_pct": 0.0, "custom_title": "同名", "ai_title": "",
             "first": ""},
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

        # 「立即啟動」：設成在背景開的不開視窗；雙擊開啟仍開視窗
        bg_started = []
        real_start_bg = launch.start_background
        launch.start_background = (lambda cfg, item:
                                   bg_started.append(item["name"])
                                   or (True, ""))
        real_items_bg = app.items
        try:
            two = free[:2]
            app.items = [dict(app.item_for(two[0]), name="前景甲"),
                         dict(app.item_for(two[1]), name="背景乙",
                              background=True)]
            app.running = set()
            opened.clear()
            app.open_many(list(app.items), confirm=False,
                          honor_background=True)
            ok = wait_until(lambda: len(opened) + len(bg_started) == 2,
                            timeout=10, root=root)
            check("立即啟動：背景項目在背景開、其餘開視窗",
                  ok and opened == ["前景甲"] and bg_started == ["背景乙"],
                  (opened, bg_started))
            app.running = set()
            app._just_launched = {}
            opened.clear()
            bg_started.clear()
            app.open_many([app.items[1]], confirm=False)
            ok = wait_until(lambda: opened, timeout=10, root=root)
            check("雙擊開啟背景項目仍開視窗",
                  ok and opened == ["背景乙"] and bg_started == [],
                  (opened, bg_started))
            app.running = {two[1].lower()}
            opened.clear()
            app.open_many(list(app.items[1:]), confirm=False,
                          honor_background=True)
            for _ in range(20):
                root.update()
                time.sleep(0.05)
            check("立即啟動：已在背景跑的不去接上",
                  opened == [] and bg_started == [], (opened, bg_started))
            # 開在一般視窗裡時也要標出這個設定；真的在背景跑時改標背景執行
            app.background = set()
            app.running_status = {two[1].lower(): "idle"}
            app.fill_picked()
            names = [app.picked.item(r, "text")
                     for r in app.picked.get_children()]
            check("啟動清單標出在背景開的項目",
                  i18n.tr("　（啟動時在背景開）") in names[1] and
                  i18n.tr("　（啟動時在背景開）") not in names[0], names)
            app.background = {two[1].lower()}
            app.fill_picked()
            names = [app.picked.item(r, "text")
                     for r in app.picked.get_children()]
            check("在背景跑時標背景執行",
                  i18n.tr("　（背景執行）") in names[1] and
                  i18n.tr("　（啟動時在背景開）") not in names[1], names)
        finally:
            launch.start_background = real_start_bg
            app.items = real_items_bg
            app.running = set()
            app.running_status = {}
            app.background = set()
            app._just_launched = {}
            app.fill_picked()
        check("右鍵選單有分出一份與桌面版",
              app.tree_menu.entrycget(app._tm_idx["fork"], "label")
              == i18n.tr("分出一份接著聊") and
              app.tree_menu.entrycget(app._tm_idx["desktop"], "label")
              == i18n.tr("在 Claude 桌面版開啟") and
              app.picked_menu.entrycget(app._pm_idx["background"], "label")
              == i18n.tr("啟動時在背景開（不開視窗）"))

        # 資料夾搬家：清單標出、一鍵改到新位置、啟動清單跟著改
        ui_base = tempfile.mkdtemp(prefix="wbui")
        ui_proj = os.path.join(sessions.PROJECTS, "zz-test-uireloc")
        real_sessions_r, real_items_r = app.sessions, app.items
        try:
            gone = os.path.join(ui_base, "舊的")
            now = os.path.join(ui_base, "新家", "舊的")
            os.makedirs(now)
            os.makedirs(ui_proj)
            uid = "00000000-0000-0000-0000-0000000000u1"
            uf = os.path.join(ui_proj, uid + ".jsonl")
            with open(uf, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "cwd": gone, "message":
                                     {"content": "zz 搬家測試"}},
                                    ensure_ascii=False) + "\n")
            uinfo = dict(sessions.read_one(uf), cost=0.0)
            app.sessions = [uinfo]
            app.items = [{"name": "搬家測試", "cwd": gone, "session": uid}]
            app.moved = {os.path.normpath(gone): [now]}
            app.fill_tree()
            heads = [app.tree.item(r, "text") for r in app.tree.get_children()]
            check("清單標出資料夾已搬到哪",
                  any(i18n.tr("　⚠ 已搬到 %s") % now in h for h in heads),
                  heads)
            rows = app._moved_rows()
            check("搬家清單列出舊位置、新位置與對話數",
                  len(rows) == 1 and rows[0]["new"] == now and
                  rows[0]["count"] == 1, rows)
            app.relocate_folders([(os.path.normpath(gone), now)],
                                 report=False)
            moved_u = sessions.find_file(uid)
            check("一鍵改到新位置：紀錄搬過去並記下新位置、啟動清單跟著改",
                  moved_u and sessions.read_one(moved_u)["cwd"] == now and
                  app.items[0]["cwd"] == now and
                  os.path.normpath(gone) not in app.moved,
                  (moved_u, app.items))
            wait_until(lambda: not app._scan_busy, timeout=120, root=root)

            # 掃描後自動跟過去：有把握的直接搬，開著的等關掉，失敗的不一直重試
            gone2 = os.path.join(ui_base, "舊的2")
            now2 = os.path.join(ui_base, "新家", "舊的2")
            os.makedirs(now2)
            uid2 = "00000000-0000-0000-0000-0000000000u2"
            uf2 = os.path.join(ui_proj, uid2 + ".jsonl")
            with open(uf2, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "cwd": gone2, "message":
                                     {"content": "zz 自動搬家測試"}},
                                    ensure_ascii=False) + "\n")
            u2 = dict(sessions.read_one(uf2), cost=0.0)
            app.sessions, app.items = [u2], []
            key2 = os.path.normpath(gone2)
            real_running = app.running
            app.running = set(real_running) | {uid2}
            app.moved = {key2: [now2]}
            waited = app._auto_relocate()
            check("開著的對話不自動搬",
                  not waited and sessions.read_one(uf2)["cwd"] == gone2,
                  waited)
            app.running = real_running
            app.moved = {key2: [now2]}
            did = app._auto_relocate()
            f2 = sessions.find_file(uid2)
            check("掃描後有把握的搬家自動跟過去",
                  did and f2 and sessions.read_one(f2)["cwd"] == now2 and
                  app._auto_note and "1" in app._auto_note[0],
                  (did, f2, app._auto_note))
            wait_until(lambda: not app._scan_busy, timeout=120, root=root)
            app.sessions, app.items = [dict(u2, cwd=gone2)], []
            bad = os.path.join(ui_base, "沒有這個")
            app.moved = {key2: [bad]}
            first = app._auto_relocate()
            app.moved = {key2: [bad]}
            again = app._auto_relocate()
            check("自動搬失敗的不會一直重試",
                  not first and not again and (key2, bad) in app._auto_tried,
                  (first, again))
            app._auto_tried.discard((key2, bad))
            app._auto_note = None
        finally:
            app.sessions, app.items = real_sessions_r, real_items_r
            app.moved = {}
            for gid in ("00000000-0000-0000-0000-0000000000u1",
                        "00000000-0000-0000-0000-0000000000u2"):
                f = sessions.find_file(gid)
                if f:
                    os.remove(f)
                    d = os.path.dirname(f)
                    if not os.listdir(d):
                        os.rmdir(d)
            shutil.rmtree(ui_proj, ignore_errors=True)
            shutil.rmtree(ui_base, ignore_errors=True)
            app.save()
            app.fill_tree()

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
