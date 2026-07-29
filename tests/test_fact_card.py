import pytest
from pydantic import ValidationError

from app.schemas import FactCard


def test_fact_card_accepts_category_independent_shapes():
    ornament = {
        "商品名称": "桌面摆件",
        "商品品类": "家居装饰",
        "关键结构": [{"名称": "主体", "数量": "1 个", "位置与关系": "中央"}],
        "自然场景": [{"场景": "桌面摆放", "具体位置": "木质书桌"}],
    }
    clothing = {
        "商品名称": "针织外套",
        "商品品类": "服饰",
        "关键结构": [
            {"名称": "衣身", "数量": "1 件", "位置与关系": "主体"},
            {"名称": "衣袖", "数量": "2 只", "位置与关系": "衣身两侧"},
        ],
        "自然场景": [{"场景": "自然穿着", "具体位置": "室内"}],
    }

    assert FactCard.model_validate(ornament).product_name == "桌面摆件"
    assert len(FactCard.model_validate(clothing).key_structures) == 2


def test_fact_card_requires_no_mandatory_fields():
    card = FactCard.model_validate({"商品名称": "未知商品"})
    assert card.category == ""
    assert card.fidelity_locks == []
    assert card.scenes == []

    # Empty dict also works with new lenient schema
    card2 = FactCard.model_validate({})
    assert card2.product_name == ""


def test_user_can_save_edited_fact_card(client, uploaded_product, mock_fact_card):
    card = {**mock_fact_card, "商品名称": "用户命名商品", "商品品类": "用户自由填写的品类"}

    response = client.post(
        f"/api/products/{uploaded_product['product_id']}/fact-card",
        json=card,
    )

    assert response.status_code == 200
    assert response.json()["fact_card"]["商品名称"] == "用户命名商品"


def test_invalid_fact_card_is_rejected(client, uploaded_product):
    response = client.post(
        f"/api/products/{uploaded_product['product_id']}/fact-card",
        json="not a dict",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FACT_CARD_INVALID"
