"""Anti-regression: image generation prompt must NEVER contain appearance injection.

2026-07-28 消融实验证实：将事实卡外观描述（颜色材质/形态/位置关系）注入生图 prompt
会导致图生图模型重绘主体而非保持参考图保真。此测试确保注入永远不会被意外恢复。
"""

from __future__ import annotations

from app.schemas import FactCard, Scene, KeyStructure
from app.services.fact_card_compress import compress_fact_card, render_generation_prompt


def _make_card_with_appearance() -> FactCard:
    """Build a FactCard with colors_materials, product_form, and position_relation populated."""
    return FactCard(
        product_name="测试商品",
        subject_definition="测试主体定义",
        overall_features="金色雕像",
        colors_materials=["金色（铜制）", "深色底座（青铜）", "红色幕布"],
        product_form="站立式人物雕像，带装饰旗帜与龙纹底座",
        key_structures=[
            KeyStructure(
                name="主体雕像",
                count="1",
                appearance="金色铠甲",
                position_relation="画面中心，支撑于底座之上",
                importance="高",
            ),
            KeyStructure(
                name="龙纹底座",
                count="1",
                appearance="深色雕刻",
                position_relation="雕像底部，承托主体",
                importance="高",
            ),
        ],
        fidelity_locks=["数量不得增减"],
        scenes=[Scene(scene="客厅博古架", placement="木质博古架中层")],
    )


INJECTION_MARKERS = [
    "颜色材质：",
    "形态：",
    "位置与关系：",
]

TEMPLATE = (
    "【商品简述】\n{product_brief}\n"
    "【本次生成设置】\n拍摄类型：{shot_type}\n场景类型：{scene_type}\n"
    "具体位置：{placement}\n尺度表现：{scale_expression}\n"
    "环境要求：{environment_requirements}\n避免内容：{avoid_content}\n"
    "【视角约束】\n{view_angle_constraint}\n"
)


def test_compress_fact_card_default_no_injection():
    card = _make_card_with_appearance()
    scene = Scene(scene="客厅博古架", placement="木质博古架中层")
    brief = compress_fact_card(card, scene)
    for marker in INJECTION_MARKERS:
        assert marker not in brief, (
            f"compress_fact_card 默认输出中包含外观注入标记 '{marker}'。"
            f"2026-07-28 消融证实：注入主体外观描述会导致图生图重绘主体。"
            f"inject_appearance 必须保持 False。"
        )


def test_render_generation_prompt_default_no_injection():
    card = _make_card_with_appearance()
    scene = Scene(scene="客厅博古架", placement="木质博古架中层")
    _, prompt, _ = render_generation_prompt(
        TEMPLATE, card, scene, "中近景", None
    )
    for marker in INJECTION_MARKERS:
        assert marker not in prompt, (
            f"render_generation_prompt 默认输出中包含外观注入标记 '{marker}'。"
            f"2026-07-28 消融证实：注入主体外观描述会导致图生图重绘主体。"
            f"inject_appearance 必须保持 False。"
        )


def test_inject_appearance_flag_does_inject_when_true():
    """Verify the flag works when explicitly enabled (for non-image use cases)."""
    card = _make_card_with_appearance()
    scene = Scene(scene="客厅博古架", placement="木质博古架中层")
    brief = compress_fact_card(card, scene, inject_appearance=True)
    assert "颜色材质：" in brief
    assert "形态：" in brief


def test_settings_default_inject_appearance_is_false():
    """Settings must default inject_appearance_into_image_prompt to False."""
    from app.config import Settings
    s = Settings()
    assert s.inject_appearance_into_image_prompt is False, (
        "Settings.inject_appearance_into_image_prompt 默认值不是 False！"
        "2026-07-28 消融证实：此开关必须默认关闭。"
    )
