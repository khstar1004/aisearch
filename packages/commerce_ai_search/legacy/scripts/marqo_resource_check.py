from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, validate_marqo_url_value
from app.engine import IMAGE_FIELD, QWEN_IMAGE_VECTOR_FIELD, QWEN_TEXT_VECTOR_FIELD, TEXT_FIELDS


SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
DEFAULT_MAX_CPU_PERCENT = 90.0
DEFAULT_MAX_MEMORY_PERCENT = 85.0
DEFAULT_STORAGE_CONTAINER = "vespa"
DEFAULT_STORAGE_PATH = "/opt/vespa/var"
DEFAULT_MAX_STORAGE_PERCENT = 85.0
DEFAULT_MIN_STORAGE_AVAILABLE_GB = 10.0
QWEN_IMAGE_PROBE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def request_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "data": data,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": raw,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "data": data,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": raw,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def collect_docker_stats(container: str, timeout: int) -> dict[str, Any]:
    if not container:
        return {"ok": False, "error": "container name is required"}
    command = ["docker", "stats", "--no-stream", "--format", "{{json .}}", container]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"ok": False, "container": container, "error": str(exc), "command": " ".join(command)}
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        return {
            "ok": False,
            "container": container,
            "exit_code": completed.returncode,
            "error": completed.stderr.strip() or stdout or "docker stats failed",
            "command": " ".join(command),
        }
    try:
        raw = json.loads(stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {"ok": False, "container": container, "error": f"invalid docker stats JSON: {exc}", "raw": stdout}
    parsed = parse_docker_stats(raw)
    return {"ok": True, "container": container, "raw": raw, **parsed}


def collect_storage_usage(container: str, path: str, timeout: int) -> dict[str, Any]:
    if not container:
        return {"ok": False, "error": "storage container name is required"}
    if not path:
        return {"ok": False, "container": container, "error": "storage path is required"}
    command = ["docker", "exec", container, "df", "-B1", "-P", path]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {
            "ok": False,
            "container": container,
            "path": path,
            "error": str(exc),
            "command": " ".join(command),
        }
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        return {
            "ok": False,
            "container": container,
            "path": path,
            "exit_code": completed.returncode,
            "error": completed.stderr.strip() or stdout or "docker exec df failed",
            "command": " ".join(command),
        }
    parsed = parse_df_output(stdout)
    return {"ok": parsed.get("ok") is True, "container": container, "path": path, **parsed}


def parse_docker_stats(raw: dict[str, Any]) -> dict[str, Any]:
    memory_usage, memory_limit = parse_memory_usage(str(raw.get("MemUsage") or ""))
    return {
        "name": raw.get("Name") or raw.get("Container") or raw.get("ID"),
        "cpu_percent": parse_percent(raw.get("CPUPerc")),
        "memory_usage_bytes": memory_usage,
        "memory_limit_bytes": memory_limit,
        "memory_percent": parse_percent(raw.get("MemPerc")),
        "network_io": raw.get("NetIO"),
        "block_io": raw.get("BlockIO"),
        "pids": parse_int(raw.get("PIDs")),
    }


def parse_df_output(output: str) -> dict[str, Any]:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return {"ok": False, "error": "df output is incomplete", "raw": output}
    values = lines[-1].split()
    if len(values) < 6:
        return {"ok": False, "error": "df output row is incomplete", "raw": output}
    total_bytes = parse_int(values[1])
    used_bytes = parse_int(values[2])
    available_bytes = parse_int(values[3])
    used_percent = parse_percent(values[4])
    problems = []
    if total_bytes is None:
        problems.append("total_bytes")
    if used_bytes is None:
        problems.append("used_bytes")
    if available_bytes is None:
        problems.append("available_bytes")
    if used_percent is None:
        problems.append("used_percent")
    return {
        "ok": not problems,
        "filesystem": values[0],
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "available_bytes": available_bytes,
        "used_percent": used_percent,
        "mounted_on": values[5],
        "problems": problems,
        "raw": output,
    }


def parse_memory_usage(value: str) -> tuple[int | None, int | None]:
    if "/" not in value:
        return parse_size_bytes(value), None
    left, right = value.split("/", 1)
    return parse_size_bytes(left), parse_size_bytes(right)


def parse_size_bytes(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = SIZE_UNITS.get(unit)
    if multiplier is None:
        return None
    return int(number * multiplier)


def parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().rstrip("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def resource_threshold_report(
    docker_stats: dict[str, Any],
    *,
    max_cpu_percent: float,
    max_memory_percent: float,
) -> dict[str, Any]:
    problems: list[str] = []
    if docker_stats.get("ok") is not True:
        problems.append("docker_stats")
    if docker_stats.get("skipped") is True:
        problems.append("docker_stats.skipped")

    cpu_percent = parse_float(docker_stats.get("cpu_percent"))
    memory_percent = parse_float(docker_stats.get("memory_percent"))
    if cpu_percent is None:
        problems.append("cpu_percent")
    elif cpu_percent > max_cpu_percent:
        problems.append("cpu_percent_high")
    if memory_percent is None:
        problems.append("memory_percent")
    elif memory_percent > max_memory_percent:
        problems.append("memory_percent_high")

    return {
        "ok": not problems,
        "max_cpu_percent": float(max_cpu_percent),
        "max_memory_percent": float(max_memory_percent),
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "problems": sorted(set(problems)),
    }


def storage_threshold_report(
    storage_usage: dict[str, Any],
    *,
    max_storage_percent: float,
    min_available_bytes: int = int(DEFAULT_MIN_STORAGE_AVAILABLE_GB * 1024**3),
) -> dict[str, Any]:
    problems: list[str] = []
    if storage_usage.get("ok") is not True:
        problems.append("storage_usage")
    if storage_usage.get("skipped") is True:
        problems.append("storage_usage.skipped")
    used_percent = parse_float(storage_usage.get("used_percent"))
    if used_percent is None:
        problems.append("used_percent")
    elif used_percent > max_storage_percent:
        problems.append("used_percent_high")
    available_bytes = parse_int(storage_usage.get("available_bytes"))
    if available_bytes is None:
        problems.append("available_bytes")
    elif available_bytes <= 0:
        problems.append("available_bytes_empty")
    elif available_bytes < int(min_available_bytes):
        problems.append("available_bytes_low")
    return {
        "ok": not problems,
        "max_storage_percent": float(max_storage_percent),
        "min_available_bytes": int(min_available_bytes),
        "used_percent": used_percent,
        "available_bytes": available_bytes,
        "problems": sorted(set(problems)),
    }


def summarize_qwen_probe_response(response: dict[str, Any]) -> dict[str, Any]:
    summarized = dict(response)
    data = summarized.get("data")
    if not isinstance(data, dict):
        return summarized
    embeddings = data.get("embeddings")
    embedding_count = None
    sample_dimensions = None
    if isinstance(embeddings, list):
        embedding_count = len(embeddings)
        first = embeddings[0] if embeddings else None
        if isinstance(first, list):
            sample_dimensions = len(first)
    sanitized = {key: value for key, value in data.items() if key != "embeddings"}
    if embedding_count is not None:
        sanitized["embedding_count"] = embedding_count
    if sample_dimensions is not None:
        sanitized["embedding_sample_dimensions"] = sample_dimensions
    summarized["data"] = sanitized
    return summarized


def qwen_embedding_contract(
    health: dict[str, Any],
    probe: dict[str, Any],
    image_probe: dict[str, Any] | None = None,
    *,
    expected_model: str,
    expected_dimensions: int,
    provider: str = "qwen",
) -> dict[str, Any]:
    prefix = "gemini" if str(provider or "").strip().lower() == "gemini" else "qwen"
    health_data = health.get("data") if isinstance(health.get("data"), dict) else {}
    probe_data = probe.get("data") if isinstance(probe.get("data"), dict) else {}
    expected_model_value = str(expected_model or Settings.qwen_model).strip()
    expected_dimension_value = int(expected_dimensions or Settings.qwen_embedding_dimensions)
    health_model = str(health_data.get("model") or "").strip()
    probe_model = str(probe_data.get("model") or "").strip()
    health_dimensions = parse_int(health_data.get("dimensions"))
    probe_dimensions = parse_int(
        probe_data.get("embedding_sample_dimensions", probe_data.get("dimensions"))
    )
    probe_count = parse_int(probe_data.get("count", probe_data.get("embedding_count")))
    problems: list[str] = []

    if health.get("ok") is not True:
        problems.append(f"{prefix}_health")
    if health.get("skipped") is True:
        problems.append(f"{prefix}_health.skipped")
    if health_data.get("ready") is not True:
        problems.append(f"{prefix}_health.ready")
    if health_data.get("loadError"):
        problems.append(f"{prefix}_health.loadError")
    if health_model != expected_model_value:
        problems.append(f"{prefix}_health.model")
    if health_dimensions != expected_dimension_value:
        problems.append(f"{prefix}_health.dimensions")

    if probe.get("ok") is not True:
        problems.append(f"{prefix}_embedding_probe")
    if probe.get("skipped") is True:
        problems.append(f"{prefix}_embedding_probe.skipped")
    if probe_model != expected_model_value:
        problems.append(f"{prefix}_embedding_probe.model")
    if probe_dimensions != expected_dimension_value:
        problems.append(f"{prefix}_embedding_probe.dimensions")
    if probe_count is None or probe_count < 1:
        problems.append(f"{prefix}_embedding_probe.count")

    image_probe_data = image_probe.get("data") if isinstance((image_probe or {}).get("data"), dict) else {}
    image_probe_model = str(image_probe_data.get("model") or "").strip()
    image_probe_dimensions = parse_int(
        image_probe_data.get("embedding_sample_dimensions", image_probe_data.get("dimensions"))
    )
    image_probe_count = parse_int(image_probe_data.get("count", image_probe_data.get("embedding_count")))
    if image_probe is not None:
        if image_probe.get("ok") is not True:
            problems.append(f"{prefix}_image_embedding_probe")
        if image_probe.get("skipped") is True:
            problems.append(f"{prefix}_image_embedding_probe.skipped")
        if image_probe_model != expected_model_value:
            problems.append(f"{prefix}_image_embedding_probe.model")
        if image_probe_dimensions != expected_dimension_value:
            problems.append(f"{prefix}_image_embedding_probe.dimensions")
        if image_probe_count is None or image_probe_count < 1:
            problems.append(f"{prefix}_image_embedding_probe.count")

    return {
        "ok": not problems,
        "provider": prefix,
        "expected_model": expected_model_value,
        "health_model": health_model or None,
        "probe_model": probe_model or None,
        "image_probe_model": image_probe_model or None,
        "expected_dimensions": expected_dimension_value,
        "health_dimensions": health_dimensions,
        "probe_dimensions": probe_dimensions,
        "image_probe_dimensions": image_probe_dimensions,
        "ready": health_data.get("ready"),
        "probe_count": probe_count,
        "image_probe_count": image_probe_count,
        "load_error": health_data.get("loadError"),
        "problems": sorted(set(problems)),
    }


def qwen_skipped_contract(embedding_backend: str) -> dict[str, Any]:
    return {
        "ok": True,
        "skipped": True,
        "embedding_backend": embedding_backend,
        "problems": [],
    }


def index_settings_contract(
    settings: dict[str, Any],
    *,
    expected_model: str,
    embedding_backend: str,
    qwen_model: str,
    qwen_embedding_dimensions: int,
) -> dict[str, Any]:
    backend = str(embedding_backend or "").strip().lower() or Settings.embedding_backend
    expected = str(expected_model or Settings.marqo_model).strip()
    problems: list[str] = []
    if not isinstance(settings, dict) or not settings:
        problems.append("index_settings")
        settings = {}
    actual_model = str(settings.get("model") or "").strip()
    tensor_fields = normalized_name_set(settings.get("tensorFields"))
    all_fields = normalized_all_fields(settings.get("allFields"))
    custom_vector_fields = {
        name
        for name, field in all_fields.items()
        if str(field.get("type") or "").strip().lower() == "custom_vector"
    }
    model_properties = settings.get("modelProperties") if isinstance(settings.get("modelProperties"), dict) else {}
    dimensions = parse_int(model_properties.get("dimensions"))
    normalize_embeddings = settings.get("normalizeEmbeddings")
    treat_urls_as_images = settings.get("treatUrlsAndPointersAsImages")

    if backend in {"qwen", "gemini"}:
        expected_tensor_fields = {QWEN_TEXT_VECTOR_FIELD, QWEN_IMAGE_VECTOR_FIELD}
        if actual_model != "no_model":
            problems.append("model")
        if str(model_properties.get("type") or "").strip().lower() != "no_model":
            problems.append("modelProperties.type")
        if dimensions != int(qwen_embedding_dimensions):
            problems.append("modelProperties.dimensions")
        if normalize_embeddings is not False:
            problems.append("normalizeEmbeddings")
        if not expected_tensor_fields.issubset(tensor_fields):
            problems.append(f"tensorFields.{backend}")
        if not expected_tensor_fields.issubset(custom_vector_fields):
            problems.append(f"allFields.{backend}_custom_vector")
    elif backend == "native":
        expected_tensor_fields = set(TEXT_FIELDS + [IMAGE_FIELD])
        if actual_model != expected:
            problems.append("model")
        if treat_urls_as_images is not True:
            problems.append("treatUrlsAndPointersAsImages")
        if normalize_embeddings is not True:
            problems.append("normalizeEmbeddings")
        if not expected_tensor_fields.issubset(tensor_fields):
            problems.append("tensorFields.native")
    else:
        problems.append("embedding_backend")

    return {
        "ok": not problems,
        "embedding_backend": backend,
        "expected_model": expected,
        "actual_model": actual_model or None,
        "provider_model": qwen_model if backend in {"qwen", "gemini"} else None,
        "expected_embedding_dimensions": int(qwen_embedding_dimensions) if backend in {"qwen", "gemini"} else None,
        "actual_embedding_dimensions": dimensions if backend in {"qwen", "gemini"} else None,
        "qwen_model": qwen_model if backend == "qwen" else None,
        "expected_qwen_embedding_dimensions": int(qwen_embedding_dimensions) if backend == "qwen" else None,
        "actual_qwen_embedding_dimensions": dimensions if backend == "qwen" else None,
        "gemini_model": qwen_model if backend == "gemini" else None,
        "expected_gemini_embedding_dimensions": int(qwen_embedding_dimensions) if backend == "gemini" else None,
        "actual_gemini_embedding_dimensions": dimensions if backend == "gemini" else None,
        "tensor_fields": sorted(tensor_fields),
        "custom_vector_fields": sorted(custom_vector_fields),
        "normalize_embeddings": normalize_embeddings,
        "treat_urls_and_pointers_as_images": treat_urls_as_images,
        "problems": sorted(set(problems)),
    }


def normalized_name_set(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                names.add(name)
    return names


def normalized_all_fields(value: Any) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                fields[name] = item
    return fields


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    expected_model = getattr(args, "expected_model", Settings.marqo_model)
    embedding_backend = str(getattr(args, "embedding_backend", Settings.embedding_backend) or "").strip().lower()
    gemini_model = getattr(args, "gemini_model", "") or None
    qwen_model = getattr(args, "qwen_model", Settings.qwen_model)
    if embedding_backend == "gemini" and gemini_model:
        qwen_model = gemini_model
    qwen_embedding_dimensions = int(
        (
            getattr(args, "gemini_embedding_dimensions", None)
            if embedding_backend == "gemini" and getattr(args, "gemini_embedding_dimensions", None)
            else getattr(args, "qwen_embedding_dimensions", Settings.qwen_embedding_dimensions)
        )
        or Settings.qwen_embedding_dimensions
    )
    storage_container = getattr(args, "storage_container", DEFAULT_STORAGE_CONTAINER)
    storage_path = getattr(args, "storage_path", DEFAULT_STORAGE_PATH)
    qwen_embedding_url = (
        getattr(args, "gemini_embedding_url", None)
        if embedding_backend == "gemini" and getattr(args, "gemini_embedding_url", None)
        else getattr(args, "qwen_embedding_url", Settings.qwen_embedding_url)
    )
    provider_label = "Gemini" if embedding_backend == "gemini" else "Qwen"
    provider_check_prefix = "gemini" if embedding_backend == "gemini" else "qwen"
    provider_url_arg = "--gemini-embedding-url" if embedding_backend == "gemini" else "--qwen-embedding-url"
    provider_timeout = int(
        getattr(args, "gemini_timeout", getattr(args, "qwen_timeout", args.timeout))
        if embedding_backend == "gemini"
        else getattr(args, "qwen_timeout", args.timeout)
    )
    skip_qwen_embedding_probe = bool(
        getattr(args, "skip_gemini_embedding_probe", False)
        if embedding_backend == "gemini"
        else getattr(args, "skip_qwen_embedding_probe", False)
    )
    try:
        base_url = validate_marqo_url_value(args.marqo_url, "--marqo-url")
    except ValueError as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "marqo_url": "",
            "index": args.index,
            "container": args.container,
            "checks": [{"name": "marqo_url", "ok": False}],
            "error": str(exc),
            "health": {"ok": False, "skipped": True},
            "index_stats": {"ok": False, "skipped": True},
            "index_settings": {"ok": False, "skipped": True},
            "index_settings_contract": {"ok": False, "skipped": True, "problems": ["index_settings"]},
            "qwen_embedding_url": "",
            "qwen_health": {"ok": False, "skipped": True},
            "qwen_embedding_probe": {"ok": False, "skipped": True},
            "qwen_image_embedding_probe": {"ok": False, "skipped": True},
            "qwen_embedding_contract": {"ok": False, "skipped": True, "problems": ["marqo_url"]},
            "docker_stats": {"ok": False, "skipped": True},
            "storage_usage": {"ok": False, "skipped": True},
            "resource_thresholds": {
                "ok": False,
                "skipped": True,
                "problems": ["docker_stats"],
                "max_cpu_percent": DEFAULT_MAX_CPU_PERCENT,
                "max_memory_percent": DEFAULT_MAX_MEMORY_PERCENT,
            },
            "storage_thresholds": {
                "ok": False,
                "skipped": True,
                "problems": ["storage_usage"],
                "max_storage_percent": DEFAULT_MAX_STORAGE_PERCENT,
                "min_available_bytes": int(DEFAULT_MIN_STORAGE_AVAILABLE_GB * 1024**3),
            },
        }
    health = request_json(base_url + "/", args.timeout)
    index_stats = request_json(f"{base_url}/indexes/{args.index}/stats", args.timeout) if args.index else {
        "ok": False,
        "error": "--index is required",
    }
    index_settings = request_json(f"{base_url}/indexes/{args.index}/settings", args.timeout) if args.index else {
        "ok": False,
        "error": "--index is required",
    }
    settings_contract = index_settings_contract(
        index_settings.get("data") if isinstance(index_settings.get("data"), dict) else {},
        expected_model=expected_model,
        embedding_backend=embedding_backend,
        qwen_model=qwen_model,
        qwen_embedding_dimensions=qwen_embedding_dimensions,
    )
    backend = str(embedding_backend or "").strip().lower()
    if backend in {"qwen", "gemini"}:
        try:
            validated_qwen_url = validate_marqo_url_value(qwen_embedding_url, provider_url_arg)
            qwen_health = request_json(f"{validated_qwen_url}/health", args.timeout)
            qwen_ready = (qwen_health.get("data") or {}).get("ready") is True if isinstance(qwen_health.get("data"), dict) else False
            if skip_qwen_embedding_probe:
                qwen_embedding_probe = {"ok": True, "skipped": True}
                qwen_image_embedding_probe = {"ok": True, "skipped": True}
            elif qwen_health.get("ok") is True and qwen_ready:
                qwen_embedding_probe = summarize_qwen_probe_response(
                    post_json(
                        f"{validated_qwen_url}/embed",
                        {"inputs": [{"text": "haeorum readiness probe"}]},
                        provider_timeout,
                    )
                )
                qwen_image_embedding_probe = summarize_qwen_probe_response(
                    post_json(
                        f"{validated_qwen_url}/embed",
                        {"inputs": [{"image": QWEN_IMAGE_PROBE_DATA_URL}]},
                        provider_timeout,
                    )
                )
            else:
                qwen_embedding_probe = {
                    "ok": False,
                    "skipped": True,
                    "reason": f"{provider_check_prefix}_health_not_ready",
                }
                qwen_image_embedding_probe = {
                    "ok": False,
                    "skipped": True,
                    "reason": f"{provider_check_prefix}_health_not_ready",
                }
            qwen_contract = qwen_embedding_contract(
                qwen_health,
                qwen_embedding_probe,
            qwen_image_embedding_probe,
            expected_model=qwen_model,
            expected_dimensions=qwen_embedding_dimensions,
            provider=backend,
        )
        except ValueError as exc:
            validated_qwen_url = ""
            qwen_health = {"ok": False, "error": str(exc)}
            qwen_embedding_probe = {"ok": False, "skipped": True}
            qwen_image_embedding_probe = {"ok": False, "skipped": True}
            qwen_contract = {
                "ok": False,
                "expected_model": qwen_model,
                "expected_dimensions": qwen_embedding_dimensions,
                "problems": [f"{provider_check_prefix}_embedding_url"],
                "error": str(exc),
            }
    else:
        validated_qwen_url = ""
        qwen_health = {"ok": True, "skipped": True}
        qwen_embedding_probe = {"ok": True, "skipped": True}
        qwen_image_embedding_probe = {"ok": True, "skipped": True}
        qwen_contract = qwen_skipped_contract(backend)
    skip_docker_stats = bool(getattr(args, "skip_docker_stats", False))
    skip_storage_usage = bool(getattr(args, "skip_storage_usage", False))
    docker_stats = {"ok": True, "skipped": True} if skip_docker_stats else collect_docker_stats(args.container, args.timeout)
    storage_usage = (
        {"ok": True, "skipped": True}
        if skip_storage_usage
        else collect_storage_usage(storage_container, storage_path, args.timeout)
    )
    resource_thresholds = resource_threshold_report(
        docker_stats,
        max_cpu_percent=float(getattr(args, "max_cpu_percent", DEFAULT_MAX_CPU_PERCENT)),
        max_memory_percent=float(getattr(args, "max_memory_percent", DEFAULT_MAX_MEMORY_PERCENT)),
    )
    storage_thresholds = storage_threshold_report(
        storage_usage,
        max_storage_percent=float(getattr(args, "max_storage_percent", DEFAULT_MAX_STORAGE_PERCENT)),
        min_available_bytes=int(
            float(getattr(args, "min_storage_available_gb", DEFAULT_MIN_STORAGE_AVAILABLE_GB)) * 1024**3
        ),
    )
    checks = [
        {"name": "marqo_health", "ok": health.get("ok") is True},
        {"name": "marqo_index_stats", "ok": index_stats.get("ok") is True},
        {"name": "marqo_index_settings", "ok": index_settings.get("ok") is True},
        {"name": "marqo_index_settings_contract", "ok": settings_contract.get("ok") is True},
        {
            "name": f"{provider_check_prefix}_health",
            "ok": qwen_health.get("ok") is True and qwen_health.get("skipped") is not True,
        },
        {
            "name": f"{provider_check_prefix}_embedding_probe",
            "ok": qwen_embedding_probe.get("ok") is True and qwen_embedding_probe.get("skipped") is not True,
        },
        {
            "name": f"{provider_check_prefix}_image_embedding_probe",
            "ok": qwen_image_embedding_probe.get("ok") is True and qwen_image_embedding_probe.get("skipped") is not True,
        },
        {"name": f"{provider_check_prefix}_embedding_contract", "ok": qwen_contract.get("ok") is True},
        {"name": "docker_stats", "ok": docker_stats.get("ok") is True and docker_stats.get("skipped") is not True},
        {"name": "resource_thresholds", "ok": resource_thresholds.get("ok") is True},
        {"name": "storage_usage", "ok": storage_usage.get("ok") is True and storage_usage.get("skipped") is not True},
        {"name": "storage_thresholds", "ok": storage_thresholds.get("ok") is True},
    ]
    if backend not in {"qwen", "gemini"}:
        checks[4]["ok"] = True
        checks[4]["skipped"] = True
        checks[5]["ok"] = True
        checks[5]["skipped"] = True
        checks[6]["ok"] = True
        checks[6]["skipped"] = True
        checks[7]["ok"] = True
        checks[7]["skipped"] = True
    if skip_docker_stats:
        checks[8]["ok"] = True
        checks[8]["skipped"] = True
        checks[9]["ok"] = True
        checks[9]["skipped"] = True
    if skip_storage_usage:
        checks[10]["ok"] = True
        checks[10]["skipped"] = True
        checks[11]["ok"] = True
        checks[11]["skipped"] = True
    return {
        "ok": all(check["ok"] for check in checks),
        "generated_at": generated_at,
        "marqo_url": base_url,
        "index": args.index,
        "container": args.container,
        "storage_container": storage_container,
        "storage_path": storage_path,
        "embedding_provider": backend,
        "embedding_provider_label": provider_label if backend in {"qwen", "gemini"} else None,
        "embedding_url": validated_qwen_url,
        "embedding_health": qwen_health,
        "embedding_probe": qwen_embedding_probe,
        "image_embedding_probe": qwen_image_embedding_probe,
        "embedding_contract": qwen_contract,
        "qwen_embedding_url": validated_qwen_url,
        "checks": checks,
        "health": health,
        "index_stats": index_stats,
        "index_settings": index_settings,
        "index_settings_contract": settings_contract,
        "qwen_health": qwen_health,
        "qwen_embedding_probe": qwen_embedding_probe,
        "qwen_image_embedding_probe": qwen_image_embedding_probe,
        "qwen_embedding_contract": qwen_contract,
        "gemini_embedding_url": validated_qwen_url if backend == "gemini" else None,
        "gemini_health": qwen_health if backend == "gemini" else None,
        "gemini_embedding_probe": qwen_embedding_probe if backend == "gemini" else None,
        "gemini_image_embedding_probe": qwen_image_embedding_probe if backend == "gemini" else None,
        "gemini_embedding_contract": qwen_contract if backend == "gemini" else None,
        "docker_stats": docker_stats,
        "resource_thresholds": resource_thresholds,
        "storage_usage": storage_usage,
        "storage_thresholds": storage_thresholds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Marqo health and container resource evidence.")
    parser.add_argument("--marqo-url", default="http://localhost:8882")
    parser.add_argument("--index", default="haeorum-products")
    parser.add_argument("--container", default="marqo-api")
    parser.add_argument("--storage-container", default=DEFAULT_STORAGE_CONTAINER)
    parser.add_argument("--storage-path", default=DEFAULT_STORAGE_PATH)
    parser.add_argument("--expected-model", default=Settings.marqo_model)
    parser.add_argument("--embedding-backend", choices=["native", "qwen", "gemini"], default=Settings.embedding_backend)
    parser.add_argument("--qwen-model", default=Settings.qwen_model)
    parser.add_argument("--qwen-embedding-url", default=Settings.qwen_embedding_url)
    parser.add_argument("--qwen-embedding-dimensions", type=int, default=Settings.qwen_embedding_dimensions)
    parser.add_argument("--qwen-timeout", type=int, default=1800)
    parser.add_argument("--gemini-model", default="")
    parser.add_argument("--gemini-embedding-url", default="")
    parser.add_argument("--gemini-embedding-dimensions", type=int)
    parser.add_argument("--gemini-timeout", type=int, default=1800)
    parser.add_argument("--max-cpu-percent", type=float, default=DEFAULT_MAX_CPU_PERCENT)
    parser.add_argument("--max-memory-percent", type=float, default=DEFAULT_MAX_MEMORY_PERCENT)
    parser.add_argument("--max-storage-percent", type=float, default=DEFAULT_MAX_STORAGE_PERCENT)
    parser.add_argument(
        "--min-storage-available-gb",
        type=float,
        default=DEFAULT_MIN_STORAGE_AVAILABLE_GB,
        help="Fail readiness when Vespa/Marqo storage has less than this much free space.",
    )
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument(
        "--skip-docker-stats",
        action="store_true",
        help="Only for local dry-runs. Production readiness should collect docker stats.",
    )
    parser.add_argument(
        "--skip-storage-usage",
        action="store_true",
        help="Only for local dry-runs. Production readiness should collect storage usage.",
    )
    parser.add_argument(
        "--skip-qwen-embedding-probe",
        action="store_true",
        help="Only for local dry-runs. Production readiness should collect a real Qwen embedding probe.",
    )
    parser.add_argument(
        "--skip-gemini-embedding-probe",
        action="store_true",
        help="Only for local dry-runs. Production readiness should collect a real Gemini embedding probe.",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
