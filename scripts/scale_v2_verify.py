"""方案A v2 双图验证：佛堂 + 书房各 1 张。"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.config import Settings  # noqa: E402
from app.services.image_generation.volcengine import VolcengineImageProvider  # noqa: E402
from app.services.scale_anchors import build_prompt_v2, _format_ratio  # noqa: E402

settings = Settings()
INPUT_IMAGE = ROOT / "storage/uploads/2d697c61-0cbf-4e8a-8fe2-8167ed9910ae.jpg"
OUTPUT_DIR = ROOT / "storage/generated"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEIGHT_CM = 36.0
PRODUCT_DESC = "一尊释迦牟尼佛坐像工艺摆件，古铜色树脂材质"
SEED = "2d697c61-buddha-test"


def generate_one(room: str, tag: str) -> Path:
    positive, negative, selection = build_prompt_v2(
        product_desc=PRODUCT_DESC,
        height_cm=HEIGHT_CM,
        room=room,
        seed=SEED,
    )

    print(f"\n{'='*60}")
    print(f"【{room}】选锚结果:")
    print(f"{'='*60}")

    if selection is None:
        print("  → 降级到无参照物近景模式")
    else:
        if selection.ambient_anchor:
            print(f"  ambient 锚: {selection.ambient_anchor['n']} "
                  f"({selection.ambient_anchor['cm']}cm, "
                  f"conf={selection.ambient_anchor['conf']})")
        for i, (anchor, ratio) in enumerate(zip(selection.ratio_anchors, selection.ratios)):
            formatted = _format_ratio(ratio, anchor["conf"])
            print(f"  ratio 锚 {i+1}: {anchor['n']} "
                  f"({anchor['cm']}cm, axis={anchor['axis']}, "
                  f"conf={anchor['conf']}) → ratio={formatted}倍")

    print(f"\n正向提示词:\n{positive}")
    print(f"\n负向提示词:\n{negative}")

    full_prompt = f"{positive}\n\nNegative: {negative}"

    provider = VolcengineImageProvider(
        api_key=settings.ark_api_key,
        base_url=(settings.ark_image_base_url or settings.ark_base_url),
        model=settings.ark_image_model,
        output_dir=OUTPUT_DIR,
        timeout=120.0,
    )

    print(f"\n调用生图: provider=volcengine, model={settings.ark_image_model}")
    result = provider.generate(
        reference_image_path=INPUT_IMAGE,
        prompt=full_prompt,
        size="1728x2304",
    )

    final_name = f"scale_v2_{tag}_{result.output_path.name}"
    final_path = OUTPUT_DIR / final_name
    result.output_path.rename(final_path)
    print(f"生成完成: seed={result.seed}")
    print(f"图片路径: {final_path.resolve()}")
    return final_path


def main():
    if not INPUT_IMAGE.exists():
        print(f"ERROR: 输入图不存在: {INPUT_IMAGE}")
        sys.exit(1)

    path1 = generate_one("佛堂", "fotang")
    path2 = generate_one("书房", "shufang")

    print(f"\n\n{'='*60}")
    print("【最终报告】")
    print(f"{'='*60}")
    print(f"佛堂图: {path1.resolve()}")
    print(f"书房图: {path2.resolve()}")
    print(f"provider=volcengine, model={settings.ark_image_model}")


if __name__ == "__main__":
    main()
