# -*- coding: utf-8 -*-
"""settings.json 與工作台設定檔的分頁式編輯器。

可選值取自安裝的 claude 執行檔而非硬編清單，因此會隨版本更新。
此版本不認得的鍵會標示出來；未列入分頁但設定檔中有值的鍵集中在「其他設定」。
「（未設定）」表示不寫入該鍵，沿用 Claude Code 的預設值。
"""
import json
import os
import shutil
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import claudeschema

CLAUDE_SETTINGS = os.path.join(os.path.expanduser("~"), ".claude",
                               "settings.json")

UNSET = "（未設定）"
BOOL_CHOICES = [UNSET, "開啟", "關閉"]

# 此清單對照過的 Claude Code 版本；與實際安裝版本不同時會在介面上標示。
VERIFIED_FOR = "2.1.235"

# (鍵, 標籤, 型別, 備援選項, 一句話說明)
#   型別 bool / choice / int / text / path / file
#   備援選項只有在掃不到執行檔時才會用到；正常情況下選項來自執行檔本身。
CLAUDE_SPEC = [
    ("模型與思考", [
        ("model", "預設模型", "choice",
         ["opus", "opus[1m]", "sonnet", "sonnet[1m]", "haiku"],
         "[1m]：100 萬 token 長脈絡版本"),
        ("effortLevel", "思考強度", "choice", ["low", "medium", "high", "xhigh"],
         "耗時隨強度遞增"),
        ("ultracode", "Ultracode", "bool", None,
         "最高強度＋常態多分身；範圍限單場對話"),
        ("alwaysThinkingEnabled", "預設啟用延伸思考", "bool", None, ""),
        ("showThinkingSummaries", "顯示思考摘要", "bool", None, ""),
        ("fastMode", "快速模式", "bool", None, "回應更快、費率較高；部分模型支援"),
        ("autoCompactEnabled", "自動壓縮對話", "bool", None,
         "接近上限時將先前內容轉為摘要"),
        ("autoCompactWindow", "壓縮觸發量（token）", "int", (100000, 1000000),
         ""),
    ]),
    ("多分身（Ultracode）", [
        ("workflowKeywordTriggerEnabled", "ultracode 關鍵字觸發", "bool", None,
         "訊息含此關鍵字時，該輪改用多分身"),
        ("workflowSizeGuideline", "分身數量上限", "choice",
         ["unrestricted", "small", "medium", "large"],
         "small ＜5／medium ＜15／large ＜50／unrestricted 不限"),
    ]),
    ("權限", [
        ("permissions.defaultMode", "預設權限模式", "choice",
         ["default", "auto", "acceptEdits", "plan", "bypassPermissions"],
         "bypassPermissions：不詢問。遠端遙控時降為 default"),
        ("skipDangerousModePermissionPrompt", "略過危險模式確認", "bool", None,
         ""),
        ("disableAllHooks", "停用 hooks 與狀態列", "bool", None,
         "hooks：事件觸發的自動指令"),
    ]),
    ("外觀", [
        ("theme", "配色", "choice", ["light", "dark"], "未設定：跟隨系統"),
        ("language", "回覆語言", "text", None, "語言名稱，如 traditional chinese"),
        ("viewMode", "畫面詳細度", "choice", ["default", "verbose", "focus"],
         "default 一般／verbose 詳盡／focus 精簡"),
        ("showTurnDuration", "顯示回覆耗時", "bool", None, ""),
        ("showMessageTimestamps", "顯示訊息時間", "bool", None, ""),
        ("spinnerTipsEnabled", "等待時顯示提示", "bool", None, ""),
    ]),
    ("記憶與紀錄", [
        ("autoMemoryEnabled", "自動記憶", "bool", None, "跨對話沿用的長期記憶"),
        ("autoMemoryDirectory", "記憶存放位置", "path", None, "未設定：預設位置"),
        ("autoDreamEnabled", "背景整理記憶", "bool", None, ""),
        ("awaySummaryEnabled", "回來時顯示重點", "bool", None, ""),
        ("fileCheckpointingEnabled", "檔案檢查點", "bool", None, "修改前留存版本"),
        ("cleanupPeriodDays", "紀錄保留天數", "int", (1, 99999), ""),
    ]),
    ("遠端與通知", [
        ("remoteControlAtStartup", "啟動時啟用 Remote Control", "bool", None,
         "從 claude.ai 操作本機對話"),
        ("disableRemoteControl", "停用 Remote Control", "bool", None, ""),
        ("autoUploadSessions", "同步對話至 claude.ai", "bool", None,
         "唯讀，不可遠端操作"),
        ("inputNeededNotifEnabled", "需回應時通知", "bool", None,
         "權限詢問與提問時推送"),
        ("agentPushNotifEnabled", "主動通知", "bool", None, ""),
        ("crossSessionInbound", "跨對話訊息", "choice",
         ["accept", "hold", "refuse"],
         "accept 收下／hold 保留／refuse 拒收"),
    ]),
    ("其他", [
        ("autoUpdatesChannel", "更新頻道", "choice", ["latest", "stable", "rc"],
         "latest 最新／stable 穩定／rc 預先發行"),
        ("defaultShell", "! 指令使用的 shell", "choice", ["bash", "powershell"],
         ""),
        ("askUserQuestionTimeout", "提問逾時", "choice",
         ["60s", "5m", "10m", "never"], "逾時後自行繼續"),
        ("verbose", "詳細輸出", "bool", None, ""),
    ]),
]

# 只列出使用者偏好。內部參數（視窗間隔、防重複開啟、執行檔位置）不開放編輯。
WORKBENCH_SPEC = [
    ("開啟對話時", [
        ("permission_mode", "權限模式", "choice",
         ["bypassPermissions", "acceptEdits", "auto", "plan", "manual",
          "dontAsk"],
         "未設定：沿用 Claude Code 全域設定"),
        ("shell", "命令列程式", "choice", ["powershell", "cmd"], ""),
        ("keep_open", "結束後保留視窗", "bool", None, ""),
        ("default_cwd", "新對話的預設資料夾", "path", None, "未設定：家目錄"),
    ]),
    ("顯示", [
        ("ui.show_cost", "顯示金額", "bool", None,
         "左側清單的金額欄、資料夾小計與底部累計；與「檢視 → 顯示金額」同一設定"),
        ("ui.show_context", "顯示脈絡使用率", "bool", None,
         "每條對話目前佔脈絡視窗的百分比；接近上限時對話會被壓縮"),
    ]),
]

# 這些是資料或內部參數，不要出現在「其他設定」分頁裡
WORKBENCH_DATA_KEYS = {"items", "ui", "stagger_ms", "skip_running", "claude"}


# ---------------------------------------------------------------- 存取
def get_path(data, dotted):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(data, dotted, value):
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    if value is None:
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_json(path, data, backup=True):
    """先備份原檔，再以暫存檔加更名的方式寫入，避免寫入中斷造成損毀。"""
    if backup and os.path.exists(path):
        import appdata
        appdata.backup(path, os.path.basename(path), keep=10)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 對話框
class OptionsDialog(tk.Toplevel):
    """依規格產生的分頁式設定視窗。"""

    def __init__(self, master, title, spec, path, loader=None, saver=None,
                 note="", schema=None, extra_source=None):
        super().__init__(master)
        self.spec = spec
        self.path = path
        self.schema = schema or {"keys": {}, "enums": {}, "version": ""}
        self._loader = loader or (lambda: load_json(path))
        self._saver = saver or (lambda d: save_json(path, d))
        self.data = self._loader()
        self.vars = {}
        self.kinds = {}
        self.changed = False

        self.title(title)
        self.transient(master)
        try:
            self.iconbitmap(default=os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "settings.ico"))
        except tk.TclError:
            pass

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        if note:
            ttk.Label(outer, text=note, foreground="#555555",
                      wraplength=800, justify="left").pack(anchor="w",
                                                           pady=(0, 8))
        warn = self._version_warning()
        if warn:
            ttk.Label(outer, text=warn, foreground="#8a6a10", wraplength=800,
                      justify="left").pack(anchor="w", pady=(0, 8))

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        for tab_name, rows in spec:
            frame = ttk.Frame(nb, padding=14)
            nb.add(frame, text=tab_name)
            frame.columnconfigure(1, weight=1)
            for r, (key, label, kind, choices, help_text) in enumerate(rows):
                self._row(frame, r * 2, key, label, kind, choices, help_text)

        self._extra_tab(nb, extra_source)
        self._raw_tab(nb)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(12, 0))
        self.status = ttk.Label(bar, text="", foreground="#555555")
        self.status.pack(side="left")
        ttk.Button(bar, text="關閉", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="儲存", command=self.save).pack(side="right",
                                                            padx=10)
        self.bind("<Escape>", lambda _: self.destroy())

    # ------------------------------------------------------------ 版本
    def _version_warning(self):
        known = self.schema.get("keys") or {}
        if not known:
            return ""
        got = self.schema.get("version") or ""
        gone = [k for _, rows in self.spec for k, *_ in rows
                if k.split(".")[0] not in known]
        if not gone:
            return ""
        bits = ["此版本不認得（已標示 ⚠）：%s" % "、".join(gone)]
        if got and got != VERIFIED_FOR:
            bits.append("安裝版本 %s　·　清單對照版本 %s" % (got, VERIFIED_FOR))
        return "　·　".join(bits)

    # ------------------------------------------------------------ 列
    def _choices_for(self, key, fallback):
        """優先採用執行檔中的定義，掃不到時改用備援清單。"""
        found = (self.schema.get("enums") or {}).get(key.split(".")[-1])
        return list(found or fallback or [])

    def _row(self, frame, r, key, label, kind, choices, help_text):
        known = self.schema.get("keys") or {}
        unknown = bool(known) and key.split(".")[0] not in known
        text = ("⚠ " + label) if unknown else label
        lab = ttk.Label(frame, text=text)
        if unknown:
            lab.configure(foreground="#8a6a10")
        lab.grid(row=r, column=0, sticky="w", padx=(0, 14), pady=(6, 0))

        cur = get_path(self.data, key)
        var = tk.StringVar()
        box = ttk.Frame(frame)
        box.grid(row=r, column=1, sticky="w", pady=(6, 0))

        if kind == "bool":
            var.set(UNSET if cur is None else ("開啟" if cur else "關閉"))
            ttk.Combobox(box, textvariable=var, values=BOOL_CHOICES,
                         state="readonly", width=12).pack(side="left")

        elif kind == "choice":
            values = [UNSET] + self._choices_for(key, choices)
            shown = UNSET if cur is None else str(cur)
            if shown not in values:
                values.append(shown)      # 保留設定檔中既有的值
            var.set(shown)
            ttk.Combobox(box, textvariable=var, values=values,
                         state="readonly", width=30).pack(side="left")

        elif kind == "int":
            var.set("" if cur is None else str(cur))
            lo, hi = choices if choices else (0, 10 ** 9)
            ttk.Spinbox(box, textvariable=var, width=14, from_=lo, to=hi,
                        increment=max(1, (hi - lo) // 20)).pack(side="left")
            ttk.Button(box, text="清除", width=5,
                       command=lambda v=var: v.set("")).pack(side="left",
                                                             padx=(6, 0))

        elif kind in ("path", "file"):
            var.set("" if cur is None else str(cur))
            ttk.Entry(box, textvariable=var, width=38).pack(side="left")
            ttk.Button(box, text="瀏覽…", width=7,
                       command=lambda v=var, k=kind: self._browse(v, k)).pack(
                side="left", padx=(6, 0))
            ttk.Button(box, text="清除", width=5,
                       command=lambda v=var: v.set("")).pack(side="left",
                                                             padx=(4, 0))
        else:
            var.set("" if cur is None else str(cur))
            ttk.Entry(box, textvariable=var, width=38).pack(side="left")

        self.vars[key] = var
        self.kinds[key] = kind
        if help_text:
            ttk.Label(frame, text=help_text, foreground="#666666",
                      wraplength=560, justify="left").grid(
                row=r + 1, column=1, sticky="w", pady=(2, 0))

    def _browse(self, var, kind):
        start = var.get().strip() or os.path.expanduser("~")
        if kind == "path":
            got = filedialog.askdirectory(parent=self, initialdir=start)
        else:
            got = filedialog.askopenfilename(parent=self)
        if got:
            var.set(os.path.normpath(got))

    # ------------------------------------------------------------ 其他設定
    def _extra_tab(self, nb, extra_source):
        """設定檔中有值、但未列入分頁的鍵。"""
        managed = {k.split(".")[0] for _, rows in self.spec for k, *_ in rows}
        skip = managed | (extra_source or set())
        extras = sorted(k for k in self.data
                        if k not in skip and not k.startswith("$"))
        frame = ttk.Frame(nb, padding=14)
        nb.add(frame, text="其他設定")
        known = self.schema.get("keys") or {}
        head = "未列入上方分頁的設定；清單與物件請用「原始 JSON」編輯"
        if known:
            head += "　·　此版本共 %d 個設定鍵" % len(known)
        ttk.Label(frame, text=head, foreground="#666666", wraplength=740,
                  justify="left").grid(row=0, column=0, columnspan=2,
                                       sticky="w", pady=(0, 10))
        frame.columnconfigure(1, weight=1)
        r = 1
        for key in extras:
            val = self.data[key]
            desc = (known.get(key) or "")[:70]
            if isinstance(val, bool):
                self._row(frame, r, key, key, "bool", None, desc)
            elif isinstance(val, (int, float)):
                self._row(frame, r, key, key, "int", None, desc)
            elif isinstance(val, str):
                self._row(frame, r, key, key, "text", None, desc)
            else:
                ttk.Label(frame, text=key).grid(row=r, column=0, sticky="w",
                                                padx=(0, 14), pady=(6, 0))
                ttk.Label(frame, foreground="#888888",
                          text="%s　·　請用原始 JSON 編輯"
                               % type(val).__name__).grid(row=r, column=1,
                                                          sticky="w",
                                                          pady=(6, 0))
            r += 2
        if r == 1:
            ttk.Label(frame, text="（沒有）", foreground="#888888").grid(
                row=1, column=0, sticky="w")

    def _raw_tab(self, nb):
        frame = ttk.Frame(nb, padding=14)
        nb.add(frame, text="原始 JSON")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, foreground="#666666", justify="left", wraplength=740,
                  text="整份設定的原始內容；套用前驗證格式並備份"
                  ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.raw = tk.Text(frame, wrap="none", height=16, undo=True)
        self.raw.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.raw.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.raw.configure(yscrollcommand=sb.set)
        self._fill_raw()
        ttk.Button(frame, text="套用", command=self.apply_raw).grid(
            row=2, column=0, sticky="w", pady=(10, 0))

    def _fill_raw(self):
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", json.dumps(self.data, ensure_ascii=False,
                                          indent=2))

    # ------------------------------------------------------------ 動作
    def collect(self):
        data = self._loader()      # 重讀，才不會蓋掉別的地方剛寫進去的東西
        ranges = {k: c for _, rows in self.spec for k, _, kd, c, _ in rows
                  if kd == "int" and c}
        for key, var in self.vars.items():
            kind = self.kinds[key]
            raw = var.get().strip()
            if kind == "bool":
                value = None if raw == UNSET else (raw == "開啟")
            elif kind == "choice":
                value = None if raw in ("", UNSET) else raw
            elif kind == "int":
                if raw == "":
                    value = None
                else:
                    try:
                        value = int(raw)
                    except ValueError:
                        raise ValueError("「%s」只能填數字，現在是：%s" % (key, raw))
                    lo, hi = ranges.get(key, (None, None))
                    if lo is not None and not (lo <= value <= hi):
                        raise ValueError("「%s」要在 %d 到 %d 之間，現在是 %d"
                                         % (key, lo, hi, value))
            else:
                value = raw or None
            set_path(data, key, value)
        return data

    def save(self):
        try:
            data = self.collect()
        except ValueError as e:
            messagebox.showwarning("設定", str(e), parent=self)
            return
        try:
            self._saver(data)
        except OSError as e:
            messagebox.showerror("設定", "寫入失敗\n\n%s" % e, parent=self)
            return
        self.data = data
        self.changed = True
        self._fill_raw()
        self.status.configure(text="已儲存　·　已備份　·　%s"
                                   % time.strftime("%H:%M:%S"))

    def apply_raw(self):
        try:
            data = json.loads(self.raw.get("1.0", "end"))
        except json.JSONDecodeError as e:
            messagebox.showwarning("設定", "JSON 格式錯誤\n\n%s" % e,
                                   parent=self)
            return
        if not isinstance(data, dict):
            messagebox.showwarning("設定", "最外層須為物件 { }", 
                                   parent=self)
            return
        try:
            self._saver(data)
        except OSError as e:
            messagebox.showerror("設定", "寫入失敗\n\n%s" % e, parent=self)
            return
        self.data = data
        self.changed = True
        for key, var in self.vars.items():
            cur = get_path(data, key)
            kind = self.kinds[key]
            if kind == "bool":
                var.set(UNSET if cur is None else ("開啟" if cur else "關閉"))
            elif kind == "choice":
                var.set(UNSET if cur is None else str(cur))
            else:
                var.set("" if cur is None else str(cur))
        self.status.configure(text="已套用原始 JSON　·　%s"
                                   % time.strftime("%H:%M:%S"))


def open_claude_settings(master):
    schema = claudeschema.load()
    ver = schema.get("version") or "?"
    return OptionsDialog(
        master, "Claude Code 設定", CLAUDE_SPEC, CLAUDE_SETTINGS,
        schema=schema,
        note="%s　·　可選值取自 Claude Code %s　·　「（未設定）」不寫入該鍵"
             % (CLAUDE_SETTINGS, ver))


def open_workbench_options(master, config_module):
    return OptionsDialog(
        master, "工作台選項", WORKBENCH_SPEC, config_module.PATH,
        loader=config_module.load,
        saver=lambda d: save_json(config_module.PATH, d),
        extra_source=WORKBENCH_DATA_KEYS,
        note="%s" % config_module.PATH)
