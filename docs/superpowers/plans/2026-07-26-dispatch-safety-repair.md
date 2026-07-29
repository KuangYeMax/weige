# Dispatch Safety Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent empty generated reviews and false WeChat-send success, while making human confirmation the only path to a real `sent` state.

**Architecture:** SQLite owns guarded task-state transitions. The scheduler validates artifacts and records either a local-submission state, a dry-run terminal state, or a retryable failure. The Windows sender is fail-closed on exact session-title verification; the FastAPI route and dispatch page expose the final manual confirmation action.

**Tech Stack:** FastAPI, SQLite, Pydantic Settings, Python stdlib mocks, pytest, Alpine.js.

---

### Task 1: Make dispatch state transitions explicit and guarded

**Files:**
- Modify: `app/services/db.py:278-420`
- Modify: `tests/test_dispatch_send.py:10-121`

- [ ] **Step 1: Write the failing state tests**

```python
def test_local_submission_requires_manual_confirmation(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_awaiting_confirmation(settings.db_path, task_id)
    assert confirm_dispatch_task_sent(settings.db_path, task_id)
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "sent"

def test_only_awaiting_confirmation_can_be_confirmed(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert not confirm_dispatch_task_sent(settings.db_path, task_id)

def test_recovery_returns_orphaned_sending_to_ready(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert recover_sending_dispatch_tasks(settings.db_path) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "ready"
```

- [ ] **Step 2: Run the focused tests and confirm they fail from missing symbols**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py -q`

Expected: FAIL because the three transition functions do not exist.

- [ ] **Step 3: Implement compare-and-set transitions**

```python
def _transition_dispatch_task(db_path: Path, task_id: str, source: str, target: str) -> bool:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE dispatch_tasks SET status = ?, fail_reason = NULL "
            "WHERE task_id = ? AND status = ?",
            (target, task_id, source),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()

def mark_dispatch_task_awaiting_confirmation(db_path: Path, task_id: str) -> bool:
    return _transition_dispatch_task(db_path, task_id, "sending", "awaiting_confirmation")

def confirm_dispatch_task_sent(db_path: Path, task_id: str) -> bool:
    return _transition_dispatch_task(db_path, task_id, "awaiting_confirmation", "sent")

def recover_sending_dispatch_tasks(db_path: Path) -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE dispatch_tasks SET status = 'ready', fail_reason = 'SEND_INTERRUPTED' "
            "WHERE status = 'sending'"
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

Make `mark_dispatch_task_sent` use `awaiting_confirmation` as its source state or replace it with `confirm_dispatch_task_sent`; add a guarded `mark_dispatch_task_dry_run_complete` from `sending` to `dry_run_complete`.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated state-layer change**

```bash
git add app/services/db.py tests/test_dispatch_send.py
git commit -m "feat: guard dispatch send states"
```

### Task 2: Make Windows sender fail closed before local submission

**Files:**
- Modify: `app/services/wechat/sender.py:32-148`
- Modify: `tests/test_wechat_send.py:1-31`

- [ ] **Step 1: Write failing mocked sender tests**

```python
class FakeWin32Gui:
    def __init__(self, title: str):
        self.title = title

    def GetWindowText(self, _hwnd: int) -> str:
        return self.title

def test_input_area_uses_bottom_edge(settings):
    assert _resolve_input_area((100, 200, 1100, 1000), settings) == (850, 960)

def test_verify_chat_session_requires_exact_title(monkeypatch):
    monkeypatch.setattr(sender, "find_wechat_main", lambda: (1, (0, 0, 1, 1)))
    monkeypatch.setitem(sys.modules, "win32gui", FakeWin32Gui("买家A - 微信"))
    with pytest.raises(RuntimeError, match="精确校验失败"):
        sender._verify_chat_session("买家A")

def test_send_rejects_empty_payload(settings, monkeypatch):
    monkeypatch.setattr(sender.sys, "platform", "win32")
    with pytest.raises(ValueError, match="发送载荷不能为空"):
        sender.send("买家A", "", [], settings)
```

Also test blank remarks, a matching exact title, and a `clip_set_image` exception propagating through `send` using mocked win32 functions.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_wechat_send.py -q`

Expected: FAIL for the coordinate result, substring title check, and missing payload validation.

- [ ] **Step 3: Implement the narrow sender changes**

```python
def _resolve_input_area(rect, settings):
    return (
        rect[0] + settings.wechat_input_x_offset,
        rect[3] - settings.wechat_input_y_offset,
    )

def _verify_chat_session(remark: str) -> None:
    title = win32gui.GetWindowText(find_wechat_main()[0]).strip()
    if not remark.strip() or title != remark.strip():
        raise RuntimeError("好友精确校验失败")
```

At `send` entry, reject blank `remark` and `not text.strip() and not images`; retain existing exception propagation and image clipboard API.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_wechat_send.py -q`

Expected: PASS on macOS using mocks only.

- [ ] **Step 5: Commit the sender change**

```bash
git add app/services/wechat/sender.py tests/test_wechat_send.py
git commit -m "fix: fail closed for wechat local submission"
```

### Task 3: Prevent scheduler false success and support confirmation

**Files:**
- Modify: `app/services/dispatch_scheduler.py:220-301`
- Modify: `tests/test_dispatch_send.py:92-121`
- Modify: `tests/test_dispatch_scheduler.py:50-89`

- [ ] **Step 1: Write failing scheduler tests**

```python
def _write_ready_artifacts(settings, task_id: str, code: str, *, content: str = "好评文案"):
    code_dir = settings.storage_root / "dispatch" / task_id / code
    code_dir.mkdir(parents=True)
    (code_dir / "content.txt").write_text(content, encoding="utf-8")
    (code_dir / "image.jpg").write_bytes(b"fake-image")
    (code_dir.parent / "manifest.json").write_text(json.dumps({"results": [{
        "code": code, "status": "ready", "image_path": f"{code}/image.jpg",
        "content_path": f"{code}/content.txt",
    }]}), encoding="utf-8")

def test_non_windows_send_returns_task_to_ready(settings, monkeypatch):
    settings.dispatch_dry_run = False
    monkeypatch.setattr(sender.sys, "platform", "darwin")
    assert process_send_tasks(settings) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "ready"

def test_missing_content_or_image_returns_task_to_ready(settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    (settings.storage_root / "dispatch" / task_id).mkdir(parents=True)
    (settings.storage_root / "dispatch" / task_id / "manifest.json").write_text(
        json.dumps({"results": [{"code": "code1", "status": "ready", "image_path": "code1/image.jpg", "content_path": "code1/content.txt"}]}),
        encoding="utf-8",
    )
    assert process_send_tasks(settings) == 1
    task = list_dispatch_tasks(settings.db_path)[0]
    assert task["status"] == "ready"
    assert task["fail_reason"] == "SEND_ARTIFACT_MISSING"

def test_successful_local_submission_awaits_confirmation(settings, monkeypatch):
    settings.dispatch_dry_run = False
    monkeypatch.setattr("app.services.wechat.sender.send", lambda **_: None)
    assert process_send_tasks(settings) == 1
    assert list_dispatch_tasks(settings.db_path)[0]["status"] == "awaiting_confirmation"
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py tests/test_dispatch_scheduler.py -q`

Expected: FAIL because the scheduler currently writes `sent` for dry-run, non-Windows, missing artifacts, and local completion.

- [ ] **Step 3: Implement artifact and platform guards**

Require every manifest `ready` result to have a nonblank `content_path` and `image_path`, resolve them under its code directory, verify both files exist and text is nonblank. Treat `NotImplementedError` as `SEND_PLATFORM_UNAVAILABLE` through `mark_dispatch_task_send_failed`.

On local sender success call `mark_dispatch_task_awaiting_confirmation`. On `dispatch_dry_run`, call `mark_dispatch_task_dry_run_complete`. At scheduler startup call both recovery functions and log their separate counts.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py tests/test_dispatch_scheduler.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the scheduler change**

```bash
git add app/services/dispatch_scheduler.py tests/test_dispatch_send.py tests/test_dispatch_scheduler.py
git commit -m "fix: avoid false dispatch send success"
```

### Task 4: Guarantee nonempty review content and configuration compatibility

**Files:**
- Modify: `app/config.py:52-87`
- Modify: `app/services/review/generator.py:86-103`
- Modify: `app/services/dispatch_generation.py:224-236`
- Modify: `.env.example:1-32`
- Modify: `tests/test_review.py:21-87`

- [ ] **Step 1: Write failing review/config tests**

```python
def test_forbidden_retries_never_return_empty_fallback(monkeypatch):
    settings = Settings(review_fallback_text="")
    monkeypatch.setattr(generator, "call_ark", lambda *_: "最好")
    assert generator.generate_review(_make_card(), settings).strip()

def test_generate_content_rejects_an_empty_generator_result(monkeypatch, settings):
    monkeypatch.setattr(generator, "generate_review", lambda *_: "   ")
    assert generate_content(_make_card(), {"name": "测试"}, settings).strip()

def test_review_forbidden_words_environment_alias(monkeypatch):
    monkeypatch.setenv("REVIEW_FORBIDDEN_WORDS", '["禁词甲"]')
    assert Settings().review_forbidden_words_hard == ["禁词甲"]
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -q`

Expected: FAIL because empty fallback is returned and the compatibility environment key is ignored.

- [ ] **Step 3: Implement nonempty normalization and aliasing**

Define a module-level nonempty default fallback. Return it whenever configured fallback is blank. Normalize every generated result with `.strip()` and raise `AppError("REVIEW_EMPTY", ...)` only if no safe fallback remains. Add a Pydantic validation alias or settings normalization so `REVIEW_FORBIDDEN_WORDS` configures the hard list while keeping existing hard/soft fields.

Replace “和想象中略有差异” with a packaging, logistics, or service-only phrase. Document all review and WeChat environment variables in `.env.example`, with JSON array syntax for list settings.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_review.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the review/config change**

```bash
git add app/config.py app/services/review/generator.py app/services/dispatch_generation.py .env.example tests/test_review.py
git commit -m "fix: guarantee review content fallback"
```

### Task 5: Expose guarded human confirmation in the dispatch UI

**Files:**
- Modify: `app/api/dispatch.py:18-65`
- Modify: `app/static/dispatch.html:95-128`
- Modify: `app/static/dispatch.js:102-124`
- Modify: `tests/test_dispatch_send.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_confirm_send_only_accepts_awaiting_confirmation(client, settings):
    task_id = uuid4().hex
    _ready_task(settings.db_path, task_id)
    assert client.post(f"/api/dispatch/{task_id}/confirm-sent").status_code == 409
    assert claim_dispatch_task_sending(settings.db_path, task_id)
    assert mark_dispatch_task_awaiting_confirmation(settings.db_path, task_id)
    assert client.post(f"/api/dispatch/{task_id}/confirm-sent").status_code == 200
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py -q`

Expected: FAIL with a 404 because the confirmation route does not exist.

- [ ] **Step 3: Implement the route and explicit UI action**

Add `POST /api/dispatch/{task_id}/confirm-sent`; return 404 for absent tasks and 409 unless the guarded DB transition succeeds. In the task list, render `awaiting_confirmation` as “待人工确认” and show a clearly named confirmation button only for that state. Its click handler posts to the route, reports API errors, and reloads task data. Render `dry_run_complete` as “模拟完成” without a confirmation button.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_dispatch_send.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the API/UI change**

```bash
git add app/api/dispatch.py app/static/dispatch.html app/static/dispatch.js tests/test_dispatch_send.py
git commit -m "feat: require manual dispatch confirmation"
```

### Task 6: Run regression checks and Windows handoff verification

**Files:**
- Modify: `README.md` only if the dispatch workflow documentation currently claims automatic `sent` status.

- [ ] **Step 1: Run focused suites**

Run: `.venv/bin/python -m pytest tests/test_review.py tests/test_wechat_send.py tests/test_dispatch_send.py tests/test_dispatch_scheduler.py -q`

Expected: PASS.

- [ ] **Step 2: Run full regression suite**

Run: `.venv/bin/python -m pytest`

Expected: `150 passed, 4 failed` or more passes with exactly the four named `z-image-turbo` baseline failures and no other failure.

- [ ] **Step 3: Run static and import checks**

Run: `.venv/bin/python -c 'import app.main; import app.services.review.generator; import app.services.wechat.sender; print("imports-ok")'`

Expected: `imports-ok` on macOS.

- [ ] **Step 4: Perform Windows manual acceptance**

Start with a disposable test contact and `dispatch_dry_run=false`. Verify exact-title mismatch aborts before any paste, matching title submits text plus an image bubble, the task becomes `awaiting_confirmation`, and only the explicit confirmation action reaches `sent`. Verify login loss, clipboard failure, missing artifact, and process restart return tasks to safe non-sent states.

- [ ] **Step 5: Commit any documentation-only clarification**

```bash
git add README.md
git commit -m "docs: explain dispatch confirmation workflow"
```
