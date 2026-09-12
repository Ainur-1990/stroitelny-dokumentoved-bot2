"""Генерация аватарки бота «Строительный Документовед» (1024x1024, круг-безопасно).

Тема: лист документа с чек-маркой «проверено» на тёмно-синем градиенте,
оранжевый акцент — строительный. Запуск: python marketing/make_avatar.py
"""
from PIL import Image, ImageDraw
from pathlib import Path

W = 1024
OUT = Path(__file__).resolve().parent / "avatar"
OUT.mkdir(exist_ok=True)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(top, bottom):
    img = Image.new("RGB", (W, W))
    for y in range(W):
        img.paste(lerp(top, bottom, y / W), (0, y, W, y + 1))
    return img


def draw_avatar(bg_top, bg_bottom, sheet, lines, accent, dark=False):
    img = gradient(bg_top, bg_bottom).convert("RGBA")
    overlay = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # мягкое свечение позади листа
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((132, 132, W - 132, W - 132), fill=(255, 255, 255, 26))
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    overlay = Image.alpha_composite(overlay, glow)
    d = ImageDraw.Draw(overlay)

    # лист документа
    sx0, sy0, sx1, sy1 = 288, 232, 736, 792
    fold = 108
    d.polygon([(sx0, sy0), (sx1 - fold, sy0), (sx1, sy0 + fold), (sx1, sy1), (sx0, sy1)], fill=sheet)
    d.polygon([(sx1 - fold, sy0), (sx1, sy0 + fold), (sx1 - fold, sy0 + fold)], fill=lerp3(sheet, (160, 172, 192)))
    d.line([(sx1 - fold, sy0), (sx1 - fold, sy0 + fold), (sx1, sy0 + fold)], fill=(203, 213, 225), width=6)

    # строки текста
    for i in range(4):
        ly = sy0 + 118 + i * 108
        lw = [276, 340, 300, 216][i]
        d.rounded_rectangle((sx0 + 56, ly, sx0 + 56 + lw, ly + 34), radius=17, fill=lines)

    # чек-марка «проверено»
    cx, cy, r = 700, 712, 128
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent, outline=(255, 255, 255, 230), width=18)
    d.line([(cx - 58, cy + 4), (cx - 14, cy + 52), (cx + 66, cy - 46)], fill=(255, 255, 255), width=26, joint="curve")

    img = Image.alpha_composite(img, overlay).convert("RGB")
    if dark:
        img = Image.eval(img, lambda p: p)  # вариант без инверсии — просто другой фон
    return img


def lerp3(c1, c2, t=0.35):
    return lerp(c1, c2, t)


base = draw_avatar((31, 42, 68), (15, 23, 42), (248, 250, 252), (203, 213, 225), (245, 158, 11))
base.save(OUT / "avatar-dark.png", quality=95)

light = draw_avatar((226, 232, 240), (193, 205, 221), (255, 255, 255), (148, 163, 184), (234, 88, 12))
light.save(OUT / "avatar-light.png", quality=95)

print("saved", OUT / "avatar-dark.png", "and avatar-light.png")
