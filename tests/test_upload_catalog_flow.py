"""End-to-end catalog flow for a product created through the upload endpoint."""

from __future__ import annotations


def _upload_product(client, image_bytes, filename="product.jpg"):
    response = client.post(
        "/api/products/upload",
        files={"image": (filename, image_bytes, "image/jpeg")},
    )
    assert response.status_code == 200
    return response.json()


def test_uploaded_product_can_be_coded_and_registered_for_dispatch(client, image_bytes):
    uploaded = _upload_product(client, image_bytes)
    product_id = uploaded["product_id"]
    code = "UPLOADED-001"

    add_code = client.post(f"/api/products/{product_id}/codes", json={"code": code})
    assert add_code.status_code == 200

    catalog = client.get("/api/products").json()["products"]
    product = next(item for item in catalog if item["product_id"] == product_id)
    assert product["codes"] == [{"code": code, "created_at": add_code.json()["created_at"]}]

    dispatch = client.post(
        "/api/dispatch",
        json={"wx_remark": "测试好友", "return_code": "RETURN-001", "send_codes": [code], "countdown_days": 1},
    )
    assert dispatch.status_code == 200
    assert dispatch.json()["send_codes"] == [code]


def test_uploaded_product_code_rejects_empty_and_global_conflicts(client, image_bytes):
    first = _upload_product(client, image_bytes, "first.jpg")
    second = _upload_product(client, image_bytes, "second.jpg")
    code = "GLOBAL-UPLOADED-001"

    assert client.post(f"/api/products/{first['product_id']}/codes", json={"code": code}).status_code == 200

    empty = client.post(f"/api/products/{second['product_id']}/codes", json={"code": "   "})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "CODE_EMPTY"

    duplicate = client.post(f"/api/products/{second['product_id']}/codes", json={"code": code})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CODE_CONFLICT"


def test_saving_fact_card_updates_name_without_changing_created_at_or_codes(client, image_bytes):
    uploaded = _upload_product(client, image_bytes)
    product_id = uploaded["product_id"]
    code = "KEEP-CODE-001"
    assert client.post(f"/api/products/{product_id}/codes", json={"code": code}).status_code == 200

    before = next(
        item for item in client.get("/api/products").json()["products"] if item["product_id"] == product_id
    )
    fact_card = {**uploaded["fact_card"], "商品名称": "编辑后的商品名称"}
    saved = client.post(f"/api/products/{product_id}/fact-card", json=fact_card)

    assert saved.status_code == 200
    after = next(
        item for item in client.get("/api/products").json()["products"] if item["product_id"] == product_id
    )
    assert after["name"] == "编辑后的商品名称"
    assert after["created_at"] == before["created_at"]
    assert after["codes"] == before["codes"] == [{"code": code, "created_at": before["codes"][0]["created_at"]}]
