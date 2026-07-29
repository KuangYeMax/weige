from __future__ import annotations

import base64
import binascii
import io
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from app.errors import AppError
from app.services.http import request_with_retry
from app.services.image_generation.mock import GenerationResult


SIZE_MAP = {"1:1": "2048x2048", "3:4": "1728x2304"}


def map_aspect_ratio(aspect_ratio: str) -> str:
    try:
        return SIZE_MAP[aspect_ratio]
    except KeyError as exc:
        raise AppError("ASPECT_RATIO_INVALID", "画面比例仅支持 3:4 或 1:1", 422) from exc


class VolcengineImageProvider:
    name = "volcengine"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        output_dir: Path,
        timeout: float = 120.0,
        max_download_bytes: int = 30 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self.transport = transport

    def build_request_payload(
        self, reference_image_path: Path, prompt: str, size: str
    ) -> dict:
        media_type = mimetypes.guess_type(reference_image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(reference_image_path.read_bytes()).decode("ascii")
        return {
            "model": self.model,
            "prompt": prompt,
            "image": f"data:{media_type};base64,{encoded}",
            "size": size,
            "response_format": "b64_json",
        }

    def _download(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AppError("IMAGE_DOWNLOAD_FAILED", "生图服务返回了不安全的图片地址", 502)
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self.transport,
        ) as client:
            response = request_with_retry(
                lambda: client.send(client.build_request("GET", url), stream=True),
                "生成图片下载",
            )
            try:
                raw_length = response.headers.get("content-length", "0") or "0"
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise AppError(
                        "IMAGE_DOWNLOAD_FAILED", "生图服务返回了无效的图片大小", 502
                    ) from exc
                if content_length > self.max_download_bytes:
                    raise AppError("IMAGE_DOWNLOAD_FAILED", "生图服务返回的图片过大", 502)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_download_bytes:
                        raise AppError("IMAGE_DOWNLOAD_FAILED", "生图服务返回的图片过大", 502)
                return bytes(content)
            except httpx.HTTPError as exc:
                raise AppError("IMAGE_DOWNLOAD_FAILED", "生成图片下载失败", 502) from exc
            finally:
                response.close()

    def save_validated_image(self, content: bytes) -> Path:
        try:
            with Image.open(io.BytesIO(content)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in {"JPEG", "PNG", "WEBP"}:
                    raise AppError("IMAGE_RESPONSE_INVALID", "生图服务返回了不支持的图片格式", 502)
                if width <= 0 or height <= 0 or width * height > 40_000_000:
                    raise AppError("IMAGE_RESPONSE_INVALID", "生图服务返回的图片尺寸无效", 502)
                image.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise AppError("IMAGE_RESPONSE_INVALID", "生图服务返回的内容不是有效图片", 502) from exc

        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
        output_path = self.output_dir / f"{uuid4()}{suffix}"
        temporary = output_path.with_suffix(output_path.suffix + ".part")
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            temporary.replace(output_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise AppError("FILE_SAVE_FAILED", "生成图片保存失败", 500) from exc
        return output_path

    def generate(self, reference_image_path: Path, prompt: str, size: str, *, model_id: str | None = None) -> GenerationResult:
        effective_model = model_id or self.model
        if not self.api_key or not effective_model:
            raise AppError(
                "VOLCENGINE_NOT_CONFIGURED", "火山生图服务尚未配置 API Key 和模型", 503
            )
        payload = self.build_request_payload(reference_image_path, prompt, size)
        payload["model"] = effective_model
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            response = request_with_retry(
                lambda: client.post(
                    f"{self.base_url}/images/generations", json=payload, headers=headers
                ),
                "火山图生图",
            )
        try:
            body = response.json()
            item = body["data"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise AppError("IMAGE_RESPONSE_EMPTY", "生图服务没有返回图片", 502) from exc

        try:
            if item.get("b64_json"):
                content = base64.b64decode(item["b64_json"], validate=True)
            elif item.get("url"):
                content = self._download(item["url"])
            else:
                raise KeyError("missing image")
        except (binascii.Error, KeyError) as exc:
            raise AppError("IMAGE_RESPONSE_EMPTY", "生图服务没有返回有效图片", 502) from exc
        if not content or len(content) > self.max_download_bytes:
            raise AppError("IMAGE_DOWNLOAD_FAILED", "生成图片为空或超过大小限制", 502)

        output_path = self.save_validated_image(content)
        return GenerationResult(
            output_path=output_path,
            model=effective_model,
            seed=item.get("seed") or body.get("seed"),
        )
