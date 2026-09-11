import os
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, ContextTypes, filters,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("construction-doc-bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
POLLING_MODE = os.getenv("POLLING_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "bot.sqlite3")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

api = FastAPI(title="Строительный Документовед")
telegram_app = Application.builder().token(TOKEN).build()

# Цифровые услуги внутри Telegram оплачиваются Stars (валюта XTR).
PLANS = {
    "start": {"title": "Старт", "stars": 99, "docs": 10, "days": 30, "description": "10 обработок документов и заявок на 30 дней"},
    "pro": {"title": "Прораб", "stars": 299, "docs": 50, "days": 30, "description": "50 обработок документов и заявок на 30 дней"},
    "business": {"title": "Компания", "stars": 799, "docs": 200, "days": 30, "description": "200 обработок документов и заявок на 30 дней"},
}

WELCOME = (
    "Здравствуйте! Я «Строительный Документовед» — помощник по строительному документообороту.\n\n"
    "Я принимаю документы и сообщения, помогаю собрать данные для проверки и подготовить черновик. "
    "Финальная проверка и утверждение всегда остаются за специалистом.\n\n"
    "В бесплатном режиме доступна пробная обработка. Для большего объёма выберите тариф."
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


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, plan TEXT, expires_at TEXT, quota INTEGER NOT NULL DEFAULT 3)")
    conn.execute("CREATE TABLE IF NOT EXISTS payments (charge_id TEXT PRIMARY KEY, user_id INTEGER, plan TEXT, stars INTEGER, paid_at TEXT)")
    conn.commit()
    return conn


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить документ", callback_data="check")],
        [InlineKeyboardButton("Тарифы и оплата", callback_data="plans")],
        [InlineKeyboardButton("Заявка на строительные работы", callback_data="lead")],
        [InlineKeyboardButton("Возможности и ограничения", callback_data="about")],
    ])


def plans_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Старт — 99 ⭐", callback_data="buy:start")],
        [InlineKeyboardButton("Прораб — 299 ⭐", callback_data="buy:pro")],
        [InlineKeyboardButton("Компания — 799 ⭐", callback_data="buy:business")],
        [InlineKeyboardButton("Назад", callback_data="home")],
    ])


def plan_text():
    return (
        "Тарифы на 30 дней:\n\n"
        "Старт — 99 ⭐\n10 обработок документов и заявок.\n\n"
        "Прораб — 299 ⭐\n50 обработок. Выгоднее для регулярной работы.\n\n"
        "Компания — 799 ⭐\n200 обработок для команды.\n\n"
        "Оплата цифровой услуги проходит внутри Telegram Stars. Перед оплатой проверьте название тарифа и количество обработок."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db().close()
    await update.message.reply_text(WELCOME, reply_markup=menu())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n/start — начать работу\n/help — помощь\n/status — состояние и остаток\n/plans — тарифы\n\n"
        "Для проверки отправьте документ или опишите задачу текстом.", reply_markup=menu()
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    row = conn.execute("SELECT plan, expires_at, quota FROM users WHERE user_id=?", (update.effective_user.id,)).fetchone()
    conn.close()
    if not row:
        text = "Бесплатный режим: доступно 3 пробные обработки."
    else:
        text = f"Тариф: {row['plan'] or 'Бесплатный'}\nОстаток обработок: {row['quota']}\nДо: {row['expires_at'] or 'без срока'}"
    await update.message.reply_text(text, reply_markup=menu())


async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plan_text(), reply_markup=plans_menu())


async def send_plan_invoice(query, plan_key: str):
    plan = PLANS[plan_key]
    payload = f"plan:{plan_key}:user:{query.from_user.id}"
    await query.message.reply_invoice(
        title=f"Тариф «{plan['title']}»",
        description=plan["description"],
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(plan["title"], plan["stars"])],
        start_parameter=f"buy-{plan_key}",
    )


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
    conn = db()
    row = conn.execute("SELECT quota FROM users WHERE user_id=?", (update.effective_user.id,)).fetchone()
    quota = row["quota"] if row else 3
    if quota <= 0:
        conn.close()
        await update.message.reply_text("Пробный лимит исчерпан. Откройте «Тарифы и оплата», чтобы продолжить.", reply_markup=plans_menu())
        return
    if row:
        conn.execute("UPDATE users SET quota=quota-1 WHERE user_id=?", (update.effective_user.id,))
    else:
        conn.execute("INSERT INTO users(user_id, quota) VALUES(?, 2)", (update.effective_user.id,))
    conn.commit(); conn.close()
    await update.message.reply_text(
        f"Файл «{name}» получен. Использована 1 обработка.\n\n"
        "Результат MVP: входные данные собраны, но автоматический OCR/RAG ещё не подключён. "
        "Для достоверного анализа подключите согласованный OCR и загрузите базу знаний. "
        "Я не буду выдумывать распознанные реквизиты или нормативы.\n\n"
        "Проверка специалистом обязательна.", reply_markup=menu())


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "about":
        await query.edit_message_text(ABOUT, reply_markup=menu())
    elif query.data == "plans":
        await query.edit_message_text(plan_text(), reply_markup=plans_menu())
    elif query.data == "home":
        await query.edit_message_text(WELCOME, reply_markup=menu())
    elif query.data == "check":
        await query.edit_message_text("Отправьте PDF, DOCX, фото или текстом перечислите, что нужно проверить. Например: «сверь объёмы КС-2 с ЛСР».", reply_markup=menu())
    elif query.data == "lead":
        await query.edit_message_text("Опишите заявку: объект, площадь, работы, бюджет, сроки и контакт. Я подготовлю черновик карточки для менеджера.", reply_markup=menu())
    elif query.data.startswith("buy:"):
        await send_plan_invoice(query, query.data.split(":", 1)[1])


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        parts = query.invoice_payload.split(":")
        plan_key = parts[1]
        valid = plan_key in PLANS and int(parts[3]) == query.from_user.id
    except (IndexError, ValueError):
        valid = False
    await query.answer(ok=valid, error_message=None if valid else "Не удалось проверить заказ. Откройте тарифы и попробуйте снова.")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    parts = payment.invoice_payload.split(":")
    plan_key = parts[1] if len(parts) > 1 else ""
    plan = PLANS.get(plan_key)
    if not plan:
        await update.message.reply_text("Платёж получен, но тариф не распознан. Напишите в поддержку.")
        return
    now = datetime.now(timezone.utc)
    expires = now.replace(day=1).isoformat()  # overwritten below with a simple 30-day epoch-safe calculation
    from datetime import timedelta
    expires = (now + timedelta(days=plan["days"])).isoformat()
    conn = db()
    conn.execute("INSERT OR IGNORE INTO payments(charge_id,user_id,plan,stars,paid_at) VALUES(?,?,?,?,?)", (payment.telegram_payment_charge_id, update.effective_user.id, plan_key, payment.total_amount, now.isoformat()))
    conn.execute("INSERT INTO users(user_id,plan,expires_at,quota) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, quota=excluded.quota", (update.effective_user.id, plan["title"], expires, plan["docs"]))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Оплата получена: «{plan['title']}». Доступ активирован на 30 дней, лимит — {plan['docs']} обработок. Спасибо!", reply_markup=menu())


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_cmd))
telegram_app.add_handler(CommandHandler("status", status))
telegram_app.add_handler(CommandHandler("plans", plans))
telegram_app.add_handler(PreCheckoutQueryHandler(pre_checkout))
telegram_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, document_message))
telegram_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
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
    db().close()
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
