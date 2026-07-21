from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat

from app.schemas import FactCard


class MockVisionProvider:
    name = "mock"
    model = "pillow-color-summary"

    def analyze(self, image_path: Path) -> FactCard:
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            sample = rgb.resize((1, 1))
            red, green, blue = (round(value) for value in ImageStat.Stat(sample).mean)

        orientation = "竖向" if height > width else "横向" if width > height else "方形"
        color = f"图片平均主色约 RGB({red}, {green}, {blue})"
        return FactCard.model_validate(
            {
                "商品名称": "待确认商品",
                "识别置信度": "低",
                "商品品类": "",
                "商品形态": "",
                "主体定义": "上传图片中居中展示的待确认商品主体",
                "画面中需忽略": [],
                "整体特征": f"{orientation}画面，{color}",
                "关键结构": [
                    {
                        "名称": "可见商品主体",
                        "数量": "无法确认",
                        "位置与关系": "画面主要区域",
                        "外观特征": color,
                        "重要性": "高",
                    }
                ],
                "颜色与材质观感": [color],
                "文字与规格": [],
                "尺寸": {},
                "自然场景": [
                    {
                        "场景": "中性实物展示",
                        "具体位置": "与商品体量匹配的普通干净承托面",
                    }
                ],
                "建议拍法": {
                    "完整照": "展示完整商品全貌",
                    "中近景": "突出主要结构",
                    "细节照": [],
                },
                "保真锁": ["保持当前主体数量", "保持当前整体轮廓", f"保持{color}"],
                "不确定项": ["当前为 mock 视觉结果，商品品类、用途、数量和文字需人工确认"],
            }
        )
