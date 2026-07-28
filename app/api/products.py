from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError
from app.schemas import CompareRequest, FactCard, GenerateRequest, Scene
from app.services.db import (
    CodeConflictError,
    CodeEmptyError,
    add_code,
    delete_product,
    get_product,
    list_codes,
    list_products,
    product_exists,
    upsert_product,
)
from app.services.dispatch_generation import create_image_provider, generate_image, prepare_generation, run_provider_generation
from app.services.image_generation.bailian import BailianImageProvider
from app.services.image_generation.mock import MockImageProvider, GenerationResult
from app.services.image_generation.models import (
    get_model,
    list_all_models,
    list_models,
    default_model,
)
from app.services.image_generation.volcengine import VolcengineImageProvider, map_aspect_ratio
from app.services.vision.mock import MockVisionProvider
from app.services.vision.volcengine import VolcengineVisionProvider

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_DECODED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _save_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise AppError("FILE_SAVE_FAILED", "元数据保存失败", 500) from exc


def _product_metadata_path(settings: Settings, product_id: str) -> Path:
    try:
        safe_id = str(UUID(product_id))
    except ValueError as exc:
        raise AppError("PRODUCT_NOT_FOUND", "商品不存在或已被删除", 404) from exc
    return settings.storage_root / "metadata" / f"product-{safe_id}.json"


def _load_product(settings: Settings, product_id: str) -> tuple[Path, dict[str, Any]]:
    path = _product_metadata_path(settings, product_id)
    if not path.is_file():
        raise AppError("PRODUCT_NOT_FOUND", "商品不存在或已被删除", 404)
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError("PRODUCT_METADATA_INVALID", "商品数据无法读取", 500) from exc


def _stored_path(settings: Settings, relative_path: str) -> Path:
    root = settings.storage_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise AppError("PRODUCT_METADATA_INVALID", "商品原图无法读取", 500)
    return candidate


def _catalog_name(fact_card: FactCard, original_filename: str | None = None) -> str:
    name = fact_card.product_name.strip()
    if name:
        return name
    filename_stem = Path(original_filename or "").stem.strip()
    return filename_stem or "未命名商品"


def _cleanup_uploaded_product_files(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to clean up uploaded product artifact: %s", path)


def _vision_provider(settings: Settings):
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


def _image_provider(settings: Settings, provider_name: str | None = None, model_id: str | None = None):
    """Compatibility seam retained for the route and its existing tests."""
    return create_image_provider(settings, provider_name, model_id)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health(request: Request):
        settings = _settings(request)
        return {
            "status": "ok",
            "vision_provider": settings.vision_provider,
            "image_provider": settings.image_provider,
            "volcengine_configured": settings.volcengine_configured,
            "bailian_configured": settings.bailian_configured,
        }

    @router.post("/products/upload")
    async def upload_product(request: Request, image: UploadFile = File(...)):
        settings = _settings(request)
        if image.content_type not in ALLOWED_CONTENT_TYPES:
            raise AppError(
                "IMAGE_FORMAT_UNSUPPORTED", "仅支持 JPG、JPEG、PNG 或 WEBP 图片", 415
            )
        content = await image.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise AppError("IMAGE_TOO_LARGE", "图片不能超过 10MB", 413)
        try:
            with Image.open(io.BytesIO(content)) as decoded:
                if decoded.format not in ALLOWED_DECODED_FORMATS:
                    raise AppError(
                        "IMAGE_FORMAT_UNSUPPORTED", "图片实际格式不受支持", 415
                    )
                width, height = decoded.size
                if width * height > settings.max_image_pixels:
                    raise AppError(
                        "IMAGE_DIMENSIONS_TOO_LARGE", "图片总像素过大，请缩小后上传", 400
                    )
                if min(width, height) < settings.min_image_dimension:
                    raise AppError("IMAGE_TOO_SMALL", "图片最小边不能低于 512 像素", 400)
                normalized = ImageOps.exif_transpose(decoded).convert("RGB")
        except AppError:
            raise
        except Image.DecompressionBombError as exc:
            raise AppError(
                "IMAGE_DIMENSIONS_TOO_LARGE", "图片总像素过大，请缩小后上传", 400
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AppError("IMAGE_INVALID", "文件不是可解析的有效图片", 400) from exc

        upload_dir = settings.storage_root / "uploads"
        output_path = upload_dir / f"{uuid4()}.jpg"
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            normalized.save(output_path, format="JPEG", quality=94, optimize=True)
        except OSError as exc:
            raise AppError("FILE_SAVE_FAILED", "原图保存失败", 500) from exc
        finally:
            normalized.close()

        product_id = str(uuid4())
        try:
            fact_card = _vision_provider(settings).analyze(output_path)
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        relative_path = output_path.relative_to(settings.storage_root).as_posix()
        created_at = datetime.now(timezone.utc).isoformat()
        metadata = {
            "product_id": product_id,
            "original_image_path": relative_path,
            "original_size_bytes": len(content),
            "width": width,
            "height": height,
            "fact_card": fact_card.model_dump(mode="json", by_alias=True),
            "vision_provider": settings.vision_provider,
            "vision_model": getattr(_vision_provider(settings), "model", settings.ark_vision_model),
            "created_at": created_at,
        }
        metadata_path = _product_metadata_path(settings, product_id)
        try:
            _save_json(metadata_path, metadata)
            dims = fact_card.dimensions
            upsert_product(
                settings.db_path,
                product_id=product_id,
                name=_catalog_name(fact_card, image.filename),
                image_path=relative_path,
                fact_card_path=metadata_path.relative_to(settings.storage_root).as_posix(),
                created_at=created_at,
                height_cm=dims.height_cm,
                width_cm=dims.width_cm,
                depth_cm=dims.length_cm,
                weight_kg=dims.weight_kg,
                size_source=dims.size_source,
                room=fact_card.room,
            )
        except AppError:
            _cleanup_uploaded_product_files(metadata_path, output_path)
            raise
        except (sqlite3.Error, OSError) as exc:
            _cleanup_uploaded_product_files(metadata_path, output_path)
            raise AppError("PRODUCT_DB_SAVE_FAILED", "商品目录写入失败，上传已回滚", 500) from exc
        return {
            "product_id": product_id,
            "original_image_url": f"/storage/{relative_path}",
            "fact_card": metadata["fact_card"],
            "image_info": {"width": width, "height": height, "size_bytes": len(content)},
            "vision_provider": settings.vision_provider,
        }

    @router.post("/products/{product_id}/fact-card")
    async def save_fact_card(product_id: str, request: Request):
        settings = _settings(request)
        metadata_path, metadata = _load_product(settings, product_id)
        try:
            body = await request.json()
            fact_card = FactCard.model_validate(body)
        except (ValidationError, json.JSONDecodeError, TypeError) as exc:
            raise AppError("FACT_CARD_INVALID", "事实卡未通过校验，请检查商品名称和字段类型", 422) from exc
        previous_fact_card = metadata.get("fact_card")
        previous_updated_at = metadata.get("updated_at")
        metadata["fact_card"] = fact_card.model_dump(mode="json", by_alias=True)
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _save_json(metadata_path, metadata)
            dims = fact_card.dimensions
            upsert_product(
                settings.db_path,
                product_id=product_id,
                name=_catalog_name(fact_card),
                image_path=metadata.get("original_image_path", ""),
                fact_card_path=metadata_path.relative_to(settings.storage_root).as_posix(),
                created_at=metadata.get("created_at") or datetime.now(timezone.utc).isoformat(),
                height_cm=dims.height_cm,
                width_cm=dims.width_cm,
                depth_cm=dims.length_cm,
                weight_kg=dims.weight_kg,
                size_source=dims.size_source,
                room=fact_card.room,
            )
        except AppError:
            raise
        except (sqlite3.Error, OSError) as exc:
            metadata["fact_card"] = previous_fact_card
            if previous_updated_at is None:
                metadata.pop("updated_at", None)
            else:
                metadata["updated_at"] = previous_updated_at
            _save_json(metadata_path, metadata)
            raise AppError("PRODUCT_DB_SAVE_FAILED", "商品目录更新失败，事实卡未保存", 500) from exc
        return {"fact_card": metadata["fact_card"]}

    @router.get("/models")
    def get_models(provider: str | None = None):
        models = list_models(provider)
        return {
            "models": [
                {
                    "provider": m.provider,
                    "model_id": m.model_id,
                    "label": m.label,
                    "api_style": m.api_style,
                    "supports_reference": m.supports_reference,
                    "note": m.note,
                }
                for m in models
            ]
        }

    @router.post("/products/{product_id}/generate")
    def generate_product_image(product_id: str, body: GenerateRequest, request: Request):
        settings = _settings(request)
        _, product = _load_product(settings, product_id)
        reference_path = _stored_path(settings, product["original_image_path"])
        generation_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        base_metadata = {
            "generation_id": generation_id,
            "product_id": product_id,
            "provider": body.image_provider or settings.image_provider,
            "model": body.image_model,
            "prompt": None,
            "product_brief": None,
            "fact_card": body.fact_card.model_dump(mode="json", by_alias=True),
            "size": None,
            "seed": None,
            "original_image_path": product["original_image_path"],
            "generated_image_path": None,
            "graded_image_path": None,
            "requested_at": created_at,
            "elapsed_ms": None,
            "error_reason": None,
            "used_reference": True,
            "realism": None,
        }
        generation_path = settings.storage_root / "metadata" / f"generation-{generation_id}.json"
        try:
            generated = generate_image(
                settings,
                reference_path=reference_path,
                fact_card=body.fact_card,
                shot_type=body.shot_type,
                scene_index=body.scene_index,
                aspect_ratio=body.aspect_ratio,
                provider_name=body.image_provider,
                model_id=body.image_model,
                provider_factory=lambda s, p, m, _output: _image_provider(s, p, m),
            )
        except AppError as exc:
            base_metadata["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            base_metadata["error_reason"] = exc.code
            _save_json(generation_path, base_metadata)
            raise
        except Exception as exc:
            base_metadata["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            base_metadata["error_reason"] = "INTERNAL_ERROR"
            _save_json(generation_path, base_metadata)
            raise AppError("GENERATION_FAILED", "图片生成失败，请稍后重试", 500) from exc
        relative_output = generated.output_path.relative_to(settings.storage_root).as_posix()
        graded_relative = generated.graded_path.relative_to(settings.storage_root).as_posix() if generated.graded_path else None
        graded_image_url = f"/storage/{graded_relative}" if graded_relative else None

        base_metadata.update(
            {
                "provider": generated.provider,
                "model": generated.model,
                "prompt": generated.prompt,
                "product_brief": generated.product_brief,
                "size": generated.size,
                "seed": generated.seed,
                "generated_image_path": relative_output,
                "graded_image_path": graded_relative,
                "elapsed_ms": generated.elapsed_ms,
                "used_reference": generated.used_reference,
                "realism": generated.realism,
                "thinking_mode": generated.thinking_mode,
                "inject_appearance": generated.inject_appearance,
                "camera_pos": generated.camera_pos,
                "generation_path": "workbench",
            }
        )
        _save_json(generation_path, base_metadata)
        return {
            "generation_id": generation_id,
            "generated_image_url": f"/storage/{relative_output}",
            "graded_image_url": graded_image_url,
            "provider": generated.provider,
            "model": generated.model,
            "prompt": generated.prompt,
            "product_brief": generated.product_brief,
            "fact_card": base_metadata["fact_card"],
            "size": generated.size,
            "seed": generated.seed,
            "elapsed_ms": generated.elapsed_ms,
            "created_at": created_at,
            "used_reference": generated.used_reference,
        }

    @router.post("/products/{product_id}/generate-compare")
    async def generate_compare(product_id: str, body: CompareRequest, request: Request):
        settings = _settings(request)
        _, product = _load_product(settings, product_id)
        reference_path = _stored_path(settings, product["original_image_path"])

        fact_card_data = product.get("fact_card")
        if not fact_card_data:
            raise AppError("FACT_CARD_MISSING", "商品尚未生成事实卡", 422)
        fact_card = FactCard.model_validate(fact_card_data)
        scenes = fact_card.scenes or []
        scene = scenes[0] if scenes else Scene(scene="通用场景", placement="自然摆放")

        prep = prepare_generation(
            settings,
            reference_path=reference_path,
            fact_card=fact_card,
            shot_type=body.shot_type,
            scene=scene,
        )
        prompt = prep.prompt
        effective_reference = prep.effective_reference
        preprocessed_tmp = prep.preprocessed_tmp
        try:
            all_models = list_all_models()
            if body.models:
                selected = [m for m in all_models if m.model_id in body.models]
                if not selected:
                    raise AppError("MODEL_NOT_FOUND", "未找到指定的模型", 400)
            else:
                selected = all_models

            semaphore = asyncio.Semaphore(4)
            total_started = time.perf_counter()

            async def run_one(model_entry):
                async with semaphore:
                    started = time.perf_counter()
                    try:
                        use_provider = None if settings.image_provider == "mock" else model_entry.provider
                        provider_obj, pname = _image_provider(settings, use_provider, model_entry.model_id)
                        used_ref = model_entry.supports_reference
                        ref_path = effective_reference if used_ref else None

                        if pname == "bailian":
                            generation_size = body.aspect_ratio
                        else:
                            generation_size = map_aspect_ratio(body.aspect_ratio)
                        result = await asyncio.to_thread(
                            run_provider_generation,
                            provider_obj,
                            pname,
                            ref_path if pname == "bailian" else effective_reference,
                            prompt,
                            generation_size,
                            model_entry.model_id if pname == "volcengine" else None,
                            used_ref,
                        )

                        elapsed = round((time.perf_counter() - started) * 1000)
                        relative = result.output_path.relative_to(settings.storage_root).as_posix()
                        return {
                            "provider": model_entry.provider,
                            "model_id": model_entry.model_id,
                            "label": model_entry.label,
                            "status": "ok",
                            "image_url": f"/storage/{relative}",
                            "error": None,
                            "elapsed_ms": elapsed,
                            "used_reference": used_ref,
                        }
                    except Exception as exc:
                        elapsed = round((time.perf_counter() - started) * 1000)
                        err_msg = str(exc) if not isinstance(exc, AppError) else exc.message
                        return {
                            "provider": model_entry.provider,
                            "model_id": model_entry.model_id,
                            "label": model_entry.label,
                            "status": "error",
                            "image_url": None,
                            "error": err_msg,
                            "elapsed_ms": elapsed,
                            "used_reference": model_entry.supports_reference,
                        }

            results = await asyncio.gather(*[run_one(m) for m in selected])

            total_elapsed = round((time.perf_counter() - total_started) * 1000)
            return {
                "results": results,
                "total_elapsed_ms": total_elapsed,
                "models_count": len(selected),
            }
        finally:
            if preprocessed_tmp:
                preprocessed_tmp.unlink(missing_ok=True)

    # --------------- Product catalog & codes ---------------

    @router.get("/products")
    def get_products(request: Request):
        settings = _settings(request)
        products = list_products(settings.db_path)
        return {"products": products}

    @router.get("/products/{product_id}")
    def get_product_detail(product_id: str, request: Request):
        settings = _settings(request)
        if not product_exists(settings.db_path, product_id):
            raise AppError("PRODUCT_NOT_FOUND", "商品不存在", 404)
        _, metadata = _load_product(settings, product_id)
        codes = list_codes(settings.db_path, product_id)
        return {
            "product_id": product_id,
            "name": metadata.get("name", ""),
            "original_image_url": metadata.get("original_image_url", ""),
            "fact_card": metadata.get("fact_card"),
            "image_info": metadata.get("image_info"),
            "codes": codes,
            "created_at": metadata.get("created_at", ""),
        }

    @router.post("/products/{product_id}/codes")
    async def add_product_code(product_id: str, request: Request):
        settings = _settings(request)
        if not product_exists(settings.db_path, product_id):
            raise AppError("PRODUCT_NOT_FOUND", "商品不存在", 404)
        body = await request.json()
        code = body.get("code", "")
        try:
            result = add_code(settings.db_path, product_id, code)
        except CodeEmptyError:
            raise AppError("CODE_EMPTY", "编号不能为空", 400)
        except CodeConflictError as exc:
            raise AppError("CODE_CONFLICT", f"编号 '{exc.code}' 已被产品 {exc.existing_product_id} 占用", 409)
        return result

    @router.get("/products/{product_id}/codes")
    def get_product_codes(product_id: str, request: Request):
        settings = _settings(request)
        if not product_exists(settings.db_path, product_id):
            raise AppError("PRODUCT_NOT_FOUND", "商品不存在", 404)
        codes = list_codes(settings.db_path, product_id)
        return {"codes": codes}

    @router.patch("/products/{product_id}/room")
    async def update_product_room(product_id: str, request: Request):
        from app.services.scale_anchors import ROOM_COMPANIONS

        settings = _settings(request)
        if not product_exists(settings.db_path, product_id):
            raise AppError("PRODUCT_NOT_FOUND", "商品不存在", 404)
        body = await request.json()
        room = body.get("room")
        if room is not None and room not in ROOM_COMPANIONS:
            valid = "、".join(ROOM_COMPANIONS.keys())
            raise AppError("ROOM_INVALID", f"房间必须为以下之一：{valid}", 400)
        from app.services.db import _connect
        conn = _connect(settings.db_path)
        try:
            conn.execute(
                "UPDATE products SET room = ? WHERE product_id = ?",
                (room, product_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"product_id": product_id, "room": room}

    @router.delete("/products/{product_id}")
    def delete_product_route(product_id: str, request: Request):
        settings = _settings(request)
        product = get_product(settings.db_path, product_id)
        if not product:
            raise AppError("PRODUCT_NOT_FOUND", "商品不存在或已被删除", 404)

        storage_root = settings.storage_root
        image_path = storage_root / product["image_path"]
        metadata_path = storage_root / product["fact_card_path"]

        try:
            delete_product(settings.db_path, product_id)
        except Exception as exc:
            logger.exception("删除产品数据库记录失败 %s", product_id)
            raise AppError("DELETE_FAILED", "删除失败，请稍后重试", 500) from exc

        errors = []
        try:
            if image_path.is_file():
                image_path.unlink(missing_ok=True)
            if metadata_path.is_file():
                metadata_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除产品文件失败 %s: %s", product_id, exc)
            errors.append(str(exc))

        gen_dir = storage_root / "metadata"
        if gen_dir.is_dir():
            for gen_file in gen_dir.glob("generation-*.json"):
                try:
                    gen_data = json.loads(gen_file.read_text(encoding="utf-8"))
                    if gen_data.get("product_id") == product_id:
                        for key in ("generated_image_path", "graded_image_path"):
                            rel = gen_data.get(key)
                            if rel:
                                (storage_root / rel).unlink(missing_ok=True)
                        gen_file.unlink(missing_ok=True)
                except (OSError, json.JSONDecodeError):
                    pass

        return {"status": "deleted", "product_id": product_id}

    return router
