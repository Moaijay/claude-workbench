# Claude Workbench

English · [繁體中文](README.zh-TW.md)

Opens a fixed set of Claude Code sessions, each in its own terminal window,
resumed where it left off. It also works as a replacement for the
`claude --resume` picker: every session on the machine is listed in one
window, and a double-click opens it.

> Unofficial community tool, not affiliated with or endorsed by Anthropic.
> Claude and Claude Code are trademarks of Anthropic.

## Install

```
git clone https://github.com/Moaijay/claude-workbench
cd claude-workbench
python install.py
```

Or download the ZIP and run `install.py` from the extracted folder (a full
path from anywhere works too — the script resolves everything relative to
its own location).

It generates the icons and creates two desktop shortcuts, handling
OneDrive-redirected desktops. `python install.py --uninstall` removes the
shortcuts and the autostart entry; deleting the folder removes the rest.

Requirements: Windows, Python 3.8+ (stdlib tkinter only), Claude Code.
The `claude` executable is found via PATH or `~\.local\bin`; anywhere else,
set `"claude"` in `config.json`.

## Updating

- Cloned with git: `git pull` inside the folder.
- Downloaded as a ZIP: download again and extract over the folder.

Either way user data survives: `config.json` is not part of the repository, and
caches, logs and backups live in `%LOCALAPPDATA%\ClaudeWorkbench`.

`Help → Check for updates` compares the installed version against the latest
GitHub release — only when clicked. The tool makes no other network requests.

## The two shortcuts

| Shortcut | What it does |
|---|---|
| **Claude 工作台** | Opens every session in the launch list, one window each |
| **Claude 工作台 設定** | Picks which sessions to pin, their order, names, and settings |

Both can be pinned to the taskbar. The shortcut names stay in Chinese
regardless of interface language, so existing shortcuts keep working.

## Main window

The left pane holds every session on the machine, grouped by folder, most
recently used first, with accumulated cost and last-used time. The right pane
is the launch list; top to bottom is the order sessions open in.

- **Double-click a row, or select and press Enter, to open it** — on either
  side, without adding it to the launch list first. Rows already in the launch
  list keep their window name; double-clicking a folder row expands or
  collapses it.
- "Add →" puts the selected sessions into the launch list, using each
  session's title as the window name.
- Click empty list space or the window background, or press **Esc**, to clear
  the selection; **Ctrl+A** selects all.
- Both lists support multi-select: **Shift+click** for a range, **Ctrl+click**
  to add or remove individual rows. Add, archive, open and remove all act on
  the whole selection.
- Selecting a folder row applies the action to every session under it.
- Opening several at once asks for confirmation, as does removing several.
  Rename and reorder handle one row at a time.
- **Context menus**: on the left, Open / Add to launch list / Archive /
  Rename / Move to another folder / Copy resume command. On the right,
  Open / Rename / Move up / Move down / Remove.
- **Click the Cost, Context or Last used header to sort**, click again to
  reverse. The sort is remembered.
- **Status dot**: green ● is open and idle, orange ● is Claude working. It
  refreshes every 3 seconds, and the status bar shows how many are running.
- **Context column**: how much of the context window that session currently
  occupies. Sessions are compacted near the limit. It can be turned off in the
  View menu or in Workbench options.
- The list checks session logs for changes every 30 seconds and updates
  itself, keeping selection and scroll position.
- Scanning, searching, opening and creating all run in the background; the
  interface does not freeze.

| Menu | Contents |
|---|---|
| **File** | New session… (Ctrl+N) / Add from resume command… / Save (Ctrl+S) / Exit |
| **View** | Show cost / Show context usage / Search session contents / Last 30 days only / Show archived |
| **Tools** | Claude Code settings… (Ctrl+,) / Workbench options… / Open at startup / Program folder / Error log |
| **Help** | Check for updates / About |

Actions that already have a button (add, rename, reorder, remove, rescan,
launch now) are not duplicated in the menus.

## Language

`Tools → Workbench options… → Display → 語言 / Language`: `auto`, `zh-TW` or
`en`. `auto` follows the Windows interface language. The setting is read once
at startup, so it takes effect the next time the program opens.

The interface was written in Traditional Chinese and the Chinese text is also
the lookup key for the English strings. Anything without an English entry
falls back to the Chinese original rather than showing a blank or raising an
error.

Low-level messages — the untitled-session placeholder, move errors, the
corrupt-config notice and the crash dialog — are translated as well.

## Archiving

Finished sessions can be folded away. Select them and press "Archive" and they
leave the list; `View → Show archived` brings them back, greyed out and marked
"(archived)", and "Unarchive" restores them.

**Archiving only records the session id in `config.json`. No session log is
touched.** It is reversible at any time.

- Archiving a session that is in the launch list asks whether to remove it
  from the launch list as well.
- If a full-text search matches an archived session, the status bar reports
  "· N archived matches not shown".
- The total at the bottom covers every session and does not change with
  archiving.
- Session ids in the archive list that no longer exist match nothing.

Claude Code has no built-in way to archive or hide an individual session. Its
cleanup tool is `claude project purge [folder]`, which deletes all state for
that folder (transcripts, tasks, file edit history, and the folder's entry in
`~/.claude.json`); `--dry-run` previews it and `-i` confirms item by item.

## New session

`File → New session…`. After choosing a folder:

- **Name left empty**: opens a new window immediately, and Claude names the
  session from its contents.
- **Name filled in**: creates the session in the background and fixes the name
  with `/rename` (a few seconds), then adds it to the launch list.

The folder defaults to the home directory, matching where `claude` starts when
run from a terminal. Set `"default_cwd"` in `config.json` to change it; if
that folder does not exist the home directory is used instead.

Creating a named session runs
`claude --session-id <new id> -p "/rename <name>"` once in the background.
`/rename` sets the custom title, which outranks the automatically generated
one, so the name stays fixed as the conversation grows and the tool never has
to rewrite a session log directly.

Sessions in the home directory ask for folder trust on every launch. The
dialog says so when the folder field is the home directory, but does not block
it.

## Rename

**F2** — or the context menu, or the button on the right — edits the row in
place: Enter confirms, Esc cancels.

- **Left pane**: changes the session's name in Claude by running `/rename` in
  the background, so it is visible in the `claude -r` picker too. If that
  session is in the launch list, the window name is updated as well. For a
  session that is currently open, run `/rename` in its own window instead.
- **Right pane**: changes the window name. For a session that is not open, the
  name in Claude is synced automatically; for one that is open, only the window
  name changes and the status bar says so.

## Move to another folder

"Move to another folder…" in the left context menu moves that session's log
into another project folder, so it can be resumed from the `claude -r` picker
there. Within one drive this is a rename, an atomic operation that leaves the
contents unchanged. A session that is currently open is refused; a session in
the launch list has its folder updated to match.

## Cost

The Cost column shows the accumulated spend per session, folder rows show a
subtotal, and the bottom of the window shows the total.
`View → Show cost` turns it off.

How it is calculated: every reply records a `usage` block (input, output,
cache write and cache read token counts) and the model in use, converted at
Anthropic's published rates. Cache reads are the input rate ×0.1, 5-minute
cache writes ×1.25, 1-hour cache writes ×2.

Fast mode (×2 rate, detected from `usage.speed`) and web search ($10 per
thousand) are included. The 1M long-context premium is not, so long-context
sessions can be underestimated.

**These are estimates.** A subscription is not billed per session; the number
is for comparing how much went into each one. Measured against the official
cost recorded in `.claude.json`, sessions completed in a single run agree
within ±11%; sessions spread over several days and several runs come out
higher, because the official field only records the last run.

Implementation note: a single reply is written to the session log several
times while streaming, and roughly half the records are duplicates. Summing
them directly overcounts by two to three times, so records are deduplicated by
message id and the last one is kept.

When a model has no rate data, the cost row is marked **⚠ No pricing data**
rather than falling back to an old rate.

## Full-text search

Off by default; turn it on with `View → Search session contents`. The search
box then matches against the body of every session, and matching rows are
marked "[N hits]".

Searching starts at two characters and is debounced by 0.35 seconds so it does
not rescan on every keystroke. It compares bytes without parsing JSON, and
gets through several hundred MB of logs in about a second.

## Settings editor

`Tools → Claude Code settings…` (Ctrl+,) edits `~\.claude\settings.json`;
`Tools → Workbench options…` edits the workbench's own config.

The Claude Code tabs are: Model and thinking, Subagents (Ultracode),
Permissions, Appearance, Memory and logs, Remote and notifications, Other,
Other settings, Raw JSON.

**The available values are read out of the installed `claude.exe`, not from a
hardcoded list.** The settings shape is embedded in the executable as a zod
schema; the workbench scans it once on open and caches the result by file size
and timestamp. As a result:

- new option values show up in the dropdowns on their own
- settings this version does not recognize are marked ⚠, so an invalid value
  cannot be set by accident
- if the installed version differs from the version the list was checked
  against, the window says so at the top
- keys that are in the file but not on a tab are collected on the
  "Other settings" tab instead of disappearing

There is no temperature field: current models (Opus 5 / 4.8, Sonnet 5,
Fable 5) no longer accept `temperature` or `top_p` and return 400. The
equivalent knob is **effort**, written to the settings file as `low`,
`medium`, `high` or `xhigh` (the CLI's `--effort` also takes `max`).

Safety properties:

- Fields with a fixed set of values are read-only dropdowns and cannot be
  typed into.
- A value already in the file but absent from the list is added to that
  dropdown and preserved.
- Path fields have Browse and Clear buttons; number fields are spinboxes and
  are range-checked on save.
- "(not set)" means the key is not written at all, leaving Claude Code's own
  default in place.
- Only the listed keys are written; everything else in the file is left as it
  was.
- Every save takes a backup first, keeping the last 10.
- "Raw JSON" is the only free-text tab, and the text is validated before it is
  applied.

Changes apply to sessions opened afterwards; windows already open need to be
reopened.

## Start at login

`Tools → Open at startup` writes a `ClaudeWorkbench` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, and deletes it when
unchecked.

- It only touches the current user's key, so no administrator rights and no
  UAC prompt.
- It does not use a Startup-folder shortcut, which can be deleted by accident
  or conflict with cloud sync.
- Each time it is checked, the value is rewritten with the current path, so it
  survives moving the install folder.
- What runs at login is the launcher, not the settings window.

## Single instance

Only one settings window exists at a time. Clicking the shortcut while it is
running brings the existing window to the front (restoring it if minimized)
instead of opening a second one. The launcher has the same protection: a
second process started right after the first exits immediately.

This is not only a cosmetic concern. Each launcher takes a snapshot of which
sessions are already open before it opens any windows, so two running at once
would each decide a session was not open yet, and the same session would end
up in two windows writing to one log.

The lock is a Windows named mutex rather than a lock file, so the operating
system releases it when the process ends and no stale lock is left behind.

## Running detection and skip-if-open

Detection reads Claude Code's runtime registry at
`~\.claude\sessions\<pid>.json` (session id, folder, status) and verifies each
process is still alive before trusting the entry — about 1 ms.
**It covers every interactive window, including ones opened by hand with
`claude -r` from the picker.** On older Claude versions without that
directory, it falls back to comparing process command lines.

Detection runs every 3 seconds, and "Rescan" runs it immediately. A window
newly started from the workbench that has not written its registry entry yet
is protected for 15 seconds; once the entry appears, detection takes over.

Sessions already open are not opened again. When the whole list is already
open, a dialog says so rather than appearing to do nothing.

## What the markers mean

- **⚠ missing** — that session's log no longer exists. Remove the row and pick
  another.
- **⚠ newer continuation exists** — the same folder contains a newer session
  created by a compaction handoff. Long sessions are compacted automatically
  and usually keep the same session id; when the id did change, pick the newer
  session instead.
- **ⓘ trust asked every time** — that session lives in the home directory.

## Why the home directory asks for Trust Folder every time

Claude Code does not store folder trust for the home directory:

> `{folder} is your home directory, and for security home-directory trust is never saved, so running claude here first won't help.`

Other folders are remembered after one confirmation (stored as
`projects[folder].hasTrustDialogAccepted` in `~\.claude.json`), and **trust is
inherited by subfolders** — the search walks up until it finds a trusted
ancestor, bounded by the git repository root. The home directory is the
exception, because agreeing once there would pre-trust everything under it.

## Remembered UI state

Window size and position (including whether it is maximized), each column
width, the sort order, and the "Last 30 days only", "Show cost",
"Show context usage", "Show archived" and full-text-search toggles are written
to the `ui` block of `config.json` when the window closes.

Two exceptions: the search box is not remembered, and the Session column width
is computed to fill the remaining space after the fixed columns, so adjust the
Cost and Last used columns instead.

If the saved coordinates fall off-screen after a monitor or resolution change,
the default size is used.

## Reliability

- **Error log**: unexpected errors are written to
  `%LOCALAPPDATA%\ClaudeWorkbench\workbench.log` and raise a dialog
  (`Tools → Error log` opens it). Exceptions under pythonw no longer vanish
  silently.
- **Corrupt config protection**: if `config.json` cannot be read it is renamed
  to `.corrupt.<timestamp>`, reported, and never overwritten, so the launch
  list is not silently emptied.
- **Backup rotation**: every save backs up to
  `%LOCALAPPDATA%\ClaudeWorkbench\backups`, keeping the last 10 of each;
  `settings.json` is handled the same way.
- **Cache location**: the scan cache and schema cache live in the user data
  directory, so the install folder can be read-only.
- **Regression tests**: `python tests/regression.py` runs against a sandbox
  copy of the config file and does not disturb a workbench in use. 75 checks,
  including the invariant that no session log is ever deleted or truncated.

## Known technical pitfalls

- Calling tkinter's `after()` from a background thread is unreliable — it
  raises `RuntimeError` when the main thread is not in the event loop.
  Everything crossing threads goes through a queue that the main thread drains
  on a timer.

## Files

```
(install folder)
  settings.pyw       settings window
  launch.pyw         launcher
  sessions.py        scans session logs, computes cost, full-text search
  claudeschema.py    reads setting keys and values out of the claude binary
  options.py         settings editor
  i18n.py            interface language and the English strings
  config.py          workbench config file access
  singleton.py       single instance
  startup.py         start at login
  install.py         install and uninstall
  make_icons.py      generates the two .ico files
  appdata.py         data directory and backup rotation
  applog.py          error log and exception hook
  tests/regression.py regression tests
  config.json        launch list, archive list and UI state

(cache, log and backups live in %LOCALAPPDATA%\ClaudeWorkbench)
```

`config.json` holds three more internal parameters that are not exposed in the
interface: `stagger_ms` (delay between windows), `skip_running` (do not open a
session twice) and `claude` (executable location). They rarely need changing.

## Environment variables

When this tool is started from inside Claude Code, new windows inherit markers
such as `CLAUDE_CODE_CHILD_SESSION`, which makes them look like child
processes: they show `Transcript saving is off` and their conversation is
never saved. `launch.pyw` therefore strips the `CLAUDE*` session markers from
the environment before opening a window.

## Known detail

`wt` treats `;` on its command line as a pane split, not an ordinary
character. The current command contains no semicolons; a more elaborate launch
command would need them written as `\;`.

## Internal dependencies

This tool relies on three undocumented Claude Code internals, verified against
2.1.235 on Windows 11:

1. The settings editor scans the zod schema inside `claude.exe` for available
   values; when the scan finds nothing it falls back to a built-in list.
2. Running detection reads the `~\.claude\sessions\<pid>.json` runtime
   registry; when that directory does not exist it falls back to comparing
   process command lines.
3. Cost rates are a hand-maintained table (published pricing, 2026-08).
   Unknown models are marked ⚠ instead of being priced at an old rate, and a
   price change to an existing model is not picked up automatically.

All three degrade rather than break when Claude Code changes. Keeping up with
every Claude Code release is not promised.

## License

MIT, see [LICENSE](LICENSE).
