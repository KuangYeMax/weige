from __future__ import annotations

import pytest

from app.services.realism_pool import (
    ClutterItem,
    CLUTTER_CATEGORIES,
    FORBIDDEN_RITUAL_ITEMS,
    PARTIAL_ENTRY_ITEMS,
    RealismContext,
    draw_realism_context,
    realism_metadata,
    render_realism_text,
)


class TestDrawRealismContext:
    def test_reproducible_with_same_seed(self):
        ctx1 = draw_realism_context(42, "中近景")
        ctx2 = draw_realism_context(42, "中近景")
        assert ctx1 == ctx2

    def test_different_seeds_produce_different_results(self):
        ctx1 = draw_realism_context(1, "中近景")
        ctx2 = draw_realism_context(2, "中近景")
        assert ctx1 != ctx2

    def test_returns_realism_context(self):
        ctx = draw_realism_context(100, "中近景")
        assert isinstance(ctx, RealismContext)
        assert ctx.seed == 100
        assert ctx.surface
        assert ctx.wear_level
        assert ctx.scene

    def test_detail_shot_downgrades_clutter_level(self):
        for seed in range(200):
            ctx = draw_realism_context(seed, "细节照")
            assert ctx.clutter_level in ("整洁", "轻"), (
                f"seed={seed} got clutter_level={ctx.clutter_level} for detail shot"
            )

    def test_clutter_count_matches_level(self):
        from app.services.realism_pool import CLUTTER_LEVEL_COUNT
        for seed in range(100):
            ctx = draw_realism_context(seed, "中近景")
            min_c, max_c = CLUTTER_LEVEL_COUNT[ctx.clutter_level]
            assert min_c <= len(ctx.clutter_items) <= max_c

    def test_no_forbidden_ritual_items_in_clutter(self):
        for seed in range(200):
            ctx = draw_realism_context(seed, "中近景")
            for item in ctx.clutter_items:
                assert item.name not in FORBIDDEN_RITUAL_ITEMS

    def test_no_duplicate_items_in_clutter(self):
        for seed in range(200):
            ctx = draw_realism_context(seed, "中近景")
            names = [i.name for i in ctx.clutter_items]
            assert len(names) == len(set(names))

    def test_partial_only_items_flagged(self):
        found_partial = False
        for seed in range(500):
            ctx = draw_realism_context(seed, "中近景")
            for item in ctx.clutter_items:
                if item.name in PARTIAL_ENTRY_ITEMS:
                    assert item.partial_only is True
                    found_partial = True
        assert found_partial, "Should find at least one partial_only item in 500 seeds"

    def test_clutter_items_cross_category(self):
        for seed in range(100):
            ctx = draw_realism_context(seed, "中近景")
            if len(ctx.clutter_items) >= 2:
                categories = [i.category for i in ctx.clutter_items]
                assert len(set(categories)) >= 2


class TestRenderRealismText:
    def test_output_is_chinese_no_json(self):
        ctx = draw_realism_context(42, "中近景")
        text = render_realism_text(ctx)
        assert "{" not in text
        assert "}" not in text
        assert "[" not in text

    def test_no_forbidden_words(self):
        forbidden = ["vivid", "vibrant", "glossy", "鲜艳", "浓郁", "高光泽", "精致"]
        for seed in range(100):
            ctx = draw_realism_context(seed, "中近景")
            text = render_realism_text(ctx)
            for word in forbidden:
                assert word not in text

    def test_contains_surface(self):
        ctx = draw_realism_context(42, "中近景")
        text = render_realism_text(ctx)
        assert f"承托面：{ctx.surface}" in text

    def test_contains_scene(self):
        ctx = draw_realism_context(42, "中近景")
        text = render_realism_text(ctx)
        assert f"摆放情境：{ctx.scene}" in text

    def test_partial_items_described_correctly(self):
        ctx = RealismContext(
            surface="原木桌",
            wear_level="轻微使用",
            wear_marks=["划痕"],
            clutter_level="轻",
            clutter_items=[ClutterItem(name="手机", category="电子", partial_only=True)],
            scene="刚放桌上未归位",
            seed=0,
        )
        text = render_realism_text(ctx)
        assert "入画不完整" in text
        assert "被边缘裁切" in text


class TestRealismMetadata:
    def test_contains_all_fields(self):
        ctx = draw_realism_context(77, "中近景")
        meta = realism_metadata(ctx)
        assert meta["realism_seed"] == 77
        assert "surface" in meta
        assert "wear_level" in meta
        assert "wear_marks" in meta
        assert "clutter_level" in meta
        assert "clutter_items" in meta
        assert "scene" in meta

    def test_clutter_items_serializable(self):
        ctx = draw_realism_context(77, "中近景")
        meta = realism_metadata(ctx)
        for item in meta["clutter_items"]:
            assert "name" in item
            assert "category" in item
            assert "partial_only" in item
