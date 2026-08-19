# -*- coding: utf-8 -*-
"""在這台電腦上安裝 Claude 工作台：產生圖示、在桌面建立兩個捷徑。

任何人拿到這個資料夾，用 Python 跑一次這支就能用：

    python install.py            安裝
    python install.py --uninstall 移除捷徑與開機自動啟動

不需要管理員權限，也不會動到這個資料夾以外的東西（除了桌面捷徑）。
"""
import os
import subprocess
import sys
import winreg

from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))
SHORTCUTS = [
    ("Claude 工作台", "launch.pyw", "launcher.ico",
     tr("開啟所有固定的 Claude Code 對話")),
    ("Claude 工作台 設定", "settings.pyw", "settings.ico",
     tr("挑選每次要自動開啟哪些 Claude Code 對話")),
]


def pythonw():
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def desktop_dir():
    """桌面實際在哪 —— 可能被 OneDrive 之類重導向，不能直接猜 ~/Desktop。"""
    try:
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            raw, kind = winreg.QueryValueEx(k, "Desktop")
        path = os.path.expandvars(raw) if kind == winreg.REG_EXPAND_SZ else raw
        if os.path.isdir(path):
            return path
    except OSError:
        pass
    fallback = os.path.join(os.path.expanduser("~"), "Desktop")
    return fallback if os.path.isdir(fallback) else os.path.expanduser("~")


def make_shortcut(path, target, args, workdir, icon, desc):
    """用 WScript.Shell 建立 .lnk（不需要額外套件）。"""
    ps = (
        "$w = New-Object -ComObject WScript.Shell;"
        "$s = $w.CreateShortcut($env:LNK_PATH);"
        "$s.TargetPath = $env:LNK_TARGET;"
        "$s.Arguments = $env:LNK_ARGS;"
        "$s.WorkingDirectory = $env:LNK_DIR;"
        "$s.Description = $env:LNK_DESC;"
        "if (Test-Path $env:LNK_ICON) { $s.IconLocation = $env:LNK_ICON + ',0' };"
        "$s.Save()"
    )
    env = dict(os.environ, LNK_PATH=path, LNK_TARGET=target, LNK_ARGS=args,
               LNK_DIR=workdir, LNK_ICON=icon, LNK_DESC=desc)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-Command", ps],
                       env=env, capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def ensure_icons():
    missing = [i for _, _, i, _ in SHORTCUTS
               if not os.path.exists(os.path.join(HERE, i))]
    if not missing:
        return True, tr("圖示已存在")
    try:
        import make_icons  # noqa: F401
        for maker, name in ((make_icons.launcher_icon, "launcher.ico"),
                            (make_icons.settings_icon, "settings.ico")):
            make_icons.save(maker(), name)
        return True, tr("已產生圖示")
    except Exception as e:
        return False, tr("產生圖示失敗（會用預設圖示）：%s") % e


def check_claude():
    sys.path.insert(0, HERE)
    import config
    p = config.find_claude()
    if p and (os.path.exists(p) or p == "claude"):
        return True, p
    return False, p


def install():
    print(tr("安裝位置：%s") % HERE)
    ok, msg = ensure_icons()
    print(tr("  圖示：%s") % msg)

    found, claude = check_claude()
    print(tr("  Claude Code：%s")
          % (claude if found else tr("找不到！請先安裝 Claude Code")))

    desk = desktop_dir()
    print(tr("  桌面：%s") % desk)
    py = pythonw()
    print(tr("  直譯器：%s") % py)

    for name, script, icon, desc in SHORTCUTS:
        lnk = os.path.join(desk, name + ".lnk")
        good, err = make_shortcut(
            lnk, py, '"%s"' % os.path.join(HERE, script), HERE,
            os.path.join(HERE, icon), desc)
        print("  %s %s" % ("[OK]" if good else "[!!]", lnk))
        if err:
            print("       %s" % err)

    print(tr("\n完成。點桌面的「Claude 工作台 設定」開始挑要固定開啟的對話。"))


def uninstall():
    desk = desktop_dir()
    for name, _, _, _ in SHORTCUTS:
        lnk = os.path.join(desk, name + ".lnk")
        if os.path.exists(lnk):
            os.remove(lnk)
            print(tr("  已刪除 %s") % lnk)
    sys.path.insert(0, HERE)
    import startup
    if startup.is_enabled():
        startup.set_enabled(False)
        print(tr("  已關閉開機自動啟動"))
    print(tr("\n捷徑已移除。程式檔案與 config.json 留著，直接刪掉這個資料夾就完全清乾淨。"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
