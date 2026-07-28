"""Deterministic variety sampling for review generation.

Uses task_id to derive stable per-pool seeds so that:
- Same task_id always produces the same sampling sequence (crash-recovery safe)
- Different pools use independent seeds (no combination collapse)
- Existing task_rng in dispatch_scheduler is NOT touched
"""

from __future__ import annotations

import hashlib
import random

from app.services.review.constants import (
    DIMENSION_POOL,
    LENGTH_TIERS,
    MINOR_FLAW_POOL,
    OPENING_POOL,
    REVIEW_STYLES,
)


def _pool_seed(task_id: str, pool_name: str) -> int:
    raw = hashlib.sha256((task_id + pool_name).encode()).digest()[:8]
    return int.from_bytes(raw, "big")


def _deterministic_shuffle(pool: list, seed: int) -> list:
    rng = random.Random(seed)
    result = pool[:]
    rng.shuffle(result)
    return result


def _pick_at_index(pool: list, seed: int, index: int) -> object:
    n = len(pool)
    if n == 0:
        raise ValueError("pool is empty")

    round_num = index // n
    pos_in_round = index % n

    shuffled = _deterministic_shuffle(pool, seed + round_num)

    if round_num > 0 and pos_in_round == 0:
        prev_round = _deterministic_shuffle(pool, seed + round_num - 1)
        prev_last = prev_round[-1]
        if shuffled[0] == prev_last and n > 1:
            shuffled[0], shuffled[1] = shuffled[1], shuffled[0]

    return shuffled[pos_in_round]


def _pick_length_tier(seed: int, index: int) -> tuple[str, int]:
    """Pick a length tier with equal probability and return (label, target_length)."""
    rng = random.Random(seed + index)
    tier = rng.choice(LENGTH_TIERS)
    label, lo, hi = tier
    target = rng.randint(lo, hi)
    return label, target


def _should_mention_color(seed: int, index: int) -> bool:
    """~25% probability of mentioning color."""
    rng = random.Random(seed + index + 3333)
    return rng.random() < 0.25


def _should_include_minor_flaw(seed: int, index: int) -> bool:
    """~67% probability of having a minor flaw (1/3 chance of no flaw)."""
    rng = random.Random(seed + index + 7777)
    return rng.random() < 0.67


def _should_include_price_word(seed: int, index: int) -> bool:
    """Price-related words appear in ~30% of reviews."""
    rng = random.Random(seed + index + 9999)
    return rng.random() < 0.30


class VarietySampler:
    """Stateless, deterministic sampler for review variety parameters.

    All outputs are fully determined by (task_id, task_index).
    """

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._seeds = {
            "style": _pool_seed(task_id, "style"),
            "opening": _pool_seed(task_id, "opening"),
            "dimension": _pool_seed(task_id, "dimension"),
            "length": _pool_seed(task_id, "length"),
            "flaw": _pool_seed(task_id, "flaw"),
            "color": _pool_seed(task_id, "color"),
            "price": _pool_seed(task_id, "price"),
        }

    def sample(self, task_index: int) -> dict:
        """Return all variety parameters for the given index within this task."""
        style = _pick_at_index(REVIEW_STYLES, self._seeds["style"], task_index)
        opening = _pick_at_index(OPENING_POOL, self._seeds["opening"], task_index)
        dimension = _pick_at_index(DIMENSION_POOL, self._seeds["dimension"], task_index)
        length_label, target_length = _pick_length_tier(
            self._seeds["length"], task_index
        )
        mention_color = _should_mention_color(self._seeds["color"], task_index)
        include_flaw = _should_include_minor_flaw(self._seeds["flaw"], task_index)
        include_price = _should_include_price_word(self._seeds["price"], task_index)

        minor_flaw = None
        if include_flaw:
            minor_flaw = _pick_at_index(
                MINOR_FLAW_POOL, self._seeds["flaw"], task_index
            )

        return {
            "style": style,
            "opening": opening,
            "dimension": dimension,
            "length_label": length_label,
            "target_length": target_length,
            "mention_color": mention_color,
            "minor_flaw": minor_flaw,
            "include_price_word": include_price,
        }
