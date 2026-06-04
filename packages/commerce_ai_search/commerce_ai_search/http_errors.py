from __future__ import annotations

from .engine import BackendProtocolError, BackendRequestError


PAYLOAD_TOO_LARGE_PREFIXES = (
    "image exceeds",
    "json body exceeds",
    "multipart body exceeds",
)
TRANSIENT_BACKEND_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def input_error_status(message: str) -> int:
    normalized = str(message or "").strip().lower()
    if normalized.startswith(PAYLOAD_TOO_LARGE_PREFIXES):
        return 413
    return 400


def backend_error_status(exc: BackendRequestError) -> int:
    if isinstance(exc, BackendProtocolError):
        return 502
    try:
        status_code = int(exc.status_code or 0)
    except (TypeError, ValueError):
        status_code = 0
    if status_code in TRANSIENT_BACKEND_STATUS_CODES:
        return 503
    return 502


def backend_error_detail(exc: BackendRequestError) -> str:
    service = str(getattr(exc, "service", "") or "backend").strip().lower() or "backend"
    if isinstance(exc, BackendProtocolError):
        return f"{service} backend returned invalid response"
    return f"{service} backend unavailable"
