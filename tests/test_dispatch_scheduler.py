from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from PIL import Image

from app.services.db import (
    add_code,
    claim_dispatch_task,
    create_dispatch_task,
    init_db,
    list_dispatch_tasks,
    recover_generating_dispatch_tasks,
)
from app.services.dispatch_scheduler import process_due_tasks
from migrate_products import migrate


def _migrate_product(storage_root, code: str) -> str:
    product_id = str(uuid4())
    image_path = storage_root / "uploads" / "source.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (720, 960), (48, 116, 92)).save(image_path, format="JPEG")

    metadata_path = storage_root / "metadata" / f"product-{product_id}.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "product_id": product_id,
                "original_image_path": "uploads/source.jpg",
                "fact_card": {
                    "商品名称": "测试摆件",
                    "整体特征": "绿色陶瓷测试摆件",
                    "自然场景": [{"场景": "书房桌面", "具体位置": "木桌一角"}],
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    migrate(storage_root)
    add_code(storage_root / "app.db", product_id, code)
    return product_id


def test_due_task_generates_mock_artifacts_and_manifest(settings):
    code = "DISPATCH-001"
    _migrate_product(settings.storage_root, code)
    now = datetime.now(timezone.utc)
    task_id = uuid4().hex
    create_dispatch_task(
        settings.db_path,
        task_id=task_id,
        wx_remark="测试好友",
        return_code="R-1",
        send_codes=[code],
        countdown_days=1,
        created_at=(now - timedelta(days=1)).isoformat(),
        trigger_at=(now - timedelta(seconds=1)).isoformat(),
    )

    assert process_due_tasks(settings, now=now) == 1

    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert task["fail_reason"] is None

    code_dir = settings.storage_root / "dispatch" / task_id / code
    assert list(code_dir.glob("image.*"))
    content = (code_dir / "content.txt").read_text(encoding="utf-8")
    assert content.strip()
    assert len(content) >= 2

    manifest = json.loads((settings.storage_root / "dispatch" / task_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "mock"
    assert manifest["model"] == "pillow-scene-preview"
    assert manifest["results"][0]["code"] == code
    assert manifest["results"][0]["status"] == "ready"
    assert manifest["results"][0]["provider"] == "mock"
    assert manifest["results"][0]["model"] == "pillow-scene-preview"
    assert manifest["results"][0]["image_path"] == f"{code}/image.jpg"
    assert manifest["results"][0]["content_path"] == f"{code}/content.txt"
    assert manifest["results"][0]["content_source"] in ("ai", "fallback")


def test_only_one_worker_can_claim_a_pending_task(settings):
    init_db(settings.db_path)
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", "", ["unused"], 1,
        now.isoformat(), now.isoformat(),
    )

    assert claim_dispatch_task(settings.db_path, task_id, now.isoformat()) is True
    assert claim_dispatch_task(settings.db_path, task_id, now.isoformat()) is False


def test_startup_recovery_returns_orphaned_generation_to_pending(settings):
    init_db(settings.db_path)
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", "", ["unused"], 1,
        now.isoformat(), now.isoformat(),
    )
    assert claim_dispatch_task(settings.db_path, task_id, now.isoformat())

    assert recover_generating_dispatch_tasks(settings.db_path) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "pending"
    assert task["generation_started_at"] is None


def test_failed_generation_removes_stale_final_and_staging_directories(settings, caplog):
    code = "DISPATCH-BROKEN"
    _migrate_product(settings.storage_root, code)
    (settings.storage_root / "uploads" / "source.jpg").unlink()
    now = datetime.now(timezone.utc)
    task_id = uuid4().hex
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", "", [code], 1,
        now.isoformat(), now.isoformat(),
    )
    dispatch_root = settings.storage_root / "dispatch"
    final_dir = dispatch_root / task_id
    stale_dir = dispatch_root / f".{task_id}.tmp"
    final_dir.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    (final_dir / "old.txt").write_text("old", encoding="utf-8")
    (stale_dir / "old.txt").write_text("old", encoding="utf-8")

    assert process_due_tasks(settings, now=now) == 1

    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "failed"
    assert task["fail_reason"] == "PRODUCT_IMAGE_MISSING"
    assert not final_dir.exists()
    assert not stale_dir.exists()
    assert '"provider": "mock"' in caplog.text
    assert '"code": "DISPATCH-BROKEN"' in caplog.text
    assert '"status": "failed"' in caplog.text


def test_dispatch_defaults_to_mock_even_when_workbench_uses_a_real_provider(settings):
    settings.image_provider = "volcengine"
    code = "DISPATCH-MOCK-DEFAULT"
    _migrate_product(settings.storage_root, code)
    now = datetime.now(timezone.utc)
    task_id = uuid4().hex
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", "", [code], 1,
        now.isoformat(), now.isoformat(),
    )

    assert process_due_tasks(settings, now=now) == 1

    manifest = json.loads((settings.storage_root / "dispatch" / task_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "mock"
    assert manifest["model"] == "pillow-scene-preview"


def test_reserved_code_is_encoded_for_its_dispatch_directory(settings):
    code = "manifest.json"
    _migrate_product(settings.storage_root, code)
    now = datetime.now(timezone.utc)
    task_id = uuid4().hex
    create_dispatch_task(
        settings.db_path, task_id, "测试好友", "", [code], 1,
        now.isoformat(), now.isoformat(),
    )

    assert process_due_tasks(settings, now=now) == 1

    task_dir = settings.storage_root / "dispatch" / task_id
    assert (task_dir / "manifest%2Ejson" / "content.txt").is_file()
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["code"] == code
    assert manifest["results"][0]["image_path"].startswith("manifest%2Ejson/")
