"""阶梯式降级重试 + 数量强调 + 数量硬校验 + 结构化主体字段的测试。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from PIL import Image

from app.schemas import FactCard
from app.services.consistency_check import ConsistencyResult
from app.services.count_hard_check import CountCheckResult, check_count
from app.services.db import add_code, create_dispatch_task
from app.services.dispatch_scheduler import (
    MINIMAL_SCENE_OPTIONS,
    _effective_subject,
    process_due_tasks,
)
from app.services.normalize import normalize_fact_card
from migrate_products import migrate
import app.services.dispatch_scheduler as ds


# ── 公共辅助 ──────────────────────────────────────────────────────────────

def _migrate_product(storage_root, code, *, subject_name=None, subject_count=None,
                     unconfirmed=False, product_name="测试摆件"):
    product_id = str(uuid4())
    image_path = storage_root / "uploads" / "source.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (720, 960), (48, 116, 92)).save(image_path, format="JPEG")
    fact_card = {
        "商品名称": product_name,
        "整体特征": "绿色陶瓷测试摆件",
        "自然场景": [
            {"场景": "书房桌面", "具体位置": "木桌一角"},
            {"场景": "客厅茶几", "具体位置": "茶几中央"},
        ],
    }
    if subject_name is not None:
        fact_card["主体名称"] = subject_name
    if subject_count is not None:
        fact_card["主体数量"] = subject_count
    if unconfirmed:
        fact_card["主体数量待确认"] = True
    metadata_path = storage_root / "metadata" / f"product-{product_id}.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "product_id": product_id,
                "original_image_path": "uploads/source.jpg",
                "fact_card": fact_card,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    migrate(storage_root)
    add_code(storage_root / "app.db", product_id, code)
    return product_id


def _run_one_task(settings, code):
    now = datetime.now(timezone.utc)
    task_id = uuid4().hex
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", [code], 1,
        now.isoformat(), now.isoformat(),
    )
    assert process_due_tasks(settings, now=now) == 1
    return task_id


def _manifest(settings, task_id):
    return json.loads(
        (settings.storage_root / "dispatch" / task_id / "manifest.json").read_text(encoding="utf-8")
    )


def _check_sequence(sequence):
    """返回一个假 check_consistency，按 sequence 依次返回 consistent。"""
    state = {"i": 0}

    def fake(settings, orig, gen):
        idx = state["i"]
        state["i"] += 1
        ok = sequence[idx] if idx < len(sequence) else sequence[-1]
        return ConsistencyResult(consistent=ok, reasons=[] if ok else ["mock fail"])

    return fake


def _spy_generate():
    calls = []
    real = ds.generate_image

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    return spy, calls


# ── Schema / normalize ────────────────────────────────────────────────────

def test_fact_card_subject_count_fields_roundtrip():
    card = FactCard.model_validate({"商品名称": "五匹马", "主体名称": "马", "主体数量": 5})
    assert card.subject_name == "马"
    assert card.subject_count == 5
    assert card.subject_count_unconfirmed is None
    d = card.model_dump(mode="json", by_alias=True)
    assert d["主体数量"] == 5
    assert d["主体名称"] == "马"


def test_normalize_parses_subject_count_variants():
    assert normalize_fact_card('{"主体名称":"马","主体数量":"5"}')["主体数量"] == 5
    assert normalize_fact_card('{"主体数量":5.0}')["主体数量"] == 5
    assert normalize_fact_card('{"主体数量":"无法确认"}')["主体数量"] is None
    assert normalize_fact_card('{"主体数量":null}')["主体数量"] is None
    assert normalize_fact_card('{"主体数量待确认":"true"}')["主体数量待确认"] is True
    assert normalize_fact_card('{"主体数量待确认":false}')["主体数量待确认"] is False


def test_effective_subject_respects_unconfirmed_flag():
    assert _effective_subject(FactCard.model_validate({"主体名称": "马", "主体数量": 5})) == (5, "马")
    # 待确认视为无数量
    assert _effective_subject(
        FactCard.model_validate({"主体名称": "马", "主体数量": 5, "主体数量待确认": True})
    ) == (None, "马")
    # 无数量
    assert _effective_subject(FactCard.model_validate({})) == (None, "商品主体")


# ── 阶梯降级主流程 ─────────────────────────────────────────────────────────

def test_degraded_retry_disabled_falls_back_to_two_attempts(settings, monkeypatch):
    settings.degraded_retry_enabled = False
    settings.consistency_check_max_retries = 1
    code = "DEG-OFF"
    _migrate_product(settings.storage_root, code)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert r["status"] == "needs_review"
    assert r["rescued_level"] is None
    assert len(r["attempts"]) == 2  # 原行为：1 + 1 retry


def test_second_level_rescues_changing_only_gen_seed(settings, monkeypatch):
    code = "DEG-L2"
    _migrate_product(settings.storage_root, code)
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, True]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert r["status"] == "ready"
    assert r["rescued_level"] == 2
    assert len(r["attempts"]) == 2
    # 第二档：只换生图种子，scene/景别/角度/氛围保持不变
    assert calls[1]["gen_seed"] != calls[0]["gen_seed"]
    assert calls[1]["camera_seed"] == calls[0]["camera_seed"]
    assert calls[1]["realism_seed"] == calls[0]["realism_seed"]
    assert calls[1]["scene_override"].scene == calls[0]["scene_override"].scene
    assert calls[1]["shot_type"] == calls[0]["shot_type"]
    assert calls[1]["count_emphasis"] == ""
    # 角度（camera_pos）输出也保持一致
    assert r["attempts"][1]["camera_pos"] == r["attempts"][0]["camera_pos"]


def test_all_four_levels_fail_into_needs_review(settings, monkeypatch):
    code = "DEG-FAIL"
    _migrate_product(settings.storage_root, code)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert r["status"] == "needs_review"
    assert r["rescued_level"] is None
    assert len(r["attempts"]) == 4
    levels = [a["level"] for a in r["attempts"]]
    assert levels == [1, 2, 3, 4]
    # 第四档保底：中近景 + 极简背景
    a4 = r["attempts"][3]
    assert a4["shot_type"] == "中近景"
    assert a4["scene"] in MINIMAL_SCENE_OPTIONS


def test_third_level_re_randomizes_scene(settings, monkeypatch):
    code = "DEG-L3-SCENE"
    _migrate_product(settings.storage_root, code)
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    _run_one_task(settings, code)
    # 第三档场景应重新抽取（与第二档保持的不同）
    assert calls[2]["scene_override"].scene != calls[1]["scene_override"].scene


# ── 数量强调 ──────────────────────────────────────────────────────────────

def test_count_emphasis_injected_from_level3(settings, monkeypatch):
    code = "DEG-COUNT"
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5)
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, True]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert r["rescued_level"] == 3
    assert calls[0]["count_emphasis"] == ""
    assert calls[1]["count_emphasis"] == ""
    assert "数量必须恰好是5个" in calls[2]["count_emphasis"]
    assert r["attempts"][2]["count_emphasis"] is True
    assert r["attempts"][0]["count_emphasis"] is False


def test_count_emphasis_skipped_when_count_is_none(settings, monkeypatch):
    code = "DEG-NOCOUNT"
    _migrate_product(settings.storage_root, code)  # 无主体数量
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    _run_one_task(settings, code)
    assert all(c["count_emphasis"] == "" for c in calls)


def test_count_emphasis_skipped_when_unconfirmed(settings, monkeypatch):
    code = "DEG-UNC"
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5, unconfirmed=True)
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    _run_one_task(settings, code)
    assert all(c["count_emphasis"] == "" for c in calls)


def test_count_emphasis_independent_switch(settings, monkeypatch):
    settings.degraded_retry_count_emphasis = False
    code = "DEG-EMPOFF"
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5)
    spy, calls = _spy_generate()
    monkeypatch.setattr(ds, "generate_image", spy)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    _run_one_task(settings, code)
    assert all(c["count_emphasis"] == "" for c in calls)


# ── 数量硬校验 ────────────────────────────────────────────────────────────

def test_hard_check_triggers_retry_when_consistency_passes(settings, monkeypatch):
    settings.count_hard_check_enabled = True
    code = "DEG-HARD"
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([True, True, True]))
    hstate = {"i": 0}

    def fake_count(settings, image, name, expected):
        idx = hstate["i"]
        hstate["i"] += 1
        ok = [False, False, True][idx] if idx < 3 else True
        return CountCheckResult(passed=ok, detected_count=6 if not ok else 5, reason="mock")

    monkeypatch.setattr(ds, "check_count", fake_count)
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert r["status"] == "ready"
    assert r["rescued_level"] == 3
    assert r["attempts"][0]["hard_check"] is False
    assert r["attempts"][1]["hard_check"] is False
    assert r["attempts"][2]["hard_check"] is True


def test_hard_check_not_run_when_disabled(settings, monkeypatch):
    code = "DEG-HARDOFF"  # count_hard_check_enabled 默认 False
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, False, False, False]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    assert all(a["hard_check"] is None for a in r["attempts"])


def test_hard_check_module_mock_branch(settings, tmp_path):
    settings.count_hard_check_enabled = True
    wrong = tmp_path / "wrongcount.jpg"
    Image.new("RGB", (100, 100)).save(wrong)
    assert check_count(settings, wrong, "马", 5).passed is False
    ok = tmp_path / "ok.jpg"
    Image.new("RGB", (100, 100)).save(ok)
    assert check_count(settings, ok, "马", 5).passed is True


def test_hard_check_module_disabled(settings, tmp_path):
    settings.count_hard_check_enabled = False
    p = tmp_path / "x.jpg"
    Image.new("RGB", (100, 100)).save(p)
    res = check_count(settings, p, "马", 5)
    assert res.passed is True
    assert "disabled" in res.reason


# ── 可观测性 ──────────────────────────────────────────────────────────────

def test_manifest_records_full_attempt_log(settings, monkeypatch):
    code = "DEG-OBS"
    _migrate_product(settings.storage_root, code, subject_name="马", subject_count=5)
    monkeypatch.setattr(ds, "check_consistency", _check_sequence([False, True]))
    task_id = _run_one_task(settings, code)
    r = _manifest(settings, task_id)["results"][0]
    a = r["attempts"][0]
    # 每次尝试都记录档位与实际使用的参数
    for key in ("level", "scene", "placement", "shot_type", "camera_pos",
                "seed", "consistent", "hard_check", "count_emphasis", "reasons"):
        assert key in a
    assert r["rescued_level"] == 2
