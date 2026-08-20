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
from i18n import tr  # noqa: E402
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


def alert(msg, title=None):
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
        messagebox.showwarning(title or tr("Claude 工作台"), msg)
        root.destroy()
    except Exception:
        print(msg)


CREATE_NO_WINDOW = 0x08000000
RESUME_ID_RE = re.compile(r"--resume\s+([0-9a-fA-F-]{36})")


SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
STILL_ACTIVE = 259


def _pid_alive(pid, proc_start=None):
    """行程是否仍在執行。登錄檔可能因為當機而殘留，逐筆驗證。

    proc_start 是登錄檔記下的行程建立時間（FILETIME）；PID 會被系統
    重用，建立時間也對得上才視為同一個行程。
    """
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not (k32.GetExitCodeProcess(h, ctypes.byref(code))
                and code.value == STILL_ACTIVE):
            return False
        try:
            want = int(proc_start)
        except (TypeError, ValueError):
            return True
        times = (ctypes.c_ulonglong * 4)()
        if not k32.GetProcessTimes(h, ctypes.byref(times, 0),
                                   ctypes.byref(times, 8),
                                   ctypes.byref(times, 16),
                                   ctypes.byref(times, 24)):
            return True
        return abs(times[0] - want) < 10 * 10 ** 7   # 容差 10 秒
    finally:
        k32.CloseHandle(h)


def _registry():
    """執行期登錄裡通過存活驗證的項目 [(對話 id 小寫, 紀錄)]。"""
    import glob
    import json
    out = []
    for f in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                o = json.load(fh)
        except Exception:
            continue
        sid, pid = o.get("sessionId"), o.get("pid")
        if sid and isinstance(pid, int) \
                and _pid_alive(pid, o.get("procStart")):
            out.append((sid.lower(), o))
    return out


def running_status():
    """目前開著的對話 {id(小寫): 狀態}。狀態為 busy／idle，未知時為 idle。

    來源是 Claude Code 的執行期登錄 ~/.claude/sessions/<pid>.json，
    涵蓋所有互動視窗，包括手動以 claude -r 從選單開啟的。
    登錄目錄不存在時退回行程指令列比對。查詢失敗回空字典。
    """
    if os.path.isdir(SESSIONS_DIR):
        return {sid: o.get("status") or "idle" for sid, o in _registry()}
    return {sid: "idle" for sid in _running_by_cmdline()}


def running_sessions():
    """目前開著的對話 id 集合（小寫）。"""
    return set(running_status())


def running_pids():
    """目前開著的對話 {id(小寫): 行程 pid}。"""
    if os.path.isdir(SESSIONS_DIR):
        return {sid: o["pid"] for sid, o in _registry()}
    return _running_by_cmdline()


def _running_by_cmdline():
    """以行程指令列比對的備援路徑：{對話 id(小寫): pid}。"""
    ps = (shutil.which("powershell")
          or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          r"System32\WindowsPowerShell\v1.0\powershell.exe"))
    cmd = [ps, "-NoProfile", "-NonInteractive", "-Command",
           'Get-CimInstance Win32_Process -Filter "Name=\'claude.exe\'" | '
           'ForEach-Object { "$($_.ProcessId)|$($_.CommandLine)" }']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return {}
    out = {}
    for line in (r.stdout or "").splitlines():
        pid, _, rest = line.partition("|")
        m = RESUME_ID_RE.search(rest)
        if m and pid.strip().isdigit():
            out[m.group(1).lower()] = int(pid)
    return out


# 承載 claude 的兩種頂層視窗類別：Windows Terminal 與傳統主控台
TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")


def _process_parents():
    """全機行程的 {pid: 父 pid}（Toolhelp 快照）。失敗回空字典。"""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    out = {}
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _window_candidates(pid, classes=TERMINAL_CLASSES):
    """可能承載該行程的可見終端機視窗 [(hwnd, 標題)]。

    視窗不屬於 claude 本身：Windows Terminal 是單行程多視窗，視窗掛在
    claude 的祖先名下；傳統主控台的視窗屬於 conhost，與 claude 是兄弟。
    因此以血緣圈出候選，無法唯一時由呼叫端用標題分辨。
    """
    import ctypes
    from ctypes import wintypes
    parents = _process_parents()
    chain = {pid}
    p, hops = parents.get(pid), 0
    while p and hops < 20 and p not in chain:
        chain.add(p)
        p = parents.get(p)
        hops += 1
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value not in classes:
            return True
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        w = wpid.value
        if not (w == pid or w in chain or parents.get(w) in chain):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def focus_session(sid, hints=()):
    """把已開啟對話的終端機視窗帶到前景。回傳是否有找到視窗。

    候選多於一個（Windows Terminal 的視窗都掛在同一個行程）時以標題分辨：
    claude 會把視窗標題設成「狀態符號＋空格＋對話標題」。分不出來就放棄，
    寧可不動作也不把別條對話的視窗抬上來。
    """
    pid = running_pids().get((sid or "").lower())
    if not pid:
        return False
    try:
        cands = _window_candidates(pid)
    except Exception:
        return False
    hwnd = None
    if len(cands) == 1:
        hwnd = cands[0][0]
    else:
        for hint in [h for h in hints if h]:
            for w, title in cands:
                if title == hint or title.endswith(" " + hint):
                    hwnd = w
                    break
            if hwnd:
                break
    if not hwnd:
        return False
    singleton.focus(hwnd)
    return True


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
        return None, tr("資料夾不存在：%s") % cwd
    sid = str(uuid.uuid4())
    ok, out = _run_claude(cfg, cwd, ["--session-id", sid, "-p",
                                     "/rename " + name])
    if not ok:
        return None, out or tr("claude 回報失敗")
    if not sessions.find_file(sid):
        return None, tr("指令跑完了，但找不到新對話的紀錄檔")
    return sid, None


def rename_session(cfg, item, name):
    """一併修改該對話在 Claude 中的名稱；工作台的視窗名稱另外儲存。"""
    ok, out = _run_claude(cfg, item["cwd"],
                          ["--resume", item["session"], "-p", "/rename " + name])
    return (True, None) if ok else (False, out or tr("claude 回報失敗"))


def claude_args(cfg, item):
    """要傳給 claude 的參數。permission_mode 空字串就沿用全域預設。

    item["new"] 為真時以 --session-id 開全新對話（互動視窗中建立），
    否則以 --resume 續接既有對話。model 與 effort 為每個項目各自的
    選擇，空字串沿用 Claude 自己的設定。
    """
    flag = "--session-id" if item.get("new") else "--resume"
    args = [flag, item["session"]]
    mode = item.get("permission_mode", cfg.get("permission_mode", ""))
    if mode:
        args += ["--permission-mode", mode]
    if item.get("model"):
        args += ["--model", item["model"]]
    if item.get("effort"):
        args += ["--effort", item["effort"]]
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
        alert(tr("啟動清單是空的\n\n請於「Claude 工作台 設定」中挑選對話。"))
        return

    # 已開啟的不重複開啟：同一條對話的兩個視窗會互相覆寫紀錄檔
    already_ids = running_sessions() if cfg.get("skip_running", True) else set()

    missing, already, unfocused, opened = [], [], [], 0
    delay = max(0, int(cfg.get("stagger_ms", 700))) / 1000.0
    for n, item in enumerate(items):
        if item["session"].lower() in already_ids:
            # 不重複開啟，改把既有視窗帶到前景；找不到視窗才留給結尾提示
            already.append(item["name"])
            p = session_file(item)
            hints = [item["name"]] + ([sessions.quick_title(p)] if p else [])
            if not focus_session(item["session"], hints):
                unfocused.append(item["name"])
            continue
        if not os.path.isdir(item["cwd"]):
            missing.append(tr("%s　資料夾不存在") % item["name"])
            continue
        if not session_file(item):
            missing.append(tr("%s　找不到對話紀錄") % item["name"])
            continue
        try:
            launch(cfg, item)
            opened += 1
        except Exception as e:
            missing.append(tr("%s　開啟失敗：%s") % (item["name"], e))
        if n < len(items) - 1:
            time.sleep(delay)

    if missing:
        note = ""
        if already:
            note = tr("\n\n已開啟而略過：%s") % tr("、").join(already)
        alert(tr("已開啟 %d 個；下列未開啟：\n\n%s%s")
              % (opened, "\n".join(missing), note))
    elif already and opened == 0 and unfocused:
        # 帶到前景本身就是回饋；只有找不到視窗的才提示，避免看起來沒反應
        alert(tr("清單中的對話均已開啟：\n\n%s") % tr("、").join(unfocused),
              title=tr("Claude 工作台"))


if __name__ == "__main__":
    main()
