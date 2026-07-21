from app.schemas import FactCard


def test_valid_image_upload_returns_accessible_original_and_fact_card(client, image_bytes):
    response = client.post(
        "/api/products/upload",
        files={"image": ("product.jpg", image_bytes, "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_id"]
    assert FactCard.model_validate(payload["fact_card"])
    assert payload["image_info"] == {"width": 720, "height": 960, "size_bytes": len(image_bytes)}
    assert client.get(payload["original_image_url"]).status_code == 200


def test_non_image_upload_is_rejected(client):
    response = client.post(
        "/api/products/upload",
        files={"image": ("fake.jpg", b"<html>not an image</html>", "image/jpeg")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_INVALID"


def test_unsupported_content_type_is_rejected(client):
    response = client.post(
        "/api/products/upload",
        files={"image": ("vector.svg", b"<svg></svg>", "image/svg+xml")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "IMAGE_FORMAT_UNSUPPORTED"


def test_oversized_file_is_rejected(client):
    response = client.post(
        "/api/products/upload",
        files={"image": ("large.jpg", b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "IMAGE_TOO_LARGE"


def test_image_with_too_small_dimension_is_rejected(client):
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (511, 900), "white").save(output, format="PNG")
    response = client.post(
        "/api/products/upload",
        files={"image": ("small.png", output.getvalue(), "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_TOO_SMALL"


def test_decompression_bomb_dimensions_are_rejected(client):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    crafted_png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")

    response = client.post(
        "/api/products/upload",
        files={"image": ("huge.png", crafted_png, "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_DIMENSIONS_TOO_LARGE"


def test_upload_file_save_failure_uses_structured_error(client, image_bytes, monkeypatch):
    from PIL import Image

    def fail_save(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    response = client.post(
        "/api/products/upload",
        files={"image": ("product.jpg", image_bytes, "image/jpeg")},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "FILE_SAVE_FAILED"
