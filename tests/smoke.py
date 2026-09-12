"""Смоук-тесты без сети: BYOK-хранилище, валидация endpoint, адаптеры ИИ (MockTransport),
извлечение текста из DOCX/PDF, импорт app. Запуск: python tests/smoke.py
"""
import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.sqlite3"))
os.environ.setdefault("POLLING_MODE", "false")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BYOK_ENCRYPTION_KEY", Fernet.generate_key().decode())

import ai  # noqa: E402
import byok  # noqa: E402
import extract  # noqa: E402

PASSED = 0


def check(name, condition, detail=""):
    global PASSED
    if not condition:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASSED += 1
    print(f"ok   {name}")


def test_byok():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    byok.init_db(conn)
    byok.save_key(conn, 1, "openai", "gpt-4o-mini", "", "sk-test-1234567890")
    item = byok.get_key(conn, 1)
    check("byok roundtrip", item and item["api_key"] == "sk-test-1234567890" and item["provider"] == "openai")
    check("byok mask", byok.mask_key(item["api_key"]).endswith("7890") and "123456" not in byok.mask_key(item["api_key"]))
    byok.delete_key(conn, 1)
    check("byok delete", byok.get_key(conn, 1) is None)

    raw_link = byok.create_link(conn, 2, "https://example.com")
    check("byok link one-time", byok.consume_link(conn, raw_link.split("/")[-1]) == 2 and byok.consume_link(conn, raw_link.split("/")[-1]) is None)

    cases = [
        ({"provider": "evil", "model": "m", "endpoint": "", "api_key": "k"}, "provider"),
        ({"provider": "openai", "model": "m", "endpoint": "http://api.example.com", "api_key": "k"}, "http endpoint"),
        ({"provider": "openai", "model": "m", "endpoint": "https://localhost/v1", "api_key": "k"}, "localhost"),
        ({"provider": "openai", "model": "m", "endpoint": "https://127.0.0.1/v1", "api_key": "k"}, "loopback ip"),
        ({"provider": "openai", "model": "m", "endpoint": "https://192.168.1.5/v1", "api_key": "k"}, "private ip"),
        ({"provider": "openai", "model": "", "endpoint": "", "api_key": "k"}, "empty model"),
        ({"provider": "openai", "model": "m", "endpoint": "", "api_key": ""}, "empty key"),
    ]
    for kwargs, label in cases:
        try:
            byok.validate_input(**kwargs)
            check(f"byok reject {label}", False)
        except ValueError:
            check(f"byok reject {label}", True)
    byok.validate_input("openai_compatible", "m", "https://api.gptunnel.example/v1/chat/completions", "k")
    check("byok accept public https", True)
    conn.close()


def test_extract():
    from docx import Document as Docx

    buffer = io.BytesIO()
    doc = Docx()
    doc.add_paragraph("Акт приёмки работ №3")
    doc.add_paragraph("Объект: жилой дом, площадь 120 м2")
    doc.save(buffer)
    text = extract.extract_text(buffer.getvalue(), extract.DOCX_MIME, "akt.docx")
    check("docx text", "Акт приёмки" in text and "120" in text)

    try:
        extract.extract_text(b"not a pdf", "application/pdf", "x.pdf")
        check("pdf corrupted raises", False)
    except extract.ExtractionError as error:
        check("pdf corrupted raises", "PDF" in str(error))

    try:
        extract.extract_text(b"PK\x03\x04broken", "application/zip", "x.zip")
        check("unsupported raises", False)
    except extract.ExtractionError as error:
        check("unsupported raises", "Поддерживаются" in str(error))


def make_transport(provider: str, status: int = 200, body: dict | None = None):
    seen_urls: list[str] = []

    def handler(request: ai.httpx.Request) -> ai.httpx.Response:
        seen_urls.append(str(request.url))
        has_auth = any(
            request.headers.get(name)
            for name in ("authorization", "x-api-key", "x-goog-api-key")
        )
        if not has_auth:
            return ai.httpx.Response(500, json={"error": "no auth header"})
        if status != 200:
            return ai.httpx.Response(status, json={"error": "x"})
        return ai.httpx.Response(200, json=body)

    transport = ai.httpx.MockTransport(handler)
    transport.seen_urls = seen_urls  # type: ignore[attr-defined]
    return transport


def test_ai():
    async def run():
        import httpx

        cfg = ai.KeyConfig("openai", "gpt-4o-mini", "", "sk-key")
        body = {"choices": [{"message": {"content": "ответ openai"}}]}
        async with httpx.AsyncClient(transport=make_transport("openai", body=body)) as client:
            text = await ai.chat(cfg, "system", "hi", client=client)
        check("ai openai", text == "ответ openai")

        cfg = ai.KeyConfig("anthropic", "claude-3", "", "ak-key")
        body = {"content": [{"type": "text", "text": "ответ anthropic"}]}
        async with httpx.AsyncClient(transport=make_transport("anthropic", body=body)) as client:
            text = await ai.chat(cfg, "system", "hi", client=client)
        check("ai anthropic", text == "ответ anthropic")

        cfg = ai.KeyConfig("google", "gemini", "", "g-key")
        body = {"candidates": [{"content": {"parts": [{"text": "ответ google"}]}}]}
        async with httpx.AsyncClient(transport=make_transport("google", body=body)) as client:
            text = await ai.chat(cfg, "system", "hi", client=client)
        check("ai google", text == "ответ google")

        cfg = ai.KeyConfig("openai_compatible", "m", "https://api.example.com/v1/chat/completions", "k")
        compat_body = {"choices": [{"message": {"content": "ответ compat"}}]}
        transport = make_transport("compat", body=compat_body)
        async with httpx.AsyncClient(transport=transport) as client:
            text = await ai.chat(cfg, "system", "hi", client=client)
        check("ai compatible", text == "ответ compat" and "api.example.com" in transport.seen_urls[-1])

        cfg = ai.KeyConfig("openai", "gpt-4o-mini", "", "sk-bad")
        async with httpx.AsyncClient(transport=make_transport("openai", status=401)) as client:
            try:
                await ai.chat(cfg, "s", "hi", client=client)
                check("ai 401 raises", False)
            except ai.AIError as error:
                check("ai 401 raises", "API-ключ" in str(error))

        cfg = ai.KeyConfig("openai_compatible", "m", "", "k")
        async with httpx.AsyncClient(transport=make_transport("x")) as client:
            try:
                await ai.chat(cfg, "s", "hi", client=client)
                check("ai compat no endpoint raises", False)
            except ai.AIError as error:
                check("ai compat no endpoint raises", "endpoint" in str(error))

    asyncio.run(run())

    parsed = ai.extract_json('префикс {"object_type": "дом", "area": null} суффикс')
    check("ai extract_json", parsed and parsed["object_type"] == "дом" and parsed["area"] is None)
    check("ai extract_json none", ai.extract_json("нет json тут") is None)


def test_app():
    os.environ["AI_PROVIDER"] = "openai_compatible"
    os.environ["AI_MODEL"] = "test-model"
    os.environ["AI_ENDPOINT"] = "https://api.example.com/v1/chat/completions"
    os.environ["AI_API_KEY"] = "test-key"
    os.environ["ADMIN_USER_IDS"] = "111,222"
    import app

    check("app plans", set(app.PLANS) == {"start", "pro", "business"})
    check("app server ai", app.SERVER_AI is not None and app.SERVER_AI.model == "test-model")
    check("app admins", app.ADMIN_IDS == {111, 222})
    handlers = [h for group in app.telegram_app.handlers.values() for h in group]
    commands = set().union(*(h.commands for h in handlers if hasattr(h, "commands")))
    check("app commands", {"start", "status", "plans", "connect_ai", "leads"} <= commands, str(commands))

    conn = app.db()
    state = app.user_state(conn, 999)
    check("app new user free", state["quota"] == 3 and state["plan"] is None)
    remaining = app.consume_quota(conn, 999)
    check("app consume first", remaining == 2)
    conn.execute("INSERT INTO users(user_id, plan, expires_at, quota) VALUES(?, 'Старт', '2001-01-01T00:00:00+00:00', 40)", (1000,))
    state = app.user_state(conn, 1000)
    check("app expired plan", state["expired"] is True and state["quota"] == 0)
    card = app.format_lead_card({"object_type": "дом", "area": None})
    check("app lead card", "Объект: дом" in card and "не указано" in card)
    conn.close()

    os.environ["AI_API_KEY"] = ""
    check("ai server_config empty", ai.server_config() is None)


if __name__ == "__main__":
    test_byok()
    test_extract()
    test_ai()
    test_app()
    print(f"\n{PASSED} проверок пройдено")
