"""Tests for review variety, dedup, forbidden words whitelist, and observability."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.schemas import FactCard, Dimensions, Scene
from app.services.review.dedup import _char_jaccard, check_similarity, read_sibling_texts
from app.services.review.variety import VarietySampler


def _make_card(**overrides) -> FactCard:
    base = {
        "product_name": "测试摆件",
        "overall_features": "绿色陶瓷材质，做工精致",
        "scenes": [Scene(scene="书房桌面", placement="木桌一角")],
        "dimensions": Dimensions(height_cm=15),
    }
    base.update(overrides)
    return FactCard(**base)


# ═══════════════════════════════════════════════════════════
# Batch 2: Forbidden words whitelist regression tests
# ═══════════════════════════════════════════════════════════


class TestForbiddenWordsWhitelist:
    """Ensure normal expressions containing sub-strings of forbidden words pass."""

    def test_hard_filter_blocks_new_risk_words(self):
        from app.services.review.generator import _hard_filter

        settings = Settings()
        hard = settings.review_forbidden_words_hard
        assert _hard_filter("好评返现联系我", hard)
        assert _hard_filter("五星好评送礼物", hard)
        assert _hard_filter("晒图返红包", hard)
        assert _hard_filter("联系客服返钱", hard)
        assert _hard_filter("全网最低价格", hard)
        assert _hard_filter("最便宜没有之一", hard)
        assert _hard_filter("这是独一无二的", hard)
        assert _hard_filter("刷单好评", hard)

    def test_whitelist_expressions_pass(self):
        """These normal expressions must NOT be blocked."""
        from app.services.review.generator import _hard_filter

        settings = Settings()
        hard = settings.review_forbidden_words_hard
        safe_expressions = [
            "最近买的，感觉还行",
            "最后还是选了这个",
            "最初有点犹豫",
            "第一次买这种东西",
            "第一天到货就用上了",
            "唯一美中不足就是物流慢了点",
            "这是我唯一一次冲动消费",
            "做工还不错，挺独特的",
            "虽然等了很久，最终还是到了",
        ]
        for expr in safe_expressions:
            assert not _hard_filter(expr, hard), f"误杀了正常表达: {expr!r}"


# ═══════════════════════════════════════════════════════════
# Batch 3: Variety determinism and diversity tests
# ═══════════════════════════════════════════════════════════


class TestVarietyDeterminism:
    """Same task_id + index must produce the same result across calls/processes."""

    def test_same_task_id_produces_same_sequence(self):
        task_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        sampler1 = VarietySampler(task_id)
        sampler2 = VarietySampler(task_id)
        for i in range(10):
            assert sampler1.sample(i) == sampler2.sample(i)

    def test_different_task_ids_produce_different_sequences(self):
        s1 = VarietySampler("aaaa1111bbbb2222cccc3333dddd4444")
        s2 = VarietySampler("ffff5555eeee6666dddd7777cccc8888")
        results1 = [s1.sample(i)["opening"] for i in range(5)]
        results2 = [s2.sample(i)["opening"] for i in range(5)]
        assert results1 != results2

    def test_no_repeated_openings_in_batch(self):
        """Within a single task, openings should not repeat until pool exhausted."""
        task_id = "deadbeef12345678deadbeef12345678"
        sampler = VarietySampler(task_id)
        from app.services.review.variety import OPENING_POOL

        # Sample pool_size items — should all be unique
        openings = [sampler.sample(i)["opening"] for i in range(len(OPENING_POOL))]
        assert len(set(openings)) == len(OPENING_POOL)

    def test_no_repeated_skeletons_in_batch(self):
        task_id = "deadbeef12345678deadbeef12345678"
        sampler = VarietySampler(task_id)
        from app.services.review.constants import REVIEW_STYLES

        styles = [sampler.sample(i)["style"] for i in range(len(REVIEW_STYLES))]
        assert len(set(styles)) == len(REVIEW_STYLES)

    def test_opening_and_skeleton_not_locked_together(self):
        """Different pools must shuffle independently — not always same pairing."""
        task_id = "11112222333344445555666677778888"
        sampler = VarietySampler(task_id)
        pairs = [(sampler.sample(i)["opening"], sampler.sample(i)["style"]) for i in range(6)]
        unique_pairs = set(pairs)
        assert len(unique_pairs) == 6

    def test_price_word_density_under_30_percent(self):
        """Over 100 samples, price word inclusion should be around 30%."""
        task_id = "abcdef01234567890abcdef012345678"
        sampler = VarietySampler(task_id)
        count = sum(1 for i in range(100) if sampler.sample(i)["include_price_word"])
        assert count < 45, f"Price word appeared {count}/100 times, expected ~30"

    def test_minor_flaw_density_around_67_percent(self):
        task_id = "abcdef01234567890abcdef012345678"
        sampler = VarietySampler(task_id)
        count = sum(1 for i in range(100) if sampler.sample(i)["minor_flaw"] is not None)
        assert 50 < count < 85, f"Minor flaw appeared {count}/100 times, expected ~67"

    def test_length_tier_distribution(self):
        """All 4 tiers should appear with some frequency."""
        task_id = "abcdef01234567890abcdef012345678"
        sampler = VarietySampler(task_id)
        tiers = [sampler.sample(i)["length_label"] for i in range(100)]
        unique_tiers = set(tiers)
        assert len(unique_tiers) >= 3, f"Only got tiers: {unique_tiers}"


# ═══════════════════════════════════════════════════════════
# Batch 4: Minor flaw tests
# ═══════════════════════════════════════════════════════════


class TestMinorFlaws:
    def test_minor_flaw_pool_has_at_least_4_items(self):
        from app.services.review.constants import MINOR_FLAW_POOL

        assert len(MINOR_FLAW_POOL) >= 4

    def test_minor_flaws_do_not_mention_product_quality(self):
        from app.services.review.constants import MINOR_FLAW_POOL

        quality_words = ["做工粗糙", "瑕疵", "色差大", "质量差", "破损", "掉色"]
        for flaw in MINOR_FLAW_POOL:
            for bad in quality_words:
                assert bad not in flaw, f"Minor flaw '{flaw}' contains quality issue '{bad}'"

    def test_minor_flaw_never_empty_string(self):
        """When include_flaw is True, the flaw text must never be empty."""
        task_id = "flaw_test_id_00000000000000000000"
        sampler = VarietySampler(task_id)
        for i in range(50):
            params = sampler.sample(i)
            if params["minor_flaw"] is not None:
                assert params["minor_flaw"].strip(), f"Empty flaw at index {i}"


# ═══════════════════════════════════════════════════════════
# Batch 5: Dedup tests
# ═══════════════════════════════════════════════════════════


class TestDedup:
    def test_jaccard_identical_strings(self):
        assert _char_jaccard("完全相同的文案内容", "完全相同的文案内容") == 1.0

    def test_jaccard_completely_different(self):
        sim = _char_jaccard("AAABBBCCC", "一二三四五六七八九")
        assert sim < 0.1

    def test_check_similarity_catches_high_overlap(self):
        candidate = "这个摆件收到了，做工挺精致的，放桌上很好看"
        siblings = ["这个摆件收到了，做工挺精致的，放书架上很好看"]
        assert check_similarity(candidate, siblings, threshold=0.6)

    def test_check_similarity_passes_different_texts(self):
        candidate = "等了两天终于到了，颜色比图上深一点但也不错"
        siblings = ["朋友推荐买的，手感确实不错"]
        assert not check_similarity(candidate, siblings, threshold=0.6)

    def test_read_sibling_texts_excludes_current_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "code_A").mkdir()
            (staging / "code_A" / "content.txt").write_text("文案A", encoding="utf-8")
            (staging / "code_B").mkdir()
            (staging / "code_B" / "content.txt").write_text("文案B", encoding="utf-8")

            texts = read_sibling_texts(staging, "code_A")
            assert texts == ["文案B"]

    def test_dedup_triggers_needs_review(self, monkeypatch, settings):
        """Two highly similar generated texts should result in needs_review."""
        from app.services.dispatch_generation import generate_content

        settings.review_provider = "ark"
        similar_text = "这个摆件收到了做工挺精致的放桌上很好看满意"

        call_count = [0]

        def mock_gen(*args, **kwargs):
            call_count[0] += 1
            return similar_text

        from app.services.review import generator
        monkeypatch.setattr(generator, "_generate_one", mock_gen)

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            sibling_dir = staging / "sibling_code"
            sibling_dir.mkdir()
            (sibling_dir / "content.txt").write_text(similar_text, encoding="utf-8")

            card = _make_card()
            result = generate_content(
                card, {"name": "测试", "code": "my_code"},
                settings, task_id="test_task_id_for_dedup_000000000",
                task_index=1, siblings_dir=staging,
            )
            assert result.status == "needs_review"
            assert result.text == similar_text
            assert call_count[0] == 2  # tried twice


# ═══════════════════════════════════════════════════════════
# Batch 6: Observability / ContentResult metadata
# ═══════════════════════════════════════════════════════════


class TestObservability:
    def test_content_result_has_metadata_fields(self, monkeypatch, settings):
        from app.services.dispatch_generation import generate_content

        settings.review_provider = "ark"

        from app.services.review import generator
        monkeypatch.setattr(
            generator, "_generate_one",
            lambda *a, **kw: "朋友推荐买的，到手看了下挺不错",
        )

        card = _make_card()
        result = generate_content(
            card, {"name": "测试", "code": "X001"},
            settings, task_id="meta_test_00000000000000000000000",
            task_index=0,
        )
        assert result.status == "ai"
        assert result.opening != ""
        assert result.skeleton != ""
        assert result.length_tier in ("30-45", "50-70", "80-110", "120-150")
        assert isinstance(result.has_minor_flaw, bool)
        assert result.model == settings.ark_review_model

    def test_fallback_result_has_status_fallback(self, settings):
        from app.services.dispatch_generation import generate_content

        settings.review_provider = "disabled"
        card = _make_card()
        result = generate_content(card, {"name": "测试", "code": "X001"}, settings)
        assert result.status == "fallback"
        assert result.is_fallback is True
        assert result.text.strip()


# ═══════════════════════════════════════════════════════════
# Batch 3 bonus: Mock batch generation of 20 — verify no repeat openings
# ═══════════════════════════════════════════════════════════


class TestBatchGeneration:
    def test_20_items_no_repeated_opening_skeleton(self, monkeypatch, settings):
        from app.services.dispatch_generation import generate_content

        settings.review_provider = "ark"

        counter = [0]

        def mock_gen(*a, **kw):
            counter[0] += 1
            return f"这是第{counter[0]}条独特的文案内容，每条都不一样哦"

        from app.services.review import generator
        monkeypatch.setattr(generator, "_generate_one", mock_gen)

        task_id = "batch20_test_id_000000000000000000"
        results = []
        for i in range(20):
            r = generate_content(
                _make_card(), {"name": "测试", "code": f"C{i:03d}"},
                settings, task_id=task_id, task_index=i,
            )
            results.append(r)

        openings = [r.opening for r in results]
        skeletons = [r.skeleton for r in results]

        # Within the pool size, all should be unique
        from app.services.review.constants import OPENING_POOL, REVIEW_STYLES

        first_round_openings = openings[:len(OPENING_POOL)]
        assert len(set(first_round_openings)) == len(OPENING_POOL)

        first_round_styles = skeletons[:len(REVIEW_STYLES)]
        assert len(set(first_round_styles)) == len(REVIEW_STYLES)

        # Print for manual inspection
        for i, r in enumerate(results):
            print(
                f"  [{i:2d}] opening={r.opening!r:20s} "
                f"skeleton={r.skeleton!r:30s} "
                f"tier={r.length_tier:6s} flaw={r.has_minor_flaw}"
            )

