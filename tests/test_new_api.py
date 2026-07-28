"""Tests for new API endpoints: settings, wechat/status, product detail,
dispatch detail, abandon."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.db import (
    create_dispatch_task,
    init_db,
    mark_dispatch_task_ready,
    claim_dispatch_task,
)


@pytest.fixture
def pending_task(settings):
    db = settings.db_path
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    create_dispatch_task(
        db_path=db, task_id=task_id, wx_remark="测试好友",
        send_codes=["CODE1"],
        countdown_days=3, created_at=now.isoformat(),
        trigger_at=(now + timedelta(days=3)).isoformat(),
    )
    return task_id


@pytest.fixture
def ready_task(settings, pending_task):
    db = settings.db_path
    claim_dispatch_task(db, pending_task, datetime.now(timezone.utc).isoformat())
    mark_dispatch_task_ready(db, pending_task)
    task_dir = settings.storage_root / "dispatch" / pending_task
    task_dir.mkdir(parents=True)
    manifest = {
        "task_id": pending_task, "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "mock", "model": "mock",
        "results": [{"code": "CODE1", "status": "ready", "provider": "mock", "model": "mock",
                     "image_path": "CODE1/image.jpg", "content_path": "CODE1/content.txt",
                     "content_source": "ai"}],
    }
    (task_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code_dir = task_dir / "CODE1"
    code_dir.mkdir()
    (code_dir / "image.jpg").write_bytes(b"\xff\xd8\xff")
    (code_dir / "content.txt").write_text("测试文案", encoding="utf-8")
    return pending_task


class TestSettings:
    def test_get_settings(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "dispatch_image_provider" in data
        assert "ark_api_key_configured" in data
        assert "ark_api_key" not in data

    def test_put_settings(self, client):
        r = client.put("/api/settings", json={"random_interval_min": 2.0, "random_interval_max": 5.0})
        assert r.status_code == 200
        assert r.json()["wechat_send_interval_min"] == 2.0


class TestWechatStatus:
    def test_returns_platform_unsupported_on_macos(self, client, monkeypatch):
        import app.api.wechat as wechat_route
        monkeypatch.setattr(wechat_route.sys, "platform", "darwin")
        r = client.get("/api/wechat/status")
        assert r.status_code == 200
        data = r.json()
        assert data["platform_supported"] is False
        assert data["connected"] is None


class TestProductDetail:
    def test_get_product_detail(self, client, uploaded_product):
        pid = uploaded_product["product_id"]
        r = client.get(f"/api/products/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["product_id"] == pid
        assert "fact_card" in data
        assert "codes" in data

    def test_product_not_found(self, client):
        r = client.get("/api/products/nonexistent123")
        assert r.status_code == 404


class TestDispatchDetail:
    def test_get_dispatch_detail(self, client, pending_task):
        r = client.get(f"/api/dispatch/{pending_task}")
        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == pending_task
        assert "manifest" in data

    def test_dispatch_not_found(self, client):
        r = client.get("/api/dispatch/nonexistent")
        assert r.status_code == 404


class TestAbandon:
    def test_abandon_needs_review(self, client, settings, ready_task):
        from app.services.db import claim_dispatch_task_sending, mark_dispatch_task_needs_review
        claim_dispatch_task_sending(settings.db_path, ready_task)
        mark_dispatch_task_needs_review(settings.db_path, ready_task, "TEST")
        r = client.post(f"/api/dispatch/{ready_task}/abandon")
        assert r.status_code == 200
        assert r.json()["status"] == "abandoned"

    def test_abandon_rejected_for_awaiting_confirmation(self, client, settings, ready_task):
        from app.services.db import claim_dispatch_task_sending, mark_dispatch_task_awaiting_confirmation
        claim_dispatch_task_sending(settings.db_path, ready_task)
        mark_dispatch_task_awaiting_confirmation(settings.db_path, ready_task)
        r = client.post(f"/api/dispatch/{ready_task}/abandon")
        assert r.status_code == 409
