"""Адаптер вызовов ИИ-провайдеров.

Два источника конфигурации:
- серверный ключ сервиса (AI_* в окружении), используется по умолчанию для всех;
- личный ключ пользователя (BYOK), подключённый через /connect_ai.

Модуль формирует запросы к провайдеру и возвращает текст ответа. API-ключи
не логируются и не попадают в тексты ошибок.
"""
import base64
import json
import os
from dataclasses import dataclass

import httpx


class AIError(Exception):
    """Ошибка вызова провайдера; текст безопасен для показа пользователю."""


@dataclass
class KeyConfig:
    provider: str
    model: str
    endpoint: str
    api_key: str


PROVIDERS = {"openai", "anthropic", "google", "openai_compatible"}

DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "google": "https://generativelanguage.googleapis.com/v1beta/models",
}

TIMEOUT = httpx.Timeout(120.0, connect=15.0)
MAX_OUTPUT_TOKENS = 2000


def server_config() -> KeyConfig | None:
    """Конфиг серверного ИИ из переменных AI_PROVIDER/AI_MODEL/AI_ENDPOINT/AI_API_KEY."""
    api_key = os.environ.get("AI_API_KEY", "")
    provider = os.environ.get("AI_PROVIDER", "openai_compatible")
    model = os.environ.get("AI_MODEL", "")
    endpoint = os.environ.get("AI_ENDPOINT", "")
    if not api_key or not model or provider not in PROVIDERS:
        return None
    if provider == "openai_compatible" and not endpoint:
        return None
    return KeyConfig(provider=provider, model=model, endpoint=endpoint, api_key=api_key)


def _status_message(status: int) -> str:
    if status in (401, 403):
        return "Провайдер отклонил API-ключ. Проверьте ключ через /connect_ai."
    if status == 404:
        return "Модель не найдена. Проверьте название модели через /connect_ai."
    if status == 429:
        return "Превышен лимит запросов у провайдера. Попробуйте позже."
    if status == 400:
        return "Провайдер отклонил запрос (400). Возможно, модель не поддерживает фото или текст слишком длинный."
    return f"Провайдер вернул ошибку {status}. Попробуйте позже."


def _data_url(image: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


async def _post_json(client: httpx.AsyncClient, url: str, headers: dict, payload: dict) -> dict:
    try:
        response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError:
        raise AIError("Не удалось связаться с провайдером ИИ. Попробуйте позже.")
    if response.status_code >= 400:
        raise AIError(_status_message(response.status_code))
    try:
        return response.json()
    except ValueError:
        raise AIError("Провайдер вернул ответ в неожиданном формате.")


async def _call_openai_style(client, cfg: KeyConfig, system: str, user_text: str, image: bytes | None, image_mime: str) -> str:
    if cfg.provider == "openai_compatible" and not cfg.endpoint:
        raise AIError("Для OpenAI-compatible API укажите HTTPS endpoint через /connect_ai.")
    url = cfg.endpoint or DEFAULT_ENDPOINTS["openai"]
    content: list = [{"type": "text", "text": user_text}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": _data_url(image, image_mime)}})
    payload = {
        "model": cfg.model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    data = await _post_json(client, url, {"Authorization": f"Bearer {cfg.api_key}"}, payload)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("Неожиданный формат ответа провайдера.")


async def _call_anthropic(client, cfg: KeyConfig, system: str, user_text: str, image: bytes | None, image_mime: str) -> str:
    url = cfg.endpoint or DEFAULT_ENDPOINTS["anthropic"]
    content: list = [{"type": "text", "text": user_text}]
    if image:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": image_mime, "data": base64.b64encode(image).decode()},
        })
    payload = {
        "model": cfg.model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
    data = await _post_json(client, url, headers, payload)
    try:
        return "".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
    except (KeyError, TypeError):
        raise AIError("Неожиданный формат ответа Anthropic.")


async def _call_google(client, cfg: KeyConfig, system: str, user_text: str, image: bytes | None, image_mime: str) -> str:
    base = cfg.endpoint or DEFAULT_ENDPOINTS["google"]
    url = f"{base.rstrip('/')}/{cfg.model}:generateContent"
    parts: list = [{"text": user_text}]
    if image:
        parts.append({"inline_data": {"mime_type": image_mime, "data": base64.b64encode(image).decode()}})
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    data = await _post_json(client, url, {"x-goog-api-key": cfg.api_key}, payload)
    try:
        return "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise AIError("Неожиданный формат ответа Google AI.")


async def chat(
    cfg: KeyConfig,
    system: str,
    user_text: str,
    image: bytes | None = None,
    image_mime: str = "image/jpeg",
    client: httpx.AsyncClient | None = None,
) -> str:
    """Один запрос к модели; возвращает текст ответа или бросает AIError."""
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        if cfg.provider in ("openai", "openai_compatible"):
            text = await _call_openai_style(client, cfg, system, user_text, image, image_mime)
        elif cfg.provider == "anthropic":
            text = await _call_anthropic(client, cfg, system, user_text, image, image_mime)
        elif cfg.provider == "google":
            text = await _call_google(client, cfg, system, user_text, image, image_mime)
        else:
            raise AIError("Неизвестный провайдер. Подключите ключ заново через /connect_ai.")
    finally:
        if own:
            await client.aclose()
    text = (text or "").strip()
    if not text:
        raise AIError("Провайдер вернул пустой ответ. Попробуйте переформулировать запрос.")
    return text


def extract_json(text: str) -> dict | None:
    """Достает первый JSON-объект из ответа модели; иначе None."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        result = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return result if isinstance(result, dict) else None
