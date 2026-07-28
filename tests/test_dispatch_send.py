from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.db import (
    claim_dispatch_task_sending,
    create_dispatch_task,
    init_db,
    list_dispatch_tasks,
    list_ready_dispatch_tasks,
    mark_dispatch_task_needs_review,
    mark_dispatch_task_ready,
    mark_dispatch_task_send_failed,
    mark_dispatch_task_sent,
    recover_sending_dispatch_tasks,
    recover_generating_dispatch_tasks,
    retry_dispatch_task_after_review,
)
from app.services.dispatch_scheduler import process_send_tasks, run_dispatch_scheduler
from app.services.wechat.sender import verify_remark as production_verify_remark
from app.services.wechat.uia import ChatVerificationError, ChatVerificationResult, UIAutomationUnavailableError


@pytest.fixture(autouse=True)
def verified_chat_preflight(monkeypatch):
    """Keep scheduler tests focused on the state boundary, not live Windows UIA."""
    monkeypatch.setattr(
        "app.services.wechat.sender.verify_remark",
        lambda remark, route_settings: ChatVerificationResult(remark, 1, remark, remark),
    )


def _ready_task(db_path, task_id: str) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc)
    create_dispatch_task(db_path, task_id, "测试好友", ["code1"], 1, now.isoformat(), now.isoformat())
    import app.services.db as db
    conn = db._connect(db_path)
    conn.execute("UPDATE dispatch_tasks SET status='ready' WHERE task_id=?", (task_id,))
    conn.commit()
    conn.close()


def _ready_task_with_artifacts(settings, task_id: str) -> tuple:
    _ready_task(settings.db_path, task_id)
    task_dir = settings.storage_root / "dispatch" / task_id
    code_dir = task_dir / "code1"
    code_dir.mkdir(parents=True)
    (code_dir / "content.txt").write_text("好评文案", encoding="utf-8")
    (code_dir / "image.jpg").write_bytes(b"image")
    (task_dir / "manifest.json").write_text(
        json.dumps({
            "results": [{
                "code": "code1",
                "status": "ready",
                "content_path": "code1/content.txt",
                "image_path": "code1/image.jpg",
            }],
        }),
        encoding="utf-8",
    )
    return task_dir, code_dir


def _ready_task_with_two_artifacts(settings, task_id: str):
    _ready_task(settings.db_path, task_id)
    task_dir = settings.storage_root / "dispatch" / task_id
    results = []
    for code in ["code1", "code2"]:
        code_dir = task_dir / code
        code_dir.mkdir(parents=True)
        (code_dir / "content.txt").write_text(code, encoding="utf-8")
        (code_dir / "image.jpg").write_bytes(b"image")
        results.append({
            "code": code,
            "status": "ready",
            "content_path": f"{code}/content.txt",
            "image_path": f"{code}/image.jpg",
        })
    (task_dir / "manifest.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return task_dir


def test_claim_sending_transitions_from_ready(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id) is True
    assert claim_dispatch_task_sending(settings.db_path, task_id) is False


def test_claim_sending_rejects_non_ready(settings):
    task_id = uuid4().hex
    init_db(settings.db_path)
    now = datetime.now(timezone.utc)
    create_dispatch_task(settings.db_path, task_id, "测试", ["c"], 1, now.isoformat(), now.isoformat())
    assert claim_dispatch_task_sending(settings.db_path, task_id) is False


def test_mark_sent_requires_sending_task(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_sent(settings.db_path, task_id) is True
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "sent"


def test_mark_sent_rejects_ready_task(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)

    assert mark_dispatch_task_sent(settings.db_path, task_id) is False
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "ready"


def test_needs_review_requires_sending_task_and_records_reason(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)

    assert mark_dispatch_task_needs_review(settings.db_path, task_id, "CHAT_VERIFICATION_FAILED") is False
    assert claim_dispatch_task_sending(settings.db_path, task_id) is True
    assert mark_dispatch_task_needs_review(settings.db_path, task_id, "CHAT_VERIFICATION_FAILED") is True
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "CHAT_VERIFICATION_FAILED"


def test_retry_after_review_requires_needs_review_task(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    import app.services.db as db

    retry_after_review = getattr(db, "retry_dispatch_task_after_review", None)
    assert retry_after_review is not None
    assert retry_after_review(settings.db_path, task_id) is False

    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_needs_review(settings.db_path, task_id, "CHAT_VERIFICATION_FAILED")
    assert retry_after_review(settings.db_path, task_id) is True
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert task["fail_reason"] is None


def test_retry_after_review_route_returns_404_for_missing_task(client):
    response = client.post(f"/api/dispatch/{uuid4().hex}/retry-after-review")

    assert response.status_code == 404


@pytest.mark.parametrize("status", ["pending", "sending", "sent"])
def test_retry_after_review_route_rejects_tasks_not_needing_review(client, settings, status):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    if status != "ready":
        import app.services.db as db
        conn = db._connect(settings.db_path)
        conn.execute("UPDATE dispatch_tasks SET status=? WHERE task_id=?", (status, task_id))
        conn.commit()
        conn.close()

    response = client.post(f"/api/dispatch/{task_id}/retry-after-review")

    assert response.status_code == 409
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == status


def test_retry_after_review_route_marks_needs_review_task_ready(client, settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_needs_review(settings.db_path, task_id, "CHAT_VERIFICATION_FAILED")

    response = client.post(f"/api/dispatch/{task_id}/retry-after-review")

    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["status"] == "ready"





@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (ChatVerificationResult("买家A", 1, "买家A", "买家A"), 200),
        (ChatVerificationError("好友精确校验失败：搜索结果不匹配"), 409),
        (ChatVerificationError("好友精确校验失败：聊天页头不可读"), 409),
        (ChatVerificationError("好友精确校验失败：搜索结果不是唯一精确匹配"), 409),
        (UIAutomationUnavailableError("Windows UIA 不可用"), 503),
        (RuntimeError("微信窗口不可用"), 503),
    ],
    ids=["exact-match", "mismatch", "unreadable", "multiple", "uia-unavailable", "uia-error"],
)
def test_verify_remark_route_is_fail_closed_and_never_creates_or_sends(
    client, settings, monkeypatch, outcome, expected_status
):
    calls = []

    def verify_remark(remark, route_settings):
        calls.append((remark, route_settings))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("app.services.wechat.sender.verify_remark", verify_remark)
    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **_: pytest.fail("remark preflight must not invoke the sender payload"),
    )

    response = client.post("/api/dispatch/verify-remark", json={"wx_remark": "  买家A  "})

    assert response.status_code == expected_status
    assert calls == [("买家A", settings)]
    assert list_dispatch_tasks(settings.db_path) == []
    if expected_status == 200:
        assert response.json() == {"verified": True, "header_name": "买家A"}
    else:
        assert response.json()["error"]["code"] == "REMARK_VERIFICATION_FAILED"


def test_verify_remark_route_rejects_blank_remark_without_uia_call(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.wechat.sender.verify_remark",
        lambda *_: pytest.fail("blank remarks must not reach UIA"),
    )

    response = client.post("/api/dispatch/verify-remark", json={"wx_remark": "   "})

    assert response.status_code == 400


def test_recover_sending_tasks_leaves_non_sending_tasks_untouched(settings):
    sending_task_id = uuid4().hex
    sent_task_id = uuid4().hex
    _ready_task(settings.db_path, sending_task_id)
    _ready_task(settings.db_path, sent_task_id)
    assert claim_dispatch_task_sending(settings.db_path, sending_task_id) is True
    assert claim_dispatch_task_sending(settings.db_path, sent_task_id) is True
    assert mark_dispatch_task_sent(settings.db_path, sent_task_id) is True

    assert recover_sending_dispatch_tasks(settings.db_path) == 1
    tasks = {task["task_id"]: task for task in list_dispatch_tasks(settings.db_path)}
    assert tasks[sending_task_id]["status"] == "ready"
    assert tasks[sending_task_id]["fail_reason"] == "SEND_INTERRUPTED"
    assert tasks[sent_task_id]["status"] == "sent"
    assert tasks[sent_task_id]["fail_reason"] is None


def test_recover_sending_task_with_uncertain_manifest_goes_to_needs_review(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id) is True
    task_dir = settings.storage_root / "dispatch" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "manifest.json").write_text(
        json.dumps({"results": [{"status": "submission_uncertain"}]}),
        encoding="utf-8",
    )

    assert recover_sending_dispatch_tasks(settings.db_path) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ACKNOWLEDGMENT_UNCERTAIN"


def test_mark_send_failed_goes_back_to_ready(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    mark_dispatch_task_send_failed(settings.db_path, task_id, "发送超时")
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert "发送超时" in task["fail_reason"]


def test_list_ready_tasks(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    ready = list_ready_dispatch_tasks(settings.db_path)
    assert len(ready) == 1
    assert ready[0]["task_id"] == task_id
    assert ready[0]["status"] == "ready"


def test_list_ready_excludes_other_statuses(settings):
    init_db(settings.db_path)
    now = datetime.now(timezone.utc)
    t1, t2 = uuid4().hex, uuid4().hex
    for tid in [t1, t2]:
        create_dispatch_task(settings.db_path, tid, "测试", ["c"], 1, now.isoformat(), now.isoformat())
    import app.services.db as db
    conn = db._connect(settings.db_path)
    conn.execute("UPDATE dispatch_tasks SET status='ready' WHERE task_id=?", (t1,))
    conn.commit()
    conn.close()
    ready = list_ready_dispatch_tasks(settings.db_path)
    assert [r["task_id"] for r in ready] == [t1]


def test_process_send_success_marks_sent(settings, monkeypatch):
    task_id = uuid4().hex
    _ready_task_with_artifacts(settings, task_id)
    sends = []

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: sends.append(kwargs),
    )

    assert process_send_tasks(settings) == 1
    assert len(sends) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sent"


def test_process_send_partial_sender_failure_is_not_retried(settings, monkeypatch):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)
    attempts = []

    def text_sent_then_image_failed(**kwargs):
        attempts.append(kwargs["text"])
        raise RuntimeError("image send failed after text")

    monkeypatch.setattr("app.services.wechat.sender.send", text_sent_then_image_failed)

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert attempts == ["好评文案"]
    assert manifest["results"][0]["status"] == "submission_uncertain"
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ACKNOWLEDGMENT_UNCERTAIN"

    assert process_send_tasks(settings) == 0
    assert attempts == ["好评文案"]


def test_process_send_uia_preflight_failure_can_be_reviewed_and_retried(settings, monkeypatch):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)
    verification_calls = []
    payload_calls = []

    def verify_remark(remark, route_settings):
        verification_calls.append((remark, route_settings))
        if len(verification_calls) == 1:
            raise ChatVerificationError("好友精确校验失败：搜索结果不是唯一精确匹配")

    monkeypatch.setattr("app.services.wechat.sender.verify_remark", verify_remark)

    def payload_send(**kwargs):
        manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["results"][0]["status"] == "submission_uncertain"
        payload_calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        payload_send,
    )

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert verification_calls == [("测试好友", settings)]
    assert payload_calls == []
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "CHAT_VERIFICATION_FAILED"
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["status"] == "ready"

    assert process_send_tasks(settings) == 0
    assert retry_dispatch_task_after_review(settings.db_path, task_id) is True
    assert process_send_tasks(settings) == 1
    assert verification_calls == [("测试好友", settings), ("测试好友", settings)]
    assert len(payload_calls) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sent"


def test_process_send_does_not_retry_needs_review_task(settings, monkeypatch):
    task_id = uuid4().hex
    _ready_task_with_artifacts(settings, task_id)
    attempts = []

    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_needs_review(settings.db_path, task_id, "CHAT_VERIFICATION_FAILED")
    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: attempts.append(kwargs["text"]),
    )

    assert process_send_tasks(settings) == 0
    assert attempts == []
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "needs_review"


def test_process_send_crash_after_sender_return_does_not_retry_after_recovery(settings, monkeypatch):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)
    attempts = []

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: attempts.append(kwargs["text"]),
    )
    import app.services.dispatch_scheduler as scheduler
    real_write_json = scheduler._write_json

    def crash_before_recording_success(path, manifest):
        if manifest["results"][0]["status"] == "local_submitted":
            raise KeyboardInterrupt("simulated process crash after sender returned")
        real_write_json(path, manifest)

    monkeypatch.setattr(scheduler, "_write_json", crash_before_recording_success)

    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        process_send_tasks(settings)

    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert attempts == ["好评文案"]
    assert manifest["results"][0]["status"] == "submission_uncertain"
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sending"

    assert recover_sending_dispatch_tasks(settings.db_path) == 1
    assert process_send_tasks(settings) == 0
    assert attempts == ["好评文案"]
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ACKNOWLEDGMENT_UNCERTAIN"


def test_process_send_does_not_retry_uncertain_code_after_recovery(settings, monkeypatch):
    task_id = uuid4().hex
    task_dir = _ready_task_with_two_artifacts(settings, task_id)
    attempts = []

    def fail_second_send(**kwargs):
        attempts.append(kwargs["text"])
        if kwargs["text"] == "code2":
            raise RuntimeError("second send failed")

    monkeypatch.setattr("app.services.wechat.sender.send", fail_second_send)
    assert process_send_tasks(settings) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "needs_review"
    first_manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [result["status"] for result in first_manifest["results"]] == ["local_submitted", "submission_uncertain"]

    assert claim_dispatch_task_sending(settings.db_path, task_id) is False
    assert recover_sending_dispatch_tasks(settings.db_path) == 0
    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: attempts.append(kwargs["text"]),
    )

    assert process_send_tasks(settings) == 0
    assert attempts == ["code1", "--------------", "code2"]
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "needs_review"
    final_manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [result["status"] for result in final_manifest["results"]] == ["local_submitted", "submission_uncertain"]


def test_process_send_emits_opening_then_image_text_with_separator(settings, monkeypatch):
    """开场语在最前、每组为图片+文字、组间用分隔符隔开。"""
    settings.wechat_opening_text = "麻烦你帮我把xxx好评一下"
    task_id = uuid4().hex
    _ready_task_with_two_artifacts(settings, task_id)
    calls = []

    def record_send(**kwargs):
        calls.append({"text": kwargs["text"], "images": list(kwargs["images"])})

    monkeypatch.setattr("app.services.wechat.sender.send", record_send)

    assert process_send_tasks(settings) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sent"

    # 顺序：开场语 → code1(图+文) → 分隔符 → code2(图+文)
    assert [c["text"] for c in calls] == [
        "麻烦你帮我把xxx好评一下",
        "code1",
        "--------------",
        "code2",
    ]
    # 开场语与分隔符不含图片，本组各含一张图片
    assert calls[0]["images"] == []
    assert calls[2]["images"] == []
    assert len(calls[1]["images"]) == 1
    assert len(calls[3]["images"]) == 1


def test_process_send_omits_opening_when_blank(settings, monkeypatch):
    """开场语留空时不发，单组任务只发一次（图+文）。"""
    task_id = uuid4().hex
    _ready_task_with_artifacts(settings, task_id)
    calls = []

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: calls.append(kwargs["text"]),
    )

    assert process_send_tasks(settings) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sent"
    assert calls == ["好评文案"]


def test_process_send_on_non_windows_stays_ready_and_cannot_be_confirmed(settings, monkeypatch):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("app.services.wechat.sender.verify_remark", production_verify_remark)
    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: pytest.fail("non-Windows must not invoke payload sender"),
    )

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert task["fail_reason"] == "SEND_PLATFORM_UNAVAILABLE"
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["status"] == "ready"

    assert mark_dispatch_task_sent(settings.db_path, task_id) is False
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "ready"


def test_process_send_sender_platform_error_reverts_intent_and_cannot_be_confirmed(
    settings, monkeypatch, client
):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: (_ for _ in ()).throw(NotImplementedError("payload unavailable")),
    )

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert task["fail_reason"] == "SEND_PLATFORM_UNAVAILABLE"
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["status"] == "ready"

    assert mark_dispatch_task_sent(settings.db_path, task_id) is False
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "ready"


@pytest.mark.parametrize("artifact_name", ["content.txt", "image.jpg"])
def test_process_send_rejects_missing_artifact_before_sender(settings, monkeypatch, artifact_name):
    task_id = uuid4().hex
    _, code_dir = _ready_task_with_artifacts(settings, task_id)
    (code_dir / artifact_name).unlink()

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: pytest.fail("invalid artifacts must not reach the sender"),
    )

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ARTIFACT_MISSING"


@pytest.mark.parametrize(
    "results",
    [[], [{"code": "code1", "status": "skipped"}]],
    ids=["empty", "no-ready-results"],
)
def test_process_send_rejects_manifest_without_ready_results(settings, results):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)
    (task_dir / "manifest.json").write_text(json.dumps({"results": results}), encoding="utf-8")

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ARTIFACT_MISSING"


def test_process_send_rejects_empty_image_before_sender(settings, monkeypatch):
    task_id = uuid4().hex
    _, code_dir = _ready_task_with_artifacts(settings, task_id)
    (code_dir / "image.jpg").write_bytes(b"")

    monkeypatch.setattr(
        "app.services.wechat.sender.send",
        lambda **kwargs: pytest.fail("invalid artifacts must not reach the sender"),
    )

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "SEND_ARTIFACT_MISSING"


@pytest.mark.parametrize(
    "manifest",
    [[], {"results": {}}],
    ids=["root-array", "results-not-list"],
)
def test_process_send_rejects_invalid_manifest_structure(settings, manifest):
    task_id = uuid4().hex
    task_dir, _ = _ready_task_with_artifacts(settings, task_id)
    (task_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "needs_review"
    assert task["fail_reason"] == "MANIFEST_INVALID"


def test_scheduler_startup_recovers_sending_tasks_only(settings):
    sending_task_id = uuid4().hex
    sent_task_id = uuid4().hex
    _ready_task(settings.db_path, sending_task_id)
    _ready_task(settings.db_path, sent_task_id)
    assert claim_dispatch_task_sending(settings.db_path, sending_task_id)
    assert claim_dispatch_task_sending(settings.db_path, sent_task_id)
    assert mark_dispatch_task_sent(settings.db_path, sent_task_id)
    stopped = asyncio.Event()
    stopped.set()

    asyncio.run(run_dispatch_scheduler(settings, stopped))

    tasks = {task["task_id"]: task for task in list_dispatch_tasks(settings.db_path)}
    assert tasks[sending_task_id]["status"] == "ready"
    assert tasks[sending_task_id]["fail_reason"] == "SEND_INTERRUPTED"
    assert tasks[sent_task_id]["status"] == "sent"


# ─── manifest 不可读 → needs_review（不许自动重跑） ───────────


def test_manifest_missing_goes_to_needs_review(settings, monkeypatch):
    """manifest 文件被删 → needs_review，绝不回 ready。"""
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    task_dir = settings.storage_root / "dispatch" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    assert claim_dispatch_task_sending(settings.db_path, task_id)
    from app.services.dispatch_scheduler import _send_ready_task
    _send_ready_task(settings, {"task_id": task_id, "wx_remark": "测试好友", "send_codes": ["code1"]})

    tasks = {t["task_id"]: t for t in list_dispatch_tasks(settings.db_path)}
    assert tasks[task_id]["status"] == "needs_review"
    assert tasks[task_id]["fail_reason"] == "MANIFEST_MISSING"

    assert process_send_tasks(settings) == 0


def test_manifest_invalid_json_goes_to_needs_review(settings, monkeypatch):
    """manifest 写成半截 JSON → needs_review，绝不回 ready。"""
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    task_dir = settings.storage_root / "dispatch" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "manifest.json").write_text('{"results": [', encoding="utf-8")

    assert claim_dispatch_task_sending(settings.db_path, task_id)
    from app.services.dispatch_scheduler import _send_ready_task
    _send_ready_task(settings, {"task_id": task_id, "wx_remark": "测试好友", "send_codes": ["code1"]})

    tasks = {t["task_id"]: t for t in list_dispatch_tasks(settings.db_path)}
    assert tasks[task_id]["status"] == "needs_review"
    assert tasks[task_id]["fail_reason"] == "MANIFEST_INVALID"

    assert process_send_tasks(settings) == 0


# ─── 剪贴板回读失败 → needs_review，manifest 保留 submission_uncertain ───────


def test_clipboard_readback_failure_goes_to_needs_review(settings, monkeypatch):
    """clip_set_image 回读失败 → needs_review，manifest 保留 submission_uncertain（不降级）。"""
    from app.services.wechat.win32 import ClipboardVerificationError

    task_id = uuid4().hex
    task_dir, code_dir = _ready_task_with_artifacts(settings, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)

    def fake_send(remark, text, images, settings):
        raise ClipboardVerificationError("剪贴板回读校验失败，图片未写入: /fake.jpg")

    monkeypatch.setattr("app.services.wechat.sender.send", fake_send)

    from app.services.dispatch_scheduler import _send_ready_task
    _send_ready_task(settings, {"task_id": task_id, "wx_remark": "测试好友", "send_codes": ["code1"]})

    tasks = {t["task_id"]: t for t in list_dispatch_tasks(settings.db_path)}
    assert tasks[task_id]["status"] == "needs_review"
    assert tasks[task_id]["fail_reason"] == "CLIPBOARD_VERIFICATION_FAILED"

    # manifest 必须保留 submission_uncertain（不可降级为 ready）
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["status"] == "submission_uncertain"

    assert process_send_tasks(settings) == 0


def test_clipboard_readback_crash_recovery_goes_to_needs_review(settings, monkeypatch):
    """模拟 kill -9 场景：clipboard 失败后如果 DB 写入前崩溃，
    恢复逻辑读到 submission_uncertain 的 manifest → needs_review。"""
    task_id = uuid4().hex
    task_dir, code_dir = _ready_task_with_artifacts(settings, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)

    # 模拟发送过程中写入了 submission_uncertain 到 manifest（line 350-352 的效果）
    manifest_path = task_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["results"][0]["status"] = "submission_uncertain"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    # DB 仍然是 sending（模拟 mark_dispatch_task_needs_review 执行前 kill -9）
    tasks = {t["task_id"]: t for t in list_dispatch_tasks(settings.db_path)}
    assert tasks[task_id]["status"] == "sending"

    # 恢复逻辑
    recovered = recover_sending_dispatch_tasks(settings.db_path)
    assert recovered == 1

    tasks = {t["task_id"]: t for t in list_dispatch_tasks(settings.db_path)}
    assert tasks[task_id]["status"] == "needs_review"
    assert tasks[task_id]["fail_reason"] == "SEND_ACKNOWLEDGMENT_UNCERTAIN"

