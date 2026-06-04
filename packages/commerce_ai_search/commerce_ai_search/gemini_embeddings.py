from __future__ import annotations

import base64
import datetime
import email.utils
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
import threading

from .image_validation import detect_mime_type, normalize_declared_mime_type
from .url_safety import open_public_http_request, safe_absolute_http_url


DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_GEMINI_EMBEDDING_DIMENSIONS = 1536
DEFAULT_GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_TIMEOUT_SECONDS = 30.0
DEFAULT_GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_GEMINI_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFAULT_GEMINI_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_GEMINI_USER_AGENT = "haeorum-ai-search-gemini-embedding-proxy/1.0"
DEFAULT_GEMINI_PROVIDER_RETRY_COUNT = 2
DEFAULT_GEMINI_PROVIDER_RETRY_DELAY_SECONDS = 0.5
DEFAULT_GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS = 5.0
GEMINI_ADC_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language.retriever",
)
JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": DEFAULT_GEMINI_USER_AGENT,
}


@dataclass(frozen=True)
class GeminiEmbeddingSettings:
    api_key: str
    model: str = DEFAULT_GEMINI_EMBEDDING_MODEL
    dimensions: int = DEFAULT_GEMINI_EMBEDDING_DIMENSIONS
    api_base_url: str = DEFAULT_GEMINI_API_BASE_URL
    timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS
    image_download_timeout_seconds: float = DEFAULT_GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS
    max_image_bytes: int = DEFAULT_GEMINI_MAX_IMAGE_BYTES
    max_response_bytes: int = DEFAULT_GEMINI_MAX_RESPONSE_BYTES
    auth_mode: str = "auto"
    quota_project: str | None = None
    provider_retry_count: int = DEFAULT_GEMINI_PROVIDER_RETRY_COUNT
    provider_retry_delay_seconds: float = DEFAULT_GEMINI_PROVIDER_RETRY_DELAY_SECONDS
    provider_retry_max_delay_seconds: float = DEFAULT_GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS


@dataclass(frozen=True)
class GeminiEmbeddingStats:
    inputs: int
    elapsed_ms: float
    provider_elapsed_ms: float
    image_download_elapsed_ms: float
    image_downloads: int
    text_inputs: int
    image_inputs: int


class GeminiProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


Transport = Callable[[str, dict[str, Any], GeminiEmbeddingSettings], dict[str, Any]]
_ADC_LOCK = threading.RLock()
_ADC_CREDENTIALS: Any = None
_ADC_PROJECT_ID: str | None = None


def load_gemini_embedding_settings_from_env() -> GeminiEmbeddingSettings:
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        or ""
    ).strip()
    return GeminiEmbeddingSettings(
        api_key=api_key,
        model=os.environ.get("GEMINI_EMBEDDING_MODEL", DEFAULT_GEMINI_EMBEDDING_MODEL).strip()
        or DEFAULT_GEMINI_EMBEDDING_MODEL,
        dimensions=int_env("GEMINI_EMBEDDING_DIMENSIONS", DEFAULT_GEMINI_EMBEDDING_DIMENSIONS),
        api_base_url=(
            os.environ.get("GEMINI_API_BASE_URL", DEFAULT_GEMINI_API_BASE_URL).strip().rstrip("/")
            or DEFAULT_GEMINI_API_BASE_URL
        ),
        timeout_seconds=float_env("GEMINI_EMBEDDING_TIMEOUT_SECONDS", DEFAULT_GEMINI_TIMEOUT_SECONDS),
        image_download_timeout_seconds=float_env(
            "GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
            DEFAULT_GEMINI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS,
        ),
        max_image_bytes=int_env("GEMINI_MAX_IMAGE_BYTES", DEFAULT_GEMINI_MAX_IMAGE_BYTES),
        max_response_bytes=int_env("GEMINI_MAX_RESPONSE_BYTES", DEFAULT_GEMINI_MAX_RESPONSE_BYTES),
        auth_mode=(
            os.environ.get("GEMINI_AUTH_MODE", "auto").strip().lower()
            or "auto"
        ),
        quota_project=(
            os.environ.get("GEMINI_QUOTA_PROJECT")
            or os.environ.get("GEMINI_GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
            or None
        ),
        provider_retry_count=int_env(
            "GEMINI_PROVIDER_RETRY_COUNT",
            DEFAULT_GEMINI_PROVIDER_RETRY_COUNT,
            minimum=0,
        ),
        provider_retry_delay_seconds=float_env(
            "GEMINI_PROVIDER_RETRY_DELAY_SECONDS",
            DEFAULT_GEMINI_PROVIDER_RETRY_DELAY_SECONDS,
            minimum=0.0,
        ),
        provider_retry_max_delay_seconds=float_env(
            "GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS",
            DEFAULT_GEMINI_PROVIDER_RETRY_MAX_DELAY_SECONDS,
        ),
    )


def int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def float_env(name: str, default: float, *, minimum: float = 0.001) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    return value


def gemini_model_resource(model: str) -> str:
    text = str(model or "").strip()
    if not text:
        text = DEFAULT_GEMINI_EMBEDDING_MODEL
    return text if text.startswith("models/") else f"models/{text}"


def embed_inputs_with_gemini(
    inputs: list[dict[str, Any]],
    *,
    settings: GeminiEmbeddingSettings,
    prompt: str | None = None,
    transport: Transport | None = None,
) -> tuple[list[list[float]], GeminiEmbeddingStats]:
    started = time.perf_counter()
    validate_gemini_auth_settings(settings)
    if not inputs:
        return [], GeminiEmbeddingStats(0, 0.0, 0.0, 0.0, 0, 0, 0)
    transport = transport or request_gemini_embedding
    request_payloads: list[dict[str, Any]] = []
    image_download_elapsed_ms = 0.0
    image_downloads = 0
    text_inputs = 0
    image_inputs = 0
    for item in inputs:
        content, kind, download_ms = gemini_content_for_input(
            item,
            settings=settings,
            prompt=prompt,
        )
        if kind == "text":
            text_inputs += 1
        else:
            image_inputs += 1
            image_download_elapsed_ms += download_ms
            if download_ms > 0:
                image_downloads += 1
        request_payloads.append(build_embed_content_request(settings, content, kind=kind, source=item, prompt=prompt))
    request_started = time.perf_counter()
    if len(request_payloads) == 1:
        response = transport("embedContent", request_payloads[0], settings)
        embeddings = [extract_embedding_values(response, settings.dimensions)]
    else:
        response = transport(
            "batchEmbedContents",
            {
                "model": gemini_model_resource(settings.model),
                "requests": request_payloads,
            },
            settings,
        )
        embeddings = extract_batch_embedding_values(response, settings.dimensions, len(request_payloads))
    provider_elapsed_ms = (time.perf_counter() - request_started) * 1000
    elapsed_ms = (time.perf_counter() - started) * 1000
    return embeddings, GeminiEmbeddingStats(
        inputs=len(inputs),
        elapsed_ms=round(elapsed_ms, 3),
        provider_elapsed_ms=round(provider_elapsed_ms, 3),
        image_download_elapsed_ms=round(image_download_elapsed_ms, 3),
        image_downloads=image_downloads,
        text_inputs=text_inputs,
        image_inputs=image_inputs,
    )


def normalize_embedding_inputs(inputs: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            raise ValueError(f"inputs[{index}] must be an object")
        text = str(item.get("text") or "").strip()
        image = str(item.get("image") or "").strip()
        if text and image:
            raise ValueError(f"inputs[{index}] must contain only one of text or image")
        if text:
            normalized.append({"text": text})
        elif image:
            normalized.append({"image": image})
        else:
            raise ValueError(f"inputs[{index}] must contain text or image")
    return normalized


def build_embed_content_request(
    settings: GeminiEmbeddingSettings,
    content: dict[str, Any],
    *,
    kind: str,
    source: dict[str, Any],
    prompt: str | None,
) -> dict[str, Any]:
    return {
        "model": gemini_model_resource(settings.model),
        "content": content,
        "embedContentConfig": {
            "outputDimensionality": settings.dimensions,
            "taskType": infer_task_type(kind=kind, source=source, prompt=prompt),
            "autoTruncate": True,
        },
    }


def infer_task_type(*, kind: str, source: dict[str, Any], prompt: str | None) -> str:
    if str(prompt or "").strip():
        return "RETRIEVAL_QUERY"
    image = str(source.get("image") or "").strip()
    if kind == "image" and image.lower().startswith("data:"):
        return "RETRIEVAL_QUERY"
    return "RETRIEVAL_DOCUMENT"


def gemini_content_for_input(
    item: dict[str, Any],
    *,
    settings: GeminiEmbeddingSettings,
    prompt: str | None = None,
) -> tuple[dict[str, Any], str, float]:
    text = str(item.get("text") or "").strip()
    image = str(item.get("image") or "").strip()
    if text and image:
        raise ValueError("each embedding input must contain only one of text or image")
    if text:
        return {"parts": [{"text": text}]}, "text", 0.0
    if image:
        started = time.perf_counter()
        inline_data = inline_image_data(image, settings=settings)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {"parts": [{"inline_data": inline_data}]}, "image", elapsed_ms
    raise ValueError("each embedding input must contain text or image")


def merge_prompt_and_text(prompt: str | None, text: str) -> str:
    prompt_text = str(prompt or "").strip()
    clean_text = str(text or "").strip()
    if not prompt_text:
        return clean_text
    return f"{prompt_text}\n{clean_text}"


def inline_image_data(value: str, *, settings: GeminiEmbeddingSettings) -> dict[str, str]:
    if value.lower().startswith("data:"):
        mime_type, raw = image_bytes_from_data_url(value, max_bytes=settings.max_image_bytes)
        return {"mime_type": mime_type, "data": base64.b64encode(raw).decode("ascii")}
    absolute_url = safe_absolute_http_url(value)
    if not absolute_url:
        raise ValueError("image must be a data URL or public HTTP(S) URL")
    mime_type, raw = download_image_bytes(
        absolute_url,
        max_bytes=settings.max_image_bytes,
        timeout=settings.image_download_timeout_seconds,
    )
    return {"mime_type": mime_type, "data": base64.b64encode(raw).decode("ascii")}


def image_bytes_from_data_url(value: str, *, max_bytes: int) -> tuple[str, bytes]:
    header, separator, encoded = str(value or "").partition(",")
    if not separator or not encoded or not header.lower().startswith("data:") or ";base64" not in header.lower():
        raise ValueError("image data URL must be base64 encoded")
    declared = normalize_declared_mime_type(header[5:].split(";", 1)[0])
    estimated = ((len(encoded.strip()) + 3) // 4) * 3
    if estimated > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("image data URL is not valid base64") from exc
    mime_type = validate_image_payload(raw, declared_mime_type=declared, max_bytes=max_bytes)
    return mime_type, raw


def download_image_bytes(url: str, *, max_bytes: int, timeout: float) -> tuple[str, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/jpeg,image/png,image/webp,*/*;q=0.1",
            "User-Agent": DEFAULT_GEMINI_USER_AGENT,
        },
        method="GET",
    )
    with open_public_http_request(request, timeout=timeout) as response:
        raw = read_limited(response, max_bytes=max_bytes)
        declared = normalize_declared_mime_type(response.headers.get("Content-Type"))
    mime_type = validate_image_payload(raw, declared_mime_type=declared, max_bytes=max_bytes)
    return mime_type, raw


def read_limited(response: Any, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"image exceeds {max_bytes} bytes")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def validate_image_payload(raw: bytes, *, declared_mime_type: str | None, max_bytes: int) -> str:
    if not raw:
        raise ValueError("image is empty")
    if len(raw) > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes")
    detected = detect_mime_type(raw)
    if detected not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("only JPG, PNG, and WEBP images are supported")
    # Some supplier/CDN image URLs return a stale Content-Type header. Gemini needs
    # the actual image MIME, so prefer magic-byte detection once the payload is valid.
    return detected


def request_gemini_embedding(method: str, payload: dict[str, Any], settings: GeminiEmbeddingSettings) -> dict[str, Any]:
    attempts = max(0, int(settings.provider_retry_count)) + 1
    for attempt in range(attempts):
        try:
            return request_gemini_embedding_once(method, payload, settings)
        except GeminiProviderError as exc:
            if attempt >= attempts - 1 or not transient_gemini_provider_error(exc):
                raise
            sleep_seconds = gemini_provider_retry_sleep_seconds(exc, attempt, settings)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    raise RuntimeError("unreachable Gemini retry state")


def request_gemini_embedding_once(
    method: str,
    payload: dict[str, Any],
    settings: GeminiEmbeddingSettings,
) -> dict[str, Any]:
    if method not in {"embedContent", "batchEmbedContents"}:
        raise ValueError(f"unsupported Gemini embedding method: {method}")
    url = f"{settings.api_base_url}/{gemini_model_resource(settings.model)}:{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=gemini_request_headers(settings),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            raw = read_gemini_response_limited(response, max_bytes=settings.max_response_bytes)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        retry_after = parse_retry_after_seconds(exc.headers.get("Retry-After"))
        raise GeminiProviderError(
            f"Gemini embedding API returned HTTP {exc.code}: {summarize_error_body(body)}",
            status_code=int(exc.code),
            retry_after_seconds=retry_after,
        ) from exc
    except urllib.error.URLError as exc:
        raise GeminiProviderError(f"Gemini embedding API request failed: {exc.reason}") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Gemini embedding API returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Gemini embedding API response must be an object")
    return data


def read_gemini_response_limited(response: Any, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    limit = max(1, int(max_bytes))
    while True:
        chunk = response.read(min(64 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise GeminiProviderError(
                f"Gemini embedding API response exceeds {limit} bytes",
                status_code=502,
            )
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def transient_gemini_provider_error(exc: GeminiProviderError) -> bool:
    if "response exceeds" in str(exc):
        return False
    if exc.status_code is None:
        return True
    return int(exc.status_code) in {408, 429, 500, 502, 503, 504}


def gemini_provider_retry_sleep_seconds(
    exc: GeminiProviderError,
    attempt: int,
    settings: GeminiEmbeddingSettings,
) -> float:
    base = max(0.0, float(settings.provider_retry_delay_seconds))
    sleep_seconds = base * (2 ** max(0, int(attempt)))
    retry_after = exc.retry_after_seconds
    if retry_after is not None:
        sleep_seconds = max(sleep_seconds, max(0.0, float(retry_after)))
    return min(sleep_seconds, max(0.001, float(settings.provider_retry_max_delay_seconds)))


def parse_retry_after_seconds(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
        seconds = (retry_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def validate_gemini_auth_settings(settings: GeminiEmbeddingSettings) -> None:
    mode = normalize_auth_mode(settings.auth_mode, settings.api_key)
    if mode == "api_key" and not settings.api_key:
        raise ValueError("GEMINI_API_KEY is required")
    if mode == "adc" and not settings.quota_project:
        raise ValueError("GEMINI_QUOTA_PROJECT or GOOGLE_CLOUD_PROJECT is required when GEMINI_AUTH_MODE=adc")


def normalize_auth_mode(auth_mode: str, api_key: str | None) -> str:
    mode = str(auth_mode or "auto").strip().lower()
    if mode in {"api-key", "api_key", "apikey", "key"}:
        return "api_key"
    if mode in {"adc", "oauth", "application_default_credentials"}:
        return "adc"
    if mode == "auto":
        return "api_key" if str(api_key or "").strip() else "adc"
    raise ValueError("GEMINI_AUTH_MODE must be one of: auto, api_key, adc")


def gemini_request_headers(settings: GeminiEmbeddingSettings) -> dict[str, str]:
    headers = dict(JSON_HEADERS)
    mode = normalize_auth_mode(settings.auth_mode, settings.api_key)
    if mode == "api_key":
        headers["x-goog-api-key"] = settings.api_key
        return headers
    token, project_id = adc_access_token(settings)
    headers["Authorization"] = f"Bearer {token}"
    headers["x-goog-user-project"] = str(settings.quota_project or project_id or "").strip()
    return headers


def adc_access_token(settings: GeminiEmbeddingSettings) -> tuple[str, str | None]:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise RuntimeError("Install google-auth or run `python -m pip install -r requirements.txt` to use ADC") from exc
    global _ADC_CREDENTIALS, _ADC_PROJECT_ID
    with _ADC_LOCK:
        if _ADC_CREDENTIALS is None:
            _ADC_CREDENTIALS, _ADC_PROJECT_ID = google.auth.default(scopes=list(GEMINI_ADC_SCOPES))
            quota_project = str(settings.quota_project or "").strip()
            if quota_project and hasattr(_ADC_CREDENTIALS, "with_quota_project"):
                _ADC_CREDENTIALS = _ADC_CREDENTIALS.with_quota_project(quota_project)
        request = google.auth.transport.requests.Request()
        if not getattr(_ADC_CREDENTIALS, "valid", False):
            _ADC_CREDENTIALS.refresh(request)
        token = str(getattr(_ADC_CREDENTIALS, "token", "") or "").strip()
        if not token:
            raise RuntimeError("ADC did not return an access token")
        return token, _ADC_PROJECT_ID


def summarize_error_body(body: str) -> str:
    text = " ".join(str(body or "").split())
    if len(text) > 500:
        return text[:500] + "..."
    return text


def extract_embedding_values(data: dict[str, Any], expected_dimensions: int) -> list[float]:
    embedding = data.get("embedding")
    if not isinstance(embedding, dict):
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            embedding = embeddings[0]
    values = embedding.get("values") if isinstance(embedding, dict) else None
    if not isinstance(values, list):
        raise RuntimeError("Gemini embedding response must contain embedding.values")
    if len(values) != expected_dimensions:
        raise RuntimeError(
            f"Gemini embedding response has {len(values)} dimensions; expected {expected_dimensions}"
        )
    vector: list[float] = []
    for index, value in enumerate(values):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Gemini embedding value {index} is not numeric") from exc
        if not math.isfinite(number):
            raise RuntimeError(f"Gemini embedding value {index} is not finite")
        vector.append(number)
    return vector


def extract_batch_embedding_values(
    data: dict[str, Any],
    expected_dimensions: int,
    expected_count: int,
) -> list[list[float]]:
    embeddings = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(embeddings, list):
        raise RuntimeError("Gemini batch embedding response must contain embeddings")
    if len(embeddings) != expected_count:
        raise RuntimeError(f"Gemini batch embedding response returned {len(embeddings)} embeddings; expected {expected_count}")
    return [
        extract_embedding_values({"embedding": embedding}, expected_dimensions)
        for embedding in embeddings
    ]
