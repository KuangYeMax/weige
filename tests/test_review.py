from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.errors import AppError
from app.schemas import FactCard, Dimensions, Scene
from app.services.review.generator import (
    _build_card_dict,
    _hard_filter,
    generate_review,
)


def _make_card(**overrides: str) -> FactCard:
    base = {
        "product_name": "测试摆件",
        "overall_features": "绿色陶瓷材质，做工精致",
        "scenes": [Scene(scene="书房桌面", placement="木桌一角")],
        "dimensions": Dimensions(height_cm=15),
    }
    base.update(overrides)
    return FactCard(**base)


def test_hard_filter_blocks_forbidden_phrase():
    settings = Settings()
    assert _hard_filter("这个是最好的选择!", settings.review_forbidden_words_hard)
    assert _hard_filter("好评返现联系微信", settings.review_forbidden_words_hard)
    assert _hard_filter("行业第一的产品", settings.review_forbidden_words_hard)


def test_hard_filter_passes_clean_text():
    settings = Settings()
    assert not _hard_filter("收到商品了，质量不错，满意。", settings.review_forbidden_words_hard)
    assert not _hard_filter("很精致，放家里很好看。", settings.review_forbidden_words_hard)
    assert not _hard_filter("最近买的，第一次用感觉还行", settings.review_forbidden_words_hard)


def test_hard_filter_ignores_single_char():
    settings = Settings()
    assert not _hard_filter("最近天气不错", settings.review_forbidden_words_hard)


def test_review_forbidden_words_environment_alias_is_hard_list(monkeypatch):
    monkeypatch.delenv("REVIEW_FORBIDDEN_WORDS_HARD", raising=False)
    monkeypatch.setenv("REVIEW_FORBIDDEN_WORDS", json.dumps(["旧配置禁词", "另一个禁词"]))

    settings = Settings(_env_file=None)

    assert settings.review_forbidden_words_hard == ["旧配置禁词", "另一个禁词"]
    assert _hard_filter("包含旧配置禁词的文案", settings.review_forbidden_words_hard)


def test_review_forbidden_words_hard_accepts_current_direct_setting():
    settings = Settings(_env_file=None, review_forbidden_words_hard=["当前配置禁词"])

    assert settings.review_forbidden_words_hard == ["当前配置禁词"]


def test_minor_flaw_defaults_stay_outside_product_quality_and_expectations():
    settings = Settings()

    assert settings.review_minor_flaw_defaults[2] == "客服回复可以再及时一点"


def test_build_card_dict_adds_review_fields():
    settings = Settings()
    card = _make_card()
    d = _build_card_dict(card, settings)
    assert "禁用词" in d
    assert settings.review_forbidden_words_hard[0] in d["禁用词"]
    assert "配件与包装" not in d
    assert "不确定项" not in d


def test_fact_card_by_alias_keys_present():
    card = _make_card()
    d = card.model_dump(by_alias=True)
    assert "商品名称" in d
    assert "整体特征" in d
    assert "自然场景" in d
    assert "尺寸" in d


def test_review_generate_content_fallback(settings):
    from app.services.dispatch_generation import generate_content

    settings.review_provider = "disabled"
    card = _make_card()
    result = generate_content(card, {"name": "测试"}, settings)
    assert result.text.strip()
    assert result.is_fallback is True


def test_review_generate_content_raises_on_missing_key(settings):
    from app.services.dispatch_generation import generate_content as gc

    settings.review_provider = "ark"
    settings.ark_api_key = ""
    card = _make_card()
    result = gc(card, {"name": "测试"}, settings)
    assert result.text


@pytest.mark.parametrize("model_output", ["   \n\t", "这是最好用的商品"])
def test_generate_review_uses_nonempty_safe_fallback_after_invalid_attempts(monkeypatch, settings, model_output):
    from app.services.review import generator

    settings.review_max_retries_on_forbidden = 0
    settings.review_fallback_text = ""
    monkeypatch.setattr(generator, "_generate_one", lambda *a, **kw: model_output)

    text = generate_review(_make_card(), settings)

    assert text
    assert text == text.strip()
    assert not _hard_filter(text, settings.review_forbidden_words_hard)


def test_generate_review_raises_when_every_fallback_hits_hard_filter(monkeypatch, settings):
    from app.services.review import generator

    settings.review_max_retries_on_forbidden = 0
    settings.review_fallback_text = ""
    settings.review_forbidden_words_hard = [
        "收到", "商品", "收货", "东西", "货收", "快递", "到手", "物流", "符合",
    ]
    monkeypatch.setattr(generator, "_generate_one", lambda *a, **kw: "收到商品了")

    with pytest.raises(AppError, match="无法生成合规的好评文案") as caught:
        generate_review(_make_card(), settings)

    assert caught.value.code == "REVIEW_EMPTY"


def test_generate_content_normalizes_review_before_writing(monkeypatch, settings):
    from app.services import review
    from app.services.dispatch_generation import generate_content

    settings.review_provider = "ark"
    monkeypatch.setattr(review.generator, "generate_review", lambda *a, **kw: "  收到商品了，满意。\n")

    result = generate_content(_make_card(), {"name": "测试"}, settings)
    assert result.text == "收到商品了，满意。"
    assert result.is_fallback is False


# ─── fallback 随机性测试 ───────────────────────────────────


def test_fallback_randomness_at_least_15_unique_in_20_and_low_similarity():
    """同一商品连续生成 20 条 fallback，去重 ≥ 15，任意两条最长公共子串 ≤ 8 字。"""
    settings = Settings(review_provider="disabled")
    card = _make_card()
    product = {"name": "保温杯"}
    from app.services.dispatch_generation import generate_content

    results = [generate_content(card, product, settings).text for _ in range(20)]
    unique = set(results)

    for text in results:
        assert text.strip(), f"空文案: {text!r}"
        for word in settings.review_forbidden_words_hard:
            assert word not in text, f"文案含硬禁词 '{word}': {text}"

    assert len(unique) >= 15, (
        f"去重后仅 {len(unique)} 条，不足 15 条。\n全部结果:\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(results))
    )

    def _longest_common_substring_len(a: str, b: str) -> int:
        m, n = len(a), len(b)
        max_len = 0
        prev = [0] * (n + 1)
        for i in range(1, m + 1):
            curr = [0] * (n + 1)
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    curr[j] = prev[j - 1] + 1
                    if curr[j] > max_len:
                        max_len = curr[j]
            prev = curr
        return max_len

    unique_list = list(unique)
    for i in range(len(unique_list)):
        for j in range(i + 1, len(unique_list)):
            lcs = _longest_common_substring_len(unique_list[i], unique_list[j])
            assert lcs <= 8, (
                f"两条文案最长公共子串 {lcs} 字 > 8:\n"
                f"  A: {unique_list[i]}\n  B: {unique_list[j]}"
            )


def test_safe_fallback_randomness_at_least_8_unique_in_20():
    """_safe_fallback 池子连续 20 次 ≥ 8 种不同文案。"""
    from app.services.review.generator import _safe_fallback

    settings = Settings()
    results = [_safe_fallback(settings, settings.review_forbidden_words_hard) for _ in range(20)]
    unique = set(results)

    for text in results:
        assert text.strip()
        for word in settings.review_forbidden_words_hard:
            assert word not in text

    assert len(unique) >= 8, (
        f"去重后仅 {len(unique)} 条。\n全部结果:\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(results))
    )
