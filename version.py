# -*- coding: utf-8 -*-
__version__ = "1.7.0"
REPO = "Moaijay/claude-workbench"


def parse(v):
    """版本字串轉成可比較的整數 tuple；非數字片段當 0。"""
    out = []
    for part in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)
