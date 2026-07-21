from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.errors import AppError


@dataclass
class GenerationResult:
    output_path: Path
    model: str
    seed: int | None = None


class MockImageProvider:
    name = "mock"
    model = "pillow-scene-preview"

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def generate(self, reference_image_path: Path, prompt: str, size: str) -> GenerationResult:
        width, height = (int(part) for part in size.split("x"))
        canvas = Image.new("RGB", (width, height), "#e8ece9")
        with Image.open(reference_image_path) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            fitted = ImageOps.contain(source, (int(width * 0.86), int(height * 0.78)))
        x = (width - fitted.width) // 2
        y = (height - fitted.height) // 2
        draw = ImageDraw.Draw(canvas)
        shadow = 18
        draw.rounded_rectangle(
            (x - 20 + shadow, y - 20 + shadow, x + fitted.width + 20 + shadow, y + fitted.height + 20 + shadow),
            radius=20,
            fill="#bac2bd",
        )
        draw.rounded_rectangle(
            (x - 20, y - 20, x + fitted.width + 20, y + fitted.height + 20),
            radius=20,
            fill="white",
        )
        canvas.paste(fitted, (x, y))

        label = "MOCK · AI 场景示意图"
        font_path = Path("/System/Library/Fonts/PingFang.ttc")
        try:
            font = ImageFont.truetype(str(font_path), max(24, width // 55))
        except OSError:
            label = "MOCK / AI SCENE PREVIEW"
            font = ImageFont.load_default(size=max(18, width // 70))
        box = draw.textbbox((0, 0), label, font=font)
        label_width = box[2] - box[0]
        label_height = box[3] - box[1]
        padding = max(18, width // 90)
        left = width - label_width - padding * 2 - max(24, width // 40)
        top = height - label_height - padding * 2 - max(24, height // 40)
        draw.rounded_rectangle(
            (left, top, width - max(24, width // 40), height - max(24, height // 40)),
            radius=10,
            fill="#18251f",
        )
        draw.text((left + padding, top + padding), label, fill="white", font=font)

        output_path = self.output_dir / f"{uuid4()}.jpg"
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path, format="JPEG", quality=92, optimize=True)
        except OSError as exc:
            raise AppError("FILE_SAVE_FAILED", "生成图片保存失败", 500) from exc
        return GenerationResult(output_path=output_path, model=self.model)
