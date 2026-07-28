"""Shared single-image generation for the product studio and dispatch scheduler."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from uuid import uuid4

from app.config import Settings
from app.errors import AppError
from app.schemas import FactCard, Scene
from app.services.fact_card_compress import render_generation_prompt
from app.services.image_generation.bailian import BailianImageProvider
from app.services.image_generation.mock import MockImageProvider
from app.services.image_generation.models import get_model
from app.services.image_generation.volcengine import VolcengineImageProvider, map_aspect_ratio
from app.services.post_grade import color_grade, GradeMetadata
from app.services.preprocess import crop_for_detail, crop_for_medium
from app.services.realism_pool import draw_realism_context, realism_metadata
from app.services.scale_anchors import build_prompt_v2, ROOM_COMPANIONS

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "image_generation.txt"
_GENERATION_LOCK = threading.Semaphore(1)


@dataclass(frozen=True)
class GeneratedImage:
    output_path: Path
    graded_path: Path | None
    provider: str
    model: str
    prompt: str
    product_brief: str
    size: str
    seed: int | None
    elapsed_ms: int
    used_reference: bool
    realism: dict | None
    thinking_mode: bool = False
    inject_appearance: bool = False
    camera_pos: dict | None = None
    generation_path: str = "dispatch"


def create_image_provider(
    settings: Settings,
    provider_name: str | None = None,
    model_id: str | None = None,
    output_dir: Path | None = None,
):
    effective_provider = provider_name or settings.image_provider
    target_dir = output_dir or settings.storage_root / "generated"
    if settings.image_provider == "mock" and not provider_name:
        return MockImageProvider(target_dir), "mock"
    if effective_provider == "mock":
        return MockImageProvider(target_dir), "mock"
    if effective_provider == "volcengine":
        model = model_id or settings.volc_image_model or settings.ark_image_model
        return (
            VolcengineImageProvider(
                settings.ark_api_key,
                settings.image_base_url,
                model,
                target_dir,
                settings.external_timeout_seconds,
                settings.max_download_bytes,
            ),
            "volcengine",
        )
    if effective_provider == "bailian":
        model = model_id or settings.bailian_image_model
        return (
            BailianImageProvider(
                settings.dashscope_api_key,
                settings.dashscope_base_url,
                model,
                target_dir,
                settings.external_timeout_seconds,
                settings.max_download_bytes,
                thinking_mode=settings.bailian_thinking_mode,
            ),
            "bailian",
        )
    raise AppError("PROVIDER_NOT_FOUND", "图片 provider 配置无效", 500)


def _size_for(provider_name: str | None, settings: Settings, aspect_ratio: str) -> str:
    if provider_name == "bailian":
        return aspect_ratio
    if provider_name == "volcengine" or settings.image_provider == "volcengine":
        return map_aspect_ratio(aspect_ratio)
    return map_aspect_ratio(aspect_ratio)


def _select_scene(fact_card: FactCard, scene_index: int) -> Scene:
    scenes = fact_card.scenes or []
    if scenes and scene_index < len(scenes):
        return scenes[scene_index]
    if scenes:
        return scenes[0]
    return Scene(scene="通用场景", placement="自然摆放")


# ── Prompt strategy interface ────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptResult:
    positive: str
    negative: str
    product_brief: str
    strategy: str
    meta: dict


_REALISM_BOOST_POSITIVE = (
    "手机随手拍，构图没刻意对齐，光线普通不完美，白平衡略偏，"
    "画面有轻微噪点，背景有真实生活痕迹和少量杂物。"
)
_REALISM_BOOST_NEGATIVE = (
    "bokeh, shallow depth of field, professional photography, studio lighting, "
    "clean minimal background, glossy reflection, symmetrical composition, "
    "perfect lighting, commercial product shot"
)


def build_image_prompt(
    settings: Settings,
    *,
    fact_card: FactCard,
    scene: Scene,
    shot_type: str,
    product_id: str = "",
    height_cm: float | None = None,
    room: str | None = None,
    realism_ctx=None,
    camera_seed: int | None = None,
) -> PromptResult:
    strategy = settings.image_prompt_strategy

    if strategy == "scale_anchor":
        return _build_scale_anchor_prompt(
            settings, fact_card=fact_card, scene=scene, shot_type=shot_type,
            product_id=product_id, height_cm=height_cm, room=room,
        )

    return _build_legacy_prompt(
        settings, fact_card=fact_card, scene=scene, shot_type=shot_type,
        realism_ctx=realism_ctx, camera_seed=camera_seed,
    )


def _build_legacy_prompt(
    settings: Settings,
    *,
    fact_card: FactCard,
    scene: Scene,
    shot_type: str,
    realism_ctx=None,
    camera_seed: int | None = None,
) -> PromptResult:
    product_brief, prompt, camera_pos = render_generation_prompt(
        PROMPT_PATH.read_text(encoding="utf-8"), fact_card, scene, shot_type, realism_ctx,
        camera_seed=camera_seed,
        inject_appearance=settings.inject_appearance_into_image_prompt,
    )
    positive = prompt
    negative = ""

    if settings.image_realism_boost == "on":
        positive = positive.rstrip() + "\n" + _REALISM_BOOST_POSITIVE
        negative = _REALISM_BOOST_NEGATIVE

    return PromptResult(
        positive=positive,
        negative=negative,
        product_brief=product_brief,
        strategy="legacy",
        meta={"camera_pos": camera_pos},
    )


def _build_scale_anchor_prompt(
    settings: Settings,
    *,
    fact_card: FactCard,
    scene: Scene,
    shot_type: str,
    product_id: str = "",
    height_cm: float | None = None,
    room: str | None = None,
) -> PromptResult:
    effective_room = room
    room_fallback = False
    route = ""

    if height_cm is None or (fact_card.dimensions.size_source == "unknown"):
        route = "C"
    elif effective_room is None:
        effective_room = "客厅"
        room_fallback = True
        route = "B"
    else:
        route = "A"

    if route == "C":
        product_desc = fact_card.product_name or "商品"
        positive = f"{product_desc}，近景特写，浅景深，背景虚化干净，看不到完整台面和周围环境。"
        negative = (
            "monumental, temple statue, altar-sized, life-size, oversized, "
            "studio lighting, product advertisement, clean minimal background, "
            "symmetrical composition, staged props, floating object, low angle, "
            "close-up filling frame, watermark, text overlay, logo, "
            "ruler, measuring tape, coin, hand, cup, book, keyboard, phone"
        )
        selection = None
        ratios = []
        anchors = []
    else:
        seed = product_id or str(uuid4())
        product_desc = fact_card.product_name or "商品"
        positive, negative, selection = build_prompt_v2(
            product_desc=product_desc,
            height_cm=height_cm,
            room=effective_room,
            seed=seed,
        )
        if selection is None:
            route = "D"
        ratios = [r for r in (selection.ratios if selection else [])]
        anchors = [a["n"] for a in (selection.ratio_anchors if selection else [])]

    if settings.image_realism_boost == "on":
        positive = positive.rstrip() + "\n" + _REALISM_BOOST_POSITIVE
        negative = (negative + ", " + _REALISM_BOOST_NEGATIVE) if negative else _REALISM_BOOST_NEGATIVE

    return PromptResult(
        positive=positive,
        negative=negative,
        product_brief=fact_card.product_name or "",
        strategy="scale_anchor",
        meta={
            "route": route,
            "room": effective_room,
            "room_fallback": room_fallback,
            "anchors": anchors,
            "ratios": ratios,
            "height_cm": height_cm,
        },
    )


@dataclass(frozen=True)
class PreparedGeneration:
    product_brief: str
    prompt: str
    effective_reference: Path
    preprocessed_tmp: Path | None
    realism_ctx: object | None
    camera_pos: object | None


def prepare_generation(
    settings: Settings,
    *,
    reference_path: Path,
    fact_card: FactCard,
    shot_type: str,
    scene: Scene,
) -> PreparedGeneration:
    """Shared prompt assembly + preprocessing for both single-generate and compare."""
    realism_ctx = None
    if settings.realism_pool:
        seed = settings.realism_seed if settings.realism_seed is not None else random.randint(0, 2**31 - 1)
        realism_ctx = draw_realism_context(seed, shot_type)
    camera_seed = random.randint(0, 2**31 - 1)
    product_brief, prompt, camera_pos = render_generation_prompt(
        PROMPT_PATH.read_text(encoding="utf-8"), fact_card, scene, shot_type, realism_ctx,
        camera_seed=camera_seed,
        inject_appearance=settings.inject_appearance_into_image_prompt,
    )

    effective_reference = reference_path
    preprocessed_tmp: Path | None = None
    if settings.shrink_reference:
        if shot_type == "细节照":
            preprocessed_bytes = crop_for_detail(
                reference_path,
                crop_ratio=settings.detail_crop_ratio,
                center_y_bias=settings.detail_crop_center_y_bias,
                model_bbox=fact_card.subject_bbox,
            )
        else:
            preprocessed_bytes = crop_for_medium(reference_path, model_bbox=fact_card.subject_bbox)
        preprocessed_tmp = settings.storage_root / "uploads" / f"_preproc_{uuid4()}.jpg"
        preprocessed_tmp.write_bytes(preprocessed_bytes)
        effective_reference = preprocessed_tmp

    return PreparedGeneration(
        product_brief=product_brief,
        prompt=prompt,
        effective_reference=effective_reference,
        preprocessed_tmp=preprocessed_tmp,
        realism_ctx=realism_ctx,
        camera_pos=camera_pos,
    )


def run_provider_generation(
    provider: object,
    provider_name: str,
    reference_path: Path | None,
    prompt: str,
    size: str,
    model_id: str | None,
    used_reference: bool,
):
    """Serialize every provider call, including workbench comparison calls."""
    with _GENERATION_LOCK:
        if provider_name == "bailian":
            return provider.generate(reference_path if used_reference else None, prompt, size)
        if provider_name == "volcengine":
            return provider.generate(reference_path, prompt, size, model_id=model_id)
        return provider.generate(reference_path, prompt, size)


def generate_image(
    settings: Settings,
    *,
    reference_path: Path,
    fact_card: FactCard,
    shot_type: str,
    scene_index: int,
    aspect_ratio: str,
    provider_name: str | None = None,
    model_id: str | None = None,
    output_dir: Path | None = None,
    scene_override: Scene | None = None,
    provider_factory: Callable[[Settings, str | None, str | None, Path | None], tuple[object, str]] = create_image_provider,
) -> GeneratedImage:
    requested_provider = provider_name
    requested_model = model_id
    if requested_model:
        model_info = get_model(requested_model)
        if not model_info:
            raise AppError("MODEL_NOT_FOUND", f"不支持的模型: {requested_model}", 400)
        if requested_provider and model_info.provider != requested_provider:
            raise AppError("MODEL_PROVIDER_MISMATCH", f"模型 {requested_model} 不属于 {requested_provider}", 400)
        requested_provider = model_info.provider

    scene = scene_override or _select_scene(fact_card, scene_index)
    size = _size_for(requested_provider, settings, aspect_ratio)

    prep = prepare_generation(
        settings,
        reference_path=reference_path,
        fact_card=fact_card,
        shot_type=shot_type,
        scene=scene,
    )
    product_brief = prep.product_brief
    prompt = prep.prompt
    effective_reference = prep.effective_reference
    preprocessed_tmp = prep.preprocessed_tmp
    realism_ctx = prep.realism_ctx

    started = time.perf_counter()
    try:
        provider, effective_provider = provider_factory(settings, requested_provider, requested_model, output_dir)
        if effective_provider == "volcengine":
            used_model = requested_model or settings.volc_image_model or settings.ark_image_model
        elif effective_provider == "bailian":
            used_model = requested_model or settings.bailian_image_model
        else:
            used_model = MockImageProvider.model
        model_info = get_model(used_model) if requested_model else None
        used_reference = not model_info or model_info.supports_reference

        result = run_provider_generation(
            provider,
            effective_provider,
            effective_reference,
            prompt,
            size,
            requested_model,
            used_reference,
        )
    finally:
        if preprocessed_tmp:
            preprocessed_tmp.unlink(missing_ok=True)

    graded_path = None
    if settings.post_grade:
        grade_seed = result.seed if result.seed is not None else random.randint(0, 2**31 - 1)
        graded_path, grade_meta = color_grade(
            result.output_path,
            result.output_path.parent,
            saturation=settings.grade_saturation,
            highlight=settings.grade_highlight,
            contrast=settings.grade_contrast,
            grain_alpha=settings.grade_grain,
            vignette_strength=settings.grade_vignette,
            micro_rotation_degrees=settings.grade_micro_rotation,
            seed=grade_seed,
        )
    return GeneratedImage(
        output_path=result.output_path,
        graded_path=graded_path,
        provider=effective_provider,
        model=result.model,
        prompt=prompt,
        product_brief=product_brief,
        size=size,
        seed=result.seed,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
        used_reference=used_reference,
        realism=realism_metadata(realism_ctx) if realism_ctx else None,
        thinking_mode=settings.bailian_thinking_mode,
        inject_appearance=settings.inject_appearance_into_image_prompt,
        camera_pos=prep.camera_pos.to_dict() if prep.camera_pos else None,
    )


@dataclass
class ContentResult:
    """Result of review content generation."""

    text: str
    is_fallback: bool
    opening: str = ""
    skeleton: str = ""
    length_tier: str = ""
    has_minor_flaw: bool = False
    model: str = ""
    status: str = "ai"  # "ai" | "fallback" | "needs_review"


def generate_content(
    fact_card: FactCard,
    product: dict[str, str],
    settings: Settings | None = None,
    task_id: str | None = None,
    task_index: int = 0,
    siblings_dir: Path | None = None,
) -> ContentResult:
    """Generate a review via the configured AI service; falls back to template on failure.

    Dedup: reads sibling content.txt files from siblings_dir (excluding current code_dir).
    If too similar, retries once. If still similar, returns status='needs_review'.
    """
    from app.services.review.dedup import check_similarity, read_sibling_texts
    from app.services.review.generator import generate_review as _gen, normalize_review_text
    from app.services.review.variety import VarietySampler

    variety_meta: dict = {}
    if task_id:
        sampler = VarietySampler(task_id)
        variety_meta = sampler.sample(task_index)

    threshold = settings.review_similarity_threshold if settings else 0.6

    def _make_result(text: str, is_fallback: bool, status: str) -> ContentResult:
        return ContentResult(
            text=text,
            is_fallback=is_fallback,
            opening=variety_meta.get("opening", ""),
            skeleton=variety_meta.get("style", ""),
            length_tier=variety_meta.get("length_label", ""),
            has_minor_flaw=variety_meta.get("minor_flaw") is not None,
            model=settings.ark_review_model if settings else "",
            status=status,
        )

    def _read_siblings() -> list[str]:
        if not siblings_dir or not task_id:
            return []
        exclude = quote(product.get("code", ""), safe="-_").replace(".", "%2E")
        return read_sibling_texts(siblings_dir, exclude)

    if settings is not None and settings.review_provider != "disabled":
        siblings: list[str] | None = None

        for dedup_attempt in range(2):
            try:
                generated = normalize_review_text(
                    _gen(fact_card, settings, task_id=task_id, task_index=task_index)
                )
            except Exception:
                logger.exception("review generation failed, using fallback")
                break

            if not generated:
                break

            if siblings is None:
                siblings = _read_siblings()

            if not siblings or not check_similarity(generated, siblings, threshold):
                return _make_result(generated, False, "ai")

            if dedup_attempt == 0:
                logger.warning(
                    "content dedup: attempt 1 too similar, retrying generation"
                )
                continue

            # Second attempt still too similar -> needs_review
            logger.warning(
                "content dedup: attempt 2 still too similar, marking needs_review"
            )
            return _make_result(generated, False, "needs_review")

    product_name = fact_card.product_name or product.get("name", "商品")
    feature = fact_card.overall_features or "商品与页面描述相符"
    logger.warning("dispatch content using fallback template product=%s", product_name)
    text = _build_fallback_review(product_name, feature)
    return _make_result(text, True, "fallback")


def _build_fallback_review(product_name: str, feature: str) -> str:
    """生成结构多样化的 fallback 文案。

    不直接引用 feature 全文（会导致 LCS 过高）。
    用通用好评短句，通过 句式×评价词×动作词 排列组合保证多样性。
    每个变量 ≤ 3 字，模板固定连续片段 ≤ 2 字 → 任意两条 LCS ≤ 8。
    """
    positives = ["不错", "满意", "可以", "好评", "值了", "推荐", "挺好", "合适", "实用", "到位"]
    actions = ["收到", "到货", "拆开", "看过", "试用", "到手", "入了"]
    extras = ["如描述", "没失望", "还会来", "比想好", "值价格", "挺满意", "会回购", "没问题"]

    p = random.choice(positives)
    a = random.choice(actions)
    e = random.choice(extras)

    style = random.randint(1, 5)
    if style == 1:
        return f"{a}，{p}。"
    elif style == 2:
        return f"{p}，{e}。"
    elif style == 3:
        return f"{a}，{e}，{p}。"
    elif style == 4:
        return f"{e}，{p}👍"
    else:
        return f"{a}{p}，{e}。"
