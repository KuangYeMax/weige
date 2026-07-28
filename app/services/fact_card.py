"""事实卡（FactCard）生成与持久化的共享 service 层。

历史背景：原本在产品上传时（``app.api.products.upload_product``）就调用视觉
模型 ``analyze`` 生成事实卡并落盘。为了在批量上传场景下节省 token，事实卡
生成被推迟到「待发记录 generating 阶段」按需触发——只有真正要发好评的产
品才会调用视觉模型，未建待发记录的产品永不消耗 token。

本模块集中提供：
- 上传/详情/生图/删除等接口复用的产品元数据读写辅助函数；
- ``ensure_fact_card``：按需生成事实卡（已有则复用、缺失则生成并回填产
  品名/尺寸/房间到 DB），供调度器 generating 阶段与 regen 兜底使用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import Settings
from app.errors import AppError
from app.schemas import FactCard
from app.services.db import upsert_product
from app.services.vision.mock import MockVisionProvider
from app.services.vision.volcengine import VolcengineVisionProvider

logger = logging.getLogger(__name__)


# ── 产品元数据读写辅助（从 app.api.products 迁移，保持行为一致） ────────────

def save_metadata_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        raise AppError("FILE_SAVE_FAILED", "元数据保存失败", 500) from exc


def product_metadata_path(settings: Settings, product_id: str) -> Path:
    try:
        safe_id = str(UUID(product_id))
    except ValueError as exc:
        raise AppError("PRODUCT_NOT_FOUND", "商品不存在或已被删除", 404) from exc
    return settings.storage_root / "metadata" / f"product-{safe_id}.json"


def load_product_metadata(
    settings: Settings, product_id: str
) -> tuple[Path, dict[str, Any]]:
    path = product_metadata_path(settings, product_id)
    if not path.is_file():
        raise AppError("PRODUCT_NOT_FOUND", "商品不存在或已被删除", 404)
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError("PRODUCT_METADATA_INVALID", "商品数据无法读取", 500) from exc


def stored_path(settings: Settings, relative_path: str) -> Path:
    root = settings.storage_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise AppError("PRODUCT_METADATA_INVALID", "商品原图无法读取", 500)
    return candidate


def catalog_name(fact_card: FactCard, original_filename: str | None = None) -> str:
    name = fact_card.product_name.strip()
    if name:
        return name
    filename_stem = Path(original_filename or "").stem.strip()
    return filename_stem or "未命名商品"


def vision_provider(settings: Settings):
    if settings.vision_provider == "mock":
        return MockVisionProvider()
    if settings.vision_provider == "volcengine":
        return VolcengineVisionProvider(
            settings.ark_api_key,
            settings.vision_base_url,
            settings.ark_vision_model,
            settings.external_timeout_seconds,
        )
    raise AppError("PROVIDER_NOT_FOUND", "视觉 provider 配置无效", 500)


def _resolve_image_path(settings: Settings, product: dict[str, str]) -> Path:
    """返回产品原图绝对路径；缺失则抛 PRODUCT_IMAGE_MISSING（与调度器一致）。"""
    root = settings.storage_root.resolve()
    image_path = (root / product["image_path"]).resolve()
    if not image_path.is_relative_to(root) or not image_path.is_file():
        raise AppError("PRODUCT_IMAGE_MISSING", "产品原图无法读取", 500)
    return image_path


# ── 按需生成 / 复用事实卡 ──────────────────────────────────────────────────

def ensure_fact_card(settings: Settings, product: dict[str, str]) -> FactCard:
    """返回产品的事实卡：已有且有效则复用，否则调用视觉模型生成并落盘回填。

    - 复用：metadata 中 ``fact_card`` 存在且能通过 ``FactCard`` 校验 → 直接返回。
    - 生成：缺失或损坏 → 调 ``vision_provider.analyze`` 生成，写回 metadata
      （含 vision_provider / vision_model / updated_at），并 ``upsert_product``
      回填产品名、尺寸、房间到 DB，使后续任务与产品库列表直接复用。
    - 这样首次用到某产品的待发任务才花 token，后续任务与产品库列表直接复用。
    """
    product_id = product["product_id"]
    metadata_path, metadata = load_product_metadata(settings, product_id)

    existing = metadata.get("fact_card")
    if existing:
        try:
            return FactCard.model_validate(existing)
        except Exception:
            logger.warning(
                "product %s fact_card 损坏，重新生成", product_id, exc_info=True
            )

    image_path = _resolve_image_path(settings, product)
    provider = vision_provider(settings)
    fact_card = provider.analyze(image_path)

    metadata["fact_card"] = fact_card.model_dump(mode="json", by_alias=True)
    metadata["vision_provider"] = settings.vision_provider
    metadata["vision_model"] = getattr(
        provider, "model", settings.ark_vision_model
    )
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_metadata_json(metadata_path, metadata)

    dims = fact_card.dimensions
    upsert_product(
        settings.db_path,
        product_id=product_id,
        name=catalog_name(fact_card),
        image_path=product["image_path"],
        fact_card_path=metadata_path.relative_to(settings.storage_root).as_posix(),
        created_at=metadata.get("created_at")
        or datetime.now(timezone.utc).isoformat(),
        height_cm=dims.height_cm,
        width_cm=dims.width_cm,
        depth_cm=dims.length_cm,
        weight_kg=dims.weight_kg,
        size_source=dims.size_source,
        room=fact_card.room,
    )
    logger.info("product %s fact_card generated by %s", product_id, settings.vision_provider)
    return fact_card
