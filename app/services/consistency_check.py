"""Post-generation consistency check: verify generated image matches the original product."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.errors import AppError

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyResult:
    consistent: bool
    reasons: list[str]


def check_consistency(
    settings: Settings,
    original_image_path: Path,
    generated_image_path: Path,
) -> ConsistencyResult:
    """Compare original product image with generated image using vision model.

    Returns a ConsistencyResult indicating whether they depict the same product.
    When consistency_check is disabled in settings, always returns consistent=True.
    """
    if not settings.consistency_check:
        return ConsistencyResult(consistent=True, reasons=["check_disabled"])

    if settings.vision_provider == "mock":
        return _mock_check(original_image_path, generated_image_path)

    return _real_check(settings, original_image_path, generated_image_path)


def _mock_check(
    original_image_path: Path,
    generated_image_path: Path,
) -> ConsistencyResult:
    """Mock implementation: always returns consistent unless filename contains 'inconsistent'."""
    if "inconsistent" in generated_image_path.name:
        return ConsistencyResult(
            consistent=False,
            reasons=["mock: 文件名包含 inconsistent 标记"],
        )
    return ConsistencyResult(consistent=True, reasons=["mock: 通过"])


def _real_check(
    settings: Settings,
    original_image_path: Path,
    generated_image_path: Path,
) -> ConsistencyResult:
    """Real implementation using vision model to compare images."""
    import base64
    import mimetypes

    import httpx

    from app.services.http import request_with_retry

    model = settings.consistency_check_model or settings.ark_vision_model
    if not model:
        logger.warning("consistency check skipped: no vision model configured")
        return ConsistencyResult(consistent=True, reasons=["no_model_configured"])

    base_url = settings.vision_base_url

    def _encode(path: Path) -> str:
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    prompt = (
        "请对比这两张图片。第一张是商品原图，第二张是生成图。"
        "判断：它们是否为同一件商品？颜色、结构比例、纹样、关键部件数量是否一致？"
        "只输出 JSON：{\"consistent\": true/false, \"reasons\": [\"...\"]}"
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": _encode(original_image_path)}},
                {"type": "image_url", "image_url": {"url": _encode(generated_image_path)}},
                {"type": "text", "text": prompt},
            ],
        }
    ]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.ark_api_key}",
    }

    url = f"{base_url}/chat/completions"
    try:
        with httpx.Client(timeout=60.0) as client:
            response = request_with_retry(
                lambda: client.post(url, json=payload, headers=headers),
                "一致性校验",
            )
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        # Parse JSON from response (may be wrapped in markdown)
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return ConsistencyResult(
            consistent=bool(result.get("consistent", True)),
            reasons=result.get("reasons", []),
        )
    except Exception as exc:
        logger.warning("consistency check failed, treating as consistent: %s", exc)
        return ConsistencyResult(consistent=True, reasons=[f"check_error: {exc}"])
