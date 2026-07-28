from __future__ import annotations

import random
from dataclasses import dataclass

from app.schemas import FactCard, Scene
from app.services.realism_pool import RealismContext, render_realism_text


SHOT_TYPE_TEXT = {
    "中近景": (
        "中近景：商品占画面约80%-90%，突出主要结构，可自然裁掉次要边缘。"
    ),
    "细节照": (
        "细节照：极近距离特写，只拍一个真实存在的关键局部（如面部+冠冕、或底座铭牌），"
        "画面中看不到商品全貌，明显裁切、部分部位拍不全；不得把局部改成另一个版本。"
        "细节照模式下「完整展示商品」类保真约束自动让位，仅保留「所拍局部真实、不乱改」的约束。"
    ),
}


def _infer_volume_tier(card: FactCard) -> str:
    dims = card.dimensions
    if dims.volume_tier:
        return dims.volume_tier
    h = dims.height_cm
    if h is not None:
        if h <= 20:
            return "小型桌面物"
        if h <= 60:
            return "中型物品"
        return "落地大型物"
    return "小型桌面物"


def scale_expression_text(card: FactCard) -> str:
    dims = card.dimensions
    tier = _infer_volume_tier(card)
    h = dims.height_cm

    if h is not None and dims.evidence != "未提供":
        if h <= 20:
            size_desc = "小型桌面摆件，明显小于常见家具与环境物"
        elif h <= 60:
            size_desc = "中型物品，约为桌面宽度的一半左右"
        else:
            size_desc = "落地大型物，高度接近家具"
        return f"高约{int(h)}cm的{size_desc}"
    return f"{tier}，画面中只占合理比例，明显小于旁边的家具和常见物品"


def _join(items: list[str] | None) -> str:
    return "、".join(item for item in (items or []) if item)


_STRUCTURAL_KEYWORDS = frozenset([
    "底座", "支架", "托盘", "连接", "承托", "固定", "材质", "透明",
    "不透明", "镜面", "哑光", "磨砂", "亮面",
])


def _is_structural_lock(lock: str) -> bool:
    return any(kw in lock for kw in _STRUCTURAL_KEYWORDS)


def _get_angle_tolerance_level(card: FactCard) -> str:
    if card.view_angle_tolerance and card.view_angle_tolerance.level:
        return card.view_angle_tolerance.level
    return "低"


VIEW_ANGLE_CONSTRAINT_TEXT = {
    "低": "保持与参考图基本一致的视角与朝向，不要转到参考图未展示的角度。禁止生成背面、俯底、大角度侧转等参考图未展示的视角。低容差商品的多样性靠换场景/光线/承托面/杂物，不靠转角度。",
    "中": "仅允许轻微视角变化，大体维持原图视角方向，不得大幅转动。",
    "高": "允许较自由换角度，但仍须保持商品可辨认。",
}


@dataclass
class CameraPosition:
    subject_ratio: float
    horizontal_position: str
    vertical_offset: float
    tilt_degrees: float
    partial_out_of_frame: bool
    camera_height_offset: float
    foreground_occlusion: bool

    def to_prompt(self) -> str:
        parts = []
        parts.append(f"商品占画幅约{int(self.subject_ratio * 100)}%")
        parts.append(f"主体位置{self.horizontal_position}")
        if abs(self.vertical_offset) > 0.02:
            direction = "偏上" if self.vertical_offset > 0 else "偏下"
            parts.append(f"垂直{direction}")
        if abs(self.tilt_degrees) > 0.3:
            parts.append(f"画面轻微倾斜{self.tilt_degrees:.1f}°")
        if self.partial_out_of_frame:
            parts.append("画面边缘自然裁切一小部分")
        if abs(self.camera_height_offset) > 1:
            direction = "略俯" if self.camera_height_offset > 0 else "略仰"
            parts.append(f"相机{direction}约{abs(self.camera_height_offset):.0f}°")
        if self.foreground_occlusion:
            parts.append("前景有物件遮挡画面一角")
        return "；".join(parts)

    def to_dict(self) -> dict:
        return {
            "subject_ratio": self.subject_ratio,
            "horizontal_position": self.horizontal_position,
            "vertical_offset": self.vertical_offset,
            "tilt_degrees": self.tilt_degrees,
            "partial_out_of_frame": self.partial_out_of_frame,
            "camera_height_offset": self.camera_height_offset,
            "foreground_occlusion": self.foreground_occlusion,
        }


def draw_camera_position(rng: random.Random, angle_level: str) -> CameraPosition:
    """Draw a random camera position based on angle tolerance level."""
    subject_ratio = rng.uniform(0.65, 0.85)
    h_positions = ["偏左", "居中", "偏右"]
    horizontal_position = rng.choice(h_positions)
    vertical_offset = rng.uniform(-0.05, 0.05)
    tilt_degrees = rng.uniform(-3.0, 3.0)

    if angle_level == "高":
        camera_height_offset = rng.uniform(-25.0, 25.0)
    elif angle_level == "中":
        camera_height_offset = rng.uniform(-15.0, 15.0)
    else:
        camera_height_offset = rng.uniform(-8.0, 8.0)

    return CameraPosition(
        subject_ratio=round(subject_ratio, 2),
        horizontal_position=horizontal_position,
        vertical_offset=round(vertical_offset, 3),
        tilt_degrees=round(tilt_degrees, 1),
        partial_out_of_frame=False,
        camera_height_offset=round(camera_height_offset, 1),
        foreground_occlusion=False,
    )


def compress_fact_card(card: FactCard, scene: Scene, *, inject_appearance: bool = False) -> str:
    identity = card.subject_definition or card.product_name
    all_locks = [item for item in card.fidelity_locks if item]
    structural_locks = [lk for lk in all_locks if _is_structural_lock(lk)]
    other_locks = [lk for lk in all_locks if not _is_structural_lock(lk)]
    locks = (structural_locks + other_locks)[:6]

    features_parts = []
    if card.overall_features:
        features_parts.append(card.overall_features)

    # 2026-07-28 消融证实：注入主体外观描述（颜色材质/形态/位置关系）会导致
    # 图生图模型重绘主体，禁止开启。字段解析保留完好供好评文案使用。
    if inject_appearance:
        if card.colors_materials:
            features_parts.append("颜色材质：" + "、".join(card.colors_materials[:4]))
        if card.product_form:
            features_parts.append(f"形态：{card.product_form}")

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
    for ts in card.text_specs:
        if ts.content and ts.confidence in ("高", "中"):
            features_parts.append(f"可读文字「{ts.content}」")

    angle_level = _get_angle_tolerance_level(card)
    angle_suffix = ""
    if angle_level == "低":
        angle_suffix = "视角约束：保持参考图视角，不换角度。"
    elif angle_level == "中":
        angle_suffix = "视角约束：仅允许轻微视角变化。"

    facts = "；".join(features_parts + locks)
    scene_text = "；".join(
        value
        for value in [
            f"场景：{scene.scene}" if scene.scene else "",
            f"位置：{scene.placement}" if scene.placement else "",
        ]
        if value
    )
    base = f"商品主体：{identity}。关键保真：{facts or '以参考原图为准'}。{scene_text}。普通手机随手拍，真实自然，不过度商业化。"
    if angle_suffix:
        base = base + angle_suffix
    return base


def render_generation_prompt(
    template: str,
    card: FactCard,
    scene: Scene,
    shot_type: str,
    realism_ctx: RealismContext | None = None,
    camera_seed: int | None = None,
    inject_appearance: bool = False,
) -> tuple[str, str, CameraPosition | None]:
    product_brief = compress_fact_card(card, scene, inject_appearance=inject_appearance)
    avoid_parts = list(card.ignored_elements or [])
    # 4.4: Always add text avoidance
    avoid_parts.append("避免画面中出现清晰可读的文字、标签、印刷字")
    if avoid_parts:
        avoid_text = "；".join(avoid_parts)
    else:
        avoid_text = ""

    environment_requirements = ""
    if realism_ctx:
        environment_requirements = render_realism_text(realism_ctx)

    angle_level = _get_angle_tolerance_level(card)
    view_angle_constraint = VIEW_ANGLE_CONSTRAINT_TEXT.get(angle_level, VIEW_ANGLE_CONSTRAINT_TEXT["低"])

    camera_pos: CameraPosition | None = None
    camera_text = ""
    if camera_seed is not None:
        cam_rng = random.Random(camera_seed)
        camera_pos = draw_camera_position(cam_rng, angle_level)
        camera_text = camera_pos.to_prompt()
        # Append camera position to environment requirements
        if environment_requirements:
            environment_requirements += f"\n构图机位：{camera_text}"
        else:
            environment_requirements = f"构图机位：{camera_text}"

    # 4.2/4.3: Accessory handling — 20-30% probability when accessories exist
    accessory_rng = random.Random(camera_seed) if camera_seed is not None else random.Random()
    if card.accessories and accessory_rng.random() < 0.25:
        acc = accessory_rng.choice(card.accessories)
        poses = [
            "盒盖打开", "倒扣放置", "只在画面边缘露一角", "上面压着别的东西",
        ]
        pose = accessory_rng.choice(poses)
        acc_text = f"配件/包装「{acc.name}」可出现，姿态：{pose}。禁止盖好+端正+标签正对镜头。"
        if environment_requirements:
            environment_requirements += f"\n{acc_text}"
        else:
            environment_requirements = acc_text

    values = {
        "product_brief": product_brief,
        "shot_type": SHOT_TYPE_TEXT.get(shot_type, shot_type),
        "scene_type": scene.scene or "",
        "placement": scene.placement or "",
        "scale_expression": scale_expression_text(card),
        "environment_requirements": environment_requirements,
        "avoid_content": avoid_text,
        "view_angle_constraint": view_angle_constraint,
    }
    return product_brief, template.format(**values), camera_pos
