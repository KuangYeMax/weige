from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.services.image_generation.models import (
    list_all_models,
    list_models,
    get_model,
    default_model,
)
from app.services.image_generation.mock import GenerationResult


class TestModelRegistry:
    def test_list_all_models_returns_seven(self):
        assert len(list_all_models()) == 7

    def test_list_models_volcengine(self):
        volc = list_models("volcengine")
        assert len(volc) == 4
        assert all(m.provider == "volcengine" for m in volc)

    def test_list_models_bailian(self):
        bl = list_models("bailian")
        assert len(bl) == 3
        assert all(m.provider == "bailian" for m in bl)

    def test_get_model_not_found(self):
        assert get_model("nonexistent") is None

    def test_default_model_volcengine(self):
        m = default_model("volcengine")
        assert m is not None
        assert m.model_id == "doubao-seedream-4-0-250828"

    def test_default_model_bailian(self):
        m = default_model("bailian")
        assert m is not None
        assert m.model_id == "wan2.7-image"

    def test_all_models_have_labels(self):
        for m in list_all_models():
            assert m.label
            assert m.model_id
            assert m.provider in ("volcengine", "bailian")

    def test_supports_reference_flags(self):
        wan = get_model("wan2.7-image-pro")
        assert wan.supports_reference is True


@pytest.fixture
def tmp_storage(tmp_path):
    for d in ("uploads", "generated", "metadata"):
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def mock_settings(tmp_storage):
    return Settings(
        vision_provider="mock",
        image_provider="mock",
        storage_root=tmp_storage,
        ark_api_key="test-key",
        ark_image_model="doubao-seedream-4-0-250828",
        volc_image_model="doubao-seedream-4-0-250828",
        dashscope_api_key="test-dashscope-key",
        dashscope_base_url="https://test.cn-beijing.maas.aliyuncs.com/api/v1",
        bailian_image_model="wan2.7-image",
        shrink_reference=False,
        post_grade=False,
    )


@pytest.fixture
def client(mock_settings):
    app = create_app(mock_settings)
    return TestClient(app)


@pytest.fixture
def uploaded_product(client, tmp_storage):
    from PIL import Image
    img = Image.new("RGB", (800, 800), "red")
    img_path = tmp_storage / "uploads" / "test.jpg"
    img.save(img_path, format="JPEG")
    product_id = str(uuid4())
    fact_card = {
        "商品名称": "测试商品",
        "识别置信度": "高",
        "商品品类": "测试",
        "商品形态": "实物",
        "主体定义": "测试主体",
        "整体特征": "红色方块",
        "自然场景": [{"场景": "桌面", "具体位置": "木质桌面上"}],
        "保真锁": ["红色"],
        "不确定项": [],
    }
    metadata = {
        "product_id": product_id,
        "original_image_path": f"uploads/test.jpg",
        "original_size_bytes": 1000,
        "width": 800,
        "height": 800,
        "fact_card": fact_card,
    }
    meta_path = tmp_storage / "metadata" / f"product-{product_id}.json"
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    return product_id, fact_card


class TestModelsEndpoint:
    def test_list_all(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 7

    def test_filter_by_provider(self, client):
        resp = client.get("/api/models?provider=bailian")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 3
        assert all(m["provider"] == "bailian" for m in data["models"])


class TestGenerateWithModelSelection:
    def test_default_provider(self, client, uploaded_product):
        pid, fc = uploaded_product
        resp = client.post(f"/api/products/{pid}/generate", json={
            "fact_card": fc,
            "shot_type": "中近景",
            "scene_index": 0,
            "aspect_ratio": "3:4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "mock"

    def test_invalid_model_returns_400(self, client, uploaded_product):
        pid, fc = uploaded_product
        resp = client.post(f"/api/products/{pid}/generate", json={
            "fact_card": fc,
            "shot_type": "中近景",
            "scene_index": 0,
            "aspect_ratio": "3:4",
            "image_model": "nonexistent-model",
        })
        assert resp.status_code == 400



class TestGenerateCompare:
    def test_compare_calls_all_models(self, client, uploaded_product):
        pid, fc = uploaded_product
        resp = client.post(f"/api/products/{pid}/generate-compare", json={
            "shot_type": "中近景",
            "aspect_ratio": "3:4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["models_count"] == 7
        assert len(data["results"]) == 7
        assert data["total_elapsed_ms"] >= 0
        ok_count = sum(1 for r in data["results"] if r["status"] == "ok")
        assert ok_count >= 1

    def test_compare_subset(self, client, uploaded_product):
        pid, fc = uploaded_product
        resp = client.post(f"/api/products/{pid}/generate-compare", json={
            "shot_type": "中近景",
            "aspect_ratio": "1:1",
            "models": ["wan2.7-image", "doubao-seedream-4-0-250828"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["models_count"] == 2

    def test_compare_single_failure_doesnt_break_batch(self, client, uploaded_product, mock_settings):
        pid, fc = uploaded_product
        original_image_provider = mock_settings.image_provider
        resp = client.post(f"/api/products/{pid}/generate-compare", json={
            "shot_type": "中近景",
            "aspect_ratio": "3:4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 7

