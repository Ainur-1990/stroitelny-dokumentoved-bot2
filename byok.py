"""BYOK storage and one-time connection links.

Keys are never written to logs or returned by the API. The database stores only
Fernet-encrypted provider keys and SHA-256 hashes of one-time link tokens.
"""
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
import time
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken


BYOK_ENCRYPTION_KEY = os.environ.get("BYOK_ENCRYPTION_KEY", "")
BYOK_ALLOWED_PROVIDERS = {"openai", "anthropic", "google", "openai_compatible"}


def _fernet() -> Fernet:
    if not BYOK_ENCRYPTION_KEY:
        raise RuntimeError("BYOK_ENCRYPTION_KEY is required")
    return Fernet(BYOK_ENCRYPTION_KEY.encode())


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS byok_keys (user_id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT, encrypted_key BLOB NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS byok_links (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
    )
    conn.commit()


def create_link(conn: sqlite3.Connection, user_id: int, base_url: str) -> str:
    if not base_url.startswith("https://"):
        raise ValueError("PUBLIC_BASE_URL must use HTTPS")
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    conn.execute("DELETE FROM byok_links WHERE user_id=?", (user_id,))
    conn.execute("INSERT INTO byok_links(token_hash,user_id,expires_at) VALUES(?,?,?)", (digest, user_id, int(time.time()) + 900))
    conn.commit()
    return f"{base_url.rstrip('/')}/connect/{raw}"


def consume_link(conn: sqlite3.Connection, raw_token: str):
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    row = conn.execute("SELECT user_id, expires_at FROM byok_links WHERE token_hash=?", (digest,)).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        if row:
            conn.execute("DELETE FROM byok_links WHERE token_hash=?", (digest,)); conn.commit()
        return None
    conn.execute("DELETE FROM byok_links WHERE token_hash=?", (digest,))
    conn.commit()
    return int(row["user_id"])


def save_key(conn: sqlite3.Connection, user_id: int, provider: str, model: str, endpoint: str, api_key: str) -> None:
    if provider not in BYOK_ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    if not api_key or len(api_key) > 4096:
        raise ValueError("invalid key")
    if len(model) > 200 or len(endpoint) > 500:
        raise ValueError("invalid model or endpoint")
    if endpoint and (urlparse(endpoint).scheme != "https" or not urlparse(endpoint).netloc):
        raise ValueError("endpoint must be HTTPS")
    encrypted = _fernet().encrypt(api_key.encode())
    conn.execute(
        "INSERT INTO byok_keys(user_id,provider,model,endpoint,encrypted_key,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider,model=excluded.model,endpoint=excluded.endpoint,encrypted_key=excluded.encrypted_key,updated_at=excluded.updated_at",
        (user_id, provider, model, endpoint, encrypted, int(time.time())),
    )
    conn.commit()


def get_key(conn: sqlite3.Connection, user_id: int):
    row = conn.execute("SELECT provider, model, endpoint, encrypted_key FROM byok_keys WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    try:
        key = _fernet().decrypt(row["encrypted_key"]).decode()
    except (InvalidToken, UnicodeDecodeError):
        return None
    return {"provider": row["provider"], "model": row["model"], "endpoint": row["endpoint"] or "", "api_key": key}


def delete_key(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM byok_keys WHERE user_id=?", (user_id,))
    conn.commit()


def page_html(token: str) -> str:
    safe_token = html.escape(token, quote=True)
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Подключение ИИ</title><style>body{{font-family:system-ui;max-width:560px;margin:40px auto;padding:0 18px;color:#17202a}}label{{display:block;margin-top:14px;font-weight:600}}input,select{{width:100%;box-sizing:border-box;padding:11px;margin-top:6px;border:1px solid #ccd3da;border-radius:8px}}button{{margin-top:20px;padding:12px 18px;border:0;border-radius:8px;background:#1769e0;color:#fff;font-size:16px}}.note{{background:#fff8e6;padding:12px;border-radius:8px}}</style></head><body><h1>Подключение личного ИИ-ключа</h1><p class='note'>Ссылка одноразовая и действует 15 минут. Никому её не пересылайте. Ключ не показывается в Telegram и хранится в зашифрованном виде.</p><form method='post' action='/connect/{safe_token}'><label>Провайдер<select name='provider'><option value='openai'>OpenAI</option><option value='anthropic'>Anthropic</option><option value='google'>Google AI</option><option value='openai_compatible'>OpenAI-compatible API</option></select></label><label>Модель<input name='model' required maxlength='200' placeholder='например, gpt-4o-mini'></label><label>HTTPS endpoint (необязательно)<input name='endpoint' maxlength='500' placeholder='для OpenAI-compatible API'></label><label>API-ключ<input name='api_key' type='password' required maxlength='4096' autocomplete='off'></label><button type='submit'>Сохранить зашифрованно</button></form></body></html>"""


def success_html() -> str:
    return "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Готово</title><body style='font-family:system-ui;max-width:560px;margin:60px auto;padding:0 18px'><h1>Ключ подключён</h1><p>Вернитесь в Telegram. Ключ сохранён зашифрованно и не будет показан повторно.</p></body></html>"


def error_html(message: str) -> str:
    return f"<!doctype html><html lang='ru'><meta charset='utf-8'><title>Ошибка</title><body style='font-family:system-ui;max-width:560px;margin:60px auto;padding:0 18px'><h1>Не удалось сохранить</h1><p>{html.escape(message)}</p></body></html>"


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:3]}••••••••{key[-4:]}"
