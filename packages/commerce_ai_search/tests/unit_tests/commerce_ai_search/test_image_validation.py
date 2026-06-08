from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image

from commerce_ai_search.image_validation import (
    max_base64_json_body_bytes,
    read_upload_bytes_limited,
    upload_bytes_to_data_url,
    validate_image_base64,
    validate_image_bytes,
    validate_json_image_content_length,
    validate_multipart_content_length,
)


def image_bytes(width: int, height: int, image_format: str = "PNG", color: str = "red") -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (width, height), color=color) as image:
        image.save(output, format=image_format)
    return output.getvalue()


def exif_oriented_jpeg_bytes(width: int, height: int, orientation: int) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (width, height), color="blue") as image:
        exif = image.getexif()
        exif[274] = orientation
        image.save(output, format="JPEG", exif=exif.tobytes())
    return output.getvalue()


def animated_webp_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    frames = [
        Image.new("RGB", (width, height), color="red"),
        Image.new("RGB", (width, height), color="blue"),
    ]
    try:
        frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:], duration=50, loop=0)
    except (OSError, ValueError) as exc:
        pytest.skip(f"Pillow was built without animated WebP support: {exc}")
    return output.getvalue()


def cmyk_jpeg_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    with Image.new("CMYK", (width, height), color=(0, 128, 128, 0)) as image:
        image.save(output, format="JPEG")
    return output.getvalue()


def large_exif_jpeg_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (width, height), color="green") as image:
        exif = image.getexif()
        exif[37510] = b"ASCII\x00\x00\x00" + (b"x" * 50_000)
        image.save(output, format="JPEG", exif=exif.tobytes())
    return output.getvalue()


def data_url(raw: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"


def test_validate_image_base64_accepts_supported_data_url_and_normalizes_alias():
    raw = image_bytes(32, 32, "JPEG")

    image = validate_image_base64(data_url(raw, "image/jpg"), max_bytes=1024 * 1024, min_dimension=16)

    assert image.mime_type == "image/jpeg"
    assert image.size_bytes == len(raw)
    assert image.width == 32
    assert image.height == 32
    assert image.sha256


def test_validate_image_base64_rejects_invalid_payload_and_mime_spoofing():
    png = image_bytes(32, 32, "PNG")

    with pytest.raises(ValueError, match="not valid base64"):
        validate_image_base64("not-base64", max_bytes=1024)

    with pytest.raises(ValueError, match="does not match"):
        validate_image_base64(data_url(png, "image/jpeg"), max_bytes=1024 * 1024)


def test_validate_image_bytes_rejects_small_and_oversized_images():
    raw = image_bytes(8, 32, "PNG")

    with pytest.raises(ValueError, match="minimum dimension is 16px"):
        validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16)

    with pytest.raises(ValueError, match="image exceeds 10 bytes"):
        validate_image_bytes(raw, max_bytes=10)


def test_validate_image_bytes_resizes_large_query_images_and_records_quality_warnings():
    raw = image_bytes(500, 100, "PNG")

    image = validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16, resize_dimension=128)

    assert image.normalized is True
    assert image.width <= 128
    assert image.height <= 128
    assert "extreme_aspect_ratio" in image.quality_warnings
    assert image.perceptual_hash


def test_validate_image_bytes_can_skip_query_feature_analysis(monkeypatch):
    raw = image_bytes(64, 64, "PNG")

    def fail_analyze_image_features(*_args, **_kwargs):
        raise AssertionError("feature analysis should be skipped")

    monkeypatch.setattr("commerce_ai_search.image_validation.analyze_image_features", fail_analyze_image_features)

    image = validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16, analyze_features=False)

    assert image.width == 64
    assert image.height == 64
    assert image.perceptual_hash is None
    assert image.quality_warnings == ()


@pytest.mark.parametrize(
    "payload",
    [
        b"\x89PNG\r\n\x1a\nnot-a-valid-png",
        b"\xff\xd8\xffnot-a-valid-jpeg",
        b"RIFF\x10\x00\x00\x00WEBPnot-a-valid-webp",
    ],
)
def test_validate_image_bytes_rejects_damaged_supported_image_payload(payload):
    with pytest.raises(ValueError, match="image is damaged or cannot be decoded"):
        validate_image_bytes(payload, max_bytes=1024 * 1024, min_dimension=16)


def test_validate_image_bytes_normalizes_exif_orientation():
    raw = exif_oriented_jpeg_bytes(40, 20, orientation=6)

    image = validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16)

    assert image.mime_type == "image/jpeg"
    assert image.normalized is True
    assert (image.width, image.height) == (20, 40)
    assert image.size_bytes != len(raw)


def test_validate_image_bytes_accepts_animated_webp_first_frame_metadata():
    raw = animated_webp_bytes(32, 32)

    image = validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16)

    assert image.mime_type == "image/webp"
    assert image.width == 32
    assert image.height == 32
    assert image.sha256


def test_validate_image_bytes_accepts_cmyk_jpeg_without_crashing_quality_analysis():
    raw = cmyk_jpeg_bytes(32, 32)

    image = validate_image_bytes(raw, max_bytes=1024 * 1024, min_dimension=16)

    assert image.mime_type == "image/jpeg"
    assert image.width == 32
    assert image.height == 32
    assert image.perceptual_hash


def test_validate_image_bytes_accepts_large_exif_metadata_within_byte_limit():
    raw = large_exif_jpeg_bytes(32, 32)

    image = validate_image_bytes(raw, max_bytes=len(raw) + 128, min_dimension=16)

    assert image.mime_type == "image/jpeg"
    assert image.width == 32
    assert image.height == 32
    assert image.size_bytes == len(raw)
    assert image.sha256


def test_upload_bytes_to_data_url_sanitizes_invalid_declared_mime_type():
    raw = image_bytes(16, 16, "PNG")

    url = upload_bytes_to_data_url(raw, "image/png; charset=utf-8")
    invalid_url = upload_bytes_to_data_url(raw, "bad mime")

    assert url.startswith("data:image/png;base64,")
    assert invalid_url.startswith("data:application/octet-stream;base64,")


def test_content_length_guards_reject_large_or_invalid_bodies():
    validate_json_image_content_length(str(max_base64_json_body_bytes(9)), max_image_bytes=9)
    validate_multipart_content_length(str(10), max_image_bytes=9, overhead_bytes=1)

    with pytest.raises(ValueError, match="Content-Length must be an integer"):
        validate_json_image_content_length("abc", max_image_bytes=9)

    with pytest.raises(ValueError, match="multipart Content-Length is required"):
        validate_multipart_content_length(None, max_image_bytes=9)

    with pytest.raises(ValueError, match="JSON body exceeds"):
        validate_json_image_content_length(str(max_base64_json_body_bytes(9) + 1), max_image_bytes=9)

    with pytest.raises(ValueError, match="multipart body exceeds"):
        validate_multipart_content_length(str(11), max_image_bytes=9, overhead_bytes=1)


def test_read_upload_bytes_limited_streams_until_limit():
    class FakeUpload:
        def __init__(self, chunks: list[bytes]):
            self.chunks = list(chunks)

        async def read(self, _size: int) -> bytes:
            if not self.chunks:
                return b""
            return self.chunks.pop(0)

    raw = asyncio.run(read_upload_bytes_limited(FakeUpload([b"abc", b"def"]), max_bytes=6, chunk_size=2))
    assert raw == b"abcdef"

    with pytest.raises(ValueError, match="image exceeds 5 bytes"):
        asyncio.run(read_upload_bytes_limited(FakeUpload([b"abc", b"def"]), max_bytes=5, chunk_size=2))
