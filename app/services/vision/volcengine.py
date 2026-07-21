from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.errors import AppError
from app.schemas import FactCard
from app.services.http import request_with_retry
from app.services.normalize import normalize_fact_card

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "fact_card.txt"


def _format_validation_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = " -> ".join(str(p) for p in err["loc"])
        lines.append(f"  - 路径: {loc}")
        lines.append(f"    收到值: {err.get('input', '?')!r}")
        lines.append(f"    错误: {err['msg']}")
    return "\n".join(lines)


def _format_errors_for_model(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"- {loc}: {err['msg']} (收到: {err.get('input', '?')!r})")
    return "\n".join(lines)


def image_data_uri(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class VolcengineVisionProvider:
    name = "volcengine"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _content(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.1}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            response = request_with_retry(
                lambda: client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                ),
                "视觉识别",
            )
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AppError("VISION_RESPONSE_INVALID", "视觉模型返回内容无法读取", 502) from exc

    def _try_validate(self, raw: str, attempt: int) -> tuple[FactCard | None, str | None, dict | None, ValidationError | None]:
        """Try to normalize and validate. Returns (card, cleaned_text, parsed_dict, error)."""
        try:
            normalized = normalize_fact_card(raw)
        except ValueError as exc:
            logger.warning(
                "视觉模型第%d次响应 JSON 解析失败:\n原始响应前500字: %s\n错误: %s",
                attempt, raw[:500], str(exc),
            )
            return None, None, None, None

        try:
            card = FactCard.model_validate(normalized)
            return card, raw, normalized, None
        except ValidationError as exc:
            logger.warning(
                "视觉模型第%d次响应 Pydantic 校验失败:\n"
                "模型ID: %s\n"
                "原始响应前500字:\n%s\n"
                "JSON 解析结果:\n%s\n"
                "Pydantic 校验错误:\n%s",
                attempt,
                self.model,
                raw[:500],
                json.dumps(normalized, ensure_ascii=False, indent=2)[:1000],
                _format_validation_errors(exc),
            )
            return None, raw, normalized, exc

    def analyze(self, image_path: Path) -> FactCard:
        if not self.api_key or not self.model:
            raise AppError(
                "VOLCENGINE_NOT_CONFIGURED", "火山视觉服务尚未配置 API Key 和模型", 503
            )
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        schema_json = json.dumps(
            FactCard.model_json_schema(by_alias=True), ensure_ascii=False, indent=2
        )
        prompt += "\n\n【JSON Schema】\n" + schema_json
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri(image_path)}},
                ],
            }
        ]

        started = time.perf_counter()
        raw = self._content(messages)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        logger.info("视觉模型第1次响应耗时: %dms, 模型: %s", elapsed_ms, self.model)

        card, _, normalized, first_error = self._try_validate(raw, 1)
        if card is not None:
            return card

        repair_prompt = (
            "上面的输出未通过 JSON Schema 校验。\n\n"
            "【校验错误】\n"
            f"{_format_errors_for_model(first_error) if first_error else '无法解析为 JSON'}\n\n"
            "【正确 JSON Schema】\n"
            f"{schema_json}\n\n"
            "【要求】\n"
            "只修复格式和字段类型，不增加新商品事实，不添加 Schema 外字段。\n"
            "只输出修复后的纯 JSON，不要输出 Markdown 代码围栏或解释。"
        )
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair_prompt},
        ]

        started2 = time.perf_counter()
        repaired_raw = self._content(repair_messages)
        elapsed_ms2 = round((time.perf_counter() - started2) * 1000)
        logger.info("视觉模型第2次修复响应耗时: %dms, 模型: %s", elapsed_ms2, self.model)

        card2, _, _, second_error = self._try_validate(repaired_raw, 2)
        if card2 is not None:
            return card2

        error_detail = ""
        if second_error:
            error_detail = _format_errors_for_model(second_error)
        raise AppError(
            "FACT_CARD_INVALID",
            "视觉模型两次返回的事实卡均未通过校验",
            502,
            detail={
                "raw_response_1": raw[:2000],
                "raw_response_2": repaired_raw[:2000],
                "validation_errors": second_error.errors() if second_error else [],
                "model": self.model,
            },
        )
