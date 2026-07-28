"""Golden snapshot tests + strategy pattern unit tests."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import Settings
from app.schemas import FactCard, Scene
from app.services.fact_card_compress import render_generation_prompt
from app.services.dispatch_generation import build_image_prompt, PromptResult

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "app" / "services" / "prompts" / "image_generation.txt"
META_DIR = Path(__file__).resolve().parent.parent / "storage" / "metadata"


def _load_golden_cases() -> list[tuple[int, Path]]:
    manifest_path = GOLDEN_DIR / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = []
    for entry in manifest:
        product_file = META_DIR / entry["file"]
        if product_file.exists():
            cases.append((entry["index"], product_file))
    return cases


CASES = _load_golden_cases()


@pytest.mark.parametrize("index,product_file", CASES, ids=[f"product_{c[0]}" for c in CASES])
def test_legacy_prompt_unchanged(index: int, product_file: Path):
    """The legacy prompt for each product must exactly match the golden baseline."""
    golden_prompt_path = GOLDEN_DIR / f"legacy_prompt_{index}.txt"
    golden_brief_path = GOLDEN_DIR / f"legacy_brief_{index}.txt"
    if not golden_prompt_path.exists():
        pytest.skip(f"No golden file for index {index}")

    template = PROMPT_PATH.read_text(encoding="utf-8")
    data = json.loads(product_file.read_text(encoding="utf-8"))
    card = FactCard.model_validate(data["fact_card"])
    scenes = card.scenes or []
    scene = scenes[0] if scenes else Scene(scene="通用场景", placement="自然摆放")

    product_brief, prompt, _ = render_generation_prompt(
        template, card, scene, "中近景",
        realism_ctx=None,
        camera_seed=42,
        inject_appearance=False,
    )

    expected_prompt = golden_prompt_path.read_text(encoding="utf-8")
    assert prompt == expected_prompt, (
        f"Legacy prompt diverged for product index {index}.\n"
        f"Golden: {golden_prompt_path}\n"
        f"Regenerate golden if this is intentional."
    )

    if golden_brief_path.exists():
        expected_brief = golden_brief_path.read_text(encoding="utf-8")
        assert product_brief == expected_brief


# ── Strategy unit tests ──────────────────────────────────────────────────────

_MINIMAL_FACT_CARD = FactCard.model_validate({
    "商品名称": "释迦牟尼佛坐像",
    "识别置信度": "高",
    "商品品类": "工艺摆件",
    "商品形态": "坐像",
    "主体定义": "佛像主体",
    "主体包围框": {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.9},
    "整体特征": "古铜色树脂材质",
    "尺寸": {"高_cm": 36.0, "体量等级": "中型物品（20-60cm）", "构图策略": "中景半身环境", "尺寸来源": "ocr"},
    "自然场景": [{"场景": "佛堂", "具体位置": "供桌上"}],
    "建议房间": "佛堂",
})
_SCENE = Scene(scene="佛堂", placement="供桌上")


def _settings_with(**overrides) -> Settings:
    defaults = {"vision_provider": "mock", "image_provider": "mock"}
    defaults.update(overrides)
    return Settings(**defaults)


class TestBuildImagePromptLegacy:
    def test_default_strategy_is_legacy(self):
        s = _settings_with()
        result = build_image_prompt(s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景", camera_seed=42)
        assert result.strategy == "legacy"
        assert result.negative == ""

    def test_realism_boost_appends(self):
        s = _settings_with(image_realism_boost="on")
        result = build_image_prompt(s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景", camera_seed=42)
        assert "手机随手拍" in result.positive
        assert "commercial product shot" in result.negative


class TestBuildImagePromptScaleAnchor:
    def test_route_a_with_height_and_room(self):
        s = _settings_with(image_prompt_strategy="scale_anchor")
        result = build_image_prompt(
            s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景",
            height_cm=36.0, room="佛堂", product_id="test-seed",
        )
        assert result.strategy == "scale_anchor"
        assert result.meta["route"] == "A"
        assert result.meta["room"] == "佛堂"
        assert not result.meta["room_fallback"]
        assert len(result.meta["anchors"]) >= 1

    def test_route_b_no_room_defaults_to_living(self):
        s = _settings_with(image_prompt_strategy="scale_anchor")
        result = build_image_prompt(
            s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景",
            height_cm=36.0, room=None, product_id="test-seed",
        )
        assert result.meta["route"] == "B"
        assert result.meta["room"] == "客厅"
        assert result.meta["room_fallback"] is True

    def test_route_c_no_height(self):
        s = _settings_with(image_prompt_strategy="scale_anchor")
        result = build_image_prompt(
            s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景",
            height_cm=None, room="佛堂", product_id="test-seed",
        )
        assert result.meta["route"] == "C"
        assert "近景特写" in result.positive

    def test_realism_boost_on_scale_anchor(self):
        s = _settings_with(image_prompt_strategy="scale_anchor", image_realism_boost="on")
        result = build_image_prompt(
            s, fact_card=_MINIMAL_FACT_CARD, scene=_SCENE, shot_type="中近景",
            height_cm=36.0, room="佛堂", product_id="test-seed",
        )
        assert "手机随手拍" in result.positive
        assert "commercial product shot" in result.negative
