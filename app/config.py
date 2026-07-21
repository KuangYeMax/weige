from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vision_provider: str = "mock"
    image_provider: str = "mock"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_vision_base_url: str = ""
    ark_image_base_url: str = ""
    ark_vision_model: str = ""
    ark_image_model: str = ""
    storage_root: Path = Field(default=PROJECT_ROOT / "storage")
    max_upload_bytes: int = 10 * 1024 * 1024
    min_image_dimension: int = 512
    max_image_pixels: int = 40_000_000
    external_timeout_seconds: float = 120.0
    max_download_bytes: int = 30 * 1024 * 1024

    @property
    def vision_base_url(self) -> str:
        return (self.ark_vision_base_url or self.ark_base_url).rstrip("/")

    @property
    def image_base_url(self) -> str:
        return (self.ark_image_base_url or self.ark_base_url).rstrip("/")

    @property
    def volcengine_configured(self) -> bool:
        if not self.ark_api_key:
            return False
        checks = []
        if self.vision_provider == "volcengine":
            checks.append(bool(self.ark_vision_model))
        if self.image_provider == "volcengine":
            checks.append(bool(self.ark_image_model))
        return bool(checks and all(checks))
