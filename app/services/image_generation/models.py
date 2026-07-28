from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ImageModel:
    provider: Literal["volcengine", "bailian"]
    model_id: str
    api_style: Literal["sync", "async"]
    supports_reference: bool
    label: str
    note: str = ""


MODELS: list[ImageModel] = [
    ImageModel(
        provider="volcengine",
        model_id="doubao-seedream-4-0-250828",
        api_style="sync",
        supports_reference=True,
        label="Seedream 4.0",
    ),
    ImageModel(
        provider="volcengine",
        model_id="doubao-seedream-4-5-251128",
        api_style="sync",
        supports_reference=True,
        label="Seedream 4.5",
    ),
    ImageModel(
        provider="volcengine",
        model_id="doubao-seedream-5-0-260128",
        api_style="sync",
        supports_reference=True,
        label="Seedream 5.0",
    ),
    ImageModel(
        provider="volcengine",
        model_id="doubao-seedream-5-0-pro-260628",
        api_style="sync",
        supports_reference=True,
        label="Seedream 5.0 Pro",
    ),
    ImageModel(
        provider="bailian",
        model_id="wan2.7-image-pro",
        api_style="sync",
        supports_reference=True,
        label="万相 2.7 Pro",
        note="图生图/多图参考/主体保持",
    ),
    ImageModel(
        provider="bailian",
        model_id="wan2.7-image",
        api_style="sync",
        supports_reference=True,
        label="万相 2.7",
        note="图生图/多图参考/主体保持",
    ),
    ImageModel(
        provider="bailian",
        model_id="wan2.5-i2i-preview",
        api_style="async",
        supports_reference=True,
        label="万相图像编辑 2.5",
        note="图生图/主体保持",
    ),
]

_BY_ID: dict[str, ImageModel] = {m.model_id: m for m in MODELS}
_BY_PROVIDER: dict[str, list[ImageModel]] = {}
for _m in MODELS:
    _BY_PROVIDER.setdefault(_m.provider, []).append(_m)

_DEFAULTS: dict[str, str] = {
    "volcengine": "doubao-seedream-4-0-250828",
    "bailian": "wan2.7-image",
}


def list_all_models() -> list[ImageModel]:
    return list(MODELS)


def list_models(provider: str | None = None) -> list[ImageModel]:
    if provider is None:
        return list(MODELS)
    return list(_BY_PROVIDER.get(provider, []))


def get_model(model_id: str) -> ImageModel | None:
    return _BY_ID.get(model_id)


def default_model(provider: str) -> ImageModel | None:
    mid = _DEFAULTS.get(provider)
    if mid:
        return _BY_ID.get(mid)
    return None
