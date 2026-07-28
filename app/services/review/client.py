from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.errors import AppError
from app.services.http import request_with_retry

logger = logging.getLogger(__name__)


def _resolve_url(settings: Settings) -> str:
    return (settings.ark_review_base_url or settings.ark_base_url).rstrip("/")


def _resolve_model(settings: Settings) -> str:
    return settings.ark_review_model or "doubao-seed-1-6-flash-250828"


def call_ark(
    messages: list[dict],
    settings: Settings,
    temperature: float | None = None,
    max_tokens: int = 500,
) -> str:
    api_key = settings.ark_api_key
    if not api_key:
        raise AppError("ARK_KEY_MISSING", "火山方舟 API key 未配置", 503)

    base_url = _resolve_url(settings)
    model = _resolve_model(settings)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.review_temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }

    response = request_with_retry(
        lambda: httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.external_timeout_seconds,
        ),
        "好评文案生成",
    )

    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()
    if not content:
        raise AppError("REVIEW_EMPTY", "模型返回空文案", 500)
    return content
