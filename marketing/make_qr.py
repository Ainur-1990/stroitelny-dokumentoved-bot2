"""QR-карточка «Строительный Документовед»: QR на t.me/dokstrobot.

Выход: marketing/avatar/qr-card.png (1080x1920, для концовки ролика)
       marketing/avatar/qr-square.png (1080x1080, для постов)
"""
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
FONTS = Path(r"C:\Users\Пользователь\Desktop\антив\MoneyPrinterTurbo\resource\fonts")
OUT = BASE / "avatar"
URL = "https://t.me/dokstrobot"

NAVY_TOP, NAVY_BOTTOM = (31, 42, 68), (15, 23, 42)
ORANGE = (245, 158, 11)
WHITE = (248, 250, 252)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    for y in range(h):
        img.paste(lerp(top, bottom, y / h), (0, y, w, y + 1))
    return img


def make_qr(box):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(URL)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    cell = box // (n + 2)
    size = cell * n
    img = Image.new("RGB", (size, size), WHITE)
    d = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                d.rounded_rectangle((x * cell, y * cell, x * cell + cell, y * cell + cell), radius=cell * 0.3, fill=(17, 24, 39))
    return img.resize((box, box), Image.LANCZOS)


def draw_card(w, h, qr_size):
    img = gradient(w, h, NAVY_TOP, NAVY_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)

    # тонкая «чертёжная» сетка
    for x in range(0, w, 90):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 10), width=1)
    for y in range(0, h, 90):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 10), width=1)

    f_title = ImageFont.truetype(str(FONTS / "Montserrat-Black.ttf"), 84)
    f_sub = ImageFont.truetype(str(FONTS / "Montserrat-SemiBold.ttf"), 44)
    f_cta = ImageFont.truetype(str(FONTS / "Montserrat-ExtraBold.ttf"), 52)
    f_nick = ImageFont.truetype(str(FONTS / "Montserrat-SemiBold.ttf"), 58)

    # аватар бота
    avatar = Image.open(OUT / "avatar-dark.png").convert("RGB").resize((240, 240))
    mask = Image.new("L", (240, 240), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 240, 240), radius=60, fill=255)
    ay = int(h * 0.115)
    img.paste(avatar, (w // 2 - 120, ay), mask)
    d.rounded_rectangle((w // 2 - 120, ay, w // 2 + 120, ay + 240), radius=60, outline=ORANGE, width=6)

    def center(text, font, y, fill=WHITE):
        tw = d.textlength(text, font=font)
        d.text(((w - tw) / 2, y), text, font=font, fill=fill)

    center("СТРОИТЕЛЬНЫЙ", f_title, ay + 280)
    center("ДОКУМЕНТОВЕД", f_title, ay + 372)
    center("Telegram-бот для стройки", f_sub, ay + 490, fill=(203, 213, 225))

    # QR на белой карточке
    card = qr_size + 72
    cy = ay + 600
    d.rounded_rectangle((w // 2 - card // 2, cy, w // 2 + card // 2, cy + card), radius=48, fill=WHITE)
    qr = make_qr(qr_size)
    img.paste(qr, (w // 2 - qr_size // 2, cy + 36))

    center("НАВЕДИ КАМЕРУ —", f_cta, cy + card + 46, fill=ORANGE)
    center("БОТ ОТКРОЕТСЯ", f_cta, cy + card + 112, fill=ORANGE)
    center("@dokstrobot", f_nick, cy + card + 220)

    center("3 обработки бесплатно • PDF, DOCX и фото", f_sub, h - 130, fill=(148, 163, 184))
    return img.convert("RGB")


OUT.mkdir(exist_ok=True)
draw_card(1080, 1920, 640).save(OUT / "qr-card.png")
square = draw_card(1080, 1080, 520)
square.save(OUT / "qr-square.png")
print("saved:", OUT / "qr-card.png", "и qr-square.png")
