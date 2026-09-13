"""SRT для рекламного ролика: реплики по пунктуации, тайминги пропорционально
длине фраз поверх длительности озвучки (voice.mp3).
"""
import re
import subprocess
from pathlib import Path

AV = Path(__file__).resolve().parent / "avatar"
VOICE = AV / "voice.mp3"
SRT = AV / "voice.srt"

SCRIPT = (
    "Акт снова потерялся в чатах, а заявку клиент прислал голосовым? "
    "Встречайте «Строительный Документовед» — Telegram-бот для стройки. "
    "Отправьте PDF или фото документа: бот распознает КС-2, акт или смету "
    "и подготовит черновик проверки — что указано, чего не хватает, какие вопросы задать. "
    "Клиент пишет заявку — бот сам собирает карточку для менеджера. "
    "Первые три обработки бесплатно. Сканируйте QR-код и запускайте. "
    "Строительный Документовед — порядок в документах."
)

MAX_CHARS = 46


def split_cues(text):
    parts = re.split(r"(?<=[.!?…;:])\s+", text.replace("\n", " "))
    cues = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= MAX_CHARS:
            cues.append(part)
            continue
        words, line = [], ""
        for word in part.split():
            cand = (line + " " + word).strip()
            if len(cand) <= MAX_CHARS or not line:
                line = cand
            else:
                words.append(line)
                line = word
        if line:
            words.append(line)
        cues.extend(words)
    return cues


def fmt(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(VOICE)],
        capture_output=True, text=True, check=True).stdout.strip())
    cues = split_cues(SCRIPT)
    total_weight = sum(len(c) + 6 for c in cues)  # +6: пауза после фразы
    t = 0.0
    blocks = []
    for i, cue in enumerate(cues, 1):
        span = (len(cue) + 6) / total_weight * dur
        start, end = t, min(t + span * 0.96, dur)
        if end - start < 0.9:
            end = min(start + 0.9, dur)
        blocks.append(f"{i}\n{fmt(start)} --> {fmt(end)}\n{cue}\n")
        t += span
    SRT.write_text("\n".join(blocks), encoding="utf-8")
    print(f"длительность {dur:.1f}s, фраз {len(cues)}, srt -> {SRT}")


if __name__ == "__main__":
    main()
