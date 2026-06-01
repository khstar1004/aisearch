from __future__ import annotations

import json
from typing import Any


async def read_request_body_limited(request: Any, max_bytes: int, label: str = "request body") -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        data = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise ValueError(f"{label} exceeds {max_bytes} bytes")
        chunks.append(data)
    return b"".join(chunks)


async def read_json_object_limited(request: Any, max_bytes: int | None = None) -> dict[str, Any]:
    try:
        if max_bytes is None:
            raw = await request.body()
        else:
            raw = await read_request_body_limited(request, max_bytes=max_bytes, label="JSON body")
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data
