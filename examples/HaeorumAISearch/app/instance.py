from __future__ import annotations

import hashlib
import os
import platform
import re


API_INSTANCE_HEADER = "X-Haeorum-API-Instance"
_SAFE_INSTANCE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")


def api_instance_id() -> str:
    explicit = str(os.getenv("HAEORUM_API_INSTANCE_ID") or "").strip()
    if explicit:
        normalized = _SAFE_INSTANCE_ID.sub("-", explicit).strip("-")
        if normalized:
            return normalized[:128]
    source = platform.node() or os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME") or "unknown-api-host"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"hai-{digest}"
