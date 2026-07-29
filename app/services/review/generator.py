from __future__ import annotations

import json
import random
import re
import logging

from app.config import Settings
from app.errors import AppError
from app.schemas import FactCard
from app.services.review.client import call_ark
from app.services.review.constants import (
    DIMENSION_POOL,
    OPENING_POOL,
    REVIEW_PROMPT_TEMPLATE,
    REVIEW_STYLES,
)
from app.services.review.variety import VarietySampler

logger = logging.getLogger(__name__)

_SAFE_FALLBACK_REVIEWS = (
    "已收到商品，整体符合描述。",
    "商品已收，整体符合描述。",
    "已收货，整体符合描述。",
    "东西到了，跟页面说的差不多。",
    "收到了，和描述基本一致。",
    "货收到了没问题，满意。",
    "刚拆的快递，东西不错。",
    "到手了看了看，没啥毛病。",
    "收到货了，质量可以。",
    "物流挺快，东西也行。",
)


def _hard_filter(text: str, forbidden: list[str]) -> bool:
    for word in forbidden:
        if len(word) < 2:
            continue
        if re.search(re.escape(word), text):
            return True
    return False


def normalize_review_text(text: str | None) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())


def _safe_fallback(settings: Settings, forbidden: list[str]) -> str:
    configured = normalize_review_text(settings.review_fallback_text)
    candidates: list[str] = []
    if configured and not _hard_filter(configured, forbidden):
        candidates.append(configured)
    for fallback in _SAFE_FALLBACK_REVIEWS:
        if not _hard_filter(fallback, forbidden):
            candidates.append(fallback)
    if not candidates:
        raise AppError("REVIEW_EMPTY", "无法生成合规的好评文案", 500)
    return random.choice(candidates)


def _build_card_dict(card: FactCard, settings: Settings) -> dict:
    d = card.model_dump(by_alias=True)
    d.pop("商品名称", None)
    d.pop("配件与包装", None)
    d.pop("不确定项", None)
    d["禁用词"] = settings.review_forbidden_words_hard + settings.review_forbidden_words_soft
    return d


def _generate_one(
    card: FactCard,
    settings: Settings,
    variety_params: dict | None = None,
    model: str | None = None,
) -> str:
    if variety_params:
        style = variety_params["style"]
        opening = variety_params["opening"]
        dimension = variety_params["dimension"]
        length_label = variety_params["length_label"]
        target_length = variety_params["target_length"]
        mention_color = variety_params["mention_color"]
        minor_flaw = variety_params.get("minor_flaw")
        include_price = variety_params.get("include_price_word", True)
    else:
        style = random.choice(REVIEW_STYLES)
        opening = random.choice(OPENING_POOL)
        dimension = random.choice(DIMENSION_POOL)
        length_label = "50-70"
        target_length = random.randint(50, 70)
        mention_color = random.random() < 0.25
        minor_flaw = None
        include_price = True

    color_switch = "开" if mention_color else "关"
    flaw_text = minor_flaw if minor_flaw else "无"

    price_instruction = ""
    if not include_price:
        price_instruction = "- 本条禁止出现任何价格相关表述（便宜、划算、性价比、实惠等）\n"

    card_dict = _build_card_dict(card, settings)
    card_json = json.dumps(card_dict, ensure_ascii=False)

    prompt = REVIEW_PROMPT_TEMPLATE.format(
        card=card_json,
        style=style,
        length=f"{length_label}字",
        dimension=dimension,
        opening=opening,
        color_switch=color_switch,
        flaw_instruction=flaw_text,
        price_instruction=price_instruction,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"请生成一条约{target_length}字的好评。"},
    ]

    temperature = round(random.uniform(0.9, 1.1), 2)
    return call_ark(messages, settings, temperature=temperature, model=model)


def generate_review(
    card: FactCard,
    settings: Settings,
    task_id: str | None = None,
    task_index: int = 0,
    model: str | None = None,
) -> str:
    """Generate a single review with variety parameters driven by task_id + index."""
    max_retries = settings.review_max_retries_on_forbidden
    hard_set = settings.review_forbidden_words_hard

    variety_params = None
    if task_id:
        sampler = VarietySampler(task_id)
        variety_params = sampler.sample(task_index)

    for attempt in range(max_retries + 1):
        try:
            text = normalize_review_text(
                _generate_one(card, settings, variety_params, model=model)
            )
        except AppError:
            logger.warning("review attempt %d failed, retrying", attempt + 1)
            continue

        if text and not _hard_filter(text, hard_set):
            return text

        logger.warning(
            "review attempt %d was empty or hit hard filter, retrying",
            attempt + 1,
        )

    return _safe_fallback(settings, hard_set)
