"""数量硬校验：直接问视觉模型「图里有几个主体」，与事实卡结构化数量做相等判定。

与 ``consistency_check``（模糊「像不像」判断）互补：模糊判断可能放行一张「六匹马」
的图，而硬校验拿数字做相等判定，不等即判失败。仅在事实卡存在有效 ``主体数量`` 且
开关 ``count_hard_check_enabled`` 打开时执行；数量为空或开关关闭时直接返回通过。

设计取舍：
- 异常（API 故障/解析失败/模型说数不清）一律视为「通过」并记 warning，不阻断流程，
  与 ``consistency_check`` 异常策略保持一致，避免硬校验自身故障拖垮整条生成链路。
- mock 模式按文件名 ``wrongcount`` 标记返回失败，便于测试驱动降级/重试逻辑。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class CountCheckResult:
    passed: bool
    detected_count: int | None
    reason: str


def check_count(
    settings: Settings,
    image_path: Path,
    subject_name: str,
    expected_count: int,
) -> CountCheckResult:
    """对生成图做主体数量硬校验。

    返回 ``passed=True`` 的情形：开关关闭、数量为空、mock 通过、真实校验数字相等、
    以及任何异常/无法判定（保守放行 + warning）。
    返回 ``passed=False`` 仅当：视觉模型明确数出一个 != expected_count 的整数。
    """
    if not settings.count_hard_check_enabled:
        return CountCheckResult(passed=True, detected_count=None, reason="check_disabled")

    if settings.vision_provider == "mock":
        return _mock_check(image_path, expected_count)

    return _real_check(settings, image_path, subject_name, expected_count)


def _mock_check(image_path: Path, expected_count: int) -> CountCheckResult:
    """Mock：文件名含 wrongcount 视为数量错；否则通过。"""
    if "wrongcount" in image_path.name:
        return CountCheckResult(
            passed=False,
            detected_count=expected_count + 1,
            reason=f"mock: 文件名包含 wrongcount 标记，判定数量不符",
        )
    return CountCheckResult(
        passed=True, detected_count=expected_count, reason="mock: 通过"
    )


def _real_check(
    settings: Settings,
    image_path: Path,
    subject_name: str,
    expected_count: int,
) -> CountCheckResult:
    """真实实现：让视觉模型数主体个数，与期望数量做相等判定。"""
    import base64
    import mimetypes

    import httpx

    from app.services.http import request_with_retry

    model = settings.consistency_check_model or settings.ark_vision_model
    if not model:
        logger.warning("count hard check skipped: no vision model configured")
        return CountCheckResult(passed=True, detected_count=None, reason="no_model_configured")

    base_url = settings.vision_base_url
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    image_data_uri = f"data:{media_type};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"

    prompt = (
        f"数一数这张图片里「{subject_name}」一共有几个？"
        f"只回答一个整数，不要解释、不要单位。如果数不清或无法逐一计数，回答 unknown。"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_uri}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    payload = {"model": model, "messages": messages, "max_tokens": 32}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.ark_api_key}",
    }
    url = f"{base_url}/chat/completions"
    try:
        with httpx.Client(timeout=60.0) as client:
            response = request_with_retry(
                lambda: client.post(url, json=payload, headers=headers),
                "数量硬校验",
            )
        body = response.json()
        content = body["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("count hard check request failed, treating as passed: %s", exc)
        return CountCheckResult(passed=True, detected_count=None, reason=f"check_error: {exc}")

    detected = _parse_int(content)
    if detected is None:
        # 模型说数不清或返回非数字 → 无法判定，保守放行（不阻断），但记 warning 供排查。
        logger.warning(
            "count hard check could not parse integer from response %r, treating as passed",
            content,
        )
        return CountCheckResult(
            passed=True, detected_count=None, reason=f"unparseable: {content[:80]}"
        )

    if detected == expected_count:
        return CountCheckResult(
            passed=True, detected_count=detected, reason=f"检测到{detected}个，符合"
        )
    logger.warning(
        "count hard check FAILED: detected=%d expected=%d subject=%s",
        detected, expected_count, subject_name,
    )
    return CountCheckResult(
        passed=False,
        detected_count=detected,
        reason=f"检测到{detected}个{subject_name}，应为{expected_count}个",
    )


_INT_RE = re.compile(r"\d+")


def _parse_int(text: str) -> int | None:
    """从模型返回文本中提取一个整数；含 unknown/数不清 等返回 None。"""
    if not text:
        return None
    lower = text.lower()
    if "unknown" in lower or "数不清" in text or "无法" in text or "不清" in text:
        return None
    match = _INT_RE.search(text)
    if not match:
        return None
    return int(match.group(0))
