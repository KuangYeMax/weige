"""Post-generation color grading: pull AI-saturated output back toward realistic phone-camera look."""
from __future__ import annotations

import io
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageEnhance

from app.errors import AppError


@dataclass
class GradeMetadata:
    saturation: float = 0.0
    contrast: float = 0.0
    highlight: float = 0.0
    warm_r: float = 0.0
    warm_b: float = 0.0
    grain_alpha: float = 0.0
    vignette_strength: float = 0.0
    vignette_cx_offset: float = 0.0
    vignette_cy_offset: float = 0.0
    vignette_x_stretch: float = 0.0
    vignette_y_stretch: float = 0.0
    micro_rotation_degrees: float = 0.0
    jpeg_quality: int = 92
    crop_left: int = 0
    crop_top: int = 0
    crop_right: int = 0
    crop_bottom: int = 0

    def to_dict(self) -> dict:
        return {
            "saturation": self.saturation,
            "contrast": self.contrast,
            "highlight": self.highlight,
            "warm_r": self.warm_r,
            "warm_b": self.warm_b,
            "grain_alpha": self.grain_alpha,
            "vignette_strength": self.vignette_strength,
            "vignette_cx_offset": self.vignette_cx_offset,
            "vignette_cy_offset": self.vignette_cy_offset,
            "vignette_x_stretch": self.vignette_x_stretch,
            "vignette_y_stretch": self.vignette_y_stretch,
            "micro_rotation_degrees": self.micro_rotation_degrees,
            "jpeg_quality": self.jpeg_quality,
            "crop_left": self.crop_left,
            "crop_top": self.crop_top,
            "crop_right": self.crop_right,
            "crop_bottom": self.crop_bottom,
        }


def _jitter(rng: random.Random, base: float, pct: float = 0.10) -> float:
    lo = base * (1 - pct)
    hi = base * (1 + pct)
    return rng.uniform(lo, hi)


def color_grade(
    image_path: Path,
    output_dir: Path,
    saturation: float = 0.75,
    highlight: float = 0.88,
    contrast: float = 0.93,
    grain_alpha: float = 0.015,
    vignette_strength: float = 0.25,
    micro_rotation_degrees: float = 1.5,
    seed: int | None = None,
) -> tuple[Path, GradeMetadata]:
    rng = random.Random(seed)

    actual_sat = _jitter(rng, saturation)
    actual_contrast = _jitter(rng, contrast)
    actual_highlight = _jitter(rng, highlight)
    actual_grain = _jitter(rng, grain_alpha)
    actual_vignette = _jitter(rng, vignette_strength)
    warm_r = rng.uniform(1.005, 1.015)
    warm_b = rng.uniform(0.975, 0.985)
    jpeg_quality = rng.randint(88, 96)

    meta = GradeMetadata(
        saturation=round(actual_sat, 4),
        contrast=round(actual_contrast, 4),
        highlight=round(actual_highlight, 4),
        warm_r=round(warm_r, 4),
        warm_b=round(warm_b, 4),
        grain_alpha=round(actual_grain, 5),
        vignette_strength=round(actual_vignette, 4),
        micro_rotation_degrees=round(micro_rotation_degrees, 2),
        jpeg_quality=jpeg_quality,
    )

    with Image.open(image_path) as img:
        img = img.convert("RGB")

        # 1.3: micro rotation BEFORE grain (so BICUBIC doesn't smooth noise)
        img = _apply_micro_rotation(img, max_degrees=micro_rotation_degrees, rng=rng)

        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(actual_sat)

        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(actual_contrast)

        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(actual_highlight)

        r, g, b = img.split()
        r = r.point(lambda x: min(255, int(x * warm_r)))
        b = b.point(lambda x: int(x * warm_b))
        img = Image.merge("RGB", (r, g, b))

        if actual_grain > 0:
            grain = Image.effect_noise(img.size, 3)
            grain = grain.convert("RGB")
            img = Image.blend(img, grain, alpha=actual_grain)

        vignette_meta = _apply_vignette_numpy(img, strength=actual_vignette, rng=rng)
        img = vignette_meta[0]
        meta.vignette_cx_offset = vignette_meta[1]
        meta.vignette_cy_offset = vignette_meta[2]
        meta.vignette_x_stretch = vignette_meta[3]
        meta.vignette_y_stretch = vignette_meta[4]

        # 1.4: random inner crop
        crop_left = rng.randint(4, 8)
        crop_top = rng.randint(4, 8)
        crop_right = rng.randint(4, 8)
        crop_bottom = rng.randint(4, 8)
        w, h = img.size
        if w > crop_left + crop_right + 100 and h > crop_top + crop_bottom + 100:
            img = img.crop((crop_left, crop_top, w - crop_right, h - crop_bottom))
            meta.crop_left = crop_left
            meta.crop_top = crop_top
            meta.crop_right = crop_right
            meta.crop_bottom = crop_bottom

    output_path = output_dir / f"{uuid4()}-graded.jpg"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        img.save(output_path, format="JPEG", quality=jpeg_quality, optimize=True)
    except OSError as exc:
        raise AppError("FILE_SAVE_FAILED", "调色图片保存失败", 500) from exc
    finally:
        img.close()
    return output_path, meta


def _apply_vignette_numpy(
    img: Image.Image, strength: float, rng: random.Random
) -> tuple[Image.Image, float, float, float, float]:
    if strength <= 0:
        return img, 0.0, 0.0, 1.0, 1.0
    w, h = img.size
    cx_offset = rng.uniform(-0.03, 0.03)
    cy_offset = rng.uniform(-0.03, 0.03)
    x_stretch = rng.uniform(1.0, 1.1)
    y_stretch = rng.uniform(1.0, 1.1)

    # Build a 256×256 mask then upscale with BICUBIC (full-size exceeds 150ms)
    mw, mh = 256, 256
    cx_m = mw * (0.5 + cx_offset)
    cy_m = mh * (0.5 + cy_offset)
    max_dist = math.sqrt((mw / 2 * x_stretch) ** 2 + (mh / 2 * y_stretch) ** 2)

    mask_data = bytearray(mw * mh)
    for row in range(mh):
        dy = (row - cy_m) * y_stretch
        dy2 = dy * dy
        row_offset = row * mw
        for col in range(mw):
            dx = (col - cx_m) * x_stretch
            dist = math.sqrt(dx * dx + dy2)
            factor = dist / max_dist
            val = int(255 * max(0.0, 1 - strength * factor * factor))
            mask_data[row_offset + col] = val

    mask_small = Image.frombytes("L", (mw, mh), bytes(mask_data))
    mask = mask_small.resize((w, h), Image.Resampling.BICUBIC)
    mask_small.close()

    dark = Image.new("RGB", (w, h), (0, 0, 0))
    result = Image.composite(img, dark, mask)
    mask.close()
    dark.close()
    return result, round(cx_offset, 4), round(cy_offset, 4), round(x_stretch, 4), round(y_stretch, 4)


def _apply_micro_rotation(img: Image.Image, max_degrees: float = 2.0, rng: random.Random | None = None) -> Image.Image:
    if max_degrees <= 0:
        return img
    if rng is None:
        rng = random.Random()
    angle = rng.uniform(-max_degrees, max_degrees)
    rotated = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    rw, rh = rotated.size
    ow, oh = img.size
    left = (rw - ow) // 2
    top = (rh - oh) // 2
    cropped = rotated.crop((left, top, left + ow, top + oh))
    rotated.close()
    return cropped
