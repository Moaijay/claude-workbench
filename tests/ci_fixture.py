# -*- coding: utf-8 -*-
"""CI 佈景：在乾淨的執行環境（如 GitHub Actions）偽造測試所需的最小狀態。

回歸測試假設機器上有真實的 Claude Code 資料：~/.claude/projects 底下的
對話紀錄、~/.claude/settings.json、以及工作台自己的 config.json。
本機已有這些時，這支什麼都不做；絕不覆寫任何既有檔案。
"""
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
PROJECTS = os.path.join(CLAUDE, "projects")


def make_session(proj_dir, tokens, title):
    """一條帶計費回覆的最小對話紀錄，回傳 session id。"""
    sid = str(uuid.uuid4())
    recs = [
        {"type": "user", "cwd": HERE,
         "timestamp": "2026-01-01T00:00:00.000Z",
         "message": {"role": "user", "content": "fixture 第一句"}},
        {"type": "assistant", "cwd": HERE,
         "timestamp": "2026-01-01T00:00:05.000Z",
         "message": {"id": "m-" + sid[:8], "model": "claude-opus-5",
                     "role": "assistant",
                     "usage": {"input_tokens": tokens, "output_tokens": 10},
                     "content": [{"type": "text", "text": "fixture 回覆"}]}},
        {"type": "custom-title", "customTitle": title},
    ]
    with open(os.path.join(proj_dir, sid + ".jsonl"), "w",
              encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return sid


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if os.path.isdir(PROJECTS):
        print("~/.claude/projects 已存在，不佈景")
        return

    proj = os.path.join(PROJECTS, "wbci-fixture")
    os.makedirs(proj)
    # 四條不同金額的對話：排序、批次開啟（選三條）等測試都吃這組
    sids = [make_session(proj, 1_000_000 * (i + 1), "fixture-%d" % i)
            for i in range(4)]
    print("已建立測試對話：%s" % ", ".join(s[:8] for s in sids))

    settings = os.path.join(CLAUDE, "settings.json")
    if not os.path.exists(settings):
        with open(settings, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        print("已建立空的 settings.json")

    cfg_path = os.path.join(HERE, "config.json")
    if not os.path.exists(cfg_path):
        cfg = {"items": [{"name": "fixture", "cwd": HERE,
                          "session": sids[0]}],
               "archived": ["arch-%d" % i for i in range(4)]}
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        print("已建立工作台 config.json")


if __name__ == "__main__":
    main()
