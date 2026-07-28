from __future__ import annotations

import base64
import io
import random
import logging
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from app.errors import AppError
from app.services.http import request_with_retry
from app.services.image_generation.mock import GenerationResult
from app.services.image_generation.models import get_model

logger = logging.getLogger(__name__)

SYNC_PATH = "/services/aigc/multimodal-generation/generation"
ASYNC_PATH = "/services/aigc/image-generation/generation"
ASYNC_I2I_PATH = "/services/aigc/image2image/image-synthesis"
TASK_PATH = "/tasks"

SIZE_MAP_BAILIAN: dict[str, str] = {
    "1:1": "2048*2048",
    "3:4": "1536*2048",
}

def _map_size(aspect_ratio: str) -> str:
    size = SIZE_MAP_BAILIAN.get(aspect_ratio)
    if not size:
        raise AppError("ASPECT_RATIO_INVALID", "画面比例仅支持 3:4 或 1:1", 422)
    return size


def _check_content_review_error(body: dict) -> None:
    code = body.get("code", "")
    message = body.get("message", "")
    markers = ("IPInfringementSuspect", "DataInspectionFailed", "ContentFilter",
               "内容审核", "敏感内容", "安全审核")
    combined = f"{code} {message}"
    if any(m in combined for m in markers):
        raise AppError(
            "CONTENT_REVIEW_REJECTED",
            f"百炼内容审核未通过: {message or code}",
            422,
        )


class BailianImageProvider:
    name = "bailian"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        output_dir: Path,
        timeout: float = 120.0,
        max_download_bytes: int = 30 * 1024 * 1024,
        poll_interval: float = 2.0,
        poll_timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        thinking_mode: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.transport = transport
        self.thinking_mode = thinking_mode

    def _headers(self, async_mode: bool = False) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return h

    def _build_payload_wan27(
        self, prompt: str, reference_image_path: Path | None, size: str, seed: int | None = None
    ) -> dict:
        content: list[dict] = []
        if reference_image_path:
            media_type = mimetypes.guess_type(reference_image_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(reference_image_path.read_bytes()).decode("ascii")
            content.append({"image": f"data:{media_type};base64,{encoded}"})
        content.append({"text": prompt})
        parameters: dict = {
            "size": size,
            "n": 1,
            "watermark": False,
            "thinking_mode": self.thinking_mode,
        }
        if seed is not None:
            parameters["seed"] = seed
        return {
            "model": self.model,
            "input": {
                "messages": [{"role": "user", "content": content}]
            },
            "parameters": parameters,
        }

    def _build_payload_wan25(
        self, prompt: str, reference_image_path: Path | None
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "input": {"prompt": prompt},
            "parameters": {"n": 1},
        }
        if reference_image_path:
            media_type = mimetypes.guess_type(reference_image_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(reference_image_path.read_bytes()).decode("ascii")
            payload["input"]["images"] = [f"data:{media_type};base64,{encoded}"]
        return payload

    def _extract_image_url_choices(self, body: dict) -> str:
        try:
            choices = body["output"]["choices"]
            for item in choices[0]["message"]["content"]:
                if "image" in item:
                    return item["image"]
        except (KeyError, IndexError, TypeError):
            pass
        raise AppError("IMAGE_RESPONSE_EMPTY", "百炼生图服务没有返回图片", 502)

    def _extract_image_url_results(self, body: dict) -> str:
        try:
            results = body["output"]["results"]
            return results[0]["url"]
        except (KeyError, IndexError, TypeError):
            pass
        raise AppError("IMAGE_RESPONSE_EMPTY", "百炼生图服务没有返回图片", 502)

    def _poll_task(self, task_id: str) -> dict:
        url = f"{self.base_url}{TASK_PATH}/{task_id}"
        headers = self._headers()
        deadline = time.time() + self.poll_timeout
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            while True:
                response = request_with_retry(
                    lambda: client.get(url, headers=headers),
                    "百炼任务轮询",
                )
                body = response.json()
                _check_content_review_error(body)
                status = body.get("output", {}).get("task_status", "")
                if status == "SUCCEEDED":
                    return body
                if status in ("FAILED", "CANCELED"):
                    msg = body.get("output", {}).get("message", "任务失败")
                    raise AppError("GENERATION_FAILED", f"百炼生图失败: {msg}", 502)
                if time.time() >= deadline:
                    raise AppError("PROVIDER_TIMEOUT", "百炼生图任务超时", 504)
                time.sleep(self.poll_interval)

    def _download_image(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AppError("IMAGE_DOWNLOAD_FAILED", "百炼返回了不安全的图片地址", 502)
        with httpx.Client(timeout=self.timeout, follow_redirects=True, transport=self.transport) as client:
            response = request_with_retry(
                lambda: client.get(url),
                "百炼图片下载",
            )
            content = response.content
            if not content or len(content) > self.max_download_bytes:
                raise AppError("IMAGE_DOWNLOAD_FAILED", "百炼图片为空或过大", 502)
            return content

    def _save_image(self, content: bytes) -> Path:
        try:
            with Image.open(io.BytesIO(content)) as img:
                fmt = img.format or "PNG"
                w, h = img.size
                if w <= 0 or h <= 0 or w * h > 40_000_000:
                    raise AppError("IMAGE_RESPONSE_INVALID", "百炼图片尺寸无效", 502)
                img.verify()
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AppError("IMAGE_RESPONSE_INVALID", "百炼返回的内容不是有效图片", 502) from exc

        suffix = ".png"
        output_path = self.output_dir / f"{uuid4()}{suffix}"
        tmp = output_path.with_suffix(output_path.suffix + ".part")
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content)
            tmp.replace(output_path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise AppError("FILE_SAVE_FAILED", "百炼图片保存失败", 500) from exc
        return output_path

    def generate(
        self,
        reference_image_path: Path | None,
        prompt: str,
        size: str,
    ) -> GenerationResult:
        if not self.api_key:
            raise AppError("BAILIAN_NOT_CONFIGURED", "百炼生图服务尚未配置 API Key", 503)

        model_info = get_model(self.model)
        aspect_size = _map_size(size) if "*" not in size else size
        seed = random.randint(0, 2**31 - 1)

        used_reference = False
        if self.model == "wan2.5-i2i-preview":
            payload = self._build_payload_wan25(prompt, reference_image_path)
            used_reference = reference_image_path is not None
        else:
            payload = self._build_payload_wan27(prompt, reference_image_path, aspect_size, seed=seed)
            used_reference = reference_image_path is not None

        api_style = model_info.api_style if model_info else "sync"

        if api_style == "sync":
            endpoint = f"{self.base_url}{SYNC_PATH}"
            headers = self._headers(async_mode=False)
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = request_with_retry(
                    lambda: client.post(endpoint, json=payload, headers=headers),
                    "百炼生图",
                )
            body = response.json()
            _check_content_review_error(body)
            image_url = self._extract_image_url_choices(body)
        else:
            if self.model == "wan2.5-i2i-preview":
                endpoint = f"{self.base_url}{ASYNC_I2I_PATH}"
            else:
                endpoint = f"{self.base_url}{ASYNC_PATH}"
            headers = self._headers(async_mode=True)
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = request_with_retry(
                    lambda: client.post(endpoint, json=payload, headers=headers),
                    "百炼生图提交",
                )
            body = response.json()
            _check_content_review_error(body)
            task_id = body.get("output", {}).get("task_id")
            if not task_id:
                raise AppError("IMAGE_RESPONSE_EMPTY", "百炼未返回任务ID", 502)
            result_body = self._poll_task(task_id)
            if self.model == "wan2.5-i2i-preview":
                image_url = self._extract_image_url_results(result_body)
            else:
                image_url = self._extract_image_url_choices(result_body)

        content = self._download_image(image_url)
        output_path = self._save_image(content)
        return GenerationResult(output_path=output_path, model=self.model, seed=seed)
