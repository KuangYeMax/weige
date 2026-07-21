"""Tests for normalize_fact_card and FactCard schema validation."""
from __future__ import annotations

import json

import pytest

from app.schemas import FactCard
from app.services.normalize import normalize_fact_card


def _valid_card() -> dict:
    return {
        "商品名称": "测试商品",
        "识别置信度": "高",
        "商品品类": "电子产品",
        "商品形态": "平板状设备",
        "主体定义": "一台平板电脑",
        "画面中需忽略": ["背景"],
        "整体特征": "黑色长方形平板",
        "关键结构": [
            {
                "名称": "屏幕",
                "数量": "1",
                "位置与关系": "正面",
                "外观特征": "黑色玻璃面板",
                "重要性": "高",
            }
        ],
        "颜色与材质观感": ["黑色金属质感"],
        "文字与规格": [{"内容": "Apple", "位置": "背面中央", "置信度": "高"}],
        "尺寸": {"长_cm": 24.0, "宽_cm": 17.0, "高_cm": 0.6, "重量_kg": 0.47, "其他规格": [], "证据": "图片明确文字"},
        "自然场景": [{"场景": "办公桌使用", "具体位置": "木质办公桌上"}],
        "建议拍法": {"完整照": "正面展示", "中近景": "屏幕特写", "细节照": ["接口"]},
        "保真锁": ["黑色长方形外观"],
        "不确定项": [],
    }


class TestFactCardSchema:
    def test_valid_card_passes(self):
        card = FactCard.model_validate(_valid_card())
        assert card.product_name == "测试商品"
        assert card.category == "电子产品"

    def test_missing_optional_fields_get_defaults(self):
        card = FactCard.model_validate({"商品名称": "简单商品"})
        assert card.key_structures == []
        assert card.colors_materials == []
        assert card.scenes == []
        assert card.fidelity_locks == []
        assert card.dimensions.length_cm is None

    def test_null_arrays_become_empty_list(self):
        data = _valid_card()
        data["保真锁"] = None
        data["关键结构"] = None
        normalized = normalize_fact_card(json.dumps(data))
        assert normalized["保真锁"] == []
        assert normalized["关键结构"] == []

    def test_string_30cm_becomes_float(self):
        data = _valid_card()
        data["尺寸"]["高_cm"] = "30cm"
        normalized = normalize_fact_card(json.dumps(data))
        assert normalized["尺寸"]["高_cm"] == 30.0

    def test_string_1_7kg_becomes_float(self):
        data = _valid_card()
        data["尺寸"]["重量_kg"] = "1.7kg"
        normalized = normalize_fact_card(json.dumps(data))
        assert normalized["尺寸"]["重量_kg"] == 1.7

    def test_unwrap_fact_card_wrapper(self):
        wrapped = json.dumps({"fact_card": _valid_card()})
        normalized = normalize_fact_card(wrapped)
        assert normalized["商品名称"] == "测试商品"

    def test_strip_markdown_code_fences(self):
        raw = "```json\n" + json.dumps(_valid_card()) + "\n```"
        normalized = normalize_fact_card(raw)
        assert normalized["商品名称"] == "测试商品"

    def test_extra_fields_ignored(self):
        data = _valid_card()
        data["额外字段"] = "应被忽略"
        data["另一个"] = [1, 2, 3]
        card = FactCard.model_validate(data)
        assert not hasattr(card, "额外字段")

    def test_free_text_category_any_value(self):
        for category in ["手工皮具", "AI芯片模组", "unknown", "日式和风小物"]:
            card = FactCard.model_validate({"商品名称": "x", "商品品类": category})
            assert card.category == category

    def test_free_text_product_form_any_value(self):
        for form in ["液态喷雾", "折叠结构", "不规则有机体", "粉末状"]:
            card = FactCard.model_validate({"商品名称": "x", "商品形态": form})
            assert card.product_form == form

    def test_invalid_json_raises_with_message(self):
        with pytest.raises(ValueError, match="JSON"):
            normalize_fact_card("这不是JSON内容")

    def test_null_numeric_patterns(self):
        data = _valid_card()
        for val in ["未知", "无法判断", "-", "", "N/A"]:
            data["尺寸"]["长_cm"] = val
            normalized = normalize_fact_card(json.dumps(data))
            assert normalized["尺寸"]["长_cm"] is None

    def test_single_string_to_array(self):
        data = _valid_card()
        data["保真锁"] = "单条保真锁"
        normalized = normalize_fact_card(json.dumps(data))
        assert normalized["保真锁"] == ["单条保真锁"]

    def test_empty_card_minimal(self):
        card = FactCard.model_validate({})
        assert card.product_name == ""
        assert card.scenes == []
