import base64
import io

import httpx
import pytest
from PIL import Image

from app.errors import AppError
from app.services.http import request_with_retry
from app.services.image_generation.volcengine import VolcengineImageProvider
from app.services.normalize import normalize_fact_card


def test_image_provider_payload_includes_reference_image_model_size_and_prompt(tmp_path):
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"fake-png")
    provider = VolcengineImageProvider(
        api_key="fake-key",
        base_url="https://ark.example/api/v3",
        model="seedream-test",
        output_dir=tmp_path,
    )

    payload = provider.build_request_payload(image_path, "手机实拍商品", "1536x2048")

    assert payload["model"] == "seedream-test"
    assert payload["prompt"] == "手机实拍商品"
    assert payload["size"] == "1536x2048"
    assert payload["response_format"] == "b64_json"
    assert payload["image"] == "data:image/png;base64," + base64.b64encode(b"fake-png").decode()


def test_vision_json_cleaner_removes_markdown_fence_and_surrounding_text():
    raw = '结果如下：\n```json\n{"商品名称":"测试商品"}\n```\n谢谢'

    assert normalize_fact_card(raw) == {"商品名称": "测试商品"}


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(output, format="PNG")
    return output.getvalue()


def test_generated_image_bytes_are_decoded_before_save(tmp_path):
    provider = VolcengineImageProvider(
        "fake", "https://ark.example/api/v3", "model", tmp_path
    )

    output_path = provider.save_validated_image(_png_bytes())

    assert output_path.suffix == ".png"
    with Image.open(output_path) as image:
        image.load()
        assert image.size == (64, 64)


def test_generated_non_image_bytes_are_rejected(tmp_path):
    provider = VolcengineImageProvider(
        "fake", "https://ark.example/api/v3", "model", tmp_path
    )

    with pytest.raises(AppError) as caught:
        provider.save_validated_image(b"<html>not an image</html>")

    assert caught.value.code == "IMAGE_RESPONSE_INVALID"


def test_external_image_download_enforces_streaming_size_limit(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 20, request=request)
    )
    provider = VolcengineImageProvider(
        "fake",
        "https://ark.example/api/v3",
        "model",
        tmp_path,
        max_download_bytes=10,
        transport=transport,
    )

    with pytest.raises(AppError) as caught:
        provider._download("https://images.example/result.png")

    assert caught.value.code == "IMAGE_DOWNLOAD_FAILED"


def test_content_review_rejection_has_distinct_error():
    response = httpx.Response(
        400,
        json={"error": {"code": "SensitiveContent", "message": "blocked"}},
        request=httpx.Request("POST", "https://ark.example/images/generations"),
    )

    with pytest.raises(AppError) as caught:
        request_with_retry(lambda: response, "火山图生图")

    assert caught.value.code == "CONTENT_REVIEW_REJECTED"
