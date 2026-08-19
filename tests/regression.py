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
import config       # noqa: E402
import launch       # noqa: E402
import options      # noqa: E402
import sessions     # noqa: E402

fails = []
passed = [0]


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

        # 執行中偵測：官方執行期登錄
        import launch as launch_real
        if os.path.isdir(launch_real.SESSIONS_DIR):
            t0 = time.time()
            live = launch_real.running_sessions()
            dt = (time.time() - t0) * 1000
            check("執行中偵測走執行期登錄", isinstance(live, set), type(live))
            check("執行中偵測夠快（<100ms）", dt < 100, "%.1f ms" % dt)
            stale = os.path.join(launch_real.SESSIONS_DIR, "4999999.json")
            try:
                with open(stale, "w", encoding="utf-8") as fh:
                    json.dump({"pid": 4999999,
                               "sessionId": "dead0000-dead-dead-dead-deaddeaddead"},
                              fh)
                live2 = launch_real.running_sessions()
                check("殘留登錄（行程已死）會被排除",
                      "dead0000-dead-dead-dead-deaddeaddead" not in live2)
            finally:
                os.remove(stale)

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

        # 執行中狀態為字典
        st_map = launch.running_status() if hasattr(launch, "running_status")             else None
        check("running_status 回傳字典", isinstance(st_map, dict),
              type(st_map))
        if st_map:
            check("狀態值為 busy/idle",
                  all(v in ("busy", "idle") for v in st_map.values()),
                  set(st_map.values()))

        # 快取位置
        check("掃描快取在資料目錄",
              sessions.CACHE.startswith(appdata.data_dir()))
        check("安裝目錄沒有快取檔",
              not os.path.exists(os.path.join(HERE, "scan-cache.json")))

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
        free = [r for r in [c for p in app.tree.get_children()
                            for c in app.tree.get_children(p)]
                if r not in in_list][:3]
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

        # 狀態燈：busy 顯示為橘色標籤
        app.running_status = {r.lower(): "busy" for r in
                              [c for pnode in app.tree.get_children()
                               for c in app.tree.get_children(pnode)][:1]}
        app.running = set(app.running_status)
        app.fill_tree()
        root.update()
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

        # 開啟中的對話：只改本地，不同步
        app.running = {"s" * 36}
        app.running_status = {"s" * 36: "idle"}
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
        check("開啟中改名不同步 Claude 端",
              app.items[0]["name"] == "another" and pushed == [], pushed)
        app.running = set()
        app.running_status = {}

        # 左側：開啟中拒絕
        rows_now = [c for pnode in app.tree.get_children()
                    for c in app.tree.get_children(pnode)]
        if rows_now:
            target_row = rows_now[0]
            app.running = {target_row.lower()}
            app.running_status = {target_row.lower(): "idle"}
            app.tree.selection_set([target_row])
            app.rename_session_title()
            root.update()
            check("左側開啟中拒絕改名並提示 /rename",
                  "/rename" in app.status.cget("text"),
                  app.status.cget("text"))
            app.running = set()
            app.running_status = {}

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
