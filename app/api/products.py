from __future__ import annotations

import io
import json
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
from app.schemas import FactCard, GenerateRequest, Scene
from app.services.fact_card_compress import render_generation_prompt
from app.services.image_generation.mock import MockImageProvider
from app.services.image_generation.volcengine import VolcengineImageProvider, map_aspect_ratio
from app.services.vision.mock import MockVisionProvider
from app.services.vision.volcengine import VolcengineVisionProvider


ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_DECODED_FORMATS = {"JPEG", "PNG", "WEBP"}
PROMPT_PATH = Path(__file__).resolve().parent.parent / "services" / "prompts" / "image_generation.txt"


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


def _image_provider(settings: Settings):
    output_dir = settings.storage_root / "generated"
    if settings.image_provider == "mock":
        return MockImageProvider(output_dir)
    if settings.image_provider == "volcengine":
        return VolcengineImageProvider(
            settings.ark_api_key,
            settings.image_base_url,
            settings.ark_image_model,
            output_dir,
            settings.external_timeout_seconds,
            settings.max_download_bytes,
        )
    raise AppError("PROVIDER_NOT_FOUND", "图片 provider 配置无效", 500)


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
        metadata = {
            "product_id": product_id,
            "original_image_path": relative_path,
            "original_size_bytes": len(content),
            "width": width,
            "height": height,
            "fact_card": fact_card.model_dump(mode="json", by_alias=True),
            "vision_provider": settings.vision_provider,
            "vision_model": getattr(_vision_provider(settings), "model", settings.ark_vision_model),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_json(_product_metadata_path(settings, product_id), metadata)
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
        metadata["fact_card"] = fact_card.model_dump(mode="json", by_alias=True)
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_json(metadata_path, metadata)
        return {"fact_card": metadata["fact_card"]}

    @router.post("/products/{product_id}/generate")
    def generate_product_image(product_id: str, body: GenerateRequest, request: Request):
        settings = _settings(request)
        _, product = _load_product(settings, product_id)
        reference_path = _stored_path(settings, product["original_image_path"])
        scenes = body.fact_card.scenes or []
        if body.scene_index >= len(scenes):
            raise AppError("SCENE_INVALID", "请选择事实卡中的有效场景", 422)
        scene = scenes[body.scene_index]
        size = map_aspect_ratio(body.aspect_ratio)
        template = PROMPT_PATH.read_text(encoding="utf-8")
        product_brief, prompt = render_generation_prompt(
            template, body.fact_card, scene, body.shot_type
        )
        generation_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        base_metadata = {
            "generation_id": generation_id,
            "product_id": product_id,
            "provider": settings.image_provider,
            "model": settings.ark_image_model if settings.image_provider == "volcengine" else MockImageProvider.model,
            "prompt": prompt,
            "product_brief": product_brief,
            "fact_card": body.fact_card.model_dump(mode="json", by_alias=True),
            "size": size,
            "seed": None,
            "original_image_path": product["original_image_path"],
            "generated_image_path": None,
            "requested_at": created_at,
            "elapsed_ms": None,
            "error_reason": None,
        }
        generation_path = settings.storage_root / "metadata" / f"generation-{generation_id}.json"
        try:
            provider = _image_provider(settings)
            result = provider.generate(reference_path, prompt, size)
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

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        relative_output = result.output_path.relative_to(settings.storage_root).as_posix()
        base_metadata.update(
            {
                "model": result.model,
                "seed": result.seed,
                "generated_image_path": relative_output,
                "elapsed_ms": elapsed_ms,
            }
        )
        _save_json(generation_path, base_metadata)
        return {
            "generation_id": generation_id,
            "generated_image_url": f"/storage/{relative_output}",
            "provider": settings.image_provider,
            "model": result.model,
            "prompt": prompt,
            "product_brief": product_brief,
            "fact_card": base_metadata["fact_card"],
            "size": size,
            "seed": result.seed,
            "elapsed_ms": elapsed_ms,
            "created_at": created_at,
        }

    return router
