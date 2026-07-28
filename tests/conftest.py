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


@pytest.fixture
def mock_fact_card(image_bytes, tmp_path):
    """MockVisionProvider 对真实图片 analyze 得到的事实卡 dict。

    上传不再生成事实卡，故需要事实卡作为输入的测试（生图/对比/保存事实卡）
    改用本 fixture，模拟「待发记录 generating 阶段」会产出的事实卡结构。
    """
    from app.services.vision.mock import MockVisionProvider

    img_path = tmp_path / "vision_src.jpg"
    img_path.write_bytes(image_bytes)
    return MockVisionProvider().analyze(img_path).model_dump(
        mode="json", by_alias=True
    )
