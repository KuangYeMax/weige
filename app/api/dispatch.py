from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.schemas import DispatchRemarkVerificationRequest, DispatchTaskCreate, DispatchTaskOut
from app.services.db import (
    confirm_dispatch_task_sent,
    create_dispatch_task,
    get_dispatch_task,
    list_dispatch_tasks,
    lookup_product_by_code,
    retry_dispatch_task_after_review,
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def create_dispatch_router() -> APIRouter:
    router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])

    @router.post("/verify-remark")
    async def verify_dispatch_remark(body: DispatchRemarkVerificationRequest, request: Request):
        wx_remark = body.wx_remark.strip()
        if not wx_remark:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "INVALID_INPUT", "message": "微信好友备注名不能为空"}},
            )

        from app.services.wechat.sender import verify_remark
        from app.services.wechat.uia import ChatVerificationError, UIAutomationUnavailableError

        try:
            result = verify_remark(wx_remark, _settings(request))
        except (NotImplementedError, UIAutomationUnavailableError):
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "REMARK_VERIFICATION_FAILED",
                        "message": "微信会话校验当前不可用，需要人工复核。",
                    }
                },
            )
        except ChatVerificationError:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "REMARK_VERIFICATION_FAILED",
                        "message": "好友备注未能精确校验，需要人工复核。",
                    }
                },
            )
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "REMARK_VERIFICATION_FAILED",
                        "message": "微信会话校验当前不可用，需要人工复核。",
                    }
                },
            )

        return {"verified": True, "header_name": result.header_name}

    @router.post("", response_model=DispatchTaskOut)
    async def register_dispatch(body: DispatchTaskCreate, request: Request):
        settings = _settings(request)
        db_path = settings.db_path

        wx_remark = body.wx_remark.strip()
        if not wx_remark:
            return JSONResponse(status_code=400, content={"error": {"code": "INVALID_INPUT", "message": "微信好友备注名不能为空"}})

        codes = list(dict.fromkeys(c.strip() for c in body.send_codes if c.strip()))
        if not codes or len(codes) > 4:
            return JSONResponse(status_code=400, content={"error": {"code": "INVALID_INPUT", "message": "发送编号需 1-4 个且不能为空"}})

        not_found = []
        for code in codes:
            if lookup_product_by_code(db_path, code) is None:
                not_found.append(code)
        if not_found:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "CODE_NOT_FOUND", "message": f"以下编号未入库：{', '.join(not_found)}"}},
            )

        now = datetime.now(timezone.utc)
        if body.trigger_at:
            try:
                trigger_dt = datetime.fromisoformat(body.trigger_at.replace("Z", "+00:00"))
                if trigger_dt.tzinfo is None:
                    trigger_dt = trigger_dt.replace(tzinfo=timezone.utc)
                trigger_at_iso = trigger_dt.isoformat()
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "INVALID_TRIGGER_AT", "message": "触发时间格式无效"}},
                )
        else:
            trigger_dt = now + timedelta(days=body.countdown_days)
            trigger_at_iso = trigger_dt.isoformat()

        task_id = uuid4().hex

        result = create_dispatch_task(
            db_path=db_path,
            task_id=task_id,
            wx_remark=wx_remark,
            send_codes=codes,
            countdown_days=body.countdown_days,
            created_at=now.isoformat(),
            trigger_at=trigger_at_iso,
        )
        return result

    @router.get("", response_model=list[DispatchTaskOut])
    async def get_dispatch_tasks(request: Request):
        settings = _settings(request)
        return list_dispatch_tasks(settings.db_path)

    @router.post("/{task_id}/retry-after-review", response_model=DispatchTaskOut)
    async def retry_after_review(task_id: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        if not retry_dispatch_task_after_review(settings.db_path, task_id):
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "DISPATCH_RETRY_NOT_ALLOWED", "message": "任务当前状态不能重新尝试发送"}},
            )
        return get_dispatch_task(settings.db_path, task_id)

    @router.post("/{task_id}/confirm-sent", response_model=DispatchTaskOut)
    async def confirm_sent(task_id: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        if not confirm_dispatch_task_sent(settings.db_path, task_id):
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "DISPATCH_CONFIRMATION_NOT_ALLOWED", "message": "任务当前状态不能确认已发送"}},
            )
        return get_dispatch_task(settings.db_path, task_id)

    @router.get("/{task_id}")
    async def get_dispatch_detail(task_id: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        manifest = _load_manifest(settings, task_id)
        return {**task, "manifest": manifest}

    @router.delete("/{task_id}")
    async def delete_dispatch_task(task_id: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        if task["status"] in ("generating", "sending"):
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "DELETE_NOT_ALLOWED", "message": "正在生成或发送中的记录不能删除"}},
            )
        _delete_task(settings, task_id)
        return {"ok": True}

    @router.post("/{task_id}/regenerate/{code}")
    async def regenerate_dispatch_item(task_id: str, code: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        if task["status"] not in ("ready", "pending", "needs_review", "failed"):
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "REGENERATE_NOT_ALLOWED", "message": "当前状态不支持重新生成"}},
            )
        if code not in task["send_codes"]:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "CODE_NOT_IN_TASK", "message": f"编号 {code} 不在此任务中"}},
            )
        result = _regenerate_item(settings, task_id, task, code)
        return result

    @router.post("/{task_id}/abandon")
    async def abandon_task(task_id: str, request: Request):
        settings = _settings(request)
        task = get_dispatch_task(settings.db_path, task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "DISPATCH_TASK_NOT_FOUND", "message": "发送任务不存在"}},
            )
        if task["status"] == "awaiting_confirmation":
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "ABANDON_NOT_ALLOWED", "message": "awaiting_confirmation 状态不能放弃：消息可能已发出，请去微信核对后再决定"}},
            )
        if task["status"] not in ("needs_review", "failed"):
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "ABANDON_NOT_ALLOWED", "message": "只能放弃 needs_review 或 failed 状态的记录"}},
            )
        _abandon_task(settings, task_id)
        return get_dispatch_task(settings.db_path, task_id)

    return router


def _load_manifest(settings: Settings, task_id: str) -> dict | None:
    dispatch_root = settings.storage_root / "dispatch"
    task_dir = dispatch_root / task_id
    manifest_path = task_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    results = manifest.get("results", [])
    enriched_results = []
    for r in results:
        if not isinstance(r, dict):
            continue
        entry = {
            "code": r.get("code", ""),
            "status": r.get("status", ""),
            "provider": r.get("provider", ""),
            "model": r.get("model", ""),
            "content_source": r.get("content_source", ""),
            "image_url": None,
            "content_text": None,
        }
        image_rel = r.get("image_path") or r.get("image")
        if image_rel:
            entry["image_url"] = f"/storage/dispatch/{task_id}/{image_rel}"
        content_rel = r.get("content_path")
        if content_rel:
            content_file = task_dir / content_rel
            if content_file.is_file():
                try:
                    entry["content_text"] = content_file.read_text(encoding="utf-8")
                except OSError:
                    pass
        enriched_results.append(entry)
    return {
        "task_id": manifest.get("task_id", task_id),
        "status": manifest.get("status", ""),
        "generated_at": manifest.get("generated_at", ""),
        "provider": manifest.get("provider", ""),
        "model": manifest.get("model", ""),
        "results": enriched_results,
    }


def _abandon_task(settings: Settings, task_id: str) -> None:
    import sqlite3
    conn = sqlite3.connect(str(settings.db_path), timeout=10)
    try:
        conn.execute(
            "UPDATE dispatch_tasks SET status = 'abandoned', fail_reason = '用户手动放弃' WHERE task_id = ?",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_task(settings: Settings, task_id: str) -> None:
    import shutil
    import sqlite3

    conn = sqlite3.connect(str(settings.db_path), timeout=10)
    try:
        conn.execute("DELETE FROM dispatch_tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()

    task_dir = settings.storage_root / "dispatch" / task_id
    if task_dir.is_dir():
        shutil.rmtree(task_dir, ignore_errors=True)


def _regenerate_item(settings: Settings, task_id: str, task: dict, code: str) -> dict:
    import os
    import random as rng

    from app.services.db import lookup_product_by_code
    from app.services.dispatch_generation import generate_image
    from app.services.review.generator import generate_review

    product = lookup_product_by_code(settings.db_path, code)
    if product is None:
        return {"error": {"code": "CODE_NOT_FOUND", "message": f"编号 {code} 未入库"}}

    fact_card = _load_fact_card_for_regen(settings, product)

    dispatch_root = settings.storage_root / "dispatch"
    task_dir = dispatch_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    from app.services.dispatch_scheduler import _code_directory_name
    directory_name = _code_directory_name(code)
    code_dir = task_dir / directory_name
    code_dir.mkdir(parents=True, exist_ok=True)

    root = settings.storage_root
    ref_path = (root / product["image_path"]).resolve()

    task_rng = rng.Random(f"{task_id}-regen-{rng.random()}")
    shot_type = task_rng.choice(["中近景", "细节照"])
    scene_index = task_rng.randint(0, max(0, len(fact_card.scenes or []) - 1))

    generated = generate_image(
        settings,
        reference_path=ref_path if ref_path.is_file() else ref_path,
        fact_card=fact_card,
        shot_type=shot_type,
        scene_index=scene_index,
        aspect_ratio="1:1",
        provider_name=settings.dispatch_image_provider,
        model_id=settings.dispatch_image_model,
        output_dir=code_dir,
    )

    selected_image = generated.graded_path or generated.output_path
    image_path = code_dir / f"image{selected_image.suffix.lower() or '.jpg'}"
    if selected_image != image_path:
        if image_path.exists():
            image_path.unlink()
        os.rename(selected_image, image_path)
    if generated.output_path.exists() and generated.output_path != selected_image and generated.output_path != image_path:
        generated.output_path.unlink(missing_ok=True)

    task_index = task["send_codes"].index(code)
    review_text = generate_review(fact_card, settings, task_id=task_id, task_index=task_index)
    content_path = code_dir / "content.txt"
    content_path.write_text(review_text, encoding="utf-8")

    manifest_path = task_dir / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}

    results = manifest.get("results", [])
    updated = False
    for r in results:
        if r.get("code") == code:
            r["image_path"] = f"{directory_name}/{image_path.name}"
            r["content_path"] = f"{directory_name}/content.txt"
            r["status"] = "ok"
            r["provider"] = generated.provider
            r["model"] = generated.model
            r["prompt"] = generated.prompt
            r["seed"] = generated.seed
            r["size"] = generated.size
            r["thinking_mode"] = generated.thinking_mode
            r["inject_appearance"] = generated.inject_appearance
            r["camera_pos"] = generated.camera_pos
            r["generation_path"] = "dispatch"
            updated = True
            break
    if not updated:
        results.append({
            "code": code,
            "status": "ok",
            "provider": generated.provider,
            "model": generated.model,
            "image_path": f"{directory_name}/{image_path.name}",
            "content_path": f"{directory_name}/content.txt",
            "prompt": generated.prompt,
            "seed": generated.seed,
            "size": generated.size,
            "thinking_mode": generated.thinking_mode,
            "inject_appearance": generated.inject_appearance,
            "camera_pos": generated.camera_pos,
            "generation_path": "dispatch",
        })
    manifest["results"] = results
    manifest["task_id"] = task_id
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "code": code,
        "image_url": f"/storage/dispatch/{task_id}/{directory_name}/{image_path.name}",
        "content_text": review_text,
    }


def _load_fact_card_for_regen(settings: Settings, product: dict):
    from app.schemas import FactCard
    fact_card_path = settings.storage_root / product.get("fact_card_path", "")
    if not fact_card_path.is_file():
        from app.errors import AppError
        raise AppError("FACT_CARD_NOT_FOUND", "产品卡片数据不存在", 500)
    raw = json.loads(fact_card_path.read_text(encoding="utf-8"))
    return FactCard.model_validate(raw)
