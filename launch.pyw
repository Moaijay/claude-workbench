# -*- coding: utf-8 -*-
"""Claude 工作台 —— 點一下，把固定的那幾個對話各自開在獨立的終端機視窗。"""
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import applog  # noqa: E402
import config  # noqa: E402
import sessions  # noqa: E402
import singleton  # noqa: E402

# 從 Claude Code 內部啟動時會繼承這些標記，導致新視窗被視為子行程而不寫對話紀錄。
CHILD_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
)


def alert(msg, title="Claude 工作台"):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "launcher.ico")
        if os.path.exists(ico):
            try:
                root.iconbitmap(default=ico)
            except tk.TclError:
                pass
        messagebox.showwarning(title, msg)
        root.destroy()
    except Exception:
        print(msg)


CREATE_NO_WINDOW = 0x08000000
RESUME_ID_RE = re.compile(r"--resume\s+([0-9a-fA-F-]{36})")


SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
STILL_ACTIVE = 259


def _pid_alive(pid):
    """行程是否仍在執行。登錄檔可能因為當機而殘留，逐筆驗證。"""
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        k32.CloseHandle(h)


def running_status():
    """目前開著的對話 {id(小寫): 狀態}。狀態為 busy／idle，未知時為 idle。

    來源是 Claude Code 的執行期登錄 ~/.claude/sessions/<pid>.json，
    涵蓋所有互動視窗，包括手動以 claude -r 從選單開啟的。
    登錄目錄不存在時退回行程指令列比對。查詢失敗回空字典。
    """
    if os.path.isdir(SESSIONS_DIR):
        import glob
        import json
        out = {}
        for f in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    o = json.load(fh)
            except Exception:
                continue
            sid, pid = o.get("sessionId"), o.get("pid")
            if sid and isinstance(pid, int) and _pid_alive(pid):
                out[sid.lower()] = o.get("status") or "idle"
        return out
    return {sid: "idle" for sid in _running_by_cmdline()}


def running_sessions():
    """目前開著的對話 id 集合（小寫）。"""
    return set(running_status())


def _running_by_cmdline():
    ps = (shutil.which("powershell")
          or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          r"System32\WindowsPowerShell\v1.0\powershell.exe"))
    cmd = [ps, "-NoProfile", "-NonInteractive", "-Command",
           '(Get-CimInstance Win32_Process -Filter "Name=\'claude.exe\'")'
           '.CommandLine']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return set()
    return {m.group(1).lower() for m in RESUME_ID_RE.finditer(r.stdout or "")}


def clean_env():
    """新視窗的環境：移除會使其誤判為子行程的標記。"""
    env = dict(os.environ)
    for key in CHILD_MARKERS:
        env.pop(key, None)
    return env


def session_file(item):
    """這條對話的紀錄檔還在不在。"""
    root = sessions.PROJECTS
    for d in os.listdir(root):
        p = os.path.join(root, d, item["session"] + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def _run_claude(cfg, cwd, args, timeout=180):
    """在背景以非互動模式執行一次 claude，回傳 (成功, 輸出)。"""
    claude = cfg.get("claude") or "claude"
    try:
        r = subprocess.run([claude] + args, cwd=cwd, env=clean_env(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return False, str(e)
    blob = " ".join(((r.stdout or "") + " " + (r.stderr or "")).split())
    return r.returncode == 0, blob[:300]


def create_session(cfg, cwd, name):
    """在指定資料夾建立新對話並命名。回傳 (session_id, 錯誤訊息)。

    透過 /rename 命名，名稱不會被自動產生的標題取代，也不需直接改寫對話紀錄檔。
    """
    if not os.path.isdir(cwd):
        return None, "資料夾不存在：%s" % cwd
    sid = str(uuid.uuid4())
    ok, out = _run_claude(cfg, cwd, ["--session-id", sid, "-p",
                                     "/rename " + name])
    if not ok:
        return None, out or "claude 回報失敗"
    if not sessions.find_file(sid):
        return None, "指令跑完了，但找不到新對話的紀錄檔"
    return sid, None


def rename_session(cfg, item, name):
    """一併修改該對話在 Claude 中的名稱；工作台的視窗名稱另外儲存。"""
    ok, out = _run_claude(cfg, item["cwd"],
                          ["--resume", item["session"], "-p", "/rename " + name])
    return (True, None) if ok else (False, out or "claude 回報失敗")


def claude_args(cfg, item):
    """要傳給 claude 的參數。permission_mode 空字串就沿用全域預設。

    item["new"] 為真時以 --session-id 開全新對話（互動視窗中建立），
    否則以 --resume 續接既有對話。
    """
    flag = "--session-id" if item.get("new") else "--resume"
    args = [flag, item["session"]]
    mode = item.get("permission_mode", cfg.get("permission_mode", ""))
    if mode:
        args += ["--permission-mode", mode]
    return args


def build_command(cfg, item):
    """視窗中執行的指令。預設使用 PowerShell。"""
    claude = cfg.get("claude") or "claude"
    keep = cfg.get("keep_open", True)
    extra = " ".join(claude_args(cfg, item))

    if cfg.get("shell", "powershell") == "cmd":
        return ["cmd", "/k" if keep else "/c", '"%s" %s' % (claude, extra)]

    ps = (shutil.which("powershell")
          or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          r"System32\WindowsPowerShell\v1.0\powershell.exe"))
    args = [ps, "-NoLogo"]
    if keep:
        args.append("-NoExit")
    args += ["-Command", "& '%s' %s" % (claude, extra)]
    return args


def launch(cfg, item):
    wt = shutil.which("wt")
    cmd = build_command(cfg, item)
    if wt:
        args = [wt, "-w", "-1", "--title", item["name"], "-d", item["cwd"]] + cmd
    else:
        # 沒有 Windows Terminal 時退回傳統主控台視窗
        args = ["cmd", "/c", "start", item["name"], "/D", item["cwd"]] + cmd
    subprocess.Popen(args, env=clean_env(), close_fds=True)


def main():
    # 連續啟動時第二個行程直接結束：兩者都會先取得「已開啟」快照再開窗，
    # 否則會各自判定尚未開啟而重複開啟。
    if not singleton.acquire("ClaudeWorkbenchLauncher"):
        return
    applog.install(name="launcher")
    cfg = config.load()
    if config.LOAD_ERROR:
        alert(config.LOAD_ERROR)
        return
    items = [i for i in cfg.get("items", []) if i.get("session") and i.get("cwd")]
    if not items:
        alert("啟動清單是空的\n\n請於「Claude 工作台 設定」中挑選對話。")
        return

    # 已開啟的不重複開啟：同一條對話的兩個視窗會互相覆寫紀錄檔
    already_ids = running_sessions() if cfg.get("skip_running", True) else set()

    missing, already, opened = [], [], 0
    delay = max(0, int(cfg.get("stagger_ms", 700))) / 1000.0
    for n, item in enumerate(items):
        if item["session"].lower() in already_ids:
            already.append(item["name"])
            continue
        if not os.path.isdir(item["cwd"]):
            missing.append("%s　資料夾不存在" % item["name"])
            continue
        if not session_file(item):
            missing.append("%s　找不到對話紀錄" % item["name"])
            continue
        try:
            launch(cfg, item)
            opened += 1
        except Exception as e:
            missing.append("%s　開啟失敗：%s" % (item["name"], e))
        if n < len(items) - 1:
            time.sleep(delay)

    if missing:
        note = ""
        if already:
            note = "\n\n已開啟而略過：%s" % "、".join(already)
        alert("已開啟 %d 個；下列未開啟：\n\n%s%s"
              % (opened, "\n".join(missing), note))
    elif already and opened == 0:
        # 全部都已開啟時仍給出回饋，避免看起來沒有反應
        alert("清單中的對話均已開啟：\n\n%s" % "、".join(already),
              title="Claude 工作台")


if __name__ == "__main__":
    main()
