from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        vision_provider="mock",
        image_provider="mock",
        dispatch_image_provider="mock",
        ark_api_key="",
        ark_vision_model="",
        ark_image_model="",
        dashscope_api_key="",
        dashscope_base_url="",
        storage_root=tmp_path / "storage",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (720, 960), (38, 122, 98)).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture
def uploaded_product(client, image_bytes):
    response = client.post(
        "/api/products/upload",
        files={"image": ("product.jpg", image_bytes, "image/jpeg")},
    )
    assert response.status_code == 200
    return response.json()
