"""方案A v2：环境锚 + 必然共现物，基于实测校准锚池。"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── A 类 · 通用环境锚 ──
UNIVERSAL_ANCHORS = [
    {"n": "86型墙面开关面板", "cm": 8.6, "axis": "边长", "conf": "hard", "role": "ratio"},
    {"n": "86型墙面插座面板", "cm": 8.6, "axis": "边长", "conf": "hard", "role": "ratio"},
    {"n": "118型开关面板", "cm": 11.8, "axis": "长", "conf": "hard", "role": "ratio"},
    {"n": "踢脚线", "cm": 8.0, "axis": "高", "conf": "common", "role": "ambient"},
    {"n": "实木地板板条", "cm": 9.0, "axis": "宽", "conf": "common", "role": "ambient"},
    {"n": "复合地板板条", "cm": 19.5, "axis": "宽", "conf": "common", "role": "ambient"},
    {"n": "地砖", "cm": 80, "axis": "边长", "conf": "common", "role": "ambient"},
    {"n": "墙砖", "cm": 30, "axis": "边长", "conf": "common", "role": "ambient"},
    {"n": "标准红砖", "cm": 24, "axis": "长", "conf": "hard", "role": "ambient"},
    {"n": "门洞", "cm": 90, "axis": "宽", "conf": "common", "role": "ambient"},
    {"n": "门把手离地", "cm": 100, "axis": "高", "conf": "common", "role": "ambient"},
    {"n": "窗台离地", "cm": 90, "axis": "高", "conf": "common", "role": "ambient"},
    {"n": "窗框横档", "cm": 6.0, "axis": "宽", "conf": "common", "role": "ambient"},
    {"n": "标准室内门", "cm": 200, "axis": "高", "conf": "hard", "role": "ambient"},
    {"n": "空调挂机", "cm": 90, "axis": "长", "conf": "common", "role": "ambient"},
    {"n": "墙面挂画", "cm": 60, "axis": "长", "conf": "common", "role": "ambient"},
    {"n": "护墙板单块", "cm": 40, "axis": "宽", "conf": "common", "role": "ambient"},
    {"n": "台阶单级", "cm": 15, "axis": "高", "conf": "hard", "role": "ambient"},
]

# ── B 类 · 分房间共现物 ──
ROOM_COMPANIONS: dict[str, list[dict]] = {
    "客厅": [
        {"n": "茶几", "cm": 45, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "边几", "cm": 55, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "电视柜", "cm": 45, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "沙发座面", "cm": 42, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "沙发扶手", "cm": 60, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "抱枕", "cm": 45, "axis": "边长", "conf": "hard", "role": "ratio"},
        {"n": "电视遥控器", "cm": 17, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "纸巾盒", "cm": 24, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "果盘", "cm": 28, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "玻璃水杯", "cm": 12, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "陶瓷茶杯", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "桌面花瓶", "cm": 30, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "香薰杯蜡", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "落地灯", "cm": 160, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "小盆绿植", "cm": 30, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "绿植花盆", "cm": 15, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "画册杂志", "cm": 28, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "木托盘", "cm": 35, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "桌面音箱", "cm": 20, "axis": "高", "conf": "common", "role": "ratio"},
    ],
    "书房": [
        {"n": "书桌台面离地", "cm": 75, "axis": "高", "conf": "hard", "role": "ambient"},
        {"n": "书架单层", "cm": 35, "axis": "层高", "conf": "common", "role": "ambient"},
        {"n": "精装书", "cm": 24, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "16开书", "cm": 26, "axis": "高", "conf": "hard", "role": "ratio"},
        {"n": "32开书", "cm": 21, "axis": "高", "conf": "hard", "role": "ratio"},
        {"n": "A4纸", "cm": 29.7, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "A5笔记本", "cm": 21, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "笔筒", "cm": 10, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "中性笔", "cm": 14, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "铅笔", "cm": 17.5, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "书桌台灯", "cm": 40, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "书立", "cm": 15, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "镇纸", "cm": 30, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "砚台", "cm": 18, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "毛笔", "cm": 25, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "卷尺", "cm": 6.5, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "地球仪", "cm": 25, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "6寸相框", "cm": 15.2, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "7寸相框", "cm": 17.8, "axis": "长", "conf": "hard", "role": "ratio"},
    ],
    "办公室": [
        {"n": "办公桌台面离地", "cm": 75, "axis": "高", "conf": "hard", "role": "ambient"},
        {"n": "办公椅座面", "cm": 45, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "全尺寸键盘", "cm": 44, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "84键键盘", "cm": 35, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "鼠标", "cm": 12, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "鼠标垫", "cm": 30, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "13寸笔记本", "cm": 30.5, "axis": "宽", "conf": "hard", "role": "ratio"},
        {"n": "14寸笔记本", "cm": 32.2, "axis": "宽", "conf": "hard", "role": "ratio"},
        {"n": "15.6寸笔记本", "cm": 36.0, "axis": "宽", "conf": "hard", "role": "ratio"},
        {"n": "24寸显示器", "cm": 54, "axis": "宽", "conf": "hard", "role": "ratio"},
        {"n": "显示器底座", "cm": 22, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "马克杯", "cm": 9.5, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "保温杯", "cm": 22, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "名片", "cm": 9.0, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "名片盒", "cm": 9.5, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "文件框", "cm": 25, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "订书机", "cm": 15, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "台历", "cm": 15, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "计算器", "cm": 15, "axis": "长", "conf": "common", "role": "ratio"},
    ],
    "卧室": [
        {"n": "床头柜", "cm": 50, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "床垫厚度", "cm": 22, "axis": "厚", "conf": "common", "role": "ambient"},
        {"n": "床面离地", "cm": 50, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "枕头", "cm": 74, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "床头台灯", "cm": 35, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "手机", "cm": 14.7, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "小闹钟", "cm": 10, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "香薰机", "cm": 18, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "首饰盒", "cm": 20, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "台式梳妆镜", "cm": 60, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "抽纸包", "cm": 22, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "衣柜层板", "cm": 35, "axis": "层高", "conf": "common", "role": "ambient"},
    ],
    "玄关": [
        {"n": "玄关柜台面离地", "cm": 85, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "玄关台面深度", "cm": 35, "axis": "深", "conf": "common", "role": "ambient"},
        {"n": "钥匙碟", "cm": 14, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "香薰瓶", "cm": 15, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "折叠雨伞", "cm": 30, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "换鞋凳", "cm": 42, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "男士皮鞋", "cm": 26.5, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "鞋柜层高", "cm": 18, "axis": "层高", "conf": "common", "role": "ambient"},
        {"n": "装饰托盘", "cm": 30, "axis": "长", "conf": "common", "role": "ratio"},
    ],
    "茶室": [
        {"n": "茶台台面离地", "cm": 70, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "茶盘", "cm": 50, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "紫砂壶", "cm": 9.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "盖碗", "cm": 10, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "品茗杯", "cm": 4.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "公道杯", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "茶叶罐", "cm": 12, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "茶宠", "cm": 8.0, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "线香", "cm": 21, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "盘香", "cm": 12, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "小香炉", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "香插", "cm": 8.0, "axis": "长", "conf": "common", "role": "ratio"},
    ],
    "佛堂": [
        {"n": "供桌台面离地", "cm": 85, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "供杯", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "供果盘", "cm": 20, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "莲花供灯", "cm": 12, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "经书", "cm": 26, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "108颗念珠", "cm": 27, "axis": "圈径", "conf": "hard", "role": "ratio"},
        {"n": "木鱼", "cm": 12, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "蒲团", "cm": 50, "axis": "直径", "conf": "common", "role": "ambient"},
        {"n": "烛台", "cm": 20, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "小香炉", "cm": 8.0, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "线香", "cm": 21, "axis": "长", "conf": "common", "role": "ratio"},
    ],
    "餐厅": [
        {"n": "餐桌台面离地", "cm": 75, "axis": "高", "conf": "hard", "role": "ambient"},
        {"n": "餐边柜台面离地", "cm": 85, "axis": "高", "conf": "common", "role": "ambient"},
        {"n": "餐盘", "cm": 23, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "筷子", "cm": 24, "axis": "长", "conf": "hard", "role": "ratio"},
        {"n": "饭碗", "cm": 11.5, "axis": "直径", "conf": "common", "role": "ratio"},
        {"n": "玻璃杯", "cm": 13, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "红酒瓶", "cm": 30, "axis": "高", "conf": "hard", "role": "ratio"},
        {"n": "红酒杯", "cm": 21, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "布餐巾", "cm": 50, "axis": "边长", "conf": "hard", "role": "ratio"},
        {"n": "调料瓶", "cm": 15, "axis": "高", "conf": "common", "role": "ratio"},
    ],
    "展示柜": [
        {"n": "博古架单格", "cm": 35, "axis": "层高", "conf": "common", "role": "ambient"},
        {"n": "玻璃展柜层板", "cm": 30, "axis": "层高", "conf": "common", "role": "ambient"},
        {"n": "展柜台面离地", "cm": 90, "axis": "高", "conf": "hard", "role": "ambient"},
        {"n": "亚克力展示台", "cm": 10, "axis": "高", "conf": "common", "role": "ratio"},
        {"n": "射灯轨道间距", "cm": 60, "axis": "间距", "conf": "common", "role": "ambient"},
        {"n": "价签卡片", "cm": 9.0, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "绒布垫", "cm": 30, "axis": "长", "conf": "common", "role": "ratio"},
        {"n": "玻璃罩", "cm": 25, "axis": "高", "conf": "common", "role": "ratio"},
    ],
}

# ── C 类 · 通杀锚 ──
UNIVERSAL_PROPS = [
    {"n": "550ml矿泉水瓶", "cm": 21, "axis": "高", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "展示柜"]},
    {"n": "330ml易拉罐", "cm": 12.2, "axis": "高", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "茶室", "展示柜"]},
    {"n": "一次性纸杯", "cm": 9.5, "axis": "高", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "展示柜"]},
    {"n": "扑克牌", "cm": 8.8, "axis": "长", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "茶室", "展示柜"]},
    {"n": "银行卡", "cm": 8.56, "axis": "长", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "茶室"]},
    {"n": "一元硬币", "cm": 2.5, "axis": "直径", "conf": "hard", "role": "ratio", "ban_rooms": []},
    {"n": "成人张开的手掌", "cm": 20, "axis": "长", "conf": "common", "role": "ratio", "ban_rooms": []},
    {"n": "AA电池", "cm": 5.0, "axis": "长", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "茶室", "展示柜"]},
    {"n": "充电宝", "cm": 14, "axis": "长", "conf": "common", "role": "ratio", "ban_rooms": ["佛堂", "茶室", "展示柜"]},
    {"n": "眼镜盒", "cm": 16, "axis": "长", "conf": "common", "role": "ratio", "ban_rooms": ["佛堂"]},
    {"n": "一次性打火机", "cm": 8.0, "axis": "长", "conf": "hard", "role": "ratio", "ban_rooms": ["佛堂", "展示柜", "卧室"]},
]

DISPLAY_SURFACE = {
    "客厅": "茶几或电视柜",
    "书房": "书桌或书架格",
    "办公室": "办公桌",
    "卧室": "床头柜",
    "玄关": "玄关柜",
    "茶室": "茶台",
    "佛堂": "供桌",
    "餐厅": "餐边柜",
    "展示柜": "博古架格内",
}

NEGATIVE_PROMPT = (
    "monumental, temple statue, altar-sized, life-size, oversized, "
    "studio lighting, product advertisement, clean minimal background, "
    "symmetrical composition, staged props, floating object, low angle, "
    "close-up filling frame, watermark, text overlay, logo"
)

NEGATIVE_PROMPT_CLOSEUP = (
    NEGATIVE_PROMPT + ", ruler, measuring tape, coin, hand, cup, book, keyboard, phone"
)


# ── 选锚算法 ──────────────────────────────────────────────────────────────────

@dataclass
class AnchorSelection:
    ratio_anchors: list[dict]
    ambient_anchor: dict | None
    ratios: list[float]
    is_fallback: bool


def _format_ratio(raw_ratio: float, conf: str) -> str:
    if conf == "hard":
        return str(round(raw_ratio, 1))
    else:
        snapped = round(raw_ratio * 2) / 2
        return f"约 {snapped}"


def _ratio_in_range(height_cm: float, anchor_cm: float) -> bool:
    r = height_cm / anchor_cm
    return 0.5 <= r <= 4.0


def _seed_int(seed: str) -> int:
    return int(hashlib.md5(seed.encode()).hexdigest(), 16)


def pick_anchors(
    height_cm: float | None,
    room: str,
    seed: str,
    exclude_names: set[str] | None = None,
) -> AnchorSelection | None:
    """Pick ratio + ambient anchors. Returns None if no ratio anchor available (fallback to closeup)."""
    if height_cm is None:
        return None

    if room not in ROOM_COMPANIONS:
        logger.warning("未知房间 %s，无法选锚", room)
        return None

    exclude = exclude_names or set()
    rng = random.Random(_seed_int(seed))

    # Step 1-2: gather ratio candidates from room pool
    room_ratio_candidates = [
        a for a in ROOM_COMPANIONS[room]
        if a["role"] == "ratio"
        and _ratio_in_range(height_cm, a["cm"])
        and a["n"] not in exclude
    ]

    # Step 4: if room pool insufficient, supplement from UNIVERSAL_PROPS
    if len(room_ratio_candidates) < 1:
        universal_candidates = [
            a for a in UNIVERSAL_PROPS
            if _ratio_in_range(height_cm, a["cm"])
            and room not in a.get("ban_rooms", [])
            and a["n"] not in exclude
        ]
        room_ratio_candidates.extend(universal_candidates)

    # Step 6: still nothing → return None (fallback to closeup mode)
    if not room_ratio_candidates:
        return None

    # Step 3: confidence-weighted sampling
    weights = [3.0 if a["conf"] == "hard" else 1.0 for a in room_ratio_candidates]
    pick_count = min(2, len(room_ratio_candidates))
    selected_ratio: list[dict] = []
    candidates = list(room_ratio_candidates)
    candidate_weights = list(weights)

    for _ in range(pick_count):
        if not candidates:
            break
        chosen = rng.choices(candidates, weights=candidate_weights, k=1)[0]
        selected_ratio.append(chosen)
        idx = candidates.index(chosen)
        candidates.pop(idx)
        candidate_weights.pop(idx)

    # Step 5: pick 1 ambient anchor
    ambient_pool = (
        [a for a in ROOM_COMPANIONS[room] if a["role"] == "ambient"]
        + [a for a in UNIVERSAL_ANCHORS if a["role"] == "ambient"]
    )
    ambient_pool = [a for a in ambient_pool if a["n"] not in exclude]
    ambient_anchor = rng.choice(ambient_pool) if ambient_pool else None

    # Compute ratios
    ratios = [height_cm / a["cm"] for a in selected_ratio]

    return AnchorSelection(
        ratio_anchors=selected_ratio,
        ambient_anchor=ambient_anchor,
        ratios=ratios,
        is_fallback=False,
    )


# ── 提示词构建 ────────────────────────────────────────────────────────────────

def build_prompt_v2(
    product_desc: str,
    height_cm: float | None,
    room: str,
    seed: str,
    exclude_names: set[str] | None = None,
) -> tuple[str, str, AnchorSelection | None]:
    """Build prompt. Returns (positive, negative, selection_or_None)."""
    selection = pick_anchors(height_cm, room, seed, exclude_names)

    if selection is None:
        positive = (
            f"{product_desc}，近景特写，浅景深，背景虚化干净，看不到完整台面和周围环境。"
        )
        return positive, NEGATIVE_PROMPT_CLOSEUP, None

    surface = DISPLAY_SURFACE.get(room, "台面")
    ambient_phrase = ""
    if selection.ambient_anchor:
        ambient_phrase = f"画面自然带到{selection.ambient_anchor['n']}。\n"

    c1 = selection.ratio_anchors[0]
    r1 = _format_ratio(selection.ratios[0], c1["conf"])
    ratio_phrase = f"旁边放着{c1['n']}，摆件总高约为{c1['n']}{c1['axis']}的{r1}倍。\n"

    if len(selection.ratio_anchors) > 1:
        c2 = selection.ratio_anchors[1]
        r2 = _format_ratio(selection.ratios[1], c2["conf"])
        ratio_phrase += f"旁边还有{c2['n']}，摆件总高约为{c2['n']}{c2['axis']}的{r2}倍。\n"

    positive = (
        f"{product_desc}，摆放在{room}的{surface}上。\n"
        f"{ambient_phrase}"
        f"{ratio_phrase}"
        f"这些都是这个空间里本来就有的日常陈设，随手摆放，不是刻意布置的道具。\n"
        f"相机略微俯视约30度，从约1.2米外用手机主摄拍摄，\n"
        f"摆件在画面中的高度约占画幅的一半，四周留出台面和室内环境。\n"
        f"自然窗光，家常生活感，轻微杂乱，非影棚布光，非广告图。"
    )

    return positive, NEGATIVE_PROMPT, selection
