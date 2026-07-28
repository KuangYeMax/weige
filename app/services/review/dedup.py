"""Batch-level deduplication for review content.

Reads sibling content.txt files from staging_dir to check similarity
against the current review before it is written to disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _char_jaccard(a: str, b: str) -> float:
    """Character bigram Jaccard similarity."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams_a = set(a[i:i+2] for i in range(len(a) - 1))
    bigrams_b = set(b[i:i+2] for i in range(len(b) - 1))
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def read_sibling_texts(siblings_dir: Path, exclude_dir_name: str) -> list[str]:
    """Read all content.txt from sibling code directories, excluding current."""
    texts = []
    if not siblings_dir.is_dir():
        return texts
    for child in siblings_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name == exclude_dir_name:
            continue
        content_file = child / "content.txt"
        if content_file.exists():
            text = content_file.read_text(encoding="utf-8").strip()
            if text:
                texts.append(text)
    return texts


def check_similarity(
    candidate: str,
    siblings: list[str],
    threshold: float = 0.6,
) -> bool:
    """Return True if candidate is too similar to any sibling."""
    for sibling in siblings:
        sim = _char_jaccard(candidate, sibling)
        if sim >= threshold:
            logger.warning(
                "review similarity %.2f >= threshold %.2f",
                sim, threshold,
            )
            return True
    return False
