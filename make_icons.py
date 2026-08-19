# -*- coding: utf-8 -*-
"""產生兩個 .ico：launcher.ico（疊起來的終端機視窗）、settings.ico（視窗＋調整桿）。

重跑就會覆蓋，形狀要調的話改這裡再執行一次即可。
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 512                      # 先畫大張再縮，邊緣才乾淨
BG = (31, 36, 48, 255)       # 深板岩藍，淺色與深色工作列上都看得清
FRAME = (222, 228, 238, 255)
DIM = (120, 132, 152, 255)
ACCENT = (217, 119, 87, 255)  # 暖橘，呼應 Claude
SIZES = [256, 128, 64, 48, 32, 24, 20, 16]


def base():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 16, S - 16, S - 16], radius=96, fill=BG)
    return img, d


def window(d, box, frame=FRAME, width=14, bar=True):
    """畫一個終端機視窗外框。"""
    d.rounded_rectangle(box, radius=26, outline=frame, width=width)
    if bar:
        x0, y0, x1, _ = box
        d.line([x0 + width, y0 + 54, x1 - width, y0 + 54], fill=frame,
               width=width)


def caret(d, x, y, size, color=ACCENT, width=20):
    """終端機的 > 提示符號，加一條底線。"""
    d.line([(x, y - size), (x + size, y), (x, y + size)], fill=color,
           width=width, joint="curve")
    d.line([(x + size + 26, y + size), (x + size + 26 + size * 1.4, y + size)],
           fill=color, width=width)


def launcher_icon():
    img, d = base()
    # 後面兩層，代表「好幾個視窗」
    window(d, [150, 88, 452, 300], frame=DIM, width=12, bar=False)
    window(d, [116, 122, 418, 334], frame=DIM, width=12, bar=False)
    # 前景那個是真正的終端機
    d.rounded_rectangle([74, 158, 438, 400], radius=26, fill=BG)
    window(d, [74, 158, 438, 400])
    caret(d, 148, 306, 46)
    return img


def settings_icon():
    img, d = base()
    window(d, [92, 108, 420, 404])
    # 三根調整桿
    y = 214
    for handle in (300, 200, 356):
        d.line([132, y, 380, y], fill=DIM, width=14)
        d.ellipse([handle - 26, y - 26, handle + 26, y + 26], fill=ACCENT)
        y += 76
    return img


def save(img, name):
    path = os.path.join(HERE, name)
    frames = [img.resize((n, n), Image.LANCZOS) for n in SIZES]
    frames[0].save(path, format="ICO",
                   sizes=[(n, n) for n in SIZES], append_images=frames[1:])
    return path


if __name__ == "__main__":
    for maker, name in ((launcher_icon, "launcher.ico"),
                        (settings_icon, "settings.ico")):
        p = save(maker(), name)
        print("已產生", p, os.path.getsize(p), "bytes")
        # 順便存一張大圖方便檢視
        maker().save(p.replace(".ico", "_preview.png"))
