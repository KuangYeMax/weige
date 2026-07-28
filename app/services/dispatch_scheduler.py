"""Due dispatch task processing and FastAPI scheduler loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.errors import AppError
from app.schemas import FactCard, Scene
from app.services.db import (
    claim_dispatch_task,
    claim_dispatch_task_sending,
    list_due_pending_dispatch_tasks,
    list_ready_dispatch_tasks,
    lookup_product_by_code,
    mark_dispatch_task_failed,
    mark_dispatch_task_sent,
    mark_dispatch_task_needs_review,
    mark_dispatch_task_ready,
    mark_dispatch_task_send_failed,
    recover_generating_dispatch_tasks,
    recover_sending_dispatch_tasks,
)
from app.services.dispatch_generation import generate_content, generate_image
from app.services.consistency_check import check_consistency
from app.services.wechat.uia import ChatVerificationError, UIAutomationUnavailableError
from app.services.wechat.win32 import ClipboardVerificationError


logger = logging.getLogger(__name__)

SUBMISSION_UNCERTAIN = "submission_uncertain"

# 多组图片与好评文案之间的分隔符，发给客户以区分不同商品
SEPARATOR = "--------------"


def _safe_child(parent: Path, name: str) -> Path:
    candidate = (parent / name).resolve()
    if candidate.parent != parent.resolve():
        raise ValueError("任务编号不能作为安全目录名")
    return candidate


def _code_directory_name(code: str) -> str:
    # Preserve normal SKU names while making arbitrary existing codes safe on disk.
    return quote(code, safe="-_").replace(".", "%2E")


def _remove_directory(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != root.resolve():
        raise ValueError("拒绝清理 storage/dispatch 以外的目录")
    if resolved.exists():
        shutil.rmtree(resolved)


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def _load_fact_card(settings: Settings, product: dict[str, str]) -> FactCard:
    root = settings.storage_root.resolve()
    fact_card_path = (root / product["fact_card_path"]).resolve()
    if not fact_card_path.is_relative_to(root) or not fact_card_path.is_file():
        raise AppError("FACT_CARD_MISSING", "产品事实卡无法读取", 500)
    try:
        metadata = json.loads(fact_card_path.read_text(encoding="utf-8"))
        return FactCard.model_validate(metadata["fact_card"])
    except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("FACT_CARD_INVALID", "产品事实卡无法读取", 500) from exc


def _reference_path(settings: Settings, product: dict[str, str]) -> Path:
    root = settings.storage_root.resolve()
    image_path = (root / product["image_path"]).resolve()
    if not image_path.is_relative_to(root) or not image_path.is_file():
        raise AppError("PRODUCT_IMAGE_MISSING", "产品原图无法读取", 500)
    return image_path


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.code
    if isinstance(exc, ValueError):
        return "DISPATCH_PATH_INVALID"
    return "GENERATION_FAILED"


def _configured_model(settings: Settings) -> str:
    if settings.dispatch_image_provider == "volcengine":
        return settings.dispatch_image_model or settings.volc_image_model or settings.ark_image_model
    if settings.dispatch_image_provider == "bailian":
        return settings.dispatch_image_model or settings.bailian_image_model
    return settings.dispatch_image_model or "unknown"


class SceneSampler:
    """No-replacement scene sampler for a single dispatch task.

    Draws from the scene pool without replacement. When exhausted,
    reshuffles and ensures the new round's first != last round's last.
    """

    def __init__(self, scenes: list[Scene], rng: random.Random):
        self._scenes = scenes if scenes else [Scene(scene="通用场景", placement="自然摆放")]
        self._rng = rng
        self._remaining: list[int] = []
        self._last_index: int | None = None
        self._refill()

    def _refill(self) -> None:
        indices = list(range(len(self._scenes)))
        self._rng.shuffle(indices)
        if self._last_index is not None and indices and indices[0] == self._last_index:
            if len(indices) > 1:
                swap_idx = self._rng.randint(1, len(indices) - 1)
                indices[0], indices[swap_idx] = indices[swap_idx], indices[0]
        self._remaining = indices

    def draw(self) -> Scene:
        if not self._remaining:
            self._refill()
        idx = self._remaining.pop(0)
        self._last_index = idx
        return self._scenes[idx]


def _process_claimed_task(settings: Settings, task: dict) -> None:
    dispatch_root = settings.storage_root / "dispatch"
    dispatch_root.mkdir(parents=True, exist_ok=True)
    task_id = task["task_id"]
    task_provider = settings.dispatch_image_provider
    task_model = _configured_model(settings)
    results: list[dict] = []
    active_code: str | None = None
    staging_dir: Path | None = None
    try:
        final_dir = _safe_child(dispatch_root, task_id)
        staging_dir = _safe_child(dispatch_root, f".{task_id}.tmp")
        _remove_directory(staging_dir, dispatch_root)
        _remove_directory(final_dir, dispatch_root)
        staging_dir.mkdir(parents=True, exist_ok=False)

        task_rng = random.Random(task_id)

        for task_index, code in enumerate(task["send_codes"]):
            active_code = code
            product = lookup_product_by_code(settings.db_path, code)
            if product is None:
                raise AppError("CODE_NOT_FOUND", f"发送编号未入库: {code}", 500)
            directory_name = _code_directory_name(code)
            code_dir = _safe_child(staging_dir, directory_name)
            code_dir.mkdir(parents=True, exist_ok=False)
            fact_card = _load_fact_card(settings, product)

            scene_sampler = SceneSampler(fact_card.scenes or [], task_rng)
            scene = scene_sampler.draw()
            shot_type = task_rng.choice(["中近景", "细节照"])
            ref_path = _reference_path(settings, product)

            # Generate with consistency check + retry
            max_attempts = 1 + settings.consistency_check_max_retries
            generated = None
            consistency_passed = False
            fail_reasons: list[str] = []
            for attempt in range(max_attempts):
                if attempt > 0:
                    scene = scene_sampler.draw()
                    shot_type = task_rng.choice(["中近景", "细节照"])
                generated = generate_image(
                    settings,
                    reference_path=ref_path,
                    fact_card=fact_card,
                    shot_type=shot_type,
                    scene_index=0,
                    scene_override=scene,
                    aspect_ratio="3:4",
                    provider_name=settings.dispatch_image_provider,
                    model_id=settings.dispatch_image_model,
                    output_dir=code_dir,
                )
                check_image = generated.graded_path or generated.output_path
                check_result = check_consistency(settings, ref_path, check_image)
                if check_result.consistent:
                    consistency_passed = True
                    break
                fail_reasons = check_result.reasons
                logger.warning(
                    "dispatch task=%s code=%s consistency failed attempt=%d reasons=%s",
                    task_id, code, attempt + 1, fail_reasons,
                )
                # Clean up failed attempt outputs for retry
                if attempt < max_attempts - 1:
                    if generated.graded_path:
                        generated.graded_path.unlink(missing_ok=True)
                    generated.output_path.unlink(missing_ok=True)

            if not consistency_passed:
                selected_image = generated.graded_path or generated.output_path
                image_path = code_dir / f"image{selected_image.suffix.lower() or '.jpg'}"
                os.rename(selected_image, image_path)
                if generated.output_path != selected_image:
                    generated.output_path.unlink(missing_ok=True)
                content_result = generate_content(
                    fact_card, product, settings,
                    task_id=task_id, task_index=task_index, siblings_dir=staging_dir,
                )
                (code_dir / "content.txt").write_text(content_result.text, encoding="utf-8")
                results.append({
                    "code": code,
                    "status": "needs_review",
                    "provider": generated.provider,
                    "model": generated.model,
                    "image_path": f"{_code_directory_name(code)}/{image_path.name}",
                    "content_path": f"{_code_directory_name(code)}/content.txt",
                    "content_source": content_result.status,
                    "reason": f"CONSISTENCY_FAILED: {'; '.join(fail_reasons)}",
                })
                active_code = None
                continue

            selected_image = generated.graded_path or generated.output_path
            image_path = code_dir / f"image{selected_image.suffix.lower() or '.jpg'}"
            os.rename(selected_image, image_path)
            if generated.output_path != selected_image:
                generated.output_path.unlink(missing_ok=True)
            content_result = generate_content(
                fact_card, product, settings,
                task_id=task_id, task_index=task_index, siblings_dir=staging_dir,
            )
            if content_result.status == "needs_review":
                # Dedup failed — write the text anyway (for human editing),
                # mark task needs_review, preserve all artifacts.
                (code_dir / "content.txt").write_text(content_result.text, encoding="utf-8")
                results.append({
                    "code": code,
                    "status": "needs_review",
                    "provider": generated.provider,
                    "model": generated.model,
                    "image_path": f"{directory_name}/{image_path.name}",
                    "content_path": f"{directory_name}/content.txt",
                    "content_source": content_result.status,
                    "content_meta": {
                        "opening": content_result.opening,
                        "skeleton": content_result.skeleton,
                        "length_tier": content_result.length_tier,
                        "has_minor_flaw": content_result.has_minor_flaw,
                        "review_model": content_result.model,
                    },
                })
                _write_json(staging_dir / "manifest.json", {
                    "task_id": task_id,
                    "status": "needs_review",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "provider": task_provider,
                    "model": task_model,
                    "results": results,
                    "needs_review_reason": "CONTENT_DEDUP_FAILED",
                })
                os.rename(staging_dir, final_dir)
                mark_dispatch_task_needs_review(
                    settings.db_path, task_id, "CONTENT_DEDUP_FAILED"
                )
                logger.warning(
                    "dispatch task=%s code=%s content dedup failed, marking needs_review",
                    task_id, code,
                )
                return
            (code_dir / "content.txt").write_text(content_result.text, encoding="utf-8")
            task_provider = generated.provider
            task_model = generated.model
            results.append({
                "code": code,
                "status": "ready",
                "provider": generated.provider,
                "model": generated.model,
                "image_path": f"{directory_name}/{image_path.name}",
                "content_path": f"{directory_name}/content.txt",
                "content_source": content_result.status,
                "content_meta": {
                    "opening": content_result.opening,
                    "skeleton": content_result.skeleton,
                    "length_tier": content_result.length_tier,
                    "has_minor_flaw": content_result.has_minor_flaw,
                    "review_model": content_result.model,
                },
            })
            active_code = None
            logger.info(
                "dispatch task=%s code=%s generated provider=%s model=%s",
                task_id,
                code,
                generated.provider,
                generated.model,
            )

        has_needs_review = any(r.get("status") == "needs_review" for r in results)
        final_status = "needs_review" if has_needs_review else "ready"
        _write_json(staging_dir / "manifest.json", {
            "task_id": task_id,
            "status": final_status,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": task_provider,
            "model": task_model,
            "results": results,
        })
        os.rename(staging_dir, final_dir)
        if has_needs_review:
            review_reasons = [r["reason"] for r in results if r.get("status") == "needs_review"]
            mark_dispatch_task_needs_review(
                settings.db_path, task_id,
                "; ".join(review_reasons),
            )
            logger.info("dispatch task=%s needs_review provider=%s model=%s", task_id, task_provider, task_model)
        else:
            mark_dispatch_task_ready(settings.db_path, task_id)
            logger.info("dispatch task=%s ready provider=%s model=%s", task_id, task_provider, task_model)
    except Exception as exc:
        reason = _error_reason(exc)
        if active_code is not None:
            results.append({
                "code": active_code,
                "status": "failed",
                "provider": task_provider,
                "model": task_model,
                "reason": reason,
            })
        if staging_dir is not None:
            try:
                _remove_directory(staging_dir, dispatch_root)
            except Exception:
                logger.exception("dispatch task=%s staging cleanup failed", task_id)
        mark_dispatch_task_failed(settings.db_path, task_id, reason)
        logger.exception(
            "dispatch task=%s failed audit=%s",
            task_id,
            json.dumps({
                "reason": reason,
                "provider": task_provider,
                "model": task_model,
                "results": results,
            }, ensure_ascii=False),
        )


def process_due_tasks(settings: Settings, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    processed = 0
    for task in list_due_pending_dispatch_tasks(settings.db_path, current):
        if not claim_dispatch_task(settings.db_path, task["task_id"], current.isoformat()):
            continue
        _process_claimed_task(settings, task)
        processed += 1
    return processed


# ─── 发送步骤（ready → sending → sent / needs_review） ────────────


def _resolve_sender(settings: Settings):
    """Return (verify_remark, send) callables based on settings.test_wechat_sender_override."""
    override = settings.test_wechat_sender_override
    if override == "real":
        from app.services.wechat.sender import send, verify_remark
        return verify_remark, send
    from app.services.wechat.failing_sender import create_failing_sender
    failure_type = override.removeprefix("failing:")
    return create_failing_sender(failure_type)


def _ready_send_items(task_dir: Path, manifest: dict) -> list[tuple[dict, str, str, Path]] | None:
    results = manifest.get("results")
    if not isinstance(results, list):
        return None

    items: list[tuple[dict, str, str, Path]] = []
    has_local_submissions = False
    for result in results:
        if not isinstance(result, dict):
            return None
        if result.get("status") == "local_submitted":
            has_local_submissions = True
            continue
        if result.get("status") != "ready":
            continue

        code = result.get("code")
        content_rel = result.get("content_path")
        image_rel = result.get("image_path")
        if not isinstance(code, str) or not code.strip():
            return None
        if not isinstance(content_rel, str) or not content_rel.strip():
            return None
        if not isinstance(image_rel, str) or not image_rel.strip():
            return None

        code_dir = _safe_child(task_dir, _code_directory_name(code))
        content_path = (task_dir / content_rel).resolve()
        image_path = (task_dir / image_rel).resolve()
        if not content_path.is_relative_to(code_dir) or not image_path.is_relative_to(code_dir):
            return None
        if not content_path.is_file() or not image_path.is_file():
            return None
        try:
            text = content_path.read_text(encoding="utf-8")
            image_size = image_path.stat().st_size
        except (OSError, UnicodeDecodeError):
            return None
        if not text.strip() or image_size <= 0:
            return None
        items.append((result, code, text, image_path))
    return items or ([] if has_local_submissions else None)


def _has_submission_uncertainty(manifest: dict) -> bool:
    results = manifest.get("results")
    return isinstance(results, list) and any(
        isinstance(result, dict) and result.get("status") == SUBMISSION_UNCERTAIN
        for result in results
    )


def _send_ready_task(settings: Settings, task: dict) -> None:
    """发送 ready 任务；成功标记 sent，异常标记 needs_review。"""
    task_id = task["task_id"]
    dispatch_root = settings.storage_root / "dispatch"
    task_dir = _safe_child(dispatch_root, task_id)

    manifest_path = task_dir / "manifest.json"
    if not manifest_path.is_file():
        mark_dispatch_task_needs_review(settings.db_path, task_id, "MANIFEST_MISSING")
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mark_dispatch_task_needs_review(settings.db_path, task_id, "MANIFEST_INVALID")
        return
    if not isinstance(manifest, dict) or not isinstance(manifest.get("results"), list):
        mark_dispatch_task_needs_review(settings.db_path, task_id, "MANIFEST_INVALID")
        return

    # A persisted intent means the external sender may already have acted.
    # It is never safe for the scheduler to retry this task automatically.
    if _has_submission_uncertainty(manifest):
        mark_dispatch_task_needs_review(
            settings.db_path, task_id, "SEND_ACKNOWLEDGMENT_UNCERTAIN"
        )
        return

    send_items = _ready_send_items(task_dir, manifest)
    if send_items is None:
        mark_dispatch_task_needs_review(settings.db_path, task_id, "SEND_ARTIFACT_MISSING")
        return

    if not send_items:
        mark_dispatch_task_sent(settings.db_path, task_id)
        return

    for idx, (result, code, text, image_path) in enumerate(send_items):
        _verify, _send = _resolve_sender(settings)

        try:
            _verify(task["wx_remark"], settings)
        except ChatVerificationError:
            logger.warning("wechat chat verification failed task=%s code=%s", task_id, code)
            mark_dispatch_task_needs_review(
                settings.db_path, task_id, "CHAT_VERIFICATION_FAILED"
            )
            return
        except UIAutomationUnavailableError:
            logger.warning("uia unavailable task=%s code=%s", task_id, code)
            mark_dispatch_task_needs_review(
                settings.db_path, task_id, "UIA_UNAVAILABLE"
            )
            return
        except NotImplementedError:
            logger.warning("wechat send unavailable on this platform (expected on macOS)")
            mark_dispatch_task_send_failed(settings.db_path, task_id, "SEND_PLATFORM_UNAVAILABLE")
            return
        except Exception:
            # TODO: 到 Windows 撞到实际异常后按真实情况拆分为精确分支：
            # - ConnectionError / TimeoutError → VERIFY_CONNECTION_FAILED
            # - OSError / PermissionError → VERIFY_SYSTEM_ERROR
            # - 第三方库(pywinauto/uiautomation)自定义异常 → VERIFY_AUTOMATION_ERROR
            # - RuntimeError（非 UIAutomationUnavailableError）→ VERIFY_INTERNAL_ERROR
            logger.exception("校验过程出现预料外错误 task=%s code=%s", task_id, code)
            mark_dispatch_task_needs_review(
                settings.db_path, task_id, "VERIFY_UNEXPECTED_ERROR"
            )
            return

        result["status"] = SUBMISSION_UNCERTAIN
        try:
            _write_json(manifest_path, manifest)
        except Exception:
            logger.exception("could not record send intent task=%s code=%s", task_id, code)
            mark_dispatch_task_needs_review(settings.db_path, task_id, "SEND_INTENT_PERSIST_FAILED")
            return

        try:
            # 前缀消息：第一组前发开场语（若配置），后续组前发分隔符
            if idx == 0:
                opening_text = (settings.wechat_opening_text or "").strip()
                if opening_text:
                    _send(
                        remark=task["wx_remark"],
                        text=opening_text,
                        images=[],
                        settings=settings,
                    )
            else:
                _send(
                    remark=task["wx_remark"],
                    text=SEPARATOR,
                    images=[],
                    settings=settings,
                )
            _send(
                remark=task["wx_remark"],
                text=text,
                images=[str(image_path)],
                settings=settings,
            )
            result["status"] = "local_submitted"
            _write_json(manifest_path, manifest)
            logger.info("sent task=%s code=%s", task_id, code)
        except NotImplementedError:
            logger.warning("wechat send unavailable on this platform (expected on macOS)")
            result["status"] = "ready"
            try:
                _write_json(manifest_path, manifest)
            except Exception:
                logger.exception("could not clear platform-unavailable intent task=%s code=%s", task_id, code)
            mark_dispatch_task_send_failed(settings.db_path, task_id, "SEND_PLATFORM_UNAVAILABLE")
            return
        except ClipboardVerificationError:
            logger.warning(
                "clipboard readback failed task=%s code=%s — text may already be sent, image not pasted",
                task_id, code,
            )
            mark_dispatch_task_needs_review(settings.db_path, task_id, "CLIPBOARD_VERIFICATION_FAILED")
            return
        except Exception as exc:
            logger.exception("send failed task=%s code=%s", task_id, code)
            mark_dispatch_task_needs_review(
                settings.db_path, task_id, "SEND_ACKNOWLEDGMENT_UNCERTAIN"
            )
            return

    mark_dispatch_task_sent(settings.db_path, task_id)


def process_send_tasks(settings: Settings, *, only_task_id: str | None = None) -> int:
    processed = 0
    for task in list_ready_dispatch_tasks(settings.db_path, datetime.now(timezone.utc)):
        if only_task_id is not None and task["task_id"] != only_task_id:
            continue
        if not claim_dispatch_task_sending(settings.db_path, task["task_id"]):
            continue
        _send_ready_task(settings, task)
        processed += 1
    return processed


async def run_dispatch_scheduler(settings: Settings, stop_event: asyncio.Event) -> None:
    recovered = recover_generating_dispatch_tasks(settings.db_path)
    if recovered:
        logger.info("dispatch scheduler restored %s orphaned generating task(s)", recovered)
    recovered_sending = recover_sending_dispatch_tasks(settings.db_path)
    if recovered_sending:
        logger.info("dispatch scheduler restored %s orphaned sending task(s)", recovered_sending)
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        await loop.run_in_executor(None, process_due_tasks, settings)
        await loop.run_in_executor(None, process_send_tasks, settings)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.dispatch_poll_seconds)
        except TimeoutError:
            pass
