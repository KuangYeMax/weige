"""Deterministic preprocessing for vision model output before Pydantic validation.

Rules: fix format only, never invent product facts.
"""
from __future__ import annotations

import json
import re
from typing import Any


_NULL_NUMERIC_PATTERNS = re.compile(
    r"^(未知|无法判断|不确定|不详|无|-|—|N/A|n/a|null|None|)$"
)

_NUMERIC_WITH_UNIT = re.compile(
    r"^[约≈~]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:cm|mm|m|kg|g|lb|lbs|千克|克|厘米|毫米|米)?$"
)

_ARRAY_FIELDS = {
    "画面中需忽略", "关键结构", "颜色与材质观感", "文字与规格",
    "自然场景", "保真锁", "不确定项", "其他规格", "细节照",
}

_OBJECT_FIELDS = {"尺寸", "建议拍法"}

_NUMERIC_FIELDS = {"长_cm", "宽_cm", "高_cm", "重量_kg"}

# 顶层结构化主体数量字段：模型可能返回 int / float / "5" / "无法确认" 等，
# 统一规整为 int 或 None，避免文本解析静默跳过的坑。
_SUBJECT_COUNT_FIELD = "主体数量"
_SUBJECT_COUNT_UNCONFIRMED_FIELD = "主体数量待确认"


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        fenced = next((part for part in parts if "{" in part), text)
        text = fenced.removeprefix("json").strip()
    return text


def _extract_json_object(text: str) -> dict:
    text = _strip_code_fences(text)
    start = text.find("{")
    if start < 0:
        raise ValueError("模型输出中没有 JSON 对象")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return value


def _unwrap_fact_card(data: dict) -> dict:
    if "fact_card" in data and isinstance(data["fact_card"], dict) and len(data) <= 2:
        return data["fact_card"]
    return data


def _try_parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    if _NULL_NUMERIC_PATTERNS.match(value.strip()):
        return None
    match = _NUMERIC_WITH_UNIT.match(value.strip())
    if match:
        return float(match.group(1))
    return None


def _try_parse_int(value: Any) -> int | None:
    """规整主体数量：数字或数字字符串转 int，其余返回 None。"""
    num = _try_parse_numeric(value)
    if num is None:
        return None
    return int(num)


def _try_parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "是", "待确认", "y"):
            return True
        if v in ("false", "0", "no", "否", "", "n"):
            return False
    return None


def _normalize_array(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        return [value]
    return []


def _normalize_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def normalize_fact_card(raw: str) -> dict:
    """Parse raw model output text into a normalized dict ready for Pydantic validation."""
    data = _extract_json_object(raw)
    data = _unwrap_fact_card(data)

    for field in _ARRAY_FIELDS:
        if field in data:
            data[field] = _normalize_array(data[field])

    for field in _OBJECT_FIELDS:
        if field in data:
            data[field] = _normalize_object(data[field])

    dims = data.get("尺寸")
    if isinstance(dims, dict):
        for nf in _NUMERIC_FIELDS:
            if nf in dims:
                dims[nf] = _try_parse_numeric(dims[nf])
        if "其他规格" in dims:
            dims["其他规格"] = _normalize_array(dims["其他规格"])

    # 主体数量结构化规整：模型若返回 "5" / 5.0 / "无法确认" 等统一成 int|None，
    # 避免下游靠文本解析静默跳过（用户明确要求结构化字段，不靠自然语言解析）。
    if _SUBJECT_COUNT_FIELD in data:
        data[_SUBJECT_COUNT_FIELD] = _try_parse_int(data[_SUBJECT_COUNT_FIELD])
    if _SUBJECT_COUNT_UNCONFIRMED_FIELD in data:
        data[_SUBJECT_COUNT_UNCONFIRMED_FIELD] = _try_parse_bool(
            data[_SUBJECT_COUNT_UNCONFIRMED_FIELD]
        )

    return data
