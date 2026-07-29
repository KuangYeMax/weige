def _regenerate_image_only(settings: Settings, task_id: str, task: dict, code: str) -> dict:
    import os
    import random as rng

    from app.services.db import lookup_product_by_code
    from app.services.dispatch_generation import generate_image

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
    }


def _regenerate_content_only(settings: Settings, task_id: str, task: dict, code: str) -> dict:
    from app.services.db import lookup_product_by_code
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

    task_index = task["send_codes"].index(code)
    cheap_model = settings.review_cheap_model or None
    review_text = generate_review(fact_card, settings, task_id=task_id, task_index=task_index, model=cheap_model)
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
            r["content_path"] = f"{directory_name}/content.txt"
            updated = True
            break
    if not updated:
        results.append({
            "code": code,
            "content_path": f"{directory_name}/content.txt",
        })
    manifest["results"] = results
    manifest["task_id"] = task_id
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "code": code,
        "content_text": review_text,
    }