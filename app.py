import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, PreCheckoutQueryHandler, filters,
)

import ai
import byok
from extract import MAX_FILE_BYTES, ExtractionError, extract_text


def _load_dotenv() -> None:
    """Локальный запуск из .env рядом с app.py; переменные окружения имеют приоритет."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
POLLING_MODE = os.getenv("POLLING_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "bot.sqlite3")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x.isdigit()}

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
LOG = logging.getLogger("docbot")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

api = FastAPI(title="Строительный Документовед")
app = api  # Vercel ищет FastAPI-инстанс с именем app
telegram_app = Application.builder().token(TOKEN).build()
SERVER_AI = ai.server_config()

# Цифровые услуги внутри Telegram оплачиваются Stars (валюта XTR).
PLANS = {
    "start": {"title": "Старт", "stars": 99, "docs": 10, "days": 30, "description": "10 обработок документов и заявок на 30 дней"},
    "pro": {"title": "Прораб", "stars": 299, "docs": 50, "days": 30, "description": "50 обработок документов и заявок на 30 дней"},
    "business": {"title": "Компания", "stars": 799, "docs": 200, "days": 30, "description": "200 обработок документов и заявок на 30 дней"},
}

WELCOME = (
    "Здравствуйте! Я «Строительный Документовед» — помощник по строительному документообороту.\n\n"
    "Что я умею:\n"
    "• принять PDF, DOCX или фото документа и подготовить структурированный черновик проверки;\n"
    "• ответить на вопрос по документообороту без выдуманных норм и цен;\n"
    "• собрать заявку на строительные работы в карточку для менеджера.\n\n"
    "Анализ работает на ИИ сервиса или на вашем личном ключе (/connect_ai). "
    "В бесплатном режиме доступно 3 обработки. Финальная проверка всегда остаётся за специалистом."
)

ABOUT = (
    "Что умею:\n"
    "• принять PDF, DOCX, фото или текст;\n"
    "• распознать документ и собрать ключевые данные;\n"
    "• подготовить черновик проверки: чего не хватает, какие вопросы задать;\n"
    "• квалифицировать заявку клиента в карточку.\n\n"
    "Ограничения:\n"
    "• не рассчитываю конструкции;\n"
    "• не подписываю документы;\n"
    "• не даю юридических заключений;\n"
    "• не выдумываю цены, объёмы и нормативы — чего нет в документе, отмечаю как «не указано».\n\n"
    "ИИ: серверный ключ сервиса по умолчанию; свой ключ можно подключить через /connect_ai — "
    "тогда обработки идут на вашем ключе и не расходуют лимит тарифа."
)

SYSTEM_ANALYSIS = (
    "Ты — «Строительный Документовед», внимательный помощник по строительному документообороту в Telegram.\n"
    "Задача: по присланным данным подготовить структурированный черновик проверки или ответ на вопрос.\n"
    "Правила:\n"
    "1. Опирайся только на данные из документа или сообщения. Ничего не выдумывай.\n"
    "2. Отсутствующие сведения помечай «не указано».\n"
    "3. Не рассчитывай конструкции, не давай юридических заключений, не подписывай документы.\n"
    "4. Не ссылайся на конкретные СНиП/СП/ГОСТ и пункты, если их нет в присланном тексте.\n"
    "5. Напоминай, что финальную проверку выполняет специалист.\n"
    "Формат ответа — компактный текст по разделам:\n"
    "«Тип документа», «Ключевые данные», «Чего не хватает», «Вопросы для уточнения», «Что делать дальше».\n"
    "Пиши по-русски, кратко, по делу."
)

SYSTEM_LEAD = (
    "Ты — помощник строительной компании. Из сообщения клиента извлеки данные заявки на строительные работы.\n"
    "Верни ТОЛЬКО JSON без пояснений и без markdown-обёрток, вида:\n"
    '{"object_type": "тип объекта", "area": "площадь", "work_types": "виды работ", '
    '"budget": "бюджет", "deadline": "сроки", "contact": "контакт", "notes": "прочее"}\n'
    "Если какое-то данное в сообщении не упомянуто — ставь null. Ничего не выдумывай."
)

LEAD_KEYS = ("object_type", "area", "work_types", "budget", "deadline", "contact", "notes")
LEAD_LABELS = {
    "object_type": "Объект", "area": "Площадь", "work_types": "Виды работ",
    "budget": "Бюджет", "deadline": "Сроки", "contact": "Контакт", "notes": "Заметки",
}


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, plan TEXT, expires_at TEXT, quota INTEGER NOT NULL DEFAULT 3)")
    conn.execute("CREATE TABLE IF NOT EXISTS payments (charge_id TEXT PRIMARY KEY, user_id INTEGER, plan TEXT, stars INTEGER, paid_at TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS leads ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, created_at TEXT, "
        "object_type TEXT, area TEXT, work_types TEXT, budget TEXT, deadline TEXT, contact TEXT, raw_text TEXT)"
    )
    byok.init_db(conn)
    conn.commit()
    return conn


def user_state(conn: sqlite3.Connection, user_id: int) -> dict:
    """Текущее состояние доступа с учётом срока тарифа."""
    row = conn.execute("SELECT plan, expires_at, quota FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"plan": None, "quota": 3, "expires_at": None, "expired": False}
    if row["expires_at"]:
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            expires = None
        if expires is None or expires <= datetime.now(timezone.utc):
            return {"plan": None, "quota": 0, "expires_at": row["expires_at"], "expired": True}
    return {"plan": row["plan"], "quota": row["quota"], "expires_at": row["expires_at"], "expired": False}


def consume_quota(conn: sqlite3.Connection, user_id: int) -> int:
    """Списывает одну обработку и возвращает остаток."""
    row = conn.execute("SELECT quota FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        conn.execute("UPDATE users SET quota=quota-1 WHERE user_id=?", (user_id,))
        remaining = row["quota"] - 1
    else:
        conn.execute("INSERT INTO users(user_id, quota) VALUES(?, 2)", (user_id,))
        remaining = 2
    conn.commit()
    return remaining


def ai_config(conn: sqlite3.Connection, user_id: int):
    """ИИ для пользователя: сначала личный ключ, затем серверный. Источник: 'byok' | 'server' | None."""
    item = byok.get_key(conn, user_id)
    if item:
        return "byok", ai.KeyConfig(provider=item["provider"], model=item["model"], endpoint=item["endpoint"], api_key=item["api_key"])
    if SERVER_AI:
        return "server", SERVER_AI
    return None, None


def fmt_date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso or "—"


def cut(text: str, limit: int = 3800) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(текст обрезан)"


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
        "Оплата цифровой услуги проходит внутри Telegram Stars. Перед оплатой проверьте название тарифа и количество обработок.\n\n"
        "Если подключён личный ИИ-ключ (/connect_ai), обработки идут на вашем ключе и лимит не расходуется."
    )


def format_lead_card(fields: dict) -> str:
    lines = ["Карточка заявки (черновик):"]
    for key in LEAD_KEYS:
        value = fields.get(key)
        text = str(value).strip() if value not in (None, "", "null") else ""
        lines.append(f"• {LEAD_LABELS[key]}: {text or 'не указано'}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); user_state(conn, update.effective_user.id); conn.close()
    await update.message.reply_text(WELCOME, reply_markup=menu())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — начать работу\n"
        "/help — помощь\n"
        "/status — состояние, ИИ и остаток обработок\n"
        "/plans — тарифы\n"
        "/connect_ai — подключить личный ИИ-ключ\n"
        "/ai_status — статус личного ИИ\n"
        "/disconnect_ai — удалить личный ИИ-ключ\n"
        "/leads — последние заявки (только для администратора)\n\n"
        "Для проверки отправьте PDF, DOCX, фото документа или опишите задачу текстом.",
        reply_markup=menu(),
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = db()
    state = user_state(conn, user_id)
    source, _cfg = ai_config(conn, user_id)
    conn.close()
    if source == "byok":
        ai_line = "ИИ: ваш личный ключ (лимит тарифа не расходуется)"
    elif source == "server":
        ai_line = "ИИ: ключ сервиса"
    else:
        ai_line = "ИИ: не настроен — администратор ещё не подключил сервис, а личного ключа нет (/connect_ai)"
    if state["plan"]:
        access = f"Тариф: {state['plan']}\nОстаток обработок: {state['quota']}\nДействует до: {fmt_date(state['expires_at'])}"
    elif state["expired"]:
        access = "Срок тарифа истёк. Продлите доступ в «Тарифы и оплата»."
    else:
        access = f"Бесплатный режим: осталось обработок — {state['quota']}."
    await update.message.reply_text(f"{access}\n{ai_line}", reply_markup=menu())


async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plan_text(), reply_markup=plans_menu())


async def connect_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not byok.BYOK_ENCRYPTION_KEY:
        await update.message.reply_text("Подключение личных ИИ-ключей пока не настроено на сервере (нет ключа шифрования).")
        return
    if not PUBLIC_BASE_URL or not PUBLIC_BASE_URL.startswith("https://"):
        await update.message.reply_text("Подключение личного ИИ временно недоступно: администратор ещё не настроил HTTPS-адрес сервиса.")
        return
    conn = db()
    link = byok.create_link(conn, update.effective_user.id, PUBLIC_BASE_URL)
    conn.close()
    await update.message.reply_text(
        "Не отправляйте API-ключ в Telegram. Откройте одноразовую ссылку (15 минут):\n\n"
        + link + "\n\nКлюч будет зашифрован и не будет показан повторно."
    )


async def ai_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); item = byok.get_key(conn, update.effective_user.id); conn.close()
    if not item:
        await update.message.reply_text(
            "Личный ИИ-ключ не подключён. Используйте /connect_ai.\n"
            "Без личного ключа анализ идёт на ИИ сервиса (если настроен администратором)."
        )
        return
    await update.message.reply_text(
        f"Личный ИИ подключён. Провайдер: {item['provider']}; модель: {item['model']}; ключ: {byok.mask_key(item['api_key'])}\n"
        "Обработки на вашем ключе не расходуют лимит тарифа."
    )


async def disconnect_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); byok.delete_key(conn, update.effective_user.id); conn.close()
    await update.message.reply_text("Личный ИИ-ключ удалён из зашифрованного хранилища.")


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


async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo = update.message.photo
    if photo:
        item = photo[-1]
        name, mime, image_data_source = "фото документа", "image/jpeg", "photo"
    else:
        item = update.message.document
        name = item.file_name or "документ"
        mime = item.mime_type or ""
        image_data_source = "photo" if mime.startswith("image/") else None

    conn = db()
    source, cfg = ai_config(conn, user_id)
    state = user_state(conn, user_id)
    conn.close()
    if not cfg:
        await update.message.reply_text(
            "Анализ пока не запущен: ИИ сервиса не настроен администратором, и у вас нет личного ключа. "
            "Подключите свой ключ через /connect_ai — тогда документ будет разобран на вашем ключе.",
            reply_markup=menu(),
        )
        return
    if source == "server" and state["quota"] <= 0:
        await update.message.reply_text(
            "Пробный лимит исчерпан. Откройте «Тарифы и оплата», чтобы продолжить, "
            "или подключите личный ИИ-ключ через /connect_ai.",
            reply_markup=plans_menu(),
        )
        return

    size = getattr(item, "file_size", 0) or 0
    if size > MAX_FILE_BYTES:
        await update.message.reply_text(f"Файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ. Пришлите документ меньше или сфотографируйте нужные страницы.")
        return

    try:
        tg_file = await context.bot.get_file(item.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        LOG.exception("file download failed")
        await update.message.reply_text("Не удалось скачать файл из Telegram. Попробуйте отправить заново.")
        return

    image: bytes | None = None
    text: str | None = None
    if image_data_source:
        image = data
    else:
        try:
            text = extract_text(data, mime, name)
        except ExtractionError as error:
            await update.message.reply_text(f"Файл «{name}» получен, но: {error}")
            return

    await update.message.chat.send_action("typing")
    user_prompt = (
        f"Документ: {name}\n\nТекст документа:\n{text}"
        if text is not None
        else "Пользователь прислал фото строительного документа. Распознай его и подготовь черновик проверки по правилам."
    )
    try:
        answer = await ai.chat(cfg, SYSTEM_ANALYSIS, user_prompt, image=image, image_mime=mime or "image/jpeg")
    except ai.AIError as error:
        await update.message.reply_text(f"Файл «{name}» получен, но анализ не удался: {error}")
        return

    if source == "server":
        conn = db(); remaining = consume_quota(conn, user_id); conn.close()
        footer = f"Осталось обработок: {remaining}"
    else:
        footer = "Обработка выполнена на вашем ИИ-ключе."
    await update.message.reply_text(cut(f"{answer}\n\n—\n{footer}"), reply_markup=menu())


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if context.user_data.get("awaiting_lead"):
        context.user_data["awaiting_lead"] = False
        await process_lead(update, context, text)
        return

    lower = text.lower()
    if any(word in lower for word in ["заявка", "строительство", "ремонт", "дом", "квартира"]):
        context.user_data["awaiting_lead"] = True
        await update.message.reply_text(
            "Чтобы передать заявку менеджеру, напишите одним сообщением:\n"
            "1) тип объекта; 2) площадь; 3) виды работ; 4) бюджет; 5) желаемые сроки; 6) контакт.\n\n"
            "Следующее сообщение я превращу в карточку заявки."
        )
        return

    user_id = update.effective_user.id
    conn = db()
    source, cfg = ai_config(conn, user_id)
    state = user_state(conn, user_id)
    conn.close()
    if not cfg:
        await update.message.reply_text(
            "Запрос принят. ИИ пока не подключён: администратор не настроил сервис, а личного ключа нет. "
            "Подключите ключ через /connect_ai или отправьте документ, когда анализ появится.\n\n"
            "Я не выдумываю цены, объёмы и нормативные ссылки — их не будет в ответе без источника.",
            reply_markup=menu(),
        )
        return
    if source == "server" and state["quota"] <= 0:
        await update.message.reply_text(
            "Пробный лимит исчерпан. Откройте «Тарифы и оплата», чтобы продолжить, "
            "или подключите личный ИИ-ключ через /connect_ai.",
            reply_markup=plans_menu(),
        )
        return

    await update.message.chat.send_action("typing")
    try:
        answer = await ai.chat(cfg, SYSTEM_ANALYSIS, text)
    except ai.AIError as error:
        await update.message.reply_text(f"Анализ не удался: {error}")
        return
    if source == "server":
        conn = db(); remaining = consume_quota(conn, user_id); conn.close()
        footer = f"Осталось обработок: {remaining}"
    else:
        footer = "Обработка выполнена на вашем ИИ-ключе."
    await update.message.reply_text(cut(f"{answer}\n\n—\n{footer}"), reply_markup=menu())


async def process_lead(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    conn = db()
    _source, cfg = ai_config(conn, user_id)
    conn.close()

    fields = None
    if cfg:
        try:
            raw = await ai.chat(cfg, SYSTEM_LEAD, text)
            fields = ai.extract_json(raw)
        except ai.AIError:
            fields = None  # черновик карточки соберём без разбора
    card = format_lead_card(fields) if fields else "Черновик карточки заявки (без ИИ-разбора):\n\n" + text[:1500]

    values = {
        key: (str(fields[key]).strip() if fields and fields.get(key) not in (None, "", "null") else None)
        for key in LEAD_KEYS
    }
    conn = db()
    cursor = conn.execute(
        "INSERT INTO leads(user_id, created_at, object_type, area, work_types, budget, deadline, contact, raw_text) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (user_id, datetime.now(timezone.utc).isoformat(), values["object_type"], values["area"],
         values["work_types"], values["budget"], values["deadline"], values["contact"], text[:4000]),
    )
    lead_id = cursor.lastrowid
    conn.commit(); conn.close()

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"Новая заявка #{lead_id}\n{cut(card, 700)}\n\nОт: https://t.me/{update.effective_user.username or update.effective_user.id}",
            )
        except Exception:
            LOG.warning("не удалось уведомить администратора %s", admin_id)

    saved = "Карточка сохранена" + (" и передана менеджеру." if ADMIN_IDS else ".")
    await update.message.reply_text(f"{card}\n\n{saved} Специалист проверит и свяжется с вами.", reply_markup=menu())


async def admin_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    conn = db()
    rows = conn.execute("SELECT id, created_at, object_type, contact, raw_text FROM leads ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Заявок пока нет.")
        return
    lines = [
        f"#{r['id']} ({fmt_date(r['created_at'])}) — {r['object_type'] or 'объект не указан'}; {r['contact'] or 'без контакта'}"
        for r in rows
    ]
    await update.message.reply_text("Последние заявки:\n\n" + "\n".join(lines))


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
        await query.edit_message_text(
            "Отправьте PDF, DOCX или фото документа — я подготовлю черновик проверки: тип, ключевые данные, "
            "чего не хватает и вопросы для уточнения. Текстом можно описать задачу, например: «сверь объёмы КС-2 с ЛСР».",
            reply_markup=menu(),
        )
    elif query.data == "lead":
        context.user_data["awaiting_lead"] = True
        await query.edit_message_text(
            "Опишите заявку одним сообщением: объект, площадь, работы, бюджет, сроки и контакт. "
            "Следующее сообщение я превращу в карточку заявки для менеджера.",
            reply_markup=menu(),
        )
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
    expires = (now + timedelta(days=plan["days"])).isoformat()
    conn = db()
    if conn.execute("SELECT 1 FROM payments WHERE charge_id=?", (payment.telegram_payment_charge_id,)).fetchone():
        conn.close()  # повторная доставка successful_payment — доступ уже выдан
        return
    conn.execute(
        "INSERT INTO payments(charge_id,user_id,plan,stars,paid_at) VALUES(?,?,?,?,?)",
        (payment.telegram_payment_charge_id, update.effective_user.id, plan_key, payment.total_amount, now.isoformat()),
    )
    conn.execute(
        "INSERT INTO users(user_id,plan,expires_at,quota) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, quota=excluded.quota",
        (update.effective_user.id, plan["title"], expires, plan["docs"]),
    )
    conn.commit(); conn.close()
    await update.message.reply_text(
        f"Оплата получена: «{plan['title']}». Доступ активирован на 30 дней, лимит — {plan['docs']} обработок. Спасибо!",
        reply_markup=menu(),
    )


def safe(handler):
    """Webhook всегда получает 200: внутренние ошибки не должны порождать ретраи Telegram."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await handler(update, context)
        except Exception:
            LOG.exception("handler %s failed", handler.__name__)
            try:
                if update.effective_message:
                    await update.effective_message.reply_text("Временная ошибка. Попробуйте ещё раз позже.")
            except Exception:
                pass
    return wrapper


telegram_app.add_handler(CommandHandler("start", safe(start)))
telegram_app.add_handler(CommandHandler("help", safe(help_cmd)))
telegram_app.add_handler(CommandHandler("status", safe(status)))
telegram_app.add_handler(CommandHandler("plans", safe(plans)))
telegram_app.add_handler(CommandHandler("connect_ai", safe(connect_ai)))
telegram_app.add_handler(CommandHandler("ai_status", safe(ai_status)))
telegram_app.add_handler(CommandHandler("disconnect_ai", safe(disconnect_ai)))
telegram_app.add_handler(CommandHandler("leads", safe(admin_leads)))
telegram_app.add_handler(PreCheckoutQueryHandler(pre_checkout))
telegram_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, safe(document_message)))
telegram_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, safe(successful_payment)))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe(text_message)))
telegram_app.add_handler(CallbackQueryHandler(safe(callback)))


@api.get("/")
async def root():
    return {"service": "construction-doc-bot", "status": "ok", "server_ai": bool(SERVER_AI)}


@api.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@api.get("/connect/{token}")
async def connect_page(token: str):
    if len(token) < 20:
        raise HTTPException(status_code=404, detail="invalid link")
    response = HTMLResponse(byok.page_html(token))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@api.post("/connect/{token}")
async def connect_submit(token: str, request: Request):
    if len(token) < 20:
        raise HTTPException(status_code=404, detail="invalid link")
    conn = db()
    form = await request.form()
    provider = str(form.get("provider", "")); model = str(form.get("model", "")); endpoint = str(form.get("endpoint", "")); api_key = str(form.get("api_key", ""))
    try:
        byok.validate_input(provider, model, endpoint, api_key)
    except ValueError as error:
        conn.close()
        return HTMLResponse(byok.error_html(str(error)), status_code=400)
    user_id = byok.consume_link(conn, token)
    if not user_id:
        conn.close()
        return HTMLResponse(byok.error_html("Ссылка недействительна или истекла. Запросите новую через /connect_ai."), status_code=410)
    try:
        byok.save_key(conn, user_id, provider, model, endpoint, api_key)
    except (ValueError, RuntimeError):
        conn.close()
        return HTMLResponse(byok.error_html("Сервер не смог сохранить ключ. Обратитесь к администратору."), status_code=500)
    conn.close()
    response = HTMLResponse(byok.success_html())
    response.headers["Cache-Control"] = "no-store"
    return response


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
    if not SERVER_AI:
        LOG.warning("Серверный ИИ не настроен (AI_API_KEY/AI_MODEL/AI_PROVIDER) — анализ работает только на личных ключах пользователей")
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
