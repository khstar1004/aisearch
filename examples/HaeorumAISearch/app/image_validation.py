from __future__ import annotations

import base64
import binascii
import hashlib
import inspect
import io
import re
from dataclasses import dataclass


SUPPORTED_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MIME_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}
DEFAULT_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
DEFAULT_MULTIPART_FORM_OVERHEAD_BYTES = 1024 * 1024
DEFAULT_JSON_BODY_OVERHEAD_BYTES = 1024 * 1024
DEFAULT_MIN_IMAGE_DIMENSION = 16
DEFAULT_MAX_DECODE_DIMENSION_MULTIPLIER = 8
DEFAULT_MAX_DECODE_PIXELS_MULTIPLIER = 16
DEFAULT_BORDER_TRIM_THRESHOLD = 18
DEFAULT_BORDER_TRIM_PADDING_RATIO = 0.035
DEFAULT_BORDER_TRIM_MIN_REMOVED_RATIO = 0.04
EXIF_ORIENTATION_TAG = 274
MIME_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


@dataclass(frozen=True)
class ValidatedImage:
    data_url: str
    mime_type: str
    size_bytes: int
    sha256: str
    perceptual_hash: str | None = None
    width: int | None = None
    height: int | None = None
    normalized: bool = False
    quality_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageFeatures:
    quality_warnings: tuple[str, ...] = ()
    perceptual_hash: str | None = None


def upload_bytes_to_data_url(raw: bytes, declared_mime_type: str | None = None) -> str:
    mime_type = normalize_declared_mime_type(declared_mime_type) or "application/octet-stream"
    if not MIME_TYPE_PATTERN.fullmatch(mime_type):
        mime_type = "application/octet-stream"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def validate_image_base64(
    value: str,
    max_bytes: int,
    max_dimension: int | None = None,
    min_dimension: int | None = None,
    resize_dimension: int | None = None,
    analyze_features: bool = True,
) -> ValidatedImage:
    mime_type = "application/octet-stream"
    payload = value.strip()
    if payload[:5].lower() == "data:":
        header, _, encoded = payload.partition(",")
        if ";base64" not in header.lower() or not encoded:
            raise ValueError("image_base64 must be a base64 data URL")
        mime_type = normalize_declared_mime_type(header[5:].split(";", 1)[0]) or "application/octet-stream"
        payload = encoded
    if estimated_base64_decoded_size(payload) > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes")
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("image_base64 is not valid base64") from exc
    return validate_image_bytes(
        raw,
        max_bytes=max_bytes,
        declared_mime_type=mime_type,
        max_dimension=max_dimension,
        min_dimension=min_dimension,
        resize_dimension=resize_dimension,
        analyze_features=analyze_features,
    )


def validate_image_bytes(
    raw: bytes,
    max_bytes: int,
    declared_mime_type: str | None = None,
    max_dimension: int | None = None,
    min_dimension: int | None = None,
    resize_dimension: int | None = None,
    analyze_features: bool = True,
) -> ValidatedImage:
    if not raw:
        raise ValueError("image is empty")
    if len(raw) > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes")
    detected = detect_mime_type(raw)
    if detected not in SUPPORTED_MIME_TYPES:
        raise ValueError("only JPG, PNG, and WEBP images are supported")
    declared = normalize_declared_mime_type(declared_mime_type)
    if declared and declared not in {"application/octet-stream", detected}:
        raise ValueError(f"declared image type {declared} does not match {detected}")
    dimensions = verify_decodable_image(raw, min_dimension=min_dimension)
    normalized = False
    if dimensions:
        validate_safe_decode_dimensions(dimensions, max_dimension=max_dimension)
        raw, dimensions, normalized = normalize_image_bytes(
            raw,
            detected,
            max_dimension=effective_normalize_dimension(max_dimension, resize_dimension),
        )
    if normalized:
        normalized = True
        validate_min_image_dimensions(dimensions, min_dimension)
        if len(raw) > max_bytes:
            raise ValueError(f"image exceeds {max_bytes} bytes after preprocessing")
    features = analyze_image_features(raw) if analyze_features else ImageFeatures()
    encoded = base64.b64encode(raw).decode("ascii")
    return ValidatedImage(
        data_url=f"data:{detected};base64,{encoded}",
        mime_type=detected,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        perceptual_hash=features.perceptual_hash,
        width=dimensions[0] if dimensions else None,
        height=dimensions[1] if dimensions else None,
        normalized=normalized,
        quality_warnings=features.quality_warnings,
    )


def effective_normalize_dimension(max_dimension: int | None = None, resize_dimension: int | None = None) -> int | None:
    dimensions = [
        int(value)
        for value in (max_dimension, resize_dimension)
        if value is not None and int(value) > 0
    ]
    return min(dimensions) if dimensions else None


async def read_upload_bytes_limited(
    upload: object,
    max_bytes: int,
    chunk_size: int = DEFAULT_UPLOAD_READ_CHUNK_SIZE,
) -> bytes:
    read = getattr(upload, "read", None)
    if read is None:
        raise ValueError("image must be an uploaded file")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = max_bytes - total + 1
        next_size = min(max(1, chunk_size), remaining)
        chunk = read(next_size)
        if inspect.isawaitable(chunk):
            chunk = await chunk
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        data = bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise ValueError(f"image exceeds {max_bytes} bytes")
        chunks.append(data)
    return b"".join(chunks)


def validate_multipart_content_length(
    content_length: str | None,
    max_image_bytes: int,
    overhead_bytes: int = DEFAULT_MULTIPART_FORM_OVERHEAD_BYTES,
) -> None:
    if content_length in (None, ""):
        raise ValueError("multipart Content-Length is required")
    if max_image_bytes < 1:
        raise ValueError("max_image_bytes must be positive")
    try:
        total_bytes = int(str(content_length).strip())
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer") from exc
    max_body_bytes = max_image_bytes + max(0, overhead_bytes)
    if total_bytes > max_body_bytes:
        raise ValueError(f"multipart body exceeds {max_body_bytes} bytes")


def validate_json_image_content_length(
    content_length: str | None,
    max_image_bytes: int,
    overhead_bytes: int = DEFAULT_JSON_BODY_OVERHEAD_BYTES,
) -> None:
    if content_length in (None, ""):
        return
    if max_image_bytes < 1:
        raise ValueError("max_image_bytes must be positive")
    try:
        total_bytes = int(str(content_length).strip())
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer") from exc
    max_body_bytes = max_base64_json_body_bytes(max_image_bytes, overhead_bytes)
    if total_bytes > max_body_bytes:
        raise ValueError(f"JSON body exceeds {max_body_bytes} bytes")


def max_base64_json_body_bytes(max_image_bytes: int, overhead_bytes: int = DEFAULT_JSON_BODY_OVERHEAD_BYTES) -> int:
    if max_image_bytes < 1:
        raise ValueError("max_image_bytes must be positive")
    base64_bytes = ((max_image_bytes + 2) // 3) * 4
    return base64_bytes + max(0, overhead_bytes)


def detect_mime_type(raw: bytes) -> str | None:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalize_declared_mime_type(value: str | None) -> str | None:
    if not value:
        return None
    mime_type = str(value).split(";", 1)[0].strip().lower()
    if not mime_type:
        return None
    return MIME_TYPE_ALIASES.get(mime_type, mime_type)


def estimated_base64_decoded_size(payload: str) -> int:
    text = str(payload or "").strip()
    if not text:
        return 0
    padding = min(len(text) - len(text.rstrip("=")), 2)
    return max(0, (((len(text) + 3) // 4) * 3) - padding)


def verify_decodable_image(raw: bytes, min_dimension: int | None = None) -> tuple[int, int] | None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("image is damaged or cannot be decoded") from exc
    validate_min_image_dimensions((width, height), min_dimension)
    return width, height


def validate_min_image_dimensions(dimensions: tuple[int, int], min_dimension: int | None = None) -> None:
    minimum = max(1, int(min_dimension or DEFAULT_MIN_IMAGE_DIMENSION))
    width, height = dimensions
    if width < minimum or height < minimum:
        raise ValueError(f"image is too small; minimum dimension is {minimum}px")


def validate_safe_decode_dimensions(dimensions: tuple[int, int], max_dimension: int | None = None) -> None:
    if not max_dimension:
        return
    safe_dimension = max(1, int(max_dimension)) * DEFAULT_MAX_DECODE_DIMENSION_MULTIPLIER
    safe_pixels = max(1, int(max_dimension)) * max(1, int(max_dimension)) * DEFAULT_MAX_DECODE_PIXELS_MULTIPLIER
    width, height = dimensions
    pixels = max(0, int(width)) * max(0, int(height))
    if width > safe_dimension or height > safe_dimension or pixels > safe_pixels:
        raise ValueError(
            "image dimensions are too large; "
            f"maximum safe dimension is {safe_dimension}px and maximum safe pixels is {safe_pixels}"
        )


def normalize_image_bytes(
    raw: bytes,
    mime_type: str,
    max_dimension: int | None = None,
) -> tuple[bytes, tuple[int, int], bool]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as image:
        original_size = image.size
        try:
            orientation = image.getexif().get(EXIF_ORIENTATION_TAG)
        except (AttributeError, OSError, ValueError):
            orientation = None
        image = ImageOps.exif_transpose(image)
        image, border_trimmed = trim_plain_image_border(image)
        normalized = bool(orientation and orientation != 1) or border_trimmed
        if max_dimension and max(image.size) > max_dimension:
            image.thumbnail((max_dimension, max_dimension))
            normalized = True
        if not normalized and image.size == original_size:
            return raw, image.size, False
        output = io.BytesIO()
        if mime_type == "image/jpeg":
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(output, format="JPEG", quality=90, optimize=True)
        elif mime_type == "image/png":
            image.save(output, format="PNG", optimize=True)
        elif mime_type == "image/webp":
            image.save(output, format="WEBP", quality=90, method=6)
        else:
            raise ValueError("only JPG, PNG, and WEBP images are supported")
        return output.getvalue(), image.size, True


def trim_plain_image_border(image):
    width, height = image.size
    if width < DEFAULT_MIN_IMAGE_DIMENSION * 2 or height < DEFAULT_MIN_IMAGE_DIMENSION * 2:
        return image, False
    bbox = plain_background_content_bbox(image)
    if bbox is None:
        return image, False
    left, top, right, bottom = bbox
    content_width = right - left
    content_height = bottom - top
    if content_width < DEFAULT_MIN_IMAGE_DIMENSION or content_height < DEFAULT_MIN_IMAGE_DIMENSION:
        return image, False
    pad = max(2, int(round(min(width, height) * DEFAULT_BORDER_TRIM_PADDING_RATIO)))
    crop_left = max(0, left - pad)
    crop_top = max(0, top - pad)
    crop_right = min(width, right + pad)
    crop_bottom = min(height, bottom + pad)
    removed = max(crop_left, crop_top, width - crop_right, height - crop_bottom)
    min_removed = max(8, int(round(min(width, height) * DEFAULT_BORDER_TRIM_MIN_REMOVED_RATIO)))
    if removed < min_removed:
        return image, False
    if crop_left == 0 and crop_top == 0 and crop_right == width and crop_bottom == height:
        return image, False
    return image.crop((crop_left, crop_top, crop_right, crop_bottom)), True


def plain_background_content_bbox(image):
    from PIL import Image, ImageChops

    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, average_corner_rgba(rgba))
    diff = ImageChops.difference(rgba, background)
    mask = diff.convert("L").point(lambda value: 255 if value > DEFAULT_BORDER_TRIM_THRESHOLD else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return None
    return focused_content_bbox(mask, bbox) or bbox


def focused_content_bbox(mask, bbox):
    left, top, right, bottom = bbox
    crop = mask.crop((left, top, right, bottom))
    width, height = crop.size
    if width < DEFAULT_MIN_IMAGE_DIMENSION or height < DEFAULT_MIN_IMAGE_DIMENSION:
        return None
    data = crop.tobytes()
    row_counts = [
        sum(data[row * width : (row + 1) * width]) // 255
        for row in range(height)
    ]
    bands = foreground_bands(
        row_counts,
        threshold=max(2, int(round(width * 0.01))),
        merge_gap=max(2, int(round(height * 0.01))),
    )
    if len(bands) < 2:
        return None
    largest_gap = max(next_start - end for (_start, end), (next_start, _next_end) in zip(bands, bands[1:]))
    if largest_gap < max(24, int(round(height * 0.08))):
        return None
    total_pixels = sum(row_counts)
    if total_pixels <= 0:
        return None
    image_center = height / 2
    best_band = None
    best_score = -1.0
    best_pixels = 0
    for start, end in bands:
        pixels = sum(row_counts[start:end])
        band_center = (start + end) / 2
        center_bonus = 1.0 - min(abs(band_center - image_center) / max(image_center, 1), 1.0)
        score = pixels * (1.0 + (0.25 * center_bonus))
        if score > best_score:
            best_band = (start, end)
            best_score = score
            best_pixels = pixels
    if best_band is None or best_pixels < total_pixels * 0.3:
        return None
    start, end = best_band
    if end - start >= height * 0.85:
        return None
    global_top = top + start
    global_bottom = top + end
    band_bbox = mask.crop((left, global_top, right, global_bottom)).getbbox()
    if band_bbox is None:
        return None
    band_left, band_top, band_right, band_bottom = band_bbox
    focused = (
        left + band_left,
        global_top + band_top,
        left + band_right,
        global_top + band_bottom,
    )
    focused_width = focused[2] - focused[0]
    focused_height = focused[3] - focused[1]
    if focused_width < DEFAULT_MIN_IMAGE_DIMENSION or focused_height < DEFAULT_MIN_IMAGE_DIMENSION:
        return None
    return focused


def foreground_bands(counts: list[int], *, threshold: int, merge_gap: int) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    start = None
    for index, count in enumerate(counts):
        if count >= threshold:
            if start is None:
                start = index
        elif start is not None:
            bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, len(counts)))
    if not bands:
        return []
    merged = [bands[0]]
    for start, end in bands[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end <= merge_gap:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return merged


def average_corner_rgba(image, sample_size: int = 8) -> tuple[int, int, int, int]:
    width, height = image.size
    sample = max(1, min(sample_size, width, height))
    boxes = (
        (0, 0, sample, sample),
        (width - sample, 0, width, sample),
        (0, height - sample, sample, height),
        (width - sample, height - sample, width, height),
    )
    totals = [0, 0, 0, 0]
    count = 0
    for box in boxes:
        data = image.crop(box).tobytes()
        for offset in range(0, len(data), 4):
            totals[0] += data[offset]
            totals[1] += data[offset + 1]
            totals[2] += data[offset + 2]
            totals[3] += data[offset + 3]
            count += 1
    if count <= 0:
        return (255, 255, 255, 255)
    return tuple(int(round(total / count)) for total in totals)  # type: ignore[return-value]


def analyze_image_quality(raw: bytes) -> tuple[str, ...]:
    return analyze_image_features(raw).quality_warnings


def analyze_image_features(raw: bytes, hash_size: int = 8) -> ImageFeatures:
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return ImageFeatures()
    warnings = []
    perceptual_hash = None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width / height > 4 or height / width > 4:
                warnings.append("extreme_aspect_ratio")
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                alpha = image.convert("RGBA").getchannel("A")
                histogram = alpha.histogram()
                transparent_pixels = sum(histogram[:245])
                if transparent_pixels / max(width * height, 1) > 0.25:
                    warnings.append("transparent_or_cutout_background")
            grayscale = image.convert("L")
            stat = ImageStat.Stat(grayscale)
            if stat.stddev and stat.stddev[0] < 8:
                warnings.append("low_contrast_or_plain_background")
            thumbnail = grayscale.resize((hash_size, hash_size))
            pixels = thumbnail.tobytes()
            if pixels:
                average = sum(pixels) / len(pixels)
                bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
                perceptual_hash = f"{int(bits, 2):0{hash_size * hash_size // 4}x}"
    except Exception:
        return ImageFeatures()
    return ImageFeatures(quality_warnings=tuple(warnings), perceptual_hash=perceptual_hash)


def compute_average_hash(raw: bytes, hash_size: int = 8) -> str | None:
    return analyze_image_features(raw, hash_size=hash_size).perceptual_hash
