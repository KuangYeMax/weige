from __future__ import annotations

from app.schemas import FactCard, Scene


def _join(items: list[str] | None) -> str:
    return "、".join(item for item in (items or []) if item)


def compress_fact_card(card: FactCard, scene: Scene) -> str:
    identity = card.subject_definition or card.product_name
    locks = [item for item in card.fidelity_locks if item][:5]
    features_parts = []
    if card.overall_features:
        features_parts.append(card.overall_features)
    for ks in card.key_structures[:3]:
        parts = []
        if ks.name:
            parts.append(ks.name)
        if ks.count:
            parts.append(f"×{ks.count}")
        if ks.appearance:
            parts.append(ks.appearance)
        if parts:
            features_parts.append("".join(parts))
    facts = "；".join(features_parts + locks)
    scene_text = "；".join(
        value
        for value in [
            f"场景：{scene.scene}" if scene.scene else "",
            f"位置：{scene.placement}" if scene.placement else "",
        ]
        if value
    )
    return f"商品主体：{identity}。关键保真：{facts or '以参考原图为准'}。{scene_text}。普通手机随手拍，真实自然，不过度商业化。"


def render_generation_prompt(
    template: str, card: FactCard, scene: Scene, shot_type: str
) -> tuple[str, str]:
    product_brief = compress_fact_card(card, scene)
    values = {
        "product_brief": product_brief,
        "shot_type": shot_type,
        "scene_type": scene.scene or "",
        "placement": scene.placement or "",
        "support_relationship": "",
        "usage_state": "",
        "scale_expression": "",
        "environment_requirements": "",
        "avoid_content": "",
    }
    return product_brief, template.format(**values)
