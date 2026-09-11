import os
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("construction-doc-bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
POLLING_MODE = os.getenv("POLLING_MODE", "false").lower() == "true"

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

api = FastAPI(title="Строительный Документовед")
telegram_app = Application.builder().token(TOKEN).build()


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить документ", callback_data="check")],
        [InlineKeyboardButton("Заявка на строительные работы", callback_data="lead")],
        [InlineKeyboardButton("Возможности и ограничения", callback_data="about")],
    ])


WELCOME = (
    "Здравствуйте! Я «Строительный Документовед» — помощник по строительному документообороту.\n\n"
    "Я принимаю документы и сообщения, помогаю собрать данные для проверки и подготовить черновик. "
    "Финальная проверка и утверждение всегда остаются за специалистом.\n\n"
    "Выберите действие или отправьте документ."
)

ABOUT = (
    "Что умеет MVP:\n"
    "• принять PDF, DOCX, фото или текст;\n"
    "• собрать исходные данные для OCR/проверки;\n"
    "• квалифицировать заявку клиента;\n"
    "• сформировать структурированный отчёт.\n\n"
    "Ограничения:\n"
    "• не рассчитываю конструкции;\n"
    "• не подписываю документы;\n"
    "• не даю юридических заключений;\n"
    "• не выдумываю цены, объёмы и нормативы без загруженной базы."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, reply_markup=menu())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n/start — начать работу\n/help — помощь\n/status — состояние бота\n\n"
        "Для проверки отправьте документ или опишите задачу текстом.", reply_markup=menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает. Режим безопасного MVP активен.")


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lower = text.lower()
    if any(word in lower for word in ["заявка", "строительство", "ремонт", "дом", "квартира"]):
        await update.message.reply_text(
            "Чтобы передать заявку менеджеру, напишите:\n"
            "1) тип объекта; 2) площадь; 3) виды работ; 4) бюджет; 5) желаемые сроки; 6) контакт.\n\n"
            "После этого я подготовлю структурированную карточку лида."
        )
        return
    await update.message.reply_text(
        "Запрос принят. Для точной проверки нужны загруженные исходные документы или база знаний. "
        "Я не буду придумывать отсутствующие цены, объёмы или нормативные ссылки.\n\n"
        "Отправьте файл и укажите, что проверить: реквизиты, комплектность, даты, смету или расхождения.",
        reply_markup=menu(),
    )


async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item = update.message.document or update.message.photo[-1]
    name = getattr(item, "file_name", "изображение")
    await update.message.reply_text(
        f"Файл «{name}» получен.\n\n"
        "Результат MVP: входные данные собраны, но автоматический OCR/RAG ещё не подключён. "
        "Для достоверного анализа подключите согласованный OCR и загрузите базу знаний. "
        "Я не буду выдумывать распознанные реквизиты или нормативы.\n\n"
        "Проверка специалистом обязательна.",
        reply_markup=menu(),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "about":
        await query.edit_message_text(ABOUT, reply_markup=menu())
    elif query.data == "check":
        await query.edit_message_text(
            "Отправьте PDF, DOCX, фото или текстом перечислите, что нужно проверить. "
            "Например: «сверь объёмы КС-2 с ЛСР».", reply_markup=menu()
        )
    elif query.data == "lead":
        await query.edit_message_text(
            "Опишите заявку: объект, площадь, работы, бюджет, сроки и контакт. "
            "Я подготовлю черновик карточки для менеджера.", reply_markup=menu()
        )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_cmd))
telegram_app.add_handler(CommandHandler("status", status))
telegram_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, document_message))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
telegram_app.add_handler(CallbackQueryHandler(callback))


@api.get("/")
async def root():
    return {"service": "construction-doc-bot", "status": "ok"}


@api.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@api.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid webhook secret")
    payload = await request.json()
    await telegram_app.process_update(Update.de_json(payload, telegram_app.bot))
    return JSONResponse({"ok": True})


@api.post("/setup-webhook")
async def setup_webhook(x_setup_secret: str | None = Header(default=None)):
    if not WEBHOOK_SECRET or x_setup_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid setup secret")
    if not PUBLIC_BASE_URL:
        raise HTTPException(status_code=400, detail="PUBLIC_BASE_URL is required")
    url = f"{PUBLIC_BASE_URL}/telegram/webhook"
    result = await telegram_app.bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    return {"ok": result, "url": url}


@api.on_event("startup")
async def startup():
    await telegram_app.initialize()
    if POLLING_MODE:
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)


@api.on_event("shutdown")
async def shutdown():
    if POLLING_MODE:
        await telegram_app.updater.stop()
        await telegram_app.stop()
    await telegram_app.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:api", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
