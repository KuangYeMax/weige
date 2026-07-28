"""方案A验证脚本：参照物锚定 + 构图约束，单图生成。"""

import base64
import json
import mimetypes
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.config import Settings  # noqa: E402

settings = Settings()

INPUT_IMAGE = ROOT / "storage/uploads/2d697c61-0cbf-4e8a-8fe2-8167ed9910ae.jpg"
OUTPUT_DIR = ROOT / "storage/generated"


# ─── Step 1: OCR dimension recognition ───────────────────────────────────────

def ocr_dimensions(image_path: Path) -> dict:
    """Call vision model to read printed dimension text from image."""
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_uri = f"data:{media_type};base64,{encoded}"

    prompt = (
        "请仔细观察这张商品图片上印刷或标注的规格文字（如产品参数表、标签等），"
        "从中提取真实的尺寸数字。\n\n"
        "【硬约束】\n"
        "- 只能从图上真实出现的文字里读取数字\n"
        "- 严禁根据品类经验、常识、训练记忆推测尺寸\n"
        "- 如果图上没有任何尺寸相关文字，全部返回 null\n\n"
        "【优先级】\n"
        '- height_cm: 优先取"总高"对应的数字，无"总高"才取"高"对应的数字\n'
        '- width_cm: 取"宽"对应的数字\n'
        '- depth_cm: 取"深"或"长"对应的数字\n'
        '- weight_kg: 取"重"或"重量"对应的数字（单位换算为kg）\n\n'
        "【额外要求】\n"
        "- 同时返回你从图上读到的所有文字，放在 all_text 字段中\n\n"
        "请只输出纯 JSON，格式如下：\n"
        "{\n"
        '  "height_cm": number|null,\n'
        '  "width_cm": number|null,\n'
        '  "depth_cm": number|null,\n'
        '  "weight_kg": number|null,\n'
        '  "size_source": "ocr"|"unknown",\n'
        '  "all_text": "图上所有可读文字"\n'
        "}\n"
        '如果完全没有读到尺寸数字，size_source 设为 "unknown"，数值字段全部为 null。'
    )

    payload = {
        "model": settings.ark_vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "temperature": 0.1,
    }
    headers = {"Authorization": f"Bearer {settings.ark_api_key}"}
    base_url = (settings.ark_vision_base_url or settings.ark_base_url).rstrip("/")

    print(f"[Step 1] 调用视觉模型 {settings.ark_vision_model} 识别尺寸...")
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    result = json.loads(text)
    return result


# ─── Step 2: Build prompt A ──────────────────────────────────────────────────

def build_prompt_a(height_cm: float) -> tuple[str, str]:
    """Return (positive_prompt, negative_prompt)."""
    ratio = round(height_cm / 10, 1)

    positive = (
        f"一尊小型桌面摆件佛像，摆在普通家庭的木质桌面上，旁边放着一只常见白色马克杯作为参照，"
        f"佛像总高约为这只马克杯高度的 {ratio} 倍。"
        f"相机略微俯视约30度拍摄，"
        f"佛像在画面中的高度约占画幅的一半，四周留出桌面和室内背景。"
        f"自然窗光，手机随手拍的真实生活感，轻微景深，非影棚布光，非广告图。"
    )

    negative = (
        "monumental, temple statue, altar-sized, life-size, oversized, "
        "studio lighting, product advertisement, low angle, close-up filling frame"
    )

    return positive, negative


# ─── Step 3: Generate image ──────────────────────────────────────────────────

def generate_image(reference_path: Path, prompt: str) -> Path:
    """Call volcengine img2img, return output path."""
    from app.services.image_generation.volcengine import VolcengineImageProvider

    provider = VolcengineImageProvider(
        api_key=settings.ark_api_key,
        base_url=(settings.ark_image_base_url or settings.ark_base_url),
        model=settings.ark_image_model,
        output_dir=OUTPUT_DIR,
        timeout=120.0,
    )

    print(f"[Step 3] 调用生图模型 {settings.ark_image_model}...")
    result = provider.generate(
        reference_image_path=reference_path,
        prompt=prompt,
        size="1728x2304",  # 3:4 portrait
    )

    # Rename to scale_a_ prefix
    new_name = f"scale_a_{result.output_path.name}"
    final_path = OUTPUT_DIR / new_name
    result.output_path.rename(final_path)

    print(f"[Step 3] 生成完成: model={result.model}, seed={result.seed}")
    return final_path


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not INPUT_IMAGE.exists():
        print(f"ERROR: 输入图不存在: {INPUT_IMAGE}")
        sys.exit(1)

    # Step 1
    dims = ocr_dimensions(INPUT_IMAGE)
    print(f"\n[Step 1 结果] OCR 返回:\n{json.dumps(dims, ensure_ascii=False, indent=2)}")

    height = dims.get("height_cm")
    source = dims.get("size_source", "unknown")

    if source == "unknown" or height is None:
        print("\n❌ 未识别到尺寸 (size_source=unknown)")
        print(f"图上读到的全部文字:\n{dims.get('all_text', '(无)')}")
        print("\n请人工判断。脚本停止，不调生图接口。")
        sys.exit(0)

    print(f"\n✓ 识别到 height_cm={height}, size_source={source}")

    # Step 2
    positive, negative = build_prompt_a(height)
    ratio = round(height / 10, 1)
    print(f"\n[Step 2] ratio = {ratio}")
    print(f"\n[Step 2] 正向提示词:\n{positive}")
    print(f"\n[Step 2] 负向提示词:\n{negative}")

    # Step 3 - combine positive and negative into one prompt string
    # Volcengine seedream uses single prompt field; prepend negative with marker
    full_prompt = f"{positive}\n\nNegative: {negative}"
    output_path = generate_image(INPUT_IMAGE, full_prompt)

    # Step 4: Report
    print("\n" + "=" * 60)
    print("【验证报告】")
    print("=" * 60)
    print(f"1. OCR 尺寸: height_cm={height}, width_cm={dims.get('width_cm')}, "
          f"depth_cm={dims.get('depth_cm')}, weight_kg={dims.get('weight_kg')}, "
          f"size_source={source}")
    print(f"2. ratio = {ratio}")
    print(f"3. 正向提示词:\n   {positive}")
    print(f"   负向提示词:\n   {negative}")
    print(f"4. 生成图片路径: {output_path.resolve()}")
    print(f"5. provider=volcengine, model={settings.ark_image_model}")


if __name__ == "__main__":
    main()
