from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


SERVICE_ENV_FILE_MAX_MODE = 0o640
COLLECTOR_ENV_FILE_MAX_MODE = 0o600


def mode_to_octal(mode: int | None) -> str | None:
    if mode is None:
        return None
    return f"{mode & 0o7777:04o}"


def check_secret_file_permissions(
    path: str | Path | None,
    *,
    name: str,
    required: bool,
    max_mode: int,
) -> dict[str, Any]:
    text = str(path or "").strip()
    if not text:
        return {
            "name": name,
            "ok": not required,
            "message": "secret env file is not configured" if required else "secret env file is not required",
            "path": None,
            "required": required,
            "checked": False,
            "platform": os.name,
            "mode": None,
            "maximum_mode": mode_to_octal(max_mode),
        }

    target = Path(text)
    if not target.exists():
        return {
            "name": name,
            "ok": False,
            "message": "secret env file is missing",
            "path": str(target),
            "required": required,
            "checked": False,
            "platform": os.name,
            "mode": None,
            "maximum_mode": mode_to_octal(max_mode),
        }
    if not target.is_file():
        return {
            "name": name,
            "ok": False,
            "message": "secret env path is not a regular file",
            "path": str(target),
            "required": required,
            "checked": False,
            "platform": os.name,
            "mode": None,
            "maximum_mode": mode_to_octal(max_mode),
        }
    if os.name != "posix":
        return {
            "name": name,
            "ok": True,
            "message": "POSIX secret env file mode check is skipped on this platform",
            "path": str(target),
            "required": required,
            "checked": False,
            "platform": os.name,
            "mode": None,
            "maximum_mode": mode_to_octal(max_mode),
        }

    mode = stat.S_IMODE(target.stat().st_mode)
    disallowed_mode = mode & ~max_mode
    ok = disallowed_mode == 0
    return {
        "name": name,
        "ok": ok,
        "message": "secret env file permissions are restricted"
        if ok
        else "secret env file permissions are too broad",
        "path": str(target),
        "required": required,
        "checked": True,
        "platform": os.name,
        "mode": mode_to_octal(mode),
        "maximum_mode": mode_to_octal(max_mode),
        "disallowed_mode": mode_to_octal(disallowed_mode),
    }
