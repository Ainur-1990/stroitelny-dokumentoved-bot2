"""E2E на фейковых апдейтах: хендлеры, БД и вызов ИИ настоящие (ключ из .env),
отправка в Telegram подменяется захватом текстов. Запуск: python tests/e2e_fake.py
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "e2e.sqlite3")
os.environ["POLLING_MODE"] = "false"
os.environ.pop("PUBLIC_BASE_URL", None)

import telegram  # noqa: E402
from telegram import Update  # noqa: E402

SENT: list[str] = []


async def fake_reply(self, text, **kwargs):
    SENT.append(str(text))
    return None


async def fake_action(self, action, **kwargs):
    return None


async def fake_bot_send(self, chat_id, text, **kwargs):
    SENT.append(f"[to {chat_id}] {text}")
    return None


async def fake_get_me(self, *args, **kwargs):
    from telegram import User
    return User(id=1, is_bot=True, first_name="Документовед", username="dokstrobot")


telegram.Message.reply_text = fake_reply  # type: ignore[method-assign]
telegram.Message.reply_invoice = fake_reply  # type: ignore[method-assign]
telegram.Chat.send_action = fake_action  # type: ignore[method-assign]
telegram.Bot.send_message = fake_bot_send  # type: ignore[method-assign]
telegram.Bot.get_me = fake_get_me  # type: ignore[method-assign]

import app  # noqa: E402  (после подмен — импорт регистрирует хендлеры)

USER_ID = 700001
_update_id = 500000


def make_update(text: str) -> Update:
    global _update_id
    _update_id += 1
    message = {
        "message_id": _update_id,
        "date": int(time.time()),
        "chat": {"id": USER_ID, "type": "private"},
        "from": {"id": USER_ID, "is_bot": False, "first_name": "Тест", "username": "tester"},
        "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"offset": 0, "length": len(text.split()[0]), "type": "bot_command"}]
    return Update.de_json({"update_id": _update_id, "message": message}, app.telegram_app.bot)


async def main():
    assert app.SERVER_AI, "нет серверного ИИ — проверь AI_* в .env"
    await app.telegram_app.initialize()
    app.telegram_app.bot._bot_user = await app.telegram_app.bot.get_me()

    await app.telegram_app.process_update(make_update("/start"))
    await app.telegram_app.process_update(make_update("/status"))
    await app.telegram_app.process_update(make_update("Сверь объёмы КС-2 с локальной сметой, вот данные: позиция 1 штукатурка 120 м2, позиция 2 стяжка 95 м2. Что проверить?"))
    await app.telegram_app.process_update(make_update("хочу заявку на строительство"))
    await app.telegram_app.process_update(make_update("Нужно отремонтировать квартиру 54 м2, стены и пол, бюджет 500 тысяч, до конца ноября, телефон +79171234567, зовут Айрат"))
    await app.telegram_app.process_update(make_update("/status"))

    conn = app.db()
    user = conn.execute("SELECT quota FROM users WHERE user_id=?", (USER_ID,)).fetchone()
    leads = conn.execute("SELECT object_type, contact, raw_text FROM leads").fetchall()
    conn.close()

    print("\n=== что бот ответил ===")
    for text in SENT:
        print("-", text.replace("\n", " | ")[:240])
    print("\n=== состояние ===")
    print("quota:", user["quota"] if user else None, "(ожидалось 2: одна ИИ-обработка текста)")
    print("leads:", [(l["object_type"], l["contact"]) for l in leads])
    ok_quota = user and user["quota"] == 2
    ok_lead = leads and leads[0]["contact"] and "54" in (leads[0]["raw_text"] or "")
    ok_analysis = any("Осталось обработок: 2" in s for s in SENT)
    ok_lead_card = any("Карточка заявки" in s and "не указано" in s for s in SENT)
    ok_status = any("Бесплатный режим: осталось обработок — 2" in s for s in SENT)
    print("\nRESULT:", "PASS" if all([ok_quota, ok_lead, ok_analysis, ok_lead_card, ok_status]) else f"FAIL quota={ok_quota} lead={ok_lead} analysis={ok_analysis} card={ok_lead_card} status={ok_status}")
    await app.telegram_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
