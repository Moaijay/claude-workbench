# -*- coding: utf-8 -*-
"""介面語言。

中文原文即是查詢用的 key：`tr("儲存")` 在中文介面回傳原字串，
在英文介面查 EN 字典；查不到就回傳中文原文，不會拋錯也不會顯示空白。

語言在模組載入時決定一次，改了設定要重新開啟程式才生效。
"""
import ctypes
import json
import os

LANGS = ("zh-TW", "en")

# 同字不同義時用第二個參數區分：tr("開啟", "bool") 是 On，tr("開啟") 是 Open。
# 帶脈絡的 key 寫成 "脈絡\x04中文"。
CTX_SEP = "\x04"

EN = {
    # ---------------------------------------------------------- 視窗與選單
    "Claude 工作台": "Claude Workbench",
    "Claude 工作台 — 設定": "Claude Workbench — Settings",
    "檔案": "File",
    "檢視": "View",
    "工具": "Tools",
    "說明": "Help",
    "關於": "About",
    "結束": "Exit",
    "儲存": "Save",
    "新增對話": "New session",
    "新增對話…": "New session…",
    "從 resume 指令加入…": "Add from resume command…",
    "顯示金額": "Show cost",
    "顯示脈絡使用率": "Show context usage",
    "搜尋對話內容": "Search session contents",
    "只看最近 30 天": "Last 30 days only",
    "顯示已封存": "Show archived",
    "Claude Code 設定…": "Claude Code settings…",
    "工作台選項…": "Workbench options…",
    "開機時自動開啟": "Open at startup",
    "程式所在資料夾": "Program folder",
    "錯誤記錄檔": "Error log",

    # ---------------------------------------------------------- 主視窗
    "搜尋": "Search",
    "重新掃描": "Rescan",
    "電腦上所有對話": "All sessions on this computer",
    "啟動清單（由上到下依序開啟）": "Launch list (opened top to bottom)",
    "對話": "Session",
    "金額": "Cost",
    "脈絡": "Context",
    "最後使用": "Last used",
    "視窗名稱": "Window name",
    "資料夾": "Folder",
    "名稱": "Name",
    "加入  →": "Add  →",
    "立即啟動": "Launch now",
    "上移": "Move up",
    "下移": "Move down",
    "移除": "Remove",
    "重新命名": "Rename",
    "開啟": "Open",
    "關閉": "Close",
    "加入啟動清單": "Add to launch list",
    "封存": "Archive",
    "取消封存": "Unarchive",
    "移至其他資料夾": "Move to another folder",
    "移至其他資料夾…": "Move to another folder…",
    "複製 resume 指令": "Copy resume command",
    "瀏覽…": "Browse…",
    "清除": "Clear",
    "建立": "Create",
    "取消": "Cancel",
    "套用": "Apply",

    # ---------------------------------------------------------- 清單標示
    "　（已封存）": "  (archived)",
    "  ⚠ 找不到": "  ⚠ missing",
    "  ⚠ 有更新的接續": "  ⚠ newer continuation exists",
    "  ⓘ 每次會問信任": "  ⓘ trust asked every time",
    "(不明)": "(unknown)",
    "未知": "unknown",
    "%s   〔命中 %d〕": "%s   [%d hits]",
    "　·　": " · ",
    "、": ", ",

    # ---------------------------------------------------------- 狀態列
    "共 %d 條": "%d sessions",
    "已封存 %d": "%d archived",
    "執行中 %d": "%d running",
    "累計 %s（估算）": "Total %s (estimated)",
    "　·　⚠ 無費率資料：%s": " · ⚠ No pricing data: %s",
    "掃描中…": "Scanning…",
    "掃描失敗，詳見錯誤記錄檔": "Scan failed; see the error log",
    "搜尋中…": "Searching…",
    "搜尋失敗，詳見錯誤記錄檔": "Search failed; see the error log",
    "命中 %d 條": "%d matches",
    "　·　另有 %d 條已封存符合": " · %d archived matches not shown",
    "已儲存　·　%d 個項目": "Saved · %d items",
    "已移至 %s": "Moved to %s",
    "已複製 resume 指令": "Resume command copied",
    "已開啟新對話": "New session opened",
    "開啟中…": "Opening…",
    "已開啟：%s": "Opened: %s",
    "開啟失敗：%s（詳見錯誤記錄檔）": "Open failed: %s (see the error log)",
    "已開啟 %d／%d": "%d/%d opened",
    "略過 %d": "%d skipped",
    "　·　略過 %d 條": " · %d skipped",
    "已加入 %d 條%s": "%d added%s",
    "已移除 %d 條": "%d removed",
    "已封存 %d 條": "%d archived",
    "已取消封存 %d 條": "%d unarchived",
    "已在啟動清單中": "Already in the launch list",
    "名稱同步中，稍候再開": "Name syncing; open again shortly",
    "同步名稱至 Claude 中…": "Syncing name to Claude…",
    "已改名：%s": "Renamed: %s",
    "已改名：%s　·　對話開啟中，Claude 端名稱未變":
        "Renamed: %s · session is open, the name in Claude is unchanged",
    "Claude 端改名失敗：%s": "Rename in Claude failed: %s",
    "對話開啟中；請於該視窗輸入 /rename":
        "Session is open; run /rename in that window",
    "設定結構讀取中…": "Reading settings schema…",
    "Claude Code 設定已更新": "Claude Code settings updated",
    "工作台選項已更新": "Workbench options updated",
    "開機自動啟動": "Startup",
    "開機自動啟動：%s": "Startup: %s",
    "設定失敗：\n\n%s": "Setting failed\n\n%s",
    "尚無記錄": "No log yet",

    # ---------------------------------------------------------- 確認與錯誤
    "啟動清單有未儲存的變更，是否儲存？":
        "Save unsaved changes to the launch list?",
    "開啟 %d 條對話？": "Open %d sessions?",
    "封存 %d 條對話？": "Archive %d sessions?",
    "從啟動清單移除 %d 條？": "Remove %d from the launch list?",
    "%d 條在啟動清單中：%s\n\n一併從啟動清單移除？":
        "%d are in the launch list: %s\n\nRemove them from the launch list too?",
    "選擇資料夾": "Select a folder",
    "請選擇資料夾": "Select a folder",
    "選擇目標資料夾": "Select the destination folder",
    "該對話開啟中，關閉後再搬移": "Session is open; close it before moving",
    "資料夾不存在\n\n%s": "Folder does not exist\n\n%s",
    "資料夾不存在：%s": "Folder does not exist: %s",
    "找不到對話紀錄：%s": "Session log not found: %s",
    "未開啟：\n\n%s": "Not opened:\n\n%s",
    "開啟失敗\n\n%s": "Open failed\n\n%s",
    "建立失敗\n\n%s": "Creation failed\n\n%s",
    "建立中…": "Creating…",
    "%s　資料夾不存在": "%s  folder does not exist",
    "%s　找不到對話紀錄": "%s  session log not found",
    "%s　名稱同步中": "%s  name syncing",
    "%s　已開啟": "%s  already open",
    "%s　開啟失敗：%s": "%s  open failed: %s",
    "貼上 resume 指令": "Paste resume command",
    "貼上 Claude 結束時輸出的指令：": "The command Claude prints on exit:",
    "無法解析。格式：\n\ncd 'D:\\某資料夾' ; claude --resume 3e3f0e02-…":
        "Unrecognized format. Expected:\n\n"
        "cd 'D:\\some folder' ; claude --resume 3e3f0e02-…",
    "ⓘ 家目錄：每次開啟皆詢問資料夾信任":
        "ⓘ Home directory: folder trust is asked on every launch",
    "留空：開啟新視窗，名稱由對話內容自動產生\n"
    "填寫：以 /rename 固定名稱並加入啟動清單（需數秒）":
        "Empty: opens a new window, named automatically from the conversation\n"
        "Filled in: fixes the name with /rename and adds it to the launch "
        "list (takes a few seconds)",

    # ---------------------------------------------------------- 關於
    "關於 Claude 工作台": "About Claude Workbench",
    "Claude 工作台 v%s\n\n"
    "把固定的幾個 Claude Code 對話各自開在獨立視窗並接續進度。\n"
    "金額為依公開費率換算的估算值；未涵蓋 1M 長脈絡溢價。\n"
    "非官方社群工具，與 Anthropic 無關。\n\n"
    "Claude Code 版本：%s\n"
    "程式位置：%s\n"
    "對話紀錄：%s\n"
    "記錄檔：%s":
        "Claude Workbench v%s\n\n"
        "Opens a fixed set of Claude Code sessions in separate windows, "
        "each resumed where it left off.\n"
        "Costs are estimates from published rates; the 1M long-context "
        "premium is not included.\n"
        "Unofficial community tool, not affiliated with Anthropic.\n\n"
        "Claude Code version: %s\n"
        "Program: %s\n"
        "Session logs: %s\n"
        "Log file: %s",

    # ---------------------------------------------------------- 設定編輯器
    "設定": "Settings",
    "Claude Code 設定": "Claude Code settings",
    "工作台選項": "Workbench options",
    "（未設定）": "(not set)",
    "bool\x04開啟": "On",
    "bool\x04關閉": "Off",
    "其他設定": "Other settings",
    "原始 JSON": "Raw JSON",
    "（沒有）": "(none)",
    "未列入上方分頁的設定；清單與物件請用「原始 JSON」編輯":
        "Settings not covered by the tabs above; "
        "edit lists and objects in Raw JSON",
    "　·　此版本共 %d 個設定鍵": " · %d setting keys in this version",
    "%s　·　請用原始 JSON 編輯": "%s · edit in Raw JSON",
    "整份設定的原始內容；套用前驗證格式並備份":
        "The whole settings file; validated and backed up before applying",
    "此版本不認得（已標示 ⚠）：%s": "Not recognized by this version (⚠): %s",
    "安裝版本 %s　·　清單對照版本 %s":
        "Installed version %s · list verified for %s",
    "%s　·　可選值取自 Claude Code %s　·　「（未設定）」不寫入該鍵":
        "%s · choices read from Claude Code %s · \"(not set)\" omits the key",
    "已儲存　·　已備份　·　%s": "Saved · backed up · %s",
    "已套用原始 JSON　·　%s": "Raw JSON applied · %s",
    "寫入失敗\n\n%s": "Write failed\n\n%s",
    "JSON 格式錯誤\n\n%s": "Invalid JSON\n\n%s",
    "最外層須為物件 { }": "The top level must be an object { }",
    "「%s」只能填數字，現在是：%s": "\"%s\" takes digits only; it is: %s",
    "「%s」要在 %d 到 %d 之間，現在是 %d":
        "\"%s\" must be between %d and %d; it is %d",

    # ---------------------------------------------------------- 分頁名稱
    "模型與思考": "Model and thinking",
    "多分身（Ultracode）": "Subagents (Ultracode)",
    "權限": "Permissions",
    "外觀": "Appearance",
    "記憶與紀錄": "Memory and logs",
    "遠端與通知": "Remote and notifications",
    "其他": "Other",
    "開啟對話時": "When opening sessions",
    "顯示": "Display",

    # ---------------------------------------------------------- Claude 設定項
    "預設模型": "Default model",
    "[1m]：100 萬 token 長脈絡版本": "[1m]: 1M-token long-context variant",
    "思考強度": "Effort level",
    "耗時隨強度遞增": "Higher effort takes longer",
    "最高強度＋常態多分身；範圍限單場對話":
        "Maximum effort plus routine subagents; scoped to one session",
    "預設啟用延伸思考": "Extended thinking by default",
    "顯示思考摘要": "Show thinking summaries",
    "快速模式": "Fast mode",
    "回應更快、費率較高；部分模型支援":
        "Faster replies at a higher rate; some models only",
    "自動壓縮對話": "Auto-compact sessions",
    "接近上限時將先前內容轉為摘要":
        "Summarizes earlier content near the limit",
    "壓縮觸發量（token）": "Compact threshold (tokens)",
    "ultracode 關鍵字觸發": "ultracode keyword trigger",
    "訊息含此關鍵字時，該輪改用多分身":
        "Messages containing the keyword run that turn with subagents",
    "分身數量上限": "Subagent count limit",
    "small ＜5／medium ＜15／large ＜50／unrestricted 不限":
        "small <5 / medium <15 / large <50 / unrestricted no limit",
    "預設權限模式": "Default permission mode",
    "bypassPermissions：不詢問。遠端遙控時降為 default":
        "bypassPermissions: no prompts. Downgraded to default under "
        "remote control",
    "略過危險模式確認": "Skip dangerous-mode confirmation",
    "停用 hooks 與狀態列": "Disable hooks and status line",
    "hooks：事件觸發的自動指令": "hooks: commands triggered by events",
    "配色": "Theme",
    "未設定：跟隨系統": "Not set: follows the system",
    "回覆語言": "Reply language",
    "語言名稱，如 traditional chinese": "Language name, e.g. traditional chinese",
    "畫面詳細度": "Display detail",
    "default 一般／verbose 詳盡／focus 精簡":
        "default normal / verbose detailed / focus minimal",
    "顯示回覆耗時": "Show turn duration",
    "顯示訊息時間": "Show message timestamps",
    "等待時顯示提示": "Show tips while waiting",
    "自動記憶": "Auto memory",
    "跨對話沿用的長期記憶": "Long-term memory carried across sessions",
    "記憶存放位置": "Memory location",
    "未設定：預設位置": "Not set: default location",
    "背景整理記憶": "Organize memory in the background",
    "回來時顯示重點": "Summary on return",
    "檔案檢查點": "File checkpoints",
    "修改前留存版本": "Keeps a version before each edit",
    "紀錄保留天數": "Log retention (days)",
    "啟動時啟用 Remote Control": "Enable Remote Control at startup",
    "從 claude.ai 操作本機對話": "Drives local sessions from claude.ai",
    "停用 Remote Control": "Disable Remote Control",
    "同步對話至 claude.ai": "Sync sessions to claude.ai",
    "唯讀，不可遠端操作": "Read-only, no remote control",
    "需回應時通知": "Notify when input is needed",
    "權限詢問與提問時推送": "Pushed on permission prompts and questions",
    "主動通知": "Proactive notifications",
    "跨對話訊息": "Cross-session messages",
    "accept 收下／hold 保留／refuse 拒收":
        "accept take / hold queue / refuse reject",
    "更新頻道": "Update channel",
    "latest 最新／stable 穩定／rc 預先發行":
        "latest newest / stable stable / rc prerelease",
    "! 指令使用的 shell": "Shell for ! commands",
    "提問逾時": "Question timeout",
    "逾時後自行繼續": "Continues on its own after the timeout",
    "詳細輸出": "Verbose output",

    # ---------------------------------------------------------- 工作台選項
    "權限模式": "Permission mode",
    "未設定：沿用 Claude Code 全域設定":
        "Not set: follows the global Claude Code setting",
    "命令列程式": "Command-line program",
    "結束後保留視窗": "Keep the window open after exit",
    "新對話的預設資料夾": "Default folder for new sessions",
    "未設定：家目錄": "Not set: home directory",
    "左側清單的金額欄、資料夾小計與底部累計；與「檢視 → 顯示金額」同一設定":
        "The cost column, folder subtotals and the bottom total; "
        "same setting as View → Show cost",
    "每條對話目前佔脈絡視窗的百分比；接近上限時對話會被壓縮":
        "Share of the context window each session currently uses; "
        "sessions are compacted near the limit",

    # ---------------------------------------------------------- 啟動器
    "啟動清單是空的\n\n請於「Claude 工作台 設定」中挑選對話。":
        "The launch list is empty\n\n"
        "Pick sessions in Claude Workbench Settings.",
    "已開啟 %d 個；下列未開啟：\n\n%s%s":
        "%d opened; the following were not:\n\n%s%s",
    "\n\n已開啟而略過：%s": "\n\nAlready open, skipped: %s",
    "清單中的對話均已開啟：\n\n%s":
        "Every session in the list is already open:\n\n%s",
    "claude 回報失敗": "claude reported a failure",
    "指令跑完了，但找不到新對話的紀錄檔":
        "The command finished but no session log was created",

    # ---------------------------------------------------------- 安裝
    "開啟所有固定的 Claude Code 對話": "Opens the pinned Claude Code sessions",
    "挑選每次要自動開啟哪些 Claude Code 對話":
        "Picks which Claude Code sessions open each time",
    "安裝位置：%s": "Install location: %s",
    "  圖示：%s": "  Icons: %s",
    "圖示已存在": "already present",
    "已產生圖示": "generated",
    "產生圖示失敗（會用預設圖示）：%s":
        "generation failed, using default icons: %s",
    "  Claude Code：%s": "  Claude Code: %s",
    "找不到！請先安裝 Claude Code": "not found — install Claude Code first",
    "  桌面：%s": "  Desktop: %s",
    "  直譯器：%s": "  Interpreter: %s",
    "\n完成。點桌面的「Claude 工作台 設定」開始挑要固定開啟的對話。":
        "\nDone. Open the \"Claude 工作台 設定\" shortcut on the desktop "
        "to pick sessions to pin.",
    "  已刪除 %s": "  Deleted %s",
    "  已關閉開機自動啟動": "  Startup entry removed",
    "\n捷徑已移除。程式檔案與 config.json 留著，直接刪掉這個資料夾就完全清乾淨。":
        "\nShortcuts removed. Program files and config.json remain; "
        "delete this folder to remove everything.",
}


def _configured():
    """設定檔裡的 language。刻意不 import config：避免循環相依。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config.json")
        with open(path, encoding="utf-8") as fh:
            want = json.load(fh).get("language")
        if want in LANGS or want == "auto":
            return want
    except Exception:
        pass
    return "auto"


def _system():
    """Windows 的介面語言。LANGID 主語言 0x04 是中文。"""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "zh-TW" if (langid & 0x3FF) == 0x04 else "en"
    except Exception:
        return "en"


def _resolve(want):
    return want if want in LANGS else _system()


_LANG = _resolve(_configured())


def language():
    return _LANG


def set_language(lang):
    """強制指定語言，"auto" 回到自動判斷。給測試與工具用。"""
    global _LANG
    _LANG = _resolve(lang)
    return _LANG


def tr(s, ctx=""):
    if _LANG == "zh-TW":
        return s
    if ctx:
        return EN.get(ctx + CTX_SEP + s) or EN.get(s, s)
    return EN.get(s, s)
