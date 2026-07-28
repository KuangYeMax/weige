from __future__ import annotations

import random
from dataclasses import dataclass, field


SURFACE_POOL: list[tuple[str, int]] = [
    ("原木桌", 10),
    ("深色木桌", 10),
    ("白色台面", 10),
    ("大理石纹台面", 8),
    ("玻璃桌面", 7),
    ("布艺桌布", 8),
    ("竹席草编", 6),
    ("木地板", 8),
    ("窗台", 7),
    ("书桌一角", 10),
    ("茶几", 8),
    ("餐桌角", 8),
]

WEAR_MARKS: list[tuple[str, int]] = [
    ("划痕", 10),
    ("水杯环渍", 8),
    ("茶渍油点", 7),
    ("薄灰", 9),
    ("木纹裂纹", 6),
    ("边缘磨损掉漆", 5),
    ("笔迹污点", 6),
    ("局部褪色", 7),
    ("反光不均", 8),
]

WEAR_LEVELS: list[tuple[str, int]] = [
    ("全新", 20),
    ("轻微使用", 45),
    ("明显使用", 30),
    ("旧", 5),
]

WEAR_LEVEL_MARK_COUNT = {
    "全新": 0,
    "轻微使用": 1,
    "明显使用": 2,
    "旧": 2,
}

CLUTTER_CATEGORIES: dict[str, list[str]] = {
    "文具": ["笔", "便签", "剪刀", "胶带"],
    "电子": ["耳机", "数据线", "充电器", "遥控器", "手机"],
    "饮食": ["茶杯", "水瓶", "零食袋", "餐巾纸"],
    "生活": ["钥匙", "眼镜", "口罩", "打火机"],
    "纸品": ["书", "杂志", "便利贴"],
}

PARTIAL_ENTRY_ITEMS = {"手机"}

FORBIDDEN_RITUAL_ITEMS = frozenset([
    "香炉", "蜡烛", "烟雾", "成对对称摆件", "燃香",
])

CLUTTER_LEVELS: list[tuple[str, int]] = [
    ("整洁", 30),
    ("轻", 40),
    ("中", 25),
    ("乱", 5),
]

CLUTTER_LEVEL_COUNT = {
    "整洁": (0, 0),
    "轻": (1, 1),
    "中": (2, 3),
    "乱": (3, 5),
}

SCENE_POOL: list[tuple[str, int]] = [
    ("已摆在架上/柜上", 20),
    ("刚放桌上未归位", 20),
    ("和其他小物摆一起", 15),
    ("办公桌一角", 15),
    ("窗台边", 10),
    ("玄关柜", 10),
    ("手扶着看", 5),
    ("刚拆快递旁有包装", 5),
]


@dataclass
class ClutterItem:
    name: str
    category: str
    partial_only: bool = False


@dataclass
class RealismContext:
    surface: str
    wear_level: str
    wear_marks: list[str]
    clutter_level: str
    clutter_items: list[ClutterItem]
    scene: str
    seed: int


def _weighted_choice(rng: random.Random, pool: list[tuple[str, int]]) -> str:
    items, weights = zip(*pool)
    return rng.choices(items, weights=weights, k=1)[0]


def _weighted_sample(
    rng: random.Random, pool: list[tuple[str, int]], k: int
) -> list[str]:
    if k <= 0:
        return []
    items, weights = zip(*pool)
    chosen: list[str] = []
    available = list(zip(items, weights))
    for _ in range(min(k, len(available))):
        cur_items, cur_weights = zip(*available)
        pick = rng.choices(cur_items, weights=cur_weights, k=1)[0]
        chosen.append(pick)
        available = [(i, w) for i, w in available if i != pick]
        if not available:
            break
    return chosen


def _pick_clutter(rng: random.Random, count: int) -> list[ClutterItem]:
    if count <= 0:
        return []
    categories = list(CLUTTER_CATEGORIES.keys())
    rng.shuffle(categories)
    result: list[ClutterItem] = []
    used_categories: set[str] = set()
    for cat in categories:
        if len(result) >= count:
            break
        if cat in used_categories:
            continue
        items = CLUTTER_CATEGORIES[cat]
        pick = rng.choice(items)
        result.append(ClutterItem(
            name=pick,
            category=cat,
            partial_only=pick in PARTIAL_ENTRY_ITEMS,
        ))
        used_categories.add(cat)
    if len(result) < count:
        for cat in categories:
            if len(result) >= count:
                break
            items = [i for i in CLUTTER_CATEGORIES[cat]
                     if not any(r.name == i for r in result)]
            if items:
                pick = rng.choice(items)
                result.append(ClutterItem(
                    name=pick,
                    category=cat,
                    partial_only=pick in PARTIAL_ENTRY_ITEMS,
                ))
    return result[:count]


def draw_realism_context(seed: int, shot_type: str = "中近景") -> RealismContext:
    rng = random.Random(seed)

    surface = _weighted_choice(rng, SURFACE_POOL)
    wear_level = _weighted_choice(rng, WEAR_LEVELS)
    mark_count = WEAR_LEVEL_MARK_COUNT[wear_level]
    marks = _weighted_sample(rng, WEAR_MARKS, mark_count)

    clutter_level = _weighted_choice(rng, CLUTTER_LEVELS)
    if shot_type == "细节照" and clutter_level in ("中", "乱"):
        clutter_level = "轻"

    min_count, max_count = CLUTTER_LEVEL_COUNT[clutter_level]
    item_count = rng.randint(min_count, max_count) if max_count > 0 else 0
    clutter_items = _pick_clutter(rng, item_count)

    scene = _weighted_choice(rng, SCENE_POOL)

    return RealismContext(
        surface=surface,
        wear_level=wear_level,
        wear_marks=marks,
        clutter_level=clutter_level,
        clutter_items=clutter_items,
        scene=scene,
        seed=seed,
    )


def render_realism_text(ctx: RealismContext) -> str:
    parts: list[str] = []
    parts.append(f"承托面：{ctx.surface}")

    if ctx.wear_marks:
        marks_text = "、".join(ctx.wear_marks)
        parts.append(f"台面状态：{ctx.wear_level}，可见{marks_text}")
    else:
        parts.append(f"台面状态：{ctx.wear_level}")

    if ctx.clutter_items:
        item_descs: list[str] = []
        for item in ctx.clutter_items:
            if item.partial_only:
                item_descs.append(f"{item.name}（入画不完整、被边缘裁切）")
            else:
                item_descs.append(item.name)
        items_text = "、".join(item_descs)
        parts.append(
            f"桌面杂物（{ctx.clutter_level}）：{items_text}，"
            f"均位于画面边缘或角落、入画不完整、不居中、不成对、不对称"
        )
    else:
        parts.append("桌面杂物：无")

    parts.append(f"摆放情境：{ctx.scene}")
    return "；".join(parts) + "。"


def realism_metadata(ctx: RealismContext) -> dict:
    return {
        "realism_seed": ctx.seed,
        "surface": ctx.surface,
        "wear_level": ctx.wear_level,
        "wear_marks": ctx.wear_marks,
        "clutter_level": ctx.clutter_level,
        "clutter_items": [
            {"name": i.name, "category": i.category, "partial_only": i.partial_only}
            for i in ctx.clutter_items
        ],
        "scene": ctx.scene,
    }
