import io
from pathlib import Path

import pytest
from PIL import Image

from app.services.preprocess import crop_for_medium, crop_for_detail, _strip_bottom_banner, _bbox_from_model
from app.services.post_grade import color_grade, GradeMetadata
from app.services.fact_card_compress import (
    SHOT_TYPE_TEXT,
    compress_fact_card,
    render_generation_prompt,
    scale_expression_text,
)
from app.schemas import FactCard, Scene, SubjectBbox, ViewAngleTolerance


@pytest.fixture
def sample_image(tmp_path):
    path = tmp_path / "product.jpg"
    img = Image.new("RGB", (800, 1000), (180, 60, 40))
    img.save(path, format="JPEG")
    img.close()
    return path


@pytest.fixture
def image_with_banner(tmp_path):
    path = tmp_path / "banner_product.jpg"
    img = Image.new("RGB", (800, 1000), (180, 60, 40))
    for x in range(800):
        for y in range(880, 1000):
            img.putpixel((x, y), (255, 255, 255))
    img.save(path, format="JPEG")
    img.close()
    return path


class TestCropForMedium:
    def test_output_is_jpeg_bytes(self, sample_image):
        result = crop_for_medium(sample_image)
        assert isinstance(result, bytes)
        with Image.open(io.BytesIO(result)) as img:
            assert img.format == "JPEG"

    def test_crop_is_tighter_than_original(self, sample_image):
        result = crop_for_medium(sample_image)
        with Image.open(io.BytesIO(result)) as img:
            w, h = img.size
            assert w <= 800
            assert h <= 1000


class TestCropForDetail:
    def test_output_is_jpeg_bytes(self, sample_image):
        result = crop_for_detail(sample_image)
        assert isinstance(result, bytes)
        with Image.open(io.BytesIO(result)) as img:
            assert img.format == "JPEG"

    def test_detail_crop_is_square_enlarged(self, sample_image):
        result = crop_for_detail(sample_image)
        with Image.open(io.BytesIO(result)) as img:
            w, h = img.size
            assert w == h

    def test_crop_ratio_controls_region_size(self, tmp_path):
        """A product surrounded by white background — crop at 0.4 should take ~40% of product area."""
        path = tmp_path / "product_padded.jpg"
        img = Image.new("RGB", (1000, 1000), (255, 255, 255))
        for x in range(200, 800):
            for y in range(200, 800):
                img.putpixel((x, y), (50, 80, 120))
        img.save(path, format="JPEG")
        img.close()

        small_crop = crop_for_detail(path, crop_ratio=0.4)
        large_crop = crop_for_detail(path, crop_ratio=0.8)
        assert len(small_crop) < len(large_crop)

    def test_configurable_center_y_bias(self, tmp_path):
        """Use a gradient image so different y-bias produces different pixel content."""
        path = tmp_path / "gradient.jpg"
        img = Image.new("RGB", (800, 1000))
        for y in range(1000):
            for x in range(800):
                img.putpixel((x, y), (50, y * 255 // 1000, 100))
        img.save(path, format="JPEG")
        img.close()
        top_crop = crop_for_detail(path, center_y_bias=0.2)
        bottom_crop = crop_for_detail(path, center_y_bias=0.8)
        assert top_crop != bottom_crop


class TestStripBottomBanner:
    def test_strips_uniform_bottom(self, image_with_banner):
        with Image.open(image_with_banner) as img:
            img = img.convert("RGB")
            stripped = _strip_bottom_banner(img)
            assert stripped.size[1] < img.size[1]

    def test_no_strip_on_normal_image(self, sample_image):
        with Image.open(sample_image) as img:
            img = img.convert("RGB")
            result = _strip_bottom_banner(img)
            assert result.size == img.size


class TestPostGrade:
    def test_produces_graded_jpeg(self, sample_image, tmp_path):
        output_dir = tmp_path / "graded"
        result, meta = color_grade(sample_image, output_dir, seed=42)
        assert result.exists()
        assert "-graded" in result.name
        with Image.open(result) as img:
            assert img.format == "JPEG"
        assert isinstance(meta, GradeMetadata)

    def test_graded_image_is_less_saturated(self, sample_image, tmp_path):
        output_dir = tmp_path / "graded"
        result, _ = color_grade(sample_image, output_dir, seed=42)
        with Image.open(sample_image) as orig:
            orig_stat = orig.convert("HSV").split()[1]
            orig_sat = sum(list(orig_stat.getdata())) / (orig_stat.width * orig_stat.height)
        with Image.open(result) as graded:
            graded_stat = graded.convert("HSV").split()[1]
            graded_sat = sum(list(graded_stat.getdata())) / (graded_stat.width * graded_stat.height)
        assert graded_sat < orig_sat

    def test_configurable_saturation(self, sample_image, tmp_path):
        mild, _ = color_grade(sample_image, tmp_path / "mild", saturation=0.9, seed=42)
        strong, _ = color_grade(sample_image, tmp_path / "strong", saturation=0.6, seed=42)
        with Image.open(mild) as m, Image.open(strong) as s:
            m_sat = sum(list(m.convert("HSV").split()[1].getdata()))
            s_sat = sum(list(s.convert("HSV").split()[1].getdata()))
        assert s_sat < m_sat

    def test_configurable_highlight(self, sample_image, tmp_path):
        bright, _ = color_grade(sample_image, tmp_path / "bright", highlight=1.0, seed=42)
        dark, _ = color_grade(sample_image, tmp_path / "dark", highlight=0.7, seed=42)
        with Image.open(bright) as b, Image.open(dark) as d:
            b_avg = sum(list(b.convert("L").getdata())) / (b.width * b.height)
            d_avg = sum(list(d.convert("L").getdata())) / (d.width * d.height)
        assert d_avg < b_avg

    def test_vignette_darkens_corners(self, tmp_path):
        """Use a bright image so corner darkening is measurable despite other adjustments."""
        path = tmp_path / "bright.jpg"
        img = Image.new("RGB", (400, 400), (200, 200, 200))
        img.save(path, format="JPEG")
        img.close()
        no_vig, _ = color_grade(path, tmp_path / "novig", vignette_strength=0.0, micro_rotation_degrees=0.0, seed=42)
        with_vig, _ = color_grade(path, tmp_path / "vig", vignette_strength=0.5, micro_rotation_degrees=0.0, seed=42)
        with Image.open(no_vig) as nv, Image.open(with_vig) as wv:
            nv_corner = sum(nv.getpixel((0, 0))) / 3
            wv_corner = sum(wv.getpixel((0, 0))) / 3
        assert wv_corner < nv_corner

    def test_micro_rotation_changes_pixels(self, sample_image, tmp_path):
        no_rot, _ = color_grade(sample_image, tmp_path / "norot", micro_rotation_degrees=0.0, seed=42)
        with_rot, _ = color_grade(sample_image, tmp_path / "rot", micro_rotation_degrees=2.0, seed=42)
        assert no_rot.stat().st_size != with_rot.stat().st_size

    def test_different_seeds_produce_different_params(self, sample_image, tmp_path):
        _, meta1 = color_grade(sample_image, tmp_path / "s1", seed=100)
        _, meta2 = color_grade(sample_image, tmp_path / "s2", seed=200)
        assert meta1.saturation != meta2.saturation or meta1.jpeg_quality != meta2.jpeg_quality


class TestShotTypeMapping:
    def test_only_medium_and_detail(self):
        assert "中近景" in SHOT_TYPE_TEXT
        assert "细节照" in SHOT_TYPE_TEXT
        assert "完整照" not in SHOT_TYPE_TEXT

    def test_medium_mentions_80_90(self):
        assert "80%" in SHOT_TYPE_TEXT["中近景"]

    def test_detail_shot_mentions_cropping(self):
        assert "裁切" in SHOT_TYPE_TEXT["细节照"]

    def test_shot_type_injected_into_prompt(self):
        card = FactCard.model_validate({"商品名称": "测试"})
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        template = "拍摄类型：{shot_type}\n{product_brief}\n{scene_type}\n{placement}\n{scale_expression}\n{environment_requirements}\n{avoid_content}\n{view_angle_constraint}"
        _, prompt, _ = render_generation_prompt(template, card, card.scenes[0], "细节照")
        assert "极近距离特写" in prompt


class TestScaleExpression:
    def test_with_height(self):
        card = FactCard.model_validate({
            "商品名称": "佛像",
            "尺寸": {"高_cm": 30, "证据": "图片明确文字"},
        })
        text = scale_expression_text(card)
        assert "30cm" in text
        assert "中型" in text

    def test_without_height_uses_default_tier(self):
        card = FactCard.model_validate({"商品名称": "小摆件"})
        text = scale_expression_text(card)
        assert "小型桌面物" in text

    def test_custom_volume_tier(self):
        card = FactCard.model_validate({
            "商品名称": "落地灯",
            "尺寸": {"体量等级": "落地大型物"},
        })
        text = scale_expression_text(card)
        assert "落地大型物" in text


class TestOverlayTextExclusion:
    """Verify that overlay/spec text is excluded from generation prompt."""

    def test_overlay_text_in_ignored_goes_to_avoid_content(self):
        card = FactCard.model_validate({
            "商品名称": "释迦牟尼佛像",
            "画面中需忽略": ["36CM 释迦牟尼 总高36CM", "店铺水印"],
            "文字与规格": [],
        })
        card.scenes = [Scene(scene="书房", placement="木桌上")]
        template = "{product_brief}|{avoid_content}"
        _, prompt, _ = render_generation_prompt(template, card, card.scenes[0], "中近景")
        assert "36CM" in prompt.split("|")[1]
        assert "店铺水印" in prompt.split("|")[1]

    def test_product_body_text_enters_brief(self):
        card = FactCard.model_validate({
            "商品名称": "佛像",
            "文字与规格": [{"内容": "BRAND", "位置": "底座", "置信度": "高"}],
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "BRAND" in brief

    def test_low_confidence_text_excluded_from_brief(self):
        card = FactCard.model_validate({
            "商品名称": "佛像",
            "文字与规格": [{"内容": "模糊文字", "位置": "侧面", "置信度": "低"}],
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "模糊文字" not in brief

    def test_no_text_specs_no_crash(self):
        card = FactCard.model_validate({"商品名称": "纯色花瓶"})
        card.scenes = [Scene(scene="窗台", placement="白色窗台")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "纯色花瓶" in brief


class TestModelBboxCropping:
    """Verify that model-provided bbox is used for cropping when available."""

    @pytest.fixture
    def padded_image(self, tmp_path):
        """800x1000 image with product in center 400x600, white text area in corners."""
        path = tmp_path / "padded.jpg"
        img = Image.new("RGB", (800, 1000), (240, 240, 240))
        for x in range(200, 600):
            for y in range(200, 800):
                img.putpixel((x, y), (80, 50, 30))
        img.save(path, format="JPEG")
        img.close()
        return path

    def test_model_bbox_crops_to_specified_region(self, padded_image):
        bbox = SubjectBbox(x=0.25, y=0.2, w=0.5, h=0.6)
        result = crop_for_medium(padded_image, model_bbox=bbox)
        with Image.open(io.BytesIO(result)) as img:
            assert img.size[0] < 800
            assert img.size[1] < 1000

    def test_trivial_bbox_falls_back_to_heuristic(self, padded_image):
        bbox = SubjectBbox(x=0.0, y=0.0, w=1.0, h=1.0)
        result_with_trivial = crop_for_medium(padded_image, model_bbox=bbox)
        result_without = crop_for_medium(padded_image, model_bbox=None)
        assert result_with_trivial == result_without

    def test_bbox_from_model_returns_none_for_trivial(self):
        img = Image.new("RGB", (800, 1000))
        assert _bbox_from_model(img, None) is None
        assert _bbox_from_model(img, SubjectBbox(x=0, y=0, w=1, h=1)) is None
        img.close()

    def test_bbox_from_model_converts_correctly(self):
        img = Image.new("RGB", (1000, 500))
        bbox = SubjectBbox(x=0.1, y=0.2, w=0.5, h=0.6)
        result = _bbox_from_model(img, bbox)
        assert result == (100, 100, 600, 400)
        img.close()

    def test_detail_crop_uses_model_bbox(self, padded_image):
        bbox = SubjectBbox(x=0.25, y=0.2, w=0.5, h=0.6)
        result = crop_for_detail(padded_image, model_bbox=bbox)
        assert isinstance(result, bytes)
        with Image.open(io.BytesIO(result)) as img:
            assert img.format == "JPEG"


class TestViewAngleTolerance:
    """Verify view_angle_tolerance field and its effect on compress output."""

    def test_field_optional_missing_passes_validation(self):
        card = FactCard.model_validate({"商品名称": "测试商品"})
        assert card.view_angle_tolerance is None

    def test_field_optional_empty_object_passes(self):
        card = FactCard.model_validate({
            "商品名称": "测试",
            "视角容差": {},
        })
        assert card.view_angle_tolerance is not None
        assert card.view_angle_tolerance.level == "低"

    def test_field_full_object_parses(self):
        card = FactCard.model_validate({
            "商品名称": "玻璃花瓶",
            "视角容差": {
                "等级": "高",
                "判断依据": "规则几何圆柱体",
                "允许角度偏移": "自由换角度",
            },
        })
        assert card.view_angle_tolerance.level == "高"
        assert card.view_angle_tolerance.reasoning == "规则几何圆柱体"

    def test_conservative_default_when_missing(self):
        """Missing angle tolerance → treated as 低 in compress."""
        card = FactCard.model_validate({"商品名称": "复杂雕塑"})
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "保持参考图视角" in brief or "不换角度" in brief

    def test_low_tolerance_compress_contains_angle_lock(self):
        card = FactCard.model_validate({
            "商品名称": "镂空灯具",
            "视角容差": {"等级": "低", "判断依据": "镂空多部件"},
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "保持参考图视角" in brief or "不换角度" in brief

    def test_high_tolerance_compress_no_angle_lock(self):
        card = FactCard.model_validate({
            "商品名称": "马克杯",
            "视角容差": {"等级": "高", "判断依据": "规则圆柱"},
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "保持参考图视角" not in brief
        assert "不换角度" not in brief

    def test_medium_tolerance_compress_has_mild_constraint(self):
        card = FactCard.model_validate({
            "商品名称": "台灯",
            "视角容差": {"等级": "中", "判断依据": "略复杂"},
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "轻微视角变化" in brief

    def test_render_prompt_injects_angle_constraint(self):
        card = FactCard.model_validate({
            "商品名称": "镂空灯具",
            "视角容差": {"等级": "低"},
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        template = (
            "{product_brief}|{shot_type}|{scene_type}|{placement}|"
            "{scale_expression}|"
            "{environment_requirements}|{avoid_content}|{view_angle_constraint}"
        )
        _, prompt, _ = render_generation_prompt(template, card, card.scenes[0], "中近景")
        assert "禁止生成背面" in prompt
        assert "不靠转角度" in prompt

    def test_render_prompt_high_tolerance_allows_angle(self):
        card = FactCard.model_validate({
            "商品名称": "马克杯",
            "视角容差": {"等级": "高"},
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        template = (
            "{product_brief}|{shot_type}|{scene_type}|{placement}|"
            "{scale_expression}|"
            "{environment_requirements}|{avoid_content}|{view_angle_constraint}"
        )
        _, prompt, _ = render_generation_prompt(template, card, card.scenes[0], "中近景")
        assert "允许较自由换角度" in prompt


class TestStructuralLockPreservation:
    """Verify structural locks are not squeezed out by main subject description."""

    def test_structural_locks_preserved_in_compress(self):
        card = FactCard.model_validate({
            "商品名称": "佛像",
            "保真锁": [
                "底座为黑色木质方形底座，承托佛像主体",
                "佛像主体金色哑光材质",
                "底座与佛像连接处无缝贴合",
                "佛像面部朝正前方",
                "透明玻璃罩包裹主体",
            ],
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "底座" in brief
        assert "连接" in brief

    def test_structural_locks_prioritized_over_others(self):
        card = FactCard.model_validate({
            "商品名称": "复杂套装",
            "保真锁": [
                "主体为红色",
                "底座为圆形金属材质支架",
                "套装成员2个",
                "透明亚克力连接件",
                "正面有LOGO",
                "整体对称",
                "底座承托主体不得变形",
                "材质分界哑光与镜面各一半",
            ],
        })
        card.scenes = [Scene(scene="桌面", placement="木桌")]
        brief = compress_fact_card(card, card.scenes[0])
        assert "底座" in brief
        assert "材质" in brief
