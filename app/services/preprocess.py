"""Reference image preprocessing: crop product image based on shot type before feeding to image generation API."""
from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

if TYPE_CHECKING:
    from app.schemas import SubjectBbox


def _detect_product_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Detect the main product region by trimming background-like borders.

    Uses edge color sampling to find where the product content starts.
    Returns (left, upper, right, lower) bounding box.
    """
    w, h = img.size
    bg_sample = img.getpixel((0, 0))

    threshold = 40
    gray = img.convert("L")

    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    avg_bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    bg_img = Image.new("RGB", img.size, avg_bg)
    diff = Image.new("L", img.size)
    for y in range(h):
        for x in range(w):
            px = img.getpixel((x, y))
            d = sum(abs(px[i] - avg_bg[i]) for i in range(3)) // 3
            diff.putpixel((x, y), d)

    bbox = diff.point(lambda p: 255 if p > threshold else 0).getbbox()
    if bbox is None:
        return (0, 0, w, h)
    return bbox


def _fast_product_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Fast product bbox detection using background color subtraction."""
    w, h = img.size
    margin = max(2, min(w, h) // 50)

    top_row = [img.getpixel((x, 0)) for x in range(0, w, max(1, w // 10))]
    bot_row = [img.getpixel((x, h - 1)) for x in range(0, w, max(1, w // 10))]
    left_col = [img.getpixel((0, y)) for y in range(0, h, max(1, h // 10))]
    right_col = [img.getpixel((w - 1, y)) for y in range(0, h, max(1, h // 10))]

    all_border = top_row + bot_row + left_col + right_col
    if not all_border:
        return (0, 0, w, h)

    avg_bg = tuple(sum(px[i] for px in all_border) // len(all_border) for i in range(3))
    bg_img = Image.new("RGB", (w, h), avg_bg)

    from PIL import ImageChops
    diff = ImageChops.difference(img, bg_img).convert("L")
    bbox = diff.point(lambda p: 255 if p > 30 else 0).getbbox()

    if bbox is None:
        return (0, 0, w, h)

    l, t, r, b = bbox
    l = max(0, l - margin)
    t = max(0, t - margin)
    r = min(w, r + margin)
    b = min(h, b + margin)
    return (l, t, r, b)


def _strip_bottom_banner(img: Image.Image) -> Image.Image:
    """Remove bottom advertising banner/text area if it looks uniform and different from main content."""
    w, h = img.size
    check_height = int(h * 0.12)
    if check_height < 20:
        return img

    bottom_strip = img.crop((0, h - check_height, w, h))
    strip_colors = bottom_strip.getcolors(maxcolors=256)

    if strip_colors and len(strip_colors) <= 10:
        dominant_count = sum(c for c, _ in strip_colors)
        top_color_count = max(c for c, _ in strip_colors)
        if top_color_count / dominant_count > 0.7:
            top_color = next(color for count, color in strip_colors if count == top_color_count)
            main_area = img.crop((0, 0, w, h - check_height))
            main_colors = main_area.getcolors(maxcolors=256)
            if main_colors:
                main_dominant = max(main_colors, key=lambda x: x[0])[1]
                diff = sum(abs(top_color[i] - main_dominant[i]) for i in range(3))
                if diff > 80:
                    return main_area
    return img


def _bbox_from_model(img: Image.Image, bbox: SubjectBbox | None) -> tuple[int, int, int, int] | None:
    """Convert a normalized model bbox to pixel coords. Returns None if bbox is trivial or absent."""
    if bbox is None:
        return None
    if bbox.x == 0 and bbox.y == 0 and bbox.w >= 0.99 and bbox.h >= 0.99:
        return None
    w, h = img.size
    l = int(bbox.x * w)
    t = int(bbox.y * h)
    r = int((bbox.x + bbox.w) * w)
    b = int((bbox.y + bbox.h) * h)
    l = max(0, l)
    t = max(0, t)
    r = min(w, r)
    b = min(h, b)
    if r - l < 20 or b - t < 20:
        return None
    return (l, t, r, b)


def crop_for_medium(image_path: Path, model_bbox: SubjectBbox | None = None) -> bytes:
    """Crop reference image for medium shot: tight crop around product main body (80-90% fill)."""
    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        source = _strip_bottom_banner(source)
        w, h = source.size

        mbbox = _bbox_from_model(source, model_bbox)
        if mbbox is not None:
            l, t, r, b = mbbox
        else:
            l, t, r, b = _fast_product_bbox(source)

        bw, bh = r - l, b - t
        pad_x = int(bw * 0.06)
        pad_y = int(bh * 0.06)
        l = max(0, l - pad_x)
        t = max(0, t - pad_y)
        r = min(w, r + pad_x)
        b = min(h, b + pad_y)

        cropped = source.crop((l, t, r, b))

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=92)
    cropped.close()
    return buf.getvalue()


def crop_for_detail(
    image_path: Path,
    crop_ratio: float = 0.4,
    center_y_bias: float = 0.35,
    model_bbox: SubjectBbox | None = None,
) -> bytes:
    """Crop reference image for detail shot: extract a key region and enlarge.

    Args:
        crop_ratio: fraction of product bbox width/height to keep (0.4 = 40%).
        center_y_bias: vertical position of crop center within bbox (0=top, 1=bottom).
    """
    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        source = _strip_bottom_banner(source)
        w, h = source.size

        mbbox = _bbox_from_model(source, model_bbox)
        if mbbox is not None:
            l, t, r, b = mbbox
        else:
            l, t, r, b = _fast_product_bbox(source)
        bw, bh = r - l, b - t

        center_x = l + bw // 2
        center_y = t + int(bh * center_y_bias)

        crop_w = int(bw * crop_ratio)
        crop_h = int(bh * crop_ratio)

        cl = max(0, center_x - crop_w // 2)
        ct = max(0, center_y - crop_h // 2)
        cr = min(w, cl + crop_w)
        cb = min(h, ct + crop_h)

        cropped = source.crop((cl, ct, cr, cb))
        target_size = max(w, h)
        cropped = cropped.resize(
            (target_size, target_size), Image.Resampling.LANCZOS
        )

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=92)
    cropped.close()
    return buf.getvalue()


def shrink_reference(image_path: Path, model_bbox: SubjectBbox | None = None) -> bytes:
    """Legacy: kept for backwards compat but now delegates to crop_for_medium."""
    return crop_for_medium(image_path, model_bbox=model_bbox)
