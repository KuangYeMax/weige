"""Tests for product codes API: uniqueness validation, empty rejection, multi-code."""

from __future__ import annotations

import pytest

from app.services.db import init_db, upsert_product


@pytest.fixture
def db_client(client, settings):
    """Ensure db is initialized and a product exists for code tests."""
    init_db(settings.db_path)
    upsert_product(
        settings.db_path,
        product_id="aaaa-bbbb-cccc-dddd",
        name="测试商品A",
        image_path="uploads/test.jpg",
        fact_card_path="metadata/product-aaaa.json",
        created_at="2026-01-01T00:00:00+00:00",
    )
    upsert_product(
        settings.db_path,
        product_id="eeee-ffff-0000-1111",
        name="测试商品B",
        image_path="uploads/test2.jpg",
        fact_card_path="metadata/product-eeee.json",
        created_at="2026-01-02T00:00:00+00:00",
    )
    return client


def test_add_code_success(db_client):
    resp = db_client.post(
        "/api/products/aaaa-bbbb-cccc-dddd/codes",
        json={"code": "SKU-001"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "SKU-001"
    assert data["product_id"] == "aaaa-bbbb-cccc-dddd"
    assert "created_at" in data


def test_add_multiple_codes_to_one_product(db_client):
    db_client.post("/api/products/aaaa-bbbb-cccc-dddd/codes", json={"code": "MULTI-1"})
    db_client.post("/api/products/aaaa-bbbb-cccc-dddd/codes", json={"code": "MULTI-2"})
    resp = db_client.get("/api/products/aaaa-bbbb-cccc-dddd/codes")
    assert resp.status_code == 200
    codes = resp.json()["codes"]
    code_values = [c["code"] for c in codes]
    assert "MULTI-1" in code_values
    assert "MULTI-2" in code_values


def test_duplicate_code_rejected(db_client):
    db_client.post("/api/products/aaaa-bbbb-cccc-dddd/codes", json={"code": "DUP-100"})
    # Same code on a different product must fail
    resp = db_client.post(
        "/api/products/eeee-ffff-0000-1111/codes",
        json={"code": "DUP-100"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CODE_CONFLICT"


def test_duplicate_code_same_product_rejected(db_client):
    db_client.post("/api/products/aaaa-bbbb-cccc-dddd/codes", json={"code": "SAME-1"})
    resp = db_client.post(
        "/api/products/aaaa-bbbb-cccc-dddd/codes",
        json={"code": "SAME-1"},
    )
    assert resp.status_code == 409


def test_empty_code_rejected(db_client):
    resp = db_client.post(
        "/api/products/aaaa-bbbb-cccc-dddd/codes",
        json={"code": ""},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "CODE_EMPTY"


def test_whitespace_only_code_rejected(db_client):
    resp = db_client.post(
        "/api/products/aaaa-bbbb-cccc-dddd/codes",
        json={"code": "   "},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "CODE_EMPTY"


def test_list_products_includes_codes(db_client):
    db_client.post("/api/products/aaaa-bbbb-cccc-dddd/codes", json={"code": "LIST-1"})
    resp = db_client.get("/api/products")
    assert resp.status_code == 200
    products = resp.json()["products"]
    assert len(products) >= 1
    product_a = next(p for p in products if p["product_id"] == "aaaa-bbbb-cccc-dddd")
    assert any(c["code"] == "LIST-1" for c in product_a["codes"])


def test_list_products_returns_products_envelope_for_dispatch_code_map(db_client):
    response = db_client.get("/api/products")

    assert response.status_code == 200
    assert isinstance(response.json(), dict)
    assert isinstance(response.json()["products"], list)


def test_nonexistent_product_404(db_client):
    resp = db_client.post(
        "/api/products/no-such-product/codes",
        json={"code": "X"},
    )
    assert resp.status_code == 404
