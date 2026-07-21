from __future__ import annotations

import time
import json
from collections.abc import Callable

import httpx

from app.errors import AppError


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_content_review_rejection(response: httpx.Response) -> bool:
    try:
        payload = response.json()
        error_text = json.dumps(payload, ensure_ascii=False).lower()
    except (ValueError, TypeError):
        return False
    markers = (
        "sensitivecontent",
        "content_filter",
        "contentpolicy",
        "content_policy",
        "moderation",
        "safety_violation",
        "内容审核",
        "敏感内容",
    )
    return any(marker in error_text for marker in markers)


def request_with_retry(send: Callable[[], httpx.Response], operation: str) -> httpx.Response:
    for attempt in range(3):
        try:
            response = send()
        except httpx.TimeoutException as exc:
            if attempt == 2:
                raise AppError(
                    "PROVIDER_TIMEOUT", f"{operation}超时，请稍后重试", 504
                ) from exc
            time.sleep(0.4 * (attempt + 1))
            continue
        except httpx.HTTPError as exc:
            raise AppError("PROVIDER_REQUEST_FAILED", f"{operation}请求失败", 502) from exc

        if response.status_code in RETRYABLE_STATUS and attempt < 2:
            response.close()
            time.sleep(0.4 * (attempt + 1))
            continue
        if response.status_code == 429:
            response.close()
            raise AppError("PROVIDER_RATE_LIMITED", "外部服务请求过多，请稍后重试", 429)
        if response.status_code in {400, 422} and _is_content_review_rejection(response):
            response.close()
            raise AppError(
                "CONTENT_REVIEW_REJECTED", "图片或提示词未通过内容审核，请调整后重试", 422
            )
        if response.status_code in {400, 422}:
            response.close()
            raise AppError("PROVIDER_BAD_REQUEST", f"{operation}参数未被服务接受", 502)
        if response.status_code in {401, 403}:
            response.close()
            raise AppError("PROVIDER_AUTH_FAILED", f"{operation}鉴权失败，请检查服务端配置", 502)
        if response.status_code >= 500:
            response.close()
            raise AppError("PROVIDER_UNAVAILABLE", f"{operation}暂时不可用", 502)
        if response.status_code >= 400:
            response.close()
            raise AppError("PROVIDER_REJECTED", f"{operation}未成功", 502)
        return response

    raise AppError("PROVIDER_UNAVAILABLE", f"{operation}暂时不可用", 502)
