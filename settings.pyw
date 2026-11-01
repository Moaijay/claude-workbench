# -*- coding: utf-8 -*-
"""Claude 工作台 設定 —— 挑選每次要自動開啟的對話。"""
import ctypes
import os
import re
import sys
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import applog      # noqa: E402
import config      # noqa: E402
from i18n import tr  # noqa: E402
import launch      # noqa: E402
import options     # noqa: E402
import sessions    # noqa: E402
import singleton   # noqa: E402
import startup     # noqa: E402

WINDOW_TITLE = tr("Claude 工作台 — 設定")

# 解析 Claude 結束時印出的那一行：cd '資料夾' ; claude --resume <id>
QUOTE = "[\x27\x22]?"
RESUME_RE = re.compile(
    r"cd\s+" + QUOTE + r"(.+?)" + QUOTE +
    r"\s*[;&]+\s*\S*claude\S*\s+--resume\s+([0-9a-fA-F-]{36})")

UI_SIZE = 11          # 介面字級
FONT_CANDIDATES = ("Microsoft JhengHei UI", "Microsoft JhengHei", "Segoe UI")
UI_FAM = FONT_CANDIDATES[0]   # setup_theme() 會依系統實際有的字體改寫

RUN_MARK = "● "       # 執行中前綴

# 會被記住的介面狀態
DEFAULT_UI = {
    "geometry": "",
    "zoomed": False,
    "tree_name": 300,
    "tree_cost": 90,
    "tree_ctx": 80,
    "tree_when": 132,
    "picked_name": 190,
    "picked_cwd": 240,
    "recent_only": False,
    "show_cost": True,
    "show_context": True,
    "fulltext": False,
    "show_archived": False,
    "show_superseded": False,   # 顯示被壓縮交接取代的舊對話
    "sort_key": "mtime",      # mtime | cost
    "sort_desc": True,
}

POLL_RUNNING_MS = 3_000       # 執行中偵測間隔（讀執行期登錄，成本極低）
POLL_CHANGES_MS = 30_000      # 紀錄檔變動偵測間隔


class CellEditor:
    """清單列的就地重新命名。Enter 確認、Esc 取消、失焦視同確認。"""

    def __init__(self, tree, item, initial, on_commit, on_done=None):
        self.tree = tree
        self.on_commit = on_commit
        self.on_done = on_done
        self.done = False
        tree.see(item)
        tree.update_idletasks()
        bbox = tree.bbox(item, "#0")
        if not bbox:
            self.done = True
            if on_done:
                on_done()
            return
        x, y, w, h = bbox
        pad = 22 if tree.parent(item) else 4    # 子列縮排
        self.var = tk.StringVar(value=initial)
        self.entry = ttk.Entry(tree, textvariable=self.var)
        self.entry.place(x=x + pad, y=y, width=max(120, w - pad - 4), height=h)
        self.entry.select_range(0, "end")
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda _: self._finish(True))
        self.entry.bind("<Escape>", lambda _: self._finish(False))
        self.entry.bind("<FocusOut>", lambda _: self._finish(True))

    def _finish(self, commit):
        if self.done:
            return "break"
        self.done = True
        value = self.var.get().strip()
        self.entry.destroy()
        if commit and value:
            self.on_commit(value)
        if self.on_done:
            self.on_done()
        return "break"      # Esc／Enter 不再往上傳（避免順帶清掉選取）


def ctx_text(info):
    """脈絡使用率的顯示文字。"""
    pct = info.get("ctx_pct") or 0
    if not pct:
        return ""
    if pct < 0.01:
        return "<1%"
    return "%d%%" % round(pct * 100)


def _effort_levels():
    """effort 的可選值：CLI 文件明列的固定清單，補上 schema 掃出的新值。"""
    base = ["low", "medium", "high", "xhigh", "max"]
    try:
        import claudeschema
        extra = (claudeschema.load().get("enums") or {}).get(
            "effortLevel") or []
    except Exception:
        extra = []
    return base + [v for v in extra if v not in base]


def is_home(path):
    """Claude Code 不儲存家目錄的信任狀態，於此開啟的對話每次都會詢問。"""
    try:
        a = os.path.normcase(os.path.realpath(path))
        b = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
        return a == b
    except OSError:
        return False


def make_dpi_aware():
    """優先使用 per-monitor v2，避免換螢幕或改縮放後模糊。"""
    for call in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            if call():
                return
        except Exception:
            continue


def screen_dpi():
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        return dpi or 96
    except Exception:
        return 96


def setup_theme(root):
    """字型與間距。採用系統介面字體。"""
    global UI_FAM
    dpi = screen_dpi()
    root.tk.call("tk", "scaling", dpi / 72.0)   # Tk 的 scaling 是「每點幾像素」

    families = set(tkfont.families(root))
    fam = next((f for f in FONT_CANDIDATES if f in families), "TkDefaultFont")
    UI_FAM = fam

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkTooltipFont", "TkIconFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=fam, size=UI_SIZE)
        except tk.TclError:
            pass

    base = (fam, UI_SIZE)
    row = tkfont.Font(family=fam, size=UI_SIZE).metrics("linespace") + 14

    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure(".", font=base)
    style.configure("TButton", font=base, padding=(12, 6))
    style.configure("TLabel", font=base)
    style.configure("TCheckbutton", font=base)
    style.configure("TEntry", padding=4)
    style.configure("TLabelframe.Label", font=(fam, UI_SIZE, "bold"))
    style.configure("Treeview", font=base, rowheight=row)
    style.configure("Treeview.Heading", font=(fam, UI_SIZE, "bold"),
                    padding=(6, 6))
    return dpi / 96.0


class NewSessionDialog(tk.Toplevel):
    """新增一條對話：選資料夾、取名字，當場建立並命名。"""

    def __init__(self, master, cfg, initial_cwd=""):
        super().__init__(master)
        self.cfg = cfg
        self.result = None
        self._busy = False
        self.title(tr("新增對話"))
        self.transient(master)
        self.resizable(False, False)
        set_icon(self)

        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        ttk.Label(body, text=tr("資料夾")).grid(row=0, column=0, sticky="w",
                                          pady=(0, 6))
        self.cwd = tk.StringVar(value=initial_cwd)
        ttk.Entry(body, textvariable=self.cwd, width=46).grid(
            row=0, column=1, sticky="ew", padx=8, pady=(0, 6))
        ttk.Button(body, text=tr("瀏覽…"), command=self.browse).grid(
            row=0, column=2, pady=(0, 6))

        ttk.Label(body, text=tr("名稱")).grid(row=1, column=0, sticky="w")
        self.name = tk.StringVar()
        e = ttk.Entry(body, textvariable=self.name, width=46)
        e.grid(row=1, column=1, sticky="ew", padx=8)

        ttk.Label(body, foreground="#555555", wraplength=430, justify="left",
                  text=tr("留空：開啟新視窗，名稱由對話內容自動產生\n"
                          "填寫：以 /rename 固定名稱並加入啟動清單（需數秒）")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # 模型／effort／ultracode：這個對話自己的選擇，經 --model、
        # --effort、--settings 傳給 claude；「沿用預設」就完全不帶旗標
        self.default_label = tr("（沿用預設）")
        picks = ttk.Frame(body)
        picks.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(picks, text=tr("模型")).pack(side="left")
        self.model = tk.StringVar(value=self.default_label)
        ttk.Combobox(picks, textvariable=self.model, width=16,
                     values=[self.default_label, "fable", "opus",
                             "sonnet"]).pack(side="left", padx=(8, 20))
        ttk.Label(picks, text="Effort").pack(side="left")
        self.effort = tk.StringVar(value=self.default_label)
        self.effort_box = ttk.Combobox(picks, textvariable=self.effort,
                                       width=12, state="readonly",
                                       values=[self.default_label]
                                       + _effort_levels())
        self.effort_box.pack(side="left", padx=(8, 20))
        self.ultracode = tk.BooleanVar(value=False)
        ttk.Checkbutton(picks, text="Ultracode", variable=self.ultracode,
                        command=self._ultracode_toggled).pack(side="left")

        self.hint = ttk.Label(body, foreground="#8a6a10", wraplength=430,
                              justify="left", text="")
        self.hint.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.cwd.trace_add("write", lambda *_: self.update_hint())
        self.update_hint()

        self.status = ttk.Label(body, text="", foreground="#555555")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        btns = ttk.Frame(body)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.ok_btn = ttk.Button(btns, text=tr("建立"), command=self.ok)
        self.ok_btn.pack(side="right")
        self.cancel_btn = ttk.Button(btns, text=tr("取消"), command=self.destroy)
        self.cancel_btn.pack(side="right", padx=8)

        body.columnconfigure(1, weight=1)
        e.focus_set()
        self.bind("<Return>", lambda _: self.ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()

    def update_hint(self):
        self.hint.configure(
            text=(tr("ⓘ 家目錄：每次開啟皆詢問資料夾信任")
                  if is_home(self.cwd.get().strip()) else ""))

    def _ultracode_toggled(self):
        # ultracode 已含 xhigh effort，勾選時鎖住 effort 避免互相矛盾
        if self.ultracode.get():
            self.effort.set(self.default_label)
            self.effort_box.configure(state="disabled")
        else:
            self.effort_box.configure(state="readonly")

    def browse(self):
        d = filedialog.askdirectory(parent=self, title=tr("選擇資料夾"))
        if d:
            self.cwd.set(os.path.normpath(d))

    def ok(self):
        if self._busy:
            return
        cwd = self.cwd.get().strip()
        name = self.name.get().strip()
        if not cwd:
            messagebox.showwarning(tr("新增對話"), tr("請選擇資料夾"), parent=self)
            return
        if not os.path.isdir(cwd):
            messagebox.showwarning(tr("新增對話"), tr("資料夾不存在\n\n%s") % cwd,
                                   parent=self)
            return
        if not name:
            # 未命名：直接開新視窗，名稱由 Claude 依對話內容自動產生
            import uuid
            self.result = {"name": os.path.basename(cwd) or cwd, "cwd": cwd,
                           "session": str(uuid.uuid4()), "new": True,
                           "model": self._pick(self.model),
                           "effort": self._pick(self.effort),
                           "ultracode": bool(self.ultracode.get())}
            self.destroy()
            return
        self._busy = True
        self.ok_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        self.status.configure(text=tr("建立中…"))

        box = {}

        def worker():
            box["r"] = launch.create_session(self.cfg, cwd, name)

        threading.Thread(target=worker, daemon=True).start()
        self._watch(box, cwd, name)

    def _watch(self, box, cwd, name):
        if "r" not in box:
            self.after(100, lambda: self._watch(box, cwd, name))
            return
        sid, err = box["r"]
        self._done(sid, err, cwd, name)

    def _done(self, sid, err, cwd, name):
        self._busy = False
        if err:
            self.ok_btn.configure(state="normal")
            self.cancel_btn.configure(state="normal")
            self.status.configure(text="")
            messagebox.showerror(tr("新增對話"), tr("建立失敗\n\n%s") % err,
                                 parent=self)
            return
        self.result = {"name": name, "cwd": cwd, "session": sid,
                       "model": self._pick(self.model),
                       "effort": self._pick(self.effort),
                       "ultracode": bool(self.ultracode.get())}
        self.destroy()

    def _pick(self, var):
        v = var.get().strip()
        return "" if not v or v == self.default_label else v


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(row=0, column=0, sticky="nsew")
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)

        self.cfg = config.load()
        self.config_error = config.LOAD_ERROR
        self.items = list(self.cfg.get("items", []))
        self.sessions = []
        self.dirty = False
        self.archived = set(self.cfg.get("archived", []))
        self.hidden = 0            # 因封存而未顯示的筆數
        self.hits = None           # 全文搜尋結果 {session_id: 次數}
        self.running = set()       # 執行中的對話 id（小寫）
        self.running_status = {}   # id -> busy／idle
        self.background = set()    # 其中在背景執行（claude --bg）的 id
        self.background_info = {}  # id -> launch.background_sessions() 的內容
        self.claude_version = ""   # 安裝的 Claude Code 版本
        self.schema_ready = False
        self._filter_job = None
        self._grep_gen = 0
        self._scan_busy = False
        self._poll_busy = False
        self._launching = False
        self._last_sig = None
        self._q = queue.Queue()
        self._just_launched = {}   # sid -> 啟動時間；防止輪詢立刻清掉
        self._rename_guard = set() # 改名同步中的對話，暫停開啟
        # 開啟中改名的待同步佇列；對話關閉後由輪詢自動補發 /rename
        self.pending_renames = dict(self.cfg.get("pending_renames") or {})
        self._editing = False      # 就地編輯中，暫停畫面刷新
        self._refresh_pending = False
        self.ui = dict(DEFAULT_UI)
        self.ui.update(self.cfg.get("ui", {}))

        self._build()
        self._build_menu(master)
        self._build_context_menus()
        self._bind_deselect()
        self.after(50, self._pump)
        self.after(60, self.rescan)
        self.after(800, self._poll_running)
        self.after(POLL_CHANGES_MS, self._poll_changes)
        self._preload_schema()

    # ---------------- 背景執行 ----------------
    def run_bg(self, fn, done, *args):
        """在背景執行 fn(*args)，完成後於主執行緒呼叫 done(result, error)。"""
        def worker():
            try:
                r, err = fn(*args), None
            except Exception as e:
                r, err = None, e
                try:
                    applog.get().exception("bg task failed")
                except Exception:
                    pass
            self._post(lambda: done(r, err))
        threading.Thread(target=worker, daemon=True).start()

    def _post(self, fn):
        """從背景執行緒排回主執行緒。

        跨執行緒呼叫 after() 不可靠（主執行緒不在事件迴圈時會拋
        RuntimeError），因此改走佇列，由主執行緒定期取出執行。
        """
        self._q.put(fn)

    def _pump(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception:
                    try:
                        applog.get().exception("ui callback failed")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        finally:
            try:
                self.after(50, self._pump)
            except Exception:
                pass

    def _preload_schema(self):
        import claudeschema
        self.run_bg(claudeschema.load,
                    lambda r, e: setattr(self, "schema_ready", True))

    # ---------------- 選單列 ----------------
    def _build_menu(self, root):
        bar = tk.Menu(root)

        # 只放介面上沒有對應按鈕、或屬於程式層級的命令。
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label=tr("新增對話…"), accelerator="Ctrl+N",
                      command=self.new_session)
        m.add_command(label=tr("從 resume 指令加入…"), command=self.paste_command)
        m.add_separator()
        m.add_command(label=tr("儲存"), accelerator="Ctrl+S", command=self.save)
        m.add_command(label=tr("結束"), command=self.on_close)
        bar.add_cascade(label=tr("檔案"), menu=m)

        m = tk.Menu(bar, tearoff=0)
        self.v_cost = tk.BooleanVar(value=bool(self.ui["show_cost"]))
        m.add_checkbutton(label=tr("顯示金額"), variable=self.v_cost,
                          command=self.apply_columns)
        self.v_ctx = tk.BooleanVar(value=bool(self.ui["show_context"]))
        m.add_checkbutton(label=tr("顯示脈絡使用率"), variable=self.v_ctx,
                          command=self.apply_columns)
        self.v_full = tk.BooleanVar(value=bool(self.ui["fulltext"]))
        m.add_checkbutton(label=tr("搜尋對話內容"), variable=self.v_full,
                          command=self.on_fulltext_toggle)
        m.add_checkbutton(label=tr("只看最近 30 天"), variable=self.recent_only,
                          command=self.fill_tree)
        m.add_checkbutton(label=tr("顯示已封存"), variable=self.v_arch,
                          command=self.on_archived_toggle)
        m.add_checkbutton(label=tr("顯示已交接"), variable=self.v_handed,
                          command=self.on_superseded_toggle)
        bar.add_cascade(label=tr("檢視"), menu=m)

        m = tk.Menu(bar, tearoff=0)
        m.add_command(label=tr("Claude Code 設定…"), accelerator="Ctrl+,",
                      command=self.edit_claude_settings)
        m.add_command(label=tr("工作台選項…"), command=self.edit_workbench_options)
        m.add_separator()
        m.add_command(label=tr("背景對話改用新版 Claude"),
                      command=self.respawn_background)
        m.add_separator()
        self.v_startup = tk.BooleanVar(value=startup.is_enabled())
        m.add_checkbutton(label=tr("開機時自動開啟"),
                          variable=self.v_startup, command=self.toggle_startup)
        m.add_separator()
        m.add_command(label=tr("程式所在資料夾"), command=self.open_folder)
        m.add_command(label=tr("錯誤記錄檔"), command=self.open_log)
        bar.add_cascade(label=tr("工具"), menu=m)

        m = tk.Menu(bar, tearoff=0)
        m.add_command(label=tr("檢查更新"), command=self.check_updates)
        m.add_command(label=tr("關於"), command=self.about)
        bar.add_cascade(label=tr("說明"), menu=m)

        root.configure(menu=bar)
        root.bind("<Control-comma>", lambda _: self.edit_claude_settings())
        root.bind("<Control-n>", lambda _: self.new_session())
        root.bind("<Control-s>", lambda _: self.save())
        root.bind("<F5>", lambda _: self.rescan())

    # ---------------- 右鍵選單 ----------------
    def _build_context_menus(self):
        # 選單項目以名字記索引，插入新項目時 entryconfigure 不會指錯位置
        def add(menu, idx, name, **kw):
            menu.add_command(**kw)
            idx[name] = menu.index("end")

        self.tree_menu = tk.Menu(self, tearoff=0)
        self._tm_idx = {}
        add(self.tree_menu, self._tm_idx, "open",
            label=tr("開啟"), command=self.open_from_tree)
        add(self.tree_menu, self._tm_idx, "preview",
            label=tr("預覽"), accelerator="Space", command=self.show_preview)
        add(self.tree_menu, self._tm_idx, "add",
            label=tr("加入啟動清單"), command=self.add_selected)
        add(self.tree_menu, self._tm_idx, "archive",
            label=tr("封存"), accelerator="Del", command=self.toggle_archive)
        add(self.tree_menu, self._tm_idx, "rename",
            label=tr("重新命名"), accelerator="F2",
            command=self.rename_session_title)
        self.tree_menu.add_separator()
        add(self.tree_menu, self._tm_idx, "fork",
            label=tr("分出一份接著聊"), command=self.fork_selected)
        add(self.tree_menu, self._tm_idx, "desktop",
            label=tr("在 Claude 桌面版開啟"), command=self.open_in_desktop)
        self.tree_menu.add_separator()
        add(self.tree_menu, self._tm_idx, "move",
            label=tr("移至其他資料夾…"), command=self.move_to_folder)
        add(self.tree_menu, self._tm_idx, "copy",
            label=tr("複製 resume 指令"), command=self.copy_resume)
        self.tree.bind("<Button-3>", self._tree_context)

        self.picked_menu = tk.Menu(self, tearoff=0)
        self._pm_idx = {}
        add(self.picked_menu, self._pm_idx, "open",
            label=tr("開啟"), command=self.open_from_picked)
        add(self.picked_menu, self._pm_idx, "preview",
            label=tr("預覽"), accelerator="Space",
            command=self.show_preview_picked)
        add(self.picked_menu, self._pm_idx, "rename",
            label=tr("重新命名"), accelerator="F2", command=self.rename_item)
        self.v_item_bg = tk.BooleanVar(value=False)
        self.picked_menu.add_checkbutton(
            label=tr("啟動時在背景開（不開視窗）"), variable=self.v_item_bg,
            command=self.toggle_item_background)
        self._pm_idx["background"] = self.picked_menu.index("end")
        self.picked_menu.add_separator()
        add(self.picked_menu, self._pm_idx, "up",
            label=tr("上移"), command=self.move_up)
        add(self.picked_menu, self._pm_idx, "down",
            label=tr("下移"), command=self.move_down)
        self.picked_menu.add_separator()
        add(self.picked_menu, self._pm_idx, "remove",
            label=tr("移除"), command=self.remove_item)
        self.picked.bind("<Button-3>", self._picked_context)

    def _context_select(self, tree, event):
        row = tree.identify_row(event.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
            tree.focus(row)
        return bool(tree.selection())

    def _tree_context(self, event):
        if not self._context_select(self.tree, event):
            return
        picked = self.selected_sessions()
        label = (tr("取消封存") if picked and
                 all(s in self.archived for s in picked) else tr("封存"))
        self.tree_menu.entryconfigure(self._tm_idx["archive"], label=label)
        for key in ("preview", "rename", "move", "copy", "fork"):
            self.tree_menu.entryconfigure(
                self._tm_idx[key],
                state="normal" if len(picked) == 1 else "disabled")
        # 開著的對話交給桌面版會變成兩個行程寫同一個紀錄檔
        self.tree_menu.entryconfigure(
            self._tm_idx["desktop"],
            state="normal" if len(picked) == 1 and
            picked[0].lower() not in self.running else "disabled")
        self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _picked_context(self, event):
        if not self._context_select(self.picked, event):
            return
        single = len(self._sels()) == 1
        for key in ("preview", "rename", "up", "down", "background"):
            self.picked_menu.entryconfigure(
                self._pm_idx[key], state="normal" if single else "disabled")
        if single:
            self.v_item_bg.set(bool(self.items[self._sels()[0]]
                                    .get("background")))
        self.picked_menu.tk_popup(event.x_root, event.y_root)

    def toggle_item_background(self):
        """啟動清單的這條：按「立即啟動」或開機自動開啟時在背景開，不開視窗。"""
        sels = self._sels()
        if len(sels) != 1:
            return
        item = self.items[sels[0]]
        if self.v_item_bg.get():
            item["background"] = True
        else:
            item.pop("background", None)
        self._touch()
        self.save()
        self.status.configure(
            text=(tr("「%s」啟動時改在背景開；要看時雙擊會開視窗接上")
                  if item.get("background") else
                  tr("「%s」啟動時改回開視窗")) % item["name"])

    def fork_selected(self):
        """從選取的對話分出一份新的，在新視窗接著聊；原本那條不動。"""
        picked = self.selected_sessions()
        if len(picked) != 1:
            return
        item = self.item_for(picked[0])
        if not item:
            return
        info = sessions.by_id(self.sessions).get(picked[0]) or {}
        title = sessions.label(info) if info else item["name"]
        item = dict(item, name=tr("%s（分支）") % title)
        try:
            launch.fork(config.load(), item)
            self.status.configure(text=tr("已分出一份：%s") % title)
        except Exception as e:
            messagebox.showwarning(tr("分出一份接著聊"),
                                   tr("開啟失敗\n\n%s") % e)

    def open_in_desktop(self):
        """把選取的對話交給 Claude 桌面版續接。"""
        picked = self.selected_sessions()
        if len(picked) != 1:
            return
        sid = picked[0]
        if sid.lower() in self.running:
            messagebox.showwarning(tr("在 Claude 桌面版開啟"),
                                   tr("該對話開啟中，關閉後再交給桌面版"))
            return
        item = self.item_for(sid)
        if not item:
            return
        if not os.path.isdir(item["cwd"]):
            messagebox.showwarning(tr("在 Claude 桌面版開啟"),
                                   tr("資料夾不存在\n\n%s") % item["cwd"])
            return
        self.status.configure(text=tr("交給 Claude 桌面版中…"))

        def done(result, err):
            ok, msg = result if result else (False, str(err))
            if ok:
                self._just_launched[sid.lower()] = time.time()
                self.status.configure(
                    text=tr("已在 Claude 桌面版開啟：%s") % item["name"])
            else:
                self.status.configure(text=tr("交給桌面版失敗"))
                messagebox.showwarning(tr("在 Claude 桌面版開啟"),
                                       msg or tr("claude 回報失敗"))

        self.run_bg(launch.open_in_desktop, done, config.load(), item)

    def respawn_background(self):
        """讓所有背景對話改用目前安裝的 Claude Code 版本。"""
        if not self.background:
            messagebox.showinfo(tr("背景對話改用新版 Claude"),
                                tr("目前沒有在背景執行的對話。"))
            return
        if not messagebox.askyesno(
                tr("背景對話改用新版 Claude"),
                tr("重新啟動 %d 條背景對話，讓它們改用目前安裝的 Claude Code？"
                   "\n\n對話內容不會遺失；正在進行中的那一輪會中斷。")
                % len(self.background)):
            return
        self.status.configure(text=tr("重新啟動背景對話中…"))

        def done(result, err):
            ok, out = result if result else (False, str(err))
            self.status.configure(
                text=tr("背景對話已改用新版") if ok else tr("重新啟動失敗"))
            if not ok:
                messagebox.showwarning(tr("背景對話改用新版 Claude"),
                                       out or tr("claude 回報失敗"))
            self.refresh_running()

        self.run_bg(launch.respawn_background, done, config.load())

    def move_to_folder(self):
        picked = self.selected_sessions()
        if len(picked) != 1:
            return
        sid = picked[0]
        if sid.lower() in self.running:
            messagebox.showwarning(tr("移至其他資料夾"),
                                   tr("該對話開啟中，關閉後再搬移"))
            return
        item = self.item_for(sid)
        dest = filedialog.askdirectory(parent=self, title=tr("選擇目標資料夾"))
        if not dest:
            return
        dest = os.path.normpath(dest)
        ok, err = sessions.move_session(sid, dest)
        if not ok:
            messagebox.showwarning(tr("移至其他資料夾"), err)
            return
        changed = False
        for it in self.items:
            if it["session"] == sid:
                it["cwd"] = dest
                changed = True
        if changed:
            self._touch()
            self.save()
        self.status.configure(text=tr("已移至 %s") % dest)
        self.rescan()

    def copy_resume(self):
        picked = self.selected_sessions()
        if len(picked) != 1:
            return
        item = self.item_for(picked[0])
        if not item:
            return
        cmd = "cd '%s' ; claude --resume %s" % (item["cwd"], item["session"])
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self.status.configure(text=tr("已複製 resume 指令"))

    # ---------------- 預覽 ----------------
    def show_preview(self, sid=None):
        """看選取對話檔尾的最後幾則往來，不必開視窗。"""
        if sid is None:
            picked = self.selected_sessions()
            if len(picked) != 1:
                return
            sid = picked[0]
        info = sessions.by_id(self.sessions).get(sid) or {}
        path = info.get("path") or sessions.find_file(sid)
        if not path or not os.path.exists(path):
            self.status.configure(text=tr("找不到對話紀錄：%s") % sid[:8])
            return

        def done(msgs, err):
            if err is not None or msgs is None:
                self.status.configure(text=tr("預覽失敗，詳見錯誤記錄檔"))
                return
            self._show_preview_window(sid, info, msgs)

        self.run_bg(sessions.tail_messages, done, path)

    def show_preview_picked(self):
        sels = self._sels()
        if len(sels) == 1:
            self.show_preview(self.items[sels[0]]["session"])

    def _show_preview_window(self, sid, info, msgs):
        w = getattr(self, "_preview", None)
        if not (w and w.winfo_exists()):
            scale = screen_dpi() / 96.0
            w = self._preview = tk.Toplevel(self.winfo_toplevel())
            set_icon(w)
            w.geometry("%dx%d" % (int(560 * scale), int(540 * scale)))
            w.bind("<Escape>", lambda _: w.destroy())
        for c in w.winfo_children():
            c.destroy()
        w.title(tr("預覽 — %s") % (sessions.label(info) if info else sid[:8]))

        head = ttk.Frame(w, padding=(14, 10, 14, 0))
        head.pack(fill="x")
        if info.get("cwd"):
            ttk.Label(head, text=info["cwd"],
                      foreground="#555555").pack(anchor="w")
        bits = [b for b in (sessions.money(info.get("cost", 0)),
                            sessions.when(info["mtime"])
                            if info.get("mtime") else "") if b]
        if bits:
            ttk.Label(head, text=tr("　·　").join(bits),
                      foreground="#555555").pack(anchor="w")
        bg = self.background_info.get(sid.lower())
        if bg:
            line = tr("背景執行中") if bg.get("status") == "busy" \
                else tr("在背景待命")
            if bg.get("detail"):
                line += tr("：%s") % bg["detail"]
            ttk.Label(head, text=line, foreground="#8a4b00",
                      wraplength=int(520 * screen_dpi() / 96.0),
                      justify="left").pack(anchor="w")

        body = ttk.Frame(w, padding=(14, 8, 14, 12))
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, wrap="word", relief="flat", padx=10, pady=8,
                      font=(UI_FAM, UI_SIZE), background="#fafafa")
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.tag_configure("me", font=(UI_FAM, UI_SIZE, "bold"),
                          foreground="#0b5394")
        txt.tag_configure("claude", font=(UI_FAM, UI_SIZE, "bold"),
                          foreground="#8a4b00")
        txt.tag_configure("dim", foreground="#8c8c8c")
        if not msgs:
            txt.insert("end", tr("（檔尾沒有可顯示的訊息）"), "dim")
        for m in msgs:
            role_me = m["role"] == "user"
            txt.insert("end", tr("我") if role_me else "Claude",
                       "me" if role_me else "claude")
            ts = sessions.when_iso(m.get("ts", ""))
            if ts:
                txt.insert("end", "　" + ts, "dim")
            txt.insert("end", "\n" + m["text"] + "\n\n")
        txt.configure(state="disabled")
        txt.see("end")
        w.deiconify()
        w.lift()
        w.focus_set()

    # ---------------- 介面 ----------------
    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(2, weight=2)
        self.rowconfigure(1, weight=1)
        self.recent_only = tk.BooleanVar(value=bool(self.ui["recent_only"]))
        self.v_arch = tk.BooleanVar(value=bool(self.ui["show_archived"]))
        self.v_handed = tk.BooleanVar(
            value=bool(self.ui["show_superseded"]))

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(bar, text=tr("搜尋")).pack(side="left")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self.schedule_filter())
        ttk.Entry(bar, textvariable=self.q, width=26).pack(side="left",
                                                           padx=(8, 16))
        ttk.Button(bar, text=tr("新增對話"),
                   command=self.new_session).pack(side="left")
        ttk.Button(bar, text=tr("重新掃描"),
                   command=self.rescan).pack(side="left", padx=10)
        self.status = ttk.Label(bar, text="", foreground="#555555")
        self.status.pack(side="right")

        # 左：全機對話。標題列直接放顯示開關，還原封存不必翻選單
        head = ttk.Frame(self)
        ttk.Label(head, text=tr("電腦上所有對話")).pack(side="left")
        ttk.Checkbutton(head, text=tr("顯示已封存"), variable=self.v_arch,
                        command=self.on_archived_toggle).pack(
            side="left", padx=(14, 0))
        ttk.Checkbutton(head, text=tr("顯示已交接"), variable=self.v_handed,
                        command=self.on_superseded_toggle).pack(
            side="left", padx=(8, 0))
        left = ttk.LabelFrame(self, labelwidget=head, padding=10)
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("cost", "ctx", "when"),
                                 show="tree headings", selectmode="extended")
        self.tree.heading("#0", text=tr("對話"), anchor="w")
        self.tree.heading("cost", text=tr("金額"), anchor="e",
                          command=lambda: self.set_sort("cost"))
        self.tree.heading("ctx", text=tr("脈絡"), anchor="e",
                          command=lambda: self.set_sort("ctx"))
        self.tree.heading("when", text=tr("最後使用"), anchor="center",
                          command=lambda: self.set_sort("mtime"))
        self.tree.column("#0", width=self.ui["tree_name"], minwidth=180,
                         stretch=True)
        self.tree.column("cost", width=self.ui["tree_cost"], minwidth=70,
                         stretch=False, anchor="e")
        self.tree.column("ctx", width=self.ui["tree_ctx"], minwidth=60,
                         stretch=False, anchor="e")
        self.tree.column("when", width=self.ui["tree_when"], minwidth=110,
                         stretch=False, anchor="center")
        self.tree.tag_configure("folder", font=(UI_FAM, UI_SIZE, "bold"))
        self.tree.tag_configure("archived", foreground="#8c8c8c")
        self.tree.tag_configure("superseded", foreground="#8c8c8c")
        self.tree.tag_configure("running", foreground="#0b6a0b")
        self.tree.tag_configure("busy", foreground="#c25400")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda _: self.open_from_tree())
        self.tree.bind("<Return>", lambda _: self.open_from_tree())
        self.tree.bind("<Control-a>", self._select_all_tree)
        self.tree.bind("<F2>", lambda _: self.rename_session_title())
        self.tree.bind("<space>", lambda _: self.show_preview() or "break")
        self.tree.bind("<Delete>", lambda _: self.toggle_archive())
        self.tree.bind("<Configure>", self.fit_columns)

        # 中：加入與封存
        mid = ttk.Frame(self)
        mid.grid(row=1, column=1, sticky="ns", padx=12)
        holder = ttk.Frame(mid)
        holder.pack(expand=True)
        ttk.Button(holder, text=tr("加入  →"), command=self.add_selected,
                   width=9).pack()
        self.arch_btn = ttk.Button(holder, text=tr("封存"),
                                   command=self.toggle_archive, width=9)
        self.arch_btn.pack(pady=(10, 0))
        self.tree.bind("<<TreeviewSelect>>",
                       lambda _: self.update_archive_button(), add="+")

        # 右：啟動清單
        right = ttk.LabelFrame(self, text=tr("啟動清單（由上到下依序開啟）"),
                               padding=10)
        right.grid(row=1, column=2, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.picked = ttk.Treeview(right, columns=("cwd",),
                                   show="tree headings", selectmode="extended")
        self.picked.heading("#0", text=tr("視窗名稱"), anchor="w")
        self.picked.heading("cwd", text=tr("資料夾"), anchor="w")
        self.picked.column("#0", width=self.ui["picked_name"], minwidth=110,
                           stretch=False)
        self.picked.column("cwd", width=self.ui["picked_cwd"], minwidth=140,
                           stretch=True)
        self.picked.tag_configure("stale", foreground="#b00020")
        self.picked.tag_configure("note", foreground="#8a6a10")
        self.picked.tag_configure("running", foreground="#0b6a0b")
        self.picked.tag_configure("busy", foreground="#c25400")
        self.picked.grid(row=0, column=0, sticky="nsew")
        self.picked.bind("<Double-1>", lambda _: self.open_from_picked())
        self.picked.bind("<Return>", lambda _: self.open_from_picked())
        self.picked.bind("<Delete>", lambda _: self.remove_item())
        self.picked.bind("<Control-a>", self._select_all_picked)
        self.picked.bind("<F2>", lambda _: self.rename_item())
        self.picked.bind("<space>",
                         lambda _: self.show_preview_picked() or "break")

        tools = ttk.Frame(right)
        tools.grid(row=1, column=0, sticky="w", pady=(10, 0))
        for txt, fn, w in [("▲", self.move_up, 3), ("▼", self.move_down, 3),
                           (tr("重新命名"), self.rename_item, 9),
                           (tr("移除"), self.remove_item, 6)]:
            ttk.Button(tools, text=txt, command=fn,
                       width=w).pack(side="left", padx=(0, 6))

        # 底部
        ttk.Separator(self, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        bot = ttk.Frame(self)
        bot.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.total = ttk.Label(bot, text="", foreground="#555555")
        self.total.pack(side="left")
        ttk.Button(bot, text=tr("立即啟動"),
                   command=self.launch_now).pack(side="right")
        ttk.Button(bot, text=tr("儲存"), command=self.save).pack(side="right",
                                                            padx=10)
        self._update_sort_marks()

    def _select_all_tree(self, _event=None):
        rows = [c for p in self.tree.get_children()
                for c in self.tree.get_children(p)]
        self.tree.selection_set(rows)
        return "break"

    def _select_all_picked(self, _event=None):
        self.picked.selection_set(self.picked.get_children())
        return "break"

    def apply_columns(self):
        cols = []
        if self.v_cost.get():
            cols.append("cost")
        if self.v_ctx.get():
            cols.append("ctx")
        cols.append("when")
        self.tree.configure(displaycolumns=tuple(cols))
        self.ui["show_cost"] = bool(self.v_cost.get())
        self.ui["show_context"] = bool(self.v_ctx.get())
        self.fit_columns()

    def fit_columns(self, event=None):
        """調整「對話」欄寬度，保留固定欄所需空間避免被截斷。"""
        avail = self.tree.winfo_width()
        if avail < 80:
            return
        fixed = int(self.tree.column("when", "width"))
        if self.v_cost.get():
            fixed += int(self.tree.column("cost", "width"))
        if self.v_ctx.get():
            fixed += int(self.tree.column("ctx", "width"))
        target = max(180, avail - fixed - 6)
        if abs(int(self.tree.column("#0", "width")) - target) > 2:
            self.tree.column("#0", width=target)

    # ---------------- 排序 ----------------
    def set_sort(self, key):
        if self.ui["sort_key"] == key:
            self.ui["sort_desc"] = not self.ui["sort_desc"]
        else:
            self.ui["sort_key"] = key
            self.ui["sort_desc"] = True
        self._update_sort_marks()
        self.fill_tree()

    def _update_sort_marks(self):
        arrow = " ▼" if self.ui["sort_desc"] else " ▲"
        key = self.ui["sort_key"]
        self.tree.heading("cost", text=tr("金額") +
                          (arrow if key == "cost" else ""))
        self.tree.heading("ctx", text=tr("脈絡") +
                          (arrow if key == "ctx" else ""))
        self.tree.heading("when", text=tr("最後使用") +
                          (arrow if key == "mtime" else ""))

    # ---------------- 取消選取 ----------------
    def _bind_deselect(self):
        """點擊清單空白列或視窗空白處時清除選取狀態。"""
        for tree in (self.tree, self.picked):
            tree.bind("<Button-1>", self._click_in_tree, add="+")
        top = self.winfo_toplevel()
        top.bind("<Button-1>", self._click_anywhere, add="+")
        top.bind("<Escape>", lambda _: self.clear_selection())

    def _click_in_tree(self, event):
        tree = event.widget
        if tree.identify_row(event.y) == "":
            tree.selection_remove(*tree.selection())
            tree.focus("")

    def _click_anywhere(self, event):
        # 只在點到背景（框架、視窗本體）時清除；按鈕與輸入框不觸發
        if isinstance(event.widget,
                      (ttk.Frame, ttk.LabelFrame, tk.Frame, tk.LabelFrame,
                       tk.Toplevel, tk.Tk)):
            self.clear_selection()

    def clear_selection(self):
        for tree in (self.tree, self.picked):
            tree.selection_remove(*tree.selection())
            tree.focus("")

    # ---------------- 記住介面狀態 ----------------
    def collect_ui(self):
        top = self.winfo_toplevel()
        ui = dict(self.ui)
        ui["tree_name"] = int(self.tree.column("#0", "width"))
        ui["tree_cost"] = int(self.tree.column("cost", "width"))
        ui["tree_ctx"] = int(self.tree.column("ctx", "width"))
        ui["tree_when"] = int(self.tree.column("when", "width"))
        ui["picked_name"] = int(self.picked.column("#0", "width"))
        ui["picked_cwd"] = int(self.picked.column("cwd", "width"))
        ui["recent_only"] = bool(self.recent_only.get())
        ui["show_cost"] = bool(self.v_cost.get())
        ui["show_context"] = bool(self.v_ctx.get())
        ui["fulltext"] = bool(self.v_full.get())
        ui["show_archived"] = bool(self.v_arch.get())
        ui["show_superseded"] = bool(self.v_handed.get())
        ui["zoomed"] = top.state() == "zoomed"
        if not ui["zoomed"]:
            ui["geometry"] = top.winfo_geometry()
        return ui

    def on_close(self):
        cfg = config.load()          # 重讀後再寫入
        cfg["ui"] = self.collect_ui()
        if self.dirty:
            ans = messagebox.askyesnocancel(
                tr("Claude 工作台"), tr("啟動清單有未儲存的變更，是否儲存？"))
            if ans is None:
                return
            if ans:
                cfg["items"] = self.items
                cfg["archived"] = sorted(self.archived)
        config.save(cfg)
        self.winfo_toplevel().destroy()

    # ---------------- 資料 ----------------
    def rescan(self):
        if self._scan_busy:
            return
        self._scan_busy = True
        self.status.configure(text=tr("掃描中…"))
        self.refresh_running()

        def done(result, err):
            self._scan_busy = False
            if err or result is None:
                self.status.configure(text=tr("掃描失敗，詳見錯誤記錄檔"))
                return
            self.sessions = result
            self._auto_repoint()
            self.apply_columns()
            self.refresh_views()
            self.update_status()

        self.run_bg(sessions.scan, done)

    def refresh_views(self):
        """重畫兩個清單，保留選取與捲動位置。就地編輯中延後執行。"""
        if self._editing:
            self._refresh_pending = True
            return
        sel_tree = self.tree.selection()
        sel_picked = self.picked.selection()
        y = self.tree.yview()
        self.fill_tree()
        self.fill_picked()
        keep = [i for i in sel_tree if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        keep = [i for i in sel_picked if self.picked.exists(i)]
        if keep:
            self.picked.selection_set(keep)
        try:
            self.tree.yview_moveto(y[0])
        except Exception:
            pass

    def update_status(self):
        total = sum(s["cost"] for s in self.sessions)
        bits = [tr("共 %d 條") % len(self.sessions)]
        n = len([s for s in self.sessions if s["id"] in self.archived])
        if n:
            bits.append(tr("已封存 %d") % n)
        if self.running:
            live = len([s for s in self.sessions
                        if s["id"].lower() in self.running])
            if live:
                bits.append(tr("執行中 %d") % live)
        old = launch.outdated_background(self.background_info,
                                         self.claude_version)
        if old:
            bits.append(tr("%d 條背景對話仍是舊版（工具 → 背景對話改用新版）")
                        % len(old))
        self.status.configure(text=tr("　·　").join(bits))
        text = tr("累計 %s（API 牌價估算）") % sessions.money(total)
        unknown = sorted({m for s in self.sessions
                          for m in s.get("unpriced", [])})
        if unknown:
            # 出現尚無費率資料的模型時明確標示，而非套用舊費率
            text += tr("　·　⚠ 無費率資料：%s") % tr("、").join(unknown[:3])
        self.total.configure(text=text)

    # ---------------- 背景輪詢 ----------------
    def refresh_running(self):
        """做一次執行中偵測。輪詢與「重新掃描」共用。"""
        if self._poll_busy:
            return
        self._poll_busy = True

        def done(result, err):
            self._poll_busy = False
            if err is not None or result is None:
                return
            result, info, version = result
            background = set(info)
            self.background_info = info
            if version:
                self.claude_version = version
            # 剛啟動的行程尚未寫入執行期登錄前保護 15 秒；
            # 一旦登錄出現即改由偵測結果作準
            now = time.time()
            self._just_launched = {k: v for k, v
                                   in self._just_launched.items()
                                   if now - v < 15 and k not in result}
            merged = dict(result)
            for sid in self._just_launched:
                merged.setdefault(sid, "idle")
            if merged != self.running_status                     or background != self.background:
                self.running_status = merged
                self.running = set(merged)
                self.background = background
                self.refresh_views()
                self.update_status()
            self._flush_pending_renames(set(merged))

        def poll():
            # 背景執行的另外記一份：清單上加註、預覽顯示進度、比對版本
            bg = launch.background_sessions()
            version = launch.claude_version(config.load()) if bg else ""
            return launch.running_status(), bg, version

        self.run_bg(poll, done)

    def _poll_running(self):
        self.refresh_running()
        self.after(POLL_RUNNING_MS, self._poll_running)

    def _poll_changes(self):
        def done(sig, err):
            if err is None:
                if self._last_sig is not None and sig != self._last_sig:
                    self.rescan()
                self._last_sig = sig

        self.run_bg(sessions.signature, done)
        self.after(POLL_CHANGES_MS, self._poll_changes)

    # ---------------- 搜尋 ----------------
    def schedule_filter(self):
        if self._filter_job:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(350, self.run_filter)

    def run_filter(self):
        self._filter_job = None
        q = self.q.get().strip()
        if self.v_full.get() and len(q) >= 2:
            self.status.configure(text=tr("搜尋中…"))
            self._grep_gen += 1
            gen = self._grep_gen

            def done(hits, err):
                if gen != self._grep_gen:      # 過期的結果直接丟棄
                    return
                if err or hits is None:
                    self.status.configure(text=tr("搜尋失敗，詳見錯誤記錄檔"))
                    return
                self.hits = hits
                self.fill_tree()
                msg = tr("命中 %d 條") % len(hits)
                if self.hidden:
                    msg += tr("　·　另有 %d 條已封存符合") % self.hidden
                self.status.configure(text=msg)

            self.run_bg(sessions.grep, done, q)
            return
        self.hits = None
        if not self.v_full.get():
            self.update_status()
        self.fill_tree()

    def on_fulltext_toggle(self):
        self.ui["fulltext"] = bool(self.v_full.get())
        self.hits = None
        self.run_filter()

    # ---------------- 清單 ----------------
    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        q = self.q.get().strip().lower()
        cutoff = time.time() - 30 * 86400 if self.recent_only.get() else 0
        show_arch = bool(self.v_arch.get())
        key = self.ui["sort_key"]
        desc = bool(self.ui["sort_desc"])
        self.hidden = 0
        groups = {}
        # 被壓縮交接取代的對話（以摘要明指的前身 id 精確配對）
        superseded = set(sessions.handoff_map(self.sessions))
        show_handed = bool(self.v_handed.get())
        for s in self.sessions:
            if s["mtime"] < cutoff:
                continue
            # 被交接的舊對話預設隱藏；全文搜尋命中時仍顯示——
            # 歷史內容只存在舊檔裡，不能讓搜尋漏掉
            if s["id"] in superseded and not show_handed \
                    and self.hits is None:
                continue
            # 排隊中的改名先顯示新名稱；同步完成後掃描結果就是同一個名字
            text = self.pending_renames.get(s["id"]) or sessions.label(s)
            if s["id"] in superseded:
                text += tr("　（已交接）")
            if self.hits is not None:
                if s["id"] not in self.hits:
                    continue
                text = tr("%s   〔命中 %d〕") % (text, self.hits[s["id"]])
            elif q and q not in text.lower() and q not in s["cwd"].lower():
                continue
            if s["id"] in self.archived:
                if not show_arch:
                    self.hidden += 1
                    continue
                text += tr("　（已封存）")
            if s["id"].lower() in self.running:
                text = RUN_MARK + text
                if s["id"].lower() in self.background:
                    text += tr("　（背景執行）")
            groups.setdefault(s["cwd"] or tr("(不明)"), []).append((s, text))

        def group_rank(cwd):
            vals = [x[0][key] for x in groups[cwd]]
            return max(vals) if desc else min(vals)

        order = sorted(groups, key=group_rank, reverse=desc)
        for cwd in order:
            rows = sorted(groups[cwd], key=lambda x: x[0][key], reverse=desc)
            subtotal = sum(x[0]["cost"] for x in rows)
            self.tree.insert("", "end", iid="F:" + cwd, text=cwd, open=True,
                             values=(sessions.money(subtotal), "", ""),
                             tags=("folder",))
            for s, text in rows:
                tags = []
                status = self.running_status.get(s["id"].lower())
                if s["id"] in self.archived:
                    tags.append("archived")
                elif status == "busy":
                    tags.append("busy")
                elif status:
                    tags.append("running")
                elif s["id"] in superseded:
                    tags.append("superseded")
                self.tree.insert("F:" + cwd, "end", iid=s["id"],
                                 text="   " + text,
                                 values=(sessions.money(s["cost"]),
                                         ctx_text(s),
                                         sessions.when(s["mtime"])),
                                 tags=tuple(tags))
        self.update_archive_button()

    def fill_picked(self):
        self.picked.delete(*self.picked.get_children())
        index = sessions.by_id(self.sessions)
        ho_map = sessions.handoff_map(self.sessions)
        for n, item in enumerate(self.items):
            s = index.get(item["session"])
            tags = ()
            name = item["name"]
            status = self.running_status.get(item["session"].lower())
            in_bg = item["session"].lower() in self.background
            if status:
                tags = ("busy",) if status == "busy" else ("running",)
                name = RUN_MARK + name
                if in_bg:
                    name += tr("　（背景執行）")
            # 設成在背景開、但此刻不是在背景跑（沒開，或開在一般視窗）時標出設定
            if item.get("background") and not in_bg:
                name += tr("　（啟動時在背景開）")
            if s is None and not sessions.find_file(item["session"]):
                tags, name = ("stale",), item["name"] + tr("  ⚠ 找不到")
            elif s is not None and s["id"] in ho_map:
                tags, name = ("stale",), item["name"] + tr("  ⚠ 有更新的接續")
            elif not tags and is_home(item["cwd"]):
                tags, name = ("note",), name + tr("  ⓘ 每次會問信任")
            self.picked.insert("", "end", iid=str(n), text=name,
                               values=(item["cwd"],), tags=tags)

    # ---------------- 動作 ----------------
    def new_session(self):
        cfg = config.load()
        dlg = NewSessionDialog(self.winfo_toplevel(), cfg,
                               config.new_session_cwd(cfg))
        self.wait_window(dlg)
        if dlg.result:
            self._new_session_done(dlg.result, cfg)

    def _new_session_done(self, result, cfg):
        """建立完成後：命名的加入啟動清單；兩種都直接開視窗。"""
        if not result.get("new"):
            self.items.append(result)
            self._touch(select_last=True)
        try:
            launch.launch(cfg, result)
            sid = result["session"].lower()
            self._just_launched[sid] = time.time()
            self.running.add(sid)
            self.status.configure(text=tr("已開啟新對話"))
        except Exception as e:
            messagebox.showwarning(tr("新增對話"), tr("開啟失敗\n\n%s") % e)
        if not result.get("new"):
            self.rescan()

    # ---------------- 直接開啟 ----------------
    def open_selected(self):
        """由選單呼叫：以右側清單的選取為優先。"""
        if self._sels():
            self.open_from_picked()
        else:
            self.open_from_tree()

    def item_for(self, sid):
        """組出開啟用的項目；已在啟動清單中的沿用其視窗名稱。"""
        for item in self.items:
            if item["session"] == sid:
                return item
        s = sessions.by_id(self.sessions).get(sid)
        if not s:
            return None
        return {"name": sessions.label(s)[:20], "cwd": s["cwd"], "session": sid}

    def _handoff_target(self, sid):
        """這條對話被壓縮交接過時，回傳鏈上最新的那條；否則回 None。"""
        ho_map = sessions.handoff_map(self.sessions)
        ho = ho_map.get(sid)
        if not ho:
            return None
        seen = {sid, ho["id"]}
        while True:
            nxt = ho_map.get(ho["id"])
            if not nxt or nxt["id"] in seen:
                return ho
            seen.add(nxt["id"])
            ho = nxt

    def _auto_repoint(self):
        """啟動清單自動跟著交接鏈走到最新，不逐次詢問。

        壓縮交接會開新的對話檔，舊檔只是歷史；清單項目指著舊 id 沒有任何
        使用情境，直接改指。排隊中的改名也一併跟過去（要改的是活著的那條）。
        """
        changed = []
        for it in self.items:
            ho = self._handoff_target(it.get("session"))
            if not ho:
                continue
            if it["session"] in self.pending_renames:
                self.pending_renames[ho["id"]] = \
                    self.pending_renames.pop(it["session"])
            it["session"] = ho["id"]
            changed.append(it["name"])
        if changed:
            cfg = config.load()
            cfg["items"] = self.items
            cfg["pending_renames"] = self.pending_renames
            config.save(cfg)
            self.status.configure(
                text=tr("啟動清單已改指到接續：%s") % tr("、").join(changed))
        return changed

    def open_many(self, targets, confirm=True, honor_background=False):
        """驗證與確認在主執行緒；實際開窗與間隔在背景執行緒。

        honor_background 為真時（「立即啟動」），設成「啟動時在背景開」的
        項目改在背景續接、不開視窗，已在跑的也不去接上。
        """
        targets = [t for t in targets if t]
        if not targets:
            return
        if self._launching:
            self.status.configure(text=tr("開啟進行中，稍候"))
            return
        if confirm and len(targets) > 1 and not messagebox.askyesno(
                tr("開啟"), tr("開啟 %d 條對話？") % len(targets)):
            return
        cfg = config.load()
        quiet = len(targets) > 1
        todo, skipped, focus_q = [], [], []
        for t in targets:
            if not os.path.isdir(t["cwd"]):
                skipped.append(tr("%s　資料夾不存在") % t["name"])
                if not quiet:
                    messagebox.showwarning(tr("Claude 工作台"),
                                           tr("資料夾不存在\n\n%s") % t["cwd"])
            elif not sessions.find_file(t["session"]):
                skipped.append(tr("%s　找不到對話紀錄") % t["name"])
                if not quiet:
                    messagebox.showwarning(tr("Claude 工作台"),
                                           tr("找不到對話紀錄：%s") % t["name"])
            elif t["session"].lower() in self._rename_guard:
                skipped.append(tr("%s　名稱同步中") % t["name"])
                if not quiet:
                    self.status.configure(text=tr("名稱同步中，稍候再開"))
            elif cfg.get("skip_running", True) and \
                    t["session"].lower() in self.running:
                if honor_background and t.get("background"):
                    continue        # 本來就要在背景跑，已在跑就好
                # 不重複開啟，改把既有視窗帶到前景
                focus_q.append(t)
            else:
                todo.append(t)
        if not todo and not focus_q:
            if quiet and skipped:
                messagebox.showinfo(tr("Claude 工作台"),
                                    tr("未開啟：\n\n%s") % "\n".join(skipped))
            return

        self._launching = True
        if todo:
            self.status.configure(text=tr("開啟中…"))

        def worker():
            opened = fronted = 0
            index = sessions.by_id(self.sessions)
            for t in focus_q:
                info = index.get(t["session"]) or {}
                ok = False
                try:
                    ok = launch.bring_to_front(
                        cfg, t,
                        [t["name"], sessions.label(info) if info else ""])
                except Exception:
                    pass
                if ok:
                    fronted += 1
                    if not quiet:
                        self._post(lambda nm=t["name"]: self.status.configure(
                            text=tr("已帶到前景：%s") % nm))
                else:
                    skipped.append(tr("%s　已開啟") % t["name"])
                    where = ""
                    try:
                        where = launch.running_entrypoints().get(
                            t["session"].lower(), "")
                    except Exception:
                        pass
                    if not quiet and where == "claude-desktop":
                        self._post(lambda nm=t["name"]: messagebox.showinfo(
                            tr("Claude 工作台"),
                            tr("「%s」正在 Claude 桌面版裡開著。") % nm))
                    elif not quiet:
                        # 只在狀態列講會被忽略；用訊息框明說
                        self._post(lambda nm=t["name"]: messagebox.showinfo(
                            tr("Claude 工作台"),
                            tr("「%s」已在某個視窗中開啟，但找不到那個視窗；"
                               "請於工作列尋找。") % nm))
            for n, item in enumerate(todo):
                try:
                    if honor_background and item.get("background"):
                        ok, out = launch.start_background(cfg, item)
                        if not ok:
                            raise RuntimeError(out)
                    else:
                        launch.launch(cfg, item)
                    opened += 1
                    sid = item["session"].lower()
                    self.running.add(sid)
                    self._just_launched[sid] = time.time()
                    self._post(lambda nm=item["name"]:
                               self.status.configure(text=tr("已開啟：%s") % nm))
                except Exception:
                    try:
                        applog.get().exception("launch failed: %s",
                                               item["name"])
                    except Exception:
                        pass
                    self._post(lambda nm=item["name"]: self.status.configure(
                        text=tr("開啟失敗：%s（詳見錯誤記錄檔）") % nm))
                if n < len(todo) - 1:
                    time.sleep(cfg.get("stagger_ms", 700) / 1000.0)

            def finish():
                self._launching = False
                if quiet:
                    bits = [tr("已開啟 %d／%d") % (opened, len(targets))]
                    if fronted:
                        bits.append(tr("帶到前景 %d") % fronted)
                    if skipped:
                        bits.append(tr("略過 %d") % len(skipped))
                    self.status.configure(text=tr("　·　").join(bits))
                    if not todo and skipped and not fronted:
                        messagebox.showinfo(tr("Claude 工作台"),
                                            tr("未開啟：\n\n%s")
                                            % "\n".join(skipped))
                self.refresh_views()
                self.update_status()
            self._post(finish)

        threading.Thread(target=worker, daemon=True).start()

    def open_from_tree(self):
        """左側清單：雙擊或 Enter 開啟選取的對話；資料夾列展開為其下的對話。"""
        self.open_many([self.item_for(sid)
                        for sid in self.selected_sessions()])

    def open_from_picked(self):
        self.open_many([self.items[n] for n in self._sels()])

    # ---------------- 封存 ----------------
    def selected_sessions(self):
        """左側選取的對話 id；資料夾列展開成其下的對話，順序不重複。"""
        out, seen = [], set()
        for iid in self.tree.selection():
            kids = (self.tree.get_children(iid)
                    if self.tree.parent(iid) == "" else [iid])
            for k in kids:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        return out

    def update_archive_button(self):
        picked = self.selected_sessions()
        if not picked:
            self.arch_btn.configure(text=tr("封存"), state="disabled")
        elif all(sid in self.archived for sid in picked):
            self.arch_btn.configure(text=tr("取消封存"), state="normal")
        else:
            self.arch_btn.configure(text=tr("封存"), state="normal")

    def toggle_archive(self):
        picked = self.selected_sessions()
        if not picked:
            return
        if all(sid in self.archived for sid in picked):
            self.archived.difference_update(picked)
            note = tr("已取消封存 %d 條") % len(picked)
        else:
            fresh = [sid for sid in picked if sid not in self.archived]
            if len(fresh) > 1 and not messagebox.askyesno(
                    tr("封存"), tr("封存 %d 條對話？") % len(fresh)):
                return
            in_list = [i for i in self.items if i["session"] in fresh]
            if in_list:
                names = tr("、").join(i["name"] for i in in_list[:5])
                if not messagebox.askyesno(
                        tr("封存"), tr("%d 條在啟動清單中：%s\n\n一併從啟動清單移除？")
                                % (len(in_list), names)):
                    return
                keep = {i["session"] for i in in_list}
                self.items = [i for i in self.items if i["session"] not in keep]
                self._touch()
            self.archived.update(fresh)
            note = tr("已封存 %d 條") % len(fresh)
        self.save_archived()
        self.fill_tree()
        self.update_status()
        self.status.configure(text=note)

    def save_archived(self):
        """只寫封存名單，不動對話紀錄。"""
        cfg = config.load()
        cfg["archived"] = sorted(self.archived)
        cfg["items"] = self.items
        cfg["ui"] = self.collect_ui()
        config.save(cfg)
        self.cfg = cfg
        self.dirty = False

    def on_archived_toggle(self):
        self.ui["show_archived"] = bool(self.v_arch.get())
        self.fill_tree()

    def on_superseded_toggle(self):
        self.ui["show_superseded"] = bool(self.v_handed.get())
        self.fill_tree()

    def add_selected(self):
        picked = self.selected_sessions()
        if not picked:
            return
        have = {i["session"] for i in self.items}
        index = sessions.by_id(self.sessions)
        added = 0
        for sid in picked:
            if sid in have or sid not in index:
                continue
            s = index[sid]
            self.items.append({"name": sessions.label(s)[:20],
                               "cwd": s["cwd"], "session": sid})
            added += 1
        if not added:
            messagebox.showinfo(tr("Claude 工作台"), tr("已在啟動清單中"))
            return
        self._touch(select_last=True)
        skipped = len(picked) - added
        self.status.configure(
            text=tr("已加入 %d 條%s")
                 % (added, (tr("　·　略過 %d 條") % skipped) if skipped else ""))

    def _touch(self, select_last=False):
        """啟動清單有變動；新加入的項目自動選取。"""
        self.dirty = True
        self.fill_picked()
        if select_last and self.items:
            last = str(len(self.items) - 1)
            self.picked.focus(last)
            self.picked.selection_set(last)
            self.picked.see(last)

    def _sels(self):
        """右側選取的索引，由小到大。"""
        return sorted(int(i) for i in self.picked.selection() if i.isdigit())

    def _sel(self):
        picked = self._sels()
        return picked[0] if picked else None

    def remove_item(self):
        picked = self._sels()
        if not picked:
            return
        if len(picked) > 1 and not messagebox.askyesno(
                tr("移除"), tr("從啟動清單移除 %d 條？") % len(picked)):
            return
        for n in reversed(picked):
            del self.items[n]
        self._touch()
        self.status.configure(text=tr("已移除 %d 條") % len(picked))

    def _push_rename(self, item, name, after=None):
        """把名稱同步到 Claude（背景）。同步期間暫停開啟該對話。"""
        sid = item["session"].lower()
        self._rename_guard.add(sid)
        self.status.configure(text=tr("同步名稱至 Claude 中…"))

        def done(result, err):
            self._rename_guard.discard(sid)
            ok, msg = result if result else (False, err)
            if not ok:
                self.status.configure(
                    text=tr("Claude 端改名失敗：%s") % str(msg)[:60])
            else:
                self.status.configure(text=tr("已改名：%s") % name)
                if after:
                    after()

        self.run_bg(launch.rename_session, done, config.load(), item, name)

    def _save_pending(self):
        cfg = config.load()
        cfg["pending_renames"] = self.pending_renames
        config.save(cfg)

    def _queue_rename(self, sid, name):
        """開啟中的對話不能同步（兩個行程會寫同一個紀錄檔）：
        先記進佇列，關閉後由輪詢補發。佇列存在設定檔，重開也不會忘。"""
        self.pending_renames[sid] = name
        self._save_pending()
        self.status.configure(
            text=tr("已改名：%s　·　對話關閉後自動同步 Claude 端") % name)

    def _flush_pending_renames(self, running_now):
        """輪詢回報後呼叫：把已關閉對話的排隊改名補同步到 Claude 端。

        只發一次：先出隊再推送，失敗由狀態列回報，不無限重試。
        """
        for sid, name in list(self.pending_renames.items()):
            if sid.lower() in running_now or sid.lower() in self._rename_guard:
                continue
            info = sessions.by_id(self.sessions).get(sid) or {}
            cwd = info.get("cwd") or next(
                (i["cwd"] for i in self.items if i["session"] == sid), "")
            if not cwd or not os.path.isdir(cwd):
                continue        # 還不知道資料夾（例如尚未掃描完），下輪再試
            self.pending_renames.pop(sid, None)
            self._save_pending()
            self._push_rename({"session": sid, "cwd": cwd, "name": name},
                              name, after=self.rescan)

    def rename_item(self):
        """右側：就地改視窗名稱；未開啟的對話一併同步 Claude 端名稱。"""
        picked = self._sels()
        if len(picked) != 1:
            return
        n = picked[0]
        item = self.items[n]

        def commit(name):
            if name == item["name"]:
                return
            item["name"] = name
            self._touch()
            self.picked.selection_set(str(n))
            if item["session"].lower() in self.running:
                self._queue_rename(item["session"], name)
                return
            self._push_rename(item, name)

        self._edit_cell(self.picked, str(n), item["name"], commit)

    def rename_session_title(self):
        """左側：就地修改對話在 Claude 中的名稱。"""
        picked = self.selected_sessions()
        if len(picked) != 1:
            return
        sid = picked[0]
        s = sessions.by_id(self.sessions).get(sid)
        if not s:
            return
        shown = self.pending_renames.get(sid) or sessions.label(s)

        def commit(name):
            if name == shown:
                return
            for it in self.items:          # 啟動清單中的名稱一併更新
                if it["session"] == sid:
                    it["name"] = name
                    self._touch()
            if sid.lower() in self.running:
                self._queue_rename(sid, name)
                self.fill_tree()           # 立即顯示排隊中的新名稱
                return
            self._push_rename({"session": sid, "cwd": s["cwd"], "name": name},
                              name, after=self.rescan)

        self._edit_cell(self.tree, sid, shown, commit)

    def _edit_cell(self, tree, item, initial, commit):
        """開啟就地編輯；編輯期間暫停清單刷新，結束後補上。"""
        self._editing = True

        def done():
            self._editing = False
            if self._refresh_pending:
                self._refresh_pending = False
                self.refresh_views()

        CellEditor(tree, item, initial, commit, on_done=done)

    def rename_selected(self):
        """F2：焦點在哪個清單就改哪邊。"""
        if self._sels():
            self.rename_item()
        else:
            self.rename_session_title()

    def _move(self, n, to):
        self.items.insert(to, self.items.pop(n))
        self._touch()
        self.picked.focus(str(to))
        self.picked.selection_set(str(to))

    def move_up(self):
        picked = self._sels()
        if len(picked) == 1 and picked[0]:
            self._move(picked[0], picked[0] - 1)

    def move_down(self):
        picked = self._sels()
        if len(picked) == 1 and picked[0] < len(self.items) - 1:
            self._move(picked[0], picked[0] + 1)

    def paste_command(self):
        try:
            text = self.clipboard_get()
        except Exception:
            text = ""
        text = simpledialog.askstring(
            tr("貼上 resume 指令"),
            tr("貼上 Claude 結束時輸出的指令："),
            initialvalue=text.strip().replace("\n", " "), parent=self)
        if not text:
            return
        m = RESUME_RE.search(text)
        if not m:
            messagebox.showwarning(
                tr("Claude 工作台"),
                tr("無法解析。格式：\n\n"
                   "cd 'D:\\某資料夾' ; claude --resume 3e3f0e02-…"))
            return
        cwd, sid = m.group(1).strip(), m.group(2)
        if any(i["session"] == sid for i in self.items):
            messagebox.showinfo(tr("Claude 工作台"), tr("已在啟動清單中"))
            return
        s = sessions.by_id(self.sessions).get(sid)
        name = sessions.label(s)[:20] if s else os.path.basename(cwd)
        self.items.append({"name": name, "cwd": cwd, "session": sid})
        self._touch(select_last=True)

    def toggle_startup(self):
        want = bool(self.v_startup.get())
        ok, err = startup.set_enabled(want)
        if not ok:
            self.v_startup.set(not want)
            messagebox.showerror(tr("開機自動啟動"), tr("設定失敗：\n\n%s") % err)
            return
        real = startup.is_enabled()
        self.v_startup.set(real)
        self.status.configure(
            text=tr("開機自動啟動：%s")
                 % (tr("開啟", "bool") if real else tr("關閉", "bool")))

    def edit_claude_settings(self):
        if not self.schema_ready:
            self.status.configure(text=tr("設定結構讀取中…"))
            self.after(400, self.edit_claude_settings)
            return
        dlg = options.open_claude_settings(self.winfo_toplevel())
        self.wait_window(dlg)
        if dlg.changed:
            self.status.configure(text=tr("Claude Code 設定已更新"))

    def edit_workbench_options(self):
        # 先寫入目前的介面狀態，對話框才讀得到最新值
        cfg = config.load()
        cfg["ui"] = self.collect_ui()
        config.save(cfg)
        dlg = options.open_workbench_options(self.winfo_toplevel(), config)
        self.wait_window(dlg)
        if dlg.changed:
            self.cfg = config.load()
            ui = self.cfg.get("ui", {})
            for key, var in (("show_cost", self.v_cost),
                             ("show_context", self.v_ctx)):
                self.ui[key] = bool(ui.get(key, DEFAULT_UI[key]))
                var.set(self.ui[key])
            self.apply_columns()
            self.status.configure(text=tr("工作台選項已更新"))

    def open_folder(self):
        try:
            os.startfile(os.path.dirname(os.path.abspath(__file__)))
        except OSError as e:
            messagebox.showwarning(tr("Claude 工作台"), str(e))

    def open_log(self):
        import appdata
        path = appdata.log_path()
        if not os.path.exists(path):
            messagebox.showinfo(tr("錯誤記錄檔"), tr("尚無記錄"))
            return
        try:
            os.startfile(path)
        except OSError as e:
            messagebox.showwarning(tr("Claude 工作台"), str(e))

    def check_updates(self):
        """點了才連 GitHub Releases API 比對版本；平時不發任何網路請求。"""
        import version
        self.status.configure(text=tr("檢查更新中…"))

        def work():
            import json as _json
            import urllib.request
            req = urllib.request.Request(
                "https://api.github.com/repos/%s/releases/latest"
                % version.REPO,
                headers={"User-Agent": "claude-workbench",
                         "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return _json.load(r)

        def done(data, err):
            if err or not isinstance(data, dict):
                self.status.configure(text=tr("檢查更新失敗"))
                return
            tag = (data.get("tag_name") or "").lstrip("vV")
            cur = version.__version__
            if tag and version.parse(tag) > version.parse(cur):
                self.status.configure(text="")
                if messagebox.askyesno(
                        tr("檢查更新"),
                        tr("有新版本 v%s（目前 v%s），開啟下載頁？")
                        % (tag, cur)):
                    import webbrowser
                    webbrowser.open(
                        data.get("html_url")
                        or "https://github.com/%s/releases" % version.REPO)
            else:
                self.status.configure(text=tr("已是最新版本（v%s）") % cur)

        self.run_bg(work, done)

    def about(self):
        import claudeschema
        schema = claudeschema.load()
        messagebox.showinfo(
            tr("關於 Claude 工作台"),
            tr("Claude 工作台 v%s\n\n"
               "把固定的幾個 Claude Code 對話各自開在獨立視窗並接續進度。\n"
               "金額為依公開費率換算的估算值；未涵蓋 1M 長脈絡溢價。\n"
               "非官方社群工具，與 Anthropic 無關。\n\n"
               "Claude Code 版本：%s\n"
               "程式位置：%s\n"
               "對話紀錄：%s\n"
               "記錄檔：%s")
            % (__import__("version").__version__,
               schema.get("version") or tr("未知"),
               os.path.dirname(os.path.abspath(__file__)), sessions.PROJECTS,
               __import__("appdata").log_path()))

    def save(self):
        # 重讀後再寫入，避免覆蓋其他地方剛寫入的內容
        cfg = config.load()
        cfg["items"] = self.items
        cfg["archived"] = sorted(self.archived)
        cfg["ui"] = self.collect_ui()
        config.save(cfg)
        self.cfg = cfg
        self.dirty = False
        self.status.configure(text=tr("已儲存　·　%d 個項目") % len(self.items))

    def launch_now(self):
        self.save()
        self.open_many(list(self.items), confirm=False, honor_background=True)


def set_icon(win, name="settings.ico"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if os.path.exists(path):
        try:
            win.iconbitmap(default=path)
        except tk.TclError:
            pass


GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")


def restore_geometry(root, ui, scale):
    """套用上次關閉時的視窗大小與位置；數值不合理時改用預設。"""
    m = GEOMETRY_RE.match((ui.get("geometry") or "").strip())
    used = False
    if m:
        w, h, x, y = (int(g) for g in m.groups())
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        on_screen = -60 <= x <= sw - 200 and -20 <= y <= sh - 120
        if w >= 700 and h >= 450 and on_screen:
            root.geometry(m.string)
            used = True
    if not used:
        root.geometry("%dx%d" % (int(1320 * scale), int(820 * scale)))
    if ui.get("zoomed"):
        root.state("zoomed")


def main():
    # 已在執行時將既有視窗帶到前景，不開啟第二個
    if not singleton.single_instance("ClaudeWorkbenchSettings", WINDOW_TITLE):
        return
    make_dpi_aware()
    root = tk.Tk()
    root.withdraw()                 # 套用大小位置後再顯示，避免閃動
    applog.install(root, "settings")
    root.title(WINDOW_TITLE)
    set_icon(root)
    scale = setup_theme(root)
    root.minsize(int(980 * scale), int(560 * scale))
    app = App(root)
    restore_geometry(root, app.ui, scale)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.deiconify()
    if app.config_error:
        root.after(300, lambda: messagebox.showwarning(
            tr("Claude 工作台"), app.config_error))
    root.mainloop()


if __name__ == "__main__":
    main()
