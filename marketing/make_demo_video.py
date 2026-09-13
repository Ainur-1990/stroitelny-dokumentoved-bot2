"""Демо-видео работы бота @dokstrobot: анимированный чат Telegram (1080x1920, 30fps).

Реальные ответы бота из E2E. Кадры PIL -> ffmpeg -> marketing/avatar/demo_raw.mp4
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
AVATAR_DIR = BASE / "avatar"
FRAMES = AVATAR_DIR / "demo_frames"
FONTS = Path(r"C:\Users\Пользователь\Desktop\антив\MoneyPrinterTurbo\resource\fonts")

W, H = 1080, 1920
FPS = 30
DURATION = 36.0
BG_TOP, BG_BOTTOM = (26, 36, 60), (12, 18, 34)
TG_BG = (14, 22, 33)          # фон чата Telegram dark
USER_BUBBLE = (43, 82, 120)   # исходящее
BOT_BUBBLE = (24, 37, 51)     # входящее
TEXT_MAIN = (233, 238, 245)
TEXT_DIM = (124, 138, 155)
ACCENT = (94, 181, 247)
ORANGE = (245, 158, 11)
WHITE = (248, 250, 252)

PHONE = dict(x=100, y=70, w=880, h=1780, r=72)
STATUS_H = 64
HEADER_H = 118
INPUT_H = 112
BUBBLE_MAX_W = 600
AVATAR_IMG = Image.open(AVATAR_DIR / "avatar-dark.png").convert("RGB")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


F_TIME = font("Montserrat-SemiBold.ttf", 30)
F_NAME = font("Montserrat-SemiBold.ttf", 40)
F_SUB = font("Montserrat-SemiBold.ttf", 27)
F_TEXT = font("Montserrat-SemiBold.ttf", 31)
F_BOLD = font("Montserrat-ExtraBold.ttf", 31)
F_SMALL = font("Montserrat-SemiBold.ttf", 25)
F_FILE = font("Montserrat-SemiBold.ttf", 31)


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split():
        cand = (line + " " + word).strip()
        if draw.textlength(cand, font=fnt) <= max_w or not line:
            line = cand
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


# ---------- сообщения (реальные ответы бота, сокращённо для ролика) ----------

MESSAGES = [
    dict(who="user", t=0.6, text="/start"),
    dict(who="bot", t=1.8, typing=0.9, text="Здравствуйте! Я «Строительный Документовед» — помощник по строительному документообороту.\n\nПришлите PDF, DOCX или фото документа — я подготовлю черновик проверки."),
    dict(who="user", t=6.4, file=("КС-2_сентябрь.pdf", "PDF • 214 КБ")),
    dict(who="bot", t=7.8, typing=1.3, text="Тип: акт приёмки работ (КС-2)\nОбъёмы: штукатурка 120 м², стяжка 95 м²\n\nЧего не хватает: ссылок на ЛСР и подписи заказчика\n\nЧерновик проверки готов — финальную сверку делает специалист"),
    dict(who="user", t=17.5, text="Нужен ремонт квартиры 54 м², стены и пол, бюджет 500 тысяч. Мой телефон +79171234567"),
    dict(who="bot", t=19.2, typing=1.4, text="Карточка заявки:\nОбъект: квартира\nПлощадь: 54 м²\nРаботы: стены и пол\nБюджет: 500 000 ₽\nКонтакт: +79171234567\n\nПередана менеджеру"),
    dict(who="bot", t=26.0, typing=1.1, text="Первые 3 обработки — бесплатно.\nОтправьте свой документ прямо сейчас"),
]


def message_height(draw, msg):
    pad = 26
    if "file" in msg:
        return 132 + pad * 2
    fnt = F_TEXT
    total = 0
    for raw in msg["text"].split("\n"):
        lines = wrap(draw, raw, fnt, BUBBLE_MAX_W - pad * 2 - 24) if raw else [""]
        total += len(lines) * 44 + (10 if raw else 4)
    return total + pad * 2 - 6


def draw_bubble(img, msg, x, y, alpha=255, appear=1.0):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    h = message_height(d, msg)
    pad = 26
    is_user = msg["who"] == "user"
    bw = BUBBLE_MAX_W
    bx = PHONE["x"] + PHONE["w"] - 30 - bw if is_user else PHONE["x"] + 30
    y = y + int((1 - appear) * 26)
    box = (bx, y, bx + bw, y + h)
    rounded(d, box, 26, fill=USER_BUBBLE if is_user else BOT_BUBBLE)
    inner = (bx + pad, y + pad)

    if "file" in msg:
        name, meta = msg["file"]
        ix, iy = inner[0], inner[1]
        # иконка документа: лист с загнутым углом
        fx0, fy0, fx1, fy1 = ix, iy + 6, ix + 88, iy + 104
        fold = 26
        d.polygon([(fx0, fy0), (fx1 - fold, fy0), (fx1, fy0 + fold), (fx1, fy1), (fx0, fy1)], fill=(38, 58, 84))
        d.polygon([(fx1 - fold, fy0), (fx1, fy0 + fold), (fx1 - fold, fy0 + fold)], fill=(52, 76, 110))
        for li in range(3):
            ly = fy0 + 40 + li * 18
            d.rounded_rectangle((fx0 + 18, ly, fx1 - 18, ly + 7), radius=3, fill=(148, 170, 197))
        d.text((ix + 116, iy + 20), name[:28], font=F_FILE, fill=TEXT_MAIN)
        d.text((ix + 116, iy + 66), meta, font=F_SMALL, fill=TEXT_DIM)
    else:
        cy = inner[1]
        for raw in msg["text"].split("\n"):
            bold = raw.strip().startswith(("Карточка", "Тип:", "Объём", "Чего")) or raw.endswith(":") and len(raw) < 40
            fnt = F_BOLD if bold else F_TEXT
            color = WHITE if bold else TEXT_MAIN
            if raw.strip() == "":
                cy += 12
                continue
            for line in wrap(d, raw, fnt, bw - pad * 2 - 24):
                d.text((inner[0], cy), line, font=fnt, fill=color)
                cy += 44
            cy += 8
        # время + галочки
        tw = d.textlength("21:47", font=F_SMALL)
        tx = box[2] - tw - 20
        d.text((tx, y + h - 38), "21:47", font=F_SMALL, fill=(170, 185, 202))
        if is_user:
            d.line([(tx - 40, y + h - 22), (tx - 33, y + h - 15), (tx - 22, y + h - 30)], fill=ACCENT, width=4, joint="curve")

    if alpha < 255:
        overlay.putalpha(overlay.getchannel("A").point(lambda a: a * alpha // 255))
    img.alpha_composite(overlay)


def draw_typing(img, t):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    bx, by = PHONE["x"] + 30, 0
    box = (bx, by, bx + 170, by + 88)
    rounded(d, box, 26, fill=BOT_BUBBLE)
    for i in range(3):
        phase = (t * 2.4 + i * 0.55) % (2 * math.pi)
        a = int(120 + 110 * (0.5 + 0.5 * math.sin(phase)))
        r = 10
        cx = bx + 48 + i * 38
        cy = by + 44
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(200, 212, 226, a))
    img.alpha_composite(overlay)


def build_bg():
    img = Image.new("RGBA", (W, H))
    for y in range(H):
        img.paste(lerp(BG_TOP, BG_BOTTOM, y / H), (0, y, W, y + 1))
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, 90):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 9))
    for y in range(0, H, 90):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 9))
    return img


def build_phone_shell(bg):
    """Корпус телефона + статусбар + хедер + поле ввода (статика)."""
    p = PHONE
    d = ImageDraw.Draw(bg, "RGBA")
    d.rounded_rectangle((p["x"] - 6, p["y"] - 6, p["x"] + p["w"] + 6, p["y"] + p["h"] + 6), radius=p["r"] + 6, fill=(0, 0, 0, 90))
    rounded(d, (p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]), p["r"], fill=TG_BG, outline=(255, 255, 255, 46), width=3)

    # статусбар
    d.text((p["x"] + 44, p["y"] + 16), "21:47", font=F_TIME, fill=WHITE)
    for i in range(4):
        bh = 8 + i * 6
        d.rounded_rectangle((p["x"] + p["w"] - 150 + i * 16, p["y"] + 44 - bh, p["x"] + p["w"] - 140 + i * 16, p["y"] + 44), radius=2, fill=(255, 255, 255, 210))
    d.rounded_rectangle((p["x"] + p["w"] - 76, p["y"] + 18, p["x"] + p["w"] - 26, p["y"] + 48), radius=10, outline=(255, 255, 255, 210), width=3)
    d.rounded_rectangle((p["x"] + p["w"] - 72, p["y"] + 22, p["x"] + p["w"] - 40, p["y"] + 44), radius=6, fill=(255, 255, 255, 210))

    # хедер чата
    hy = p["y"] + STATUS_H
    d.line([(p["x"], hy + HEADER_H), (p["x"] + p["w"], hy + HEADER_H)], fill=(255, 255, 255, 18), width=2)
    d.text((p["x"] + 34, hy + 42), "‹", font=font("Montserrat-Black.ttf", 46), fill=TEXT_MAIN)
    av = AVATAR_IMG.resize((78, 78))
    mask = Image.new("L", (78, 78), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 78, 78), radius=24, fill=255)
    bg.paste(av, (p["x"] + 86, hy + 20), mask)
    d.text((p["x"] + 182, hy + 18), "Документовед", font=F_NAME, fill=WHITE)
    d.text((p["x"] + 182, hy + 68), "bot", font=F_SUB, fill=ACCENT)

    # поле ввода
    iy = p["y"] + p["h"] - INPUT_H
    d.rounded_rectangle((p["x"] + 26, iy + 22, p["x"] + p["w"] - 130, iy + 90), radius=34, fill=(24, 37, 51))
    d.text((p["x"] + 56, iy + 40), "Сообщение", font=F_TEXT, fill=TEXT_DIM)
    d.ellipse((p["x"] + p["w"] - 106, iy + 28, p["x"] + p["w"] - 44, iy + 90), outline=TEXT_DIM, width=3)
    d.rounded_rectangle((p["x"] + p["w"] - 88, iy + 46, p["x"] + p["w"] - 62, iy + 72), radius=8, fill=TEXT_DIM)
    return hy + HEADER_H


def render_frame(t, chat_top, bg, template):
    img = bg.copy()
    d = ImageDraw.Draw(img, "RGBA")
    p = PHONE
    chat_bottom = p["y"] + p["h"] - INPUT_H - 16

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    visible = []
    for msg in MESSAGES:
        if msg["t"] <= t:
            visible.append(msg)

    total = sum(message_height(d, m) + 18 for m in visible)
    offset = max(0, total - (chat_bottom - chat_top))

    y_cursor = chat_top - offset
    for msg in visible:
        appear = min(1.0, max(0.0, (t - msg["t"]) / 0.28))
        alpha = int(255 * min(1.0, max(0.0, (t - msg["t"]) / 0.2)))
        draw_bubble(layer, msg, 0, y_cursor, alpha=alpha, appear=appear)
        y_cursor += message_height(d, msg) + 18

    for msg in MESSAGES:
        if msg["who"] == "bot" and "typing" in msg and 0 < msg["t"] - t <= msg["typing"]:
            draw_typing(layer, t)

    # клип по области чата: ничего не заезжает на хедер и поле ввода
    clipped = layer.crop((p["x"], chat_top, p["x"] + p["w"], chat_bottom))
    img.alpha_composite(clipped, (p["x"], chat_top))
    return img


def main():
    FRAMES.mkdir(exist_ok=True)
    bg = build_bg()
    chat_top = build_phone_shell(bg)
    total_frames = int(DURATION * FPS)
    for n in range(total_frames):
        t = n / FPS
        frame = render_frame(t, chat_top, bg, None)
        frame.convert("RGB").save(FRAMES / f"frame_{n:04d}.jpg", quality=90)
        if n % 150 == 0:
            print(f"frame {n}/{total_frames}")
    out = AVATAR_DIR / "demo_raw.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(FRAMES / "frame_%04d.jpg"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        str(out),
    ], check=True, capture_output=True)
    print("saved", out)


if __name__ == "__main__":
    main()
