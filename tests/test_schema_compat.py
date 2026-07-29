"""Backward compatibility: old product JSONs must deserialize without error."""

import json
from pathlib import Path

import pytest

from app.schemas import FactCard


SAMPLE_OLD_FACT_CARD = {
    "商品名称": "释迦牟尼佛坐像",
    "识别置信度": "高",
    "商品品类": "工艺摆件",
    "商品形态": "坐像",
    "主体定义": "佛像主体",
    "主体包围框": {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.9},
    "画面中需忽略": ["水印"],
    "整体特征": "古铜色树脂材质",
    "关键结构": [],
    "颜色与材质观感": ["古铜色", "树脂"],
    "文字与规格": [],
    "尺寸": {
        "长_cm": 10.0,
        "宽_cm": 15.0,
        "高_cm": 33.0,
        "重量_kg": 1.7,
        "其他规格": [],
        "证据": "图片明确文字",
        "体量等级": "中型物品（20-60cm）",
        "构图策略": "中景半身环境",
    },
    "自然场景": [{"场景": "佛堂", "具体位置": "供桌上"}],
    "建议拍法": {"完整照": "", "中近景": "", "细节照": []},
    "保真锁": [],
    "视角容差": {"等级": "低", "判断依据": "复杂结构", "允许角度偏移": "5度"},
    "配件与包装": [],
    "不确定项": [],
}


def test_old_fact_card_without_new_fields():
    """Old JSON without 尺寸来源 and 建议房间 must parse successfully."""
    card = FactCard.model_validate(SAMPLE_OLD_FACT_CARD)
    assert card.product_name == "释迦牟尼佛坐像"
    assert card.dimensions.height_cm == 33.0
    assert card.dimensions.size_source is None
    assert card.room is None


def test_new_fact_card_with_all_fields():
    """New JSON with 尺寸来源 and 建议房间 parses correctly."""
    data = dict(SAMPLE_OLD_FACT_CARD)
    data["尺寸"] = dict(data["尺寸"])
    data["尺寸"]["尺寸来源"] = "ocr"
    data["建议房间"] = "佛堂"
    card = FactCard.model_validate(data)
    assert card.dimensions.size_source == "ocr"
    assert card.room == "佛堂"


def test_real_product_json_compat():
    """If a real product JSON exists on disk, it must deserialize."""
    meta_dir = Path(__file__).resolve().parent.parent / "storage" / "metadata"
    if not meta_dir.exists():
        pytest.skip("No local storage/metadata directory")
    jsons = list(meta_dir.glob("product-*.json"))
    if not jsons:
        pytest.skip("No product JSON files found")
    for p in jsons[:5]:
        data = json.loads(p.read_text(encoding="utf-8"))
        fc_data = data.get("fact_card")
        if fc_data is None:
            continue
        card = FactCard.model_validate(fc_data)
        assert card.dimensions.size_source is None or isinstance(card.dimensions.size_source, str)
        assert card.room is None or isinstance(card.room, str)
