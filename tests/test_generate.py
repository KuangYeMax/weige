from app.services.image_generation.volcengine import map_aspect_ratio


def test_mock_vision_is_generic_and_valid(mock_fact_card):
    card = mock_fact_card
    assert card["商品品类"] == ""
    assert "mock" in " ".join(card["不确定项"]).lower()
    assert card["自然场景"]


def test_aspect_ratio_maps_to_supported_volcengine_size():
    assert map_aspect_ratio("1:1") == "2048x2048"
    assert map_aspect_ratio("3:4") == "1728x2304"


def test_mock_generation_returns_accessible_image_and_metadata(client, uploaded_product, mock_fact_card):
    response = client.post(
        f"/api/products/{uploaded_product['product_id']}/generate",
        json={
            "fact_card": mock_fact_card,
            "shot_type": "中近景",
            "scene_index": 0,
            "aspect_ratio": "3:4",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "mock"
    assert payload["size"] == "1728x2304"
    assert "{product_brief}" not in payload["prompt"]
    assert client.get(payload["generated_image_url"]).status_code == 200
    assert payload["fact_card"]["商品名称"]
    assert payload["product_brief"]
    assert payload["graded_image_url"] is not None
    assert client.get(payload["graded_image_url"]).status_code == 200


def test_missing_product_returns_404(client):
    response = client.post(
        "/api/products/00000000-0000-0000-0000-000000000000/generate",
        json={
            "fact_card": {"商品名称": "测试"},
            "shot_type": "中近景",
            "scene_index": 0,
            "aspect_ratio": "1:1",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_unconfigured_volcengine_image_provider_has_clear_error(settings, image_bytes, mock_fact_card):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.image_provider = "volcengine"
    with TestClient(create_app(settings)) as client:
        upload = client.post(
            "/api/products/upload",
            files={"image": ("product.jpg", image_bytes, "image/jpeg")},
        ).json()
        response = client.post(
            f"/api/products/{upload['product_id']}/generate",
            json={
                "fact_card": mock_fact_card,
                "shot_type": "中近景",
                "scene_index": 0,
                "aspect_ratio": "1:1",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOLCENGINE_NOT_CONFIGURED"


def test_storage_route_cannot_escape_storage_root(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")

    response = client.get("/storage/%2e%2e/secret.txt")

    assert response.status_code == 404
    assert "private" not in response.text


def test_storage_route_does_not_expose_metadata(client, settings):
    metadata_file = settings.storage_root / "metadata" / "private.json"
    metadata_file.write_text('{"prompt":"private"}', encoding="utf-8")

    response = client.get("/storage/metadata/private.json")

    assert response.status_code == 404
    assert "private" not in response.text


def test_unexpected_generation_failure_is_recorded(client, uploaded_product, settings, monkeypatch, mock_fact_card):
    import json

    from app.api import products

    class BrokenProvider:
        def generate(self, reference_image_path, prompt, size):
            raise OSError("disk unavailable")

    monkeypatch.setattr(products, "_image_provider", lambda _s, _p=None, _m=None: (BrokenProvider(), "mock"))
    response = client.post(
        f"/api/products/{uploaded_product['product_id']}/generate",
        json={
            "fact_card": mock_fact_card,
            "shot_type": "中近景",
            "scene_index": 0,
            "aspect_ratio": "1:1",
        },
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "GENERATION_FAILED"
    generation_files = list((settings.storage_root / "metadata").glob("generation-*.json"))
    assert len(generation_files) == 1
    metadata = json.loads(generation_files[0].read_text(encoding="utf-8"))
    assert metadata["error_reason"] == "INTERNAL_ERROR"
