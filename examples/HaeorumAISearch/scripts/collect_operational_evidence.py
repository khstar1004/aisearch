from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (
    PLACEHOLDER_ADMIN_API_KEYS,
    Settings,
    check_sync_alert_webhook_url,
    is_placeholder_public_api_key,
    normalize_origin_value,
    validate_mssql_connection_string_value,
)
from app.image_validation import validate_image_bytes
from app.sql_safety import validate_readonly_query
from app.url_safety import is_non_public_host
from scripts.env_file_security import COLLECTOR_ENV_FILE_MAX_MODE, check_secret_file_permissions
from scripts.load_compare import operational_target_url_problems, summarize_load_report
from scripts.mall_config_builder import build_mall_config_from_csv
from scripts.mall_config_check import api_key_hash, validate_mall_config
from scripts.operational_readiness import (
    check_csv_index,
    check_image_urls,
    check_load,
    check_mall_config_build,
    check_mall_config,
    check_marqo_resource,
    check_mssql_export,
    check_poc_dataset,
    summarize_quality_case_result_evidence,
    summarize_quality_result_contract_evidence,
)
from scripts.quality_report import load_quality_cases, summarize_case_contract
from scripts.representative_site_check import (
    expected_list,
    load_site_configs,
    url_matches_prefix,
    validate_site_collection,
    validate_site_config,
)
from scripts.widget_integration_probe import (
    analyze_html_source,
    has_explicit_probe_source,
    has_local_explicit_probe_sources,
    inject_snippet_preview_html,
    is_remote_probe_source,
    missing_pc_mobile_variants,
    probe_source_entries,
    site_widget_src,
    validate_preview_html_body,
)
from scripts.requirements_audit import build_report as build_requirements_audit_report
from scripts.requirements_audit import join_posix_path, normalize_posix_path, render_blocker_text
from scripts.requirements_audit import sanitize_report_for_deployment as sanitize_handoff_report
from scripts.requirements_audit import to_blocker_checklist as requirements_audit_to_blocker_checklist
from scripts.requirements_audit import to_markdown as requirements_audit_to_markdown

DEPLOYMENT_CONFIG_PATH = "/etc/haeorum-ai-search/operational-evidence.config.json"
DEPLOYMENT_ENV_PATH = "/etc/haeorum-ai-search/operational-evidence.env"
DEFAULT_EVIDENCE_FILES = {
    "mssql_export": "mssql-export.json",
    "poc_dataset": "poc-dataset.json",
    "api_smoke": "api-smoke.json",
    "mssql_view": "mssql-view.json",
    "image_urls": "image-url-check.json",
    "quality_report": "quality-report.json",
    "csv_poc_index": "csv-index.json",
    "mall_config_build": "mall-config-build.json",
    "mall_config": "mall-config-check.json",
    "marqo_resource": "marqo-resource.json",
    "server_preflight": "server-preflight.json",
    "env_preflight": "env-check.json",
    "load_text_100_concurrent": "load-text.json",
    "load_image_30_concurrent": "load-image.json",
    "load_mixed_30_concurrent": "load-mixed.json",
    "load_mixed_traffic_850_active_users": "load-mixed-traffic.json",
    "api_scale_comparison": "api-scale.json",
    "representative_mall_sites": "representative-sites.json",
    "security": "security.json",
    "operational_readiness": "operational-readiness.json",
    "requirements_audit": "requirements-audit.json",
}
WIDGET_PREVIEW_VALIDATION_BLOCKER_HINTS = (
    "data_auto_init_script_multiple",
    "preview_marker_count",
    "snippet_not_embedded",
)
EVIDENCE_REQUIRED_KEYS = {
    "api_smoke": ["base_url", "mall_id", "checks", "target_validation"],
    "mssql_export": ["output_csv", "output_csv_fingerprint", "limit", "since_configured", "exported_products", "active_products", "domain_filter_coverage"],
    "poc_dataset": ["source_csv", "output_csv", "source_csv_fingerprint", "output_csv_fingerprint", "selected_products"],
    "mssql_view": ["permission_report", "column_report", "sample_report", "sample_quality_report", "incremental_probe"],
    "image_urls": ["csv", "csv_fingerprint", "checked", "failed", "active_products"],
    "quality_report": ["csv", "dataset", "case_contract", "response_time", "quality_ok"],
    "csv_poc_index": ["csv", "csv_fingerprint", "engine", "summary", "indexed"],
    "mall_config_build": ["input", "output", "mall_count", "enabled_count", "generated_api_key_count", "validation"],
    "mall_config": ["mall_count", "enabled_count", "enabled_mall_ids", "enabled_mall_api_key_hashes"],
    "marqo_resource": [
        "marqo_url",
        "index",
        "health",
        "index_stats",
        "index_settings",
        "index_settings_contract",
        "embedding_url",
        "embedding_health",
        "embedding_probe",
        "image_embedding_probe",
        "embedding_contract",
        "resource_thresholds",
        "storage_usage",
        "storage_thresholds",
        "checks",
    ],
    "server_preflight": ["role", "checks", "requirements"],
    "env_preflight": ["env_file", "role", "checks", "api_server_count"],
    "load_text_100_concurrent": ["base_url", "mall_id", "requests", "concurrency", "latency_ms", "response_contract", "server_metrics"],
    "load_image_30_concurrent": [
        "base_url",
        "mall_id",
        "requests",
        "concurrency",
        "latency_ms",
        "response_contract",
        "server_metrics",
        "image_input",
    ],
    "load_mixed_30_concurrent": [
        "base_url",
        "mall_id",
        "requests",
        "concurrency",
        "latency_ms",
        "response_contract",
        "server_metrics",
        "image_input",
    ],
    "load_mixed_traffic_850_active_users": [
        "base_url",
        "mall_id",
        "requests",
        "concurrency",
        "latency_ms",
        "response_contract",
        "server_metrics",
        "image_input",
    ],
    "api_scale_comparison": ["single", "multi", "comparison", "problems"],
    "representative_mall_sites": ["api_base_url", "site_count", "sites", "image_input"],
    "security": ["base_url", "environment", "https", "admin_key", "mall_api_key", "allowed_origins", "nginx", "logrotate"],
    "operational_readiness": ["status_counts", "checks"],
    "requirements_audit": ["summary", "requirements"],
}
EVIDENCE_REQUIRED_ALIASES = {
    "marqo_resource": {
        "embedding_url": ("gemini_embedding_url", "qwen_embedding_url"),
        "embedding_health": ("gemini_health", "qwen_health"),
        "embedding_probe": ("gemini_embedding_probe", "qwen_embedding_probe"),
        "image_embedding_probe": ("gemini_image_embedding_probe", "qwen_image_embedding_probe"),
        "embedding_contract": ("gemini_embedding_contract", "qwen_embedding_contract"),
    },
}
IMAGE_FILE_EVIDENCE_STEPS = {
    "load_image_30_concurrent",
    "load_mixed_30_concurrent",
    "load_mixed_traffic_850_active_users",
    "representative_mall_sites",
}
DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS = 14
EVIDENCE_FUTURE_SKEW_SECONDS = 300
SENSITIVE_OPTIONS = {"--api-key", "--admin-key", "--connection-string"}
SENSITIVE_ENV_NAME_PATTERN = re.compile(
    r"(API[_-]?KEY|ADMIN[_-]?KEY|PASSWORD|PASSWD|PWD|TOKEN|SECRET|CONNECTION[_-]?STRING|WEBHOOK)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b((?:password|passwd|pwd|api[_-]?key|admin[_-]?key|x[_-]?api[_-]?key|"
    r"x[_-]?admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|connection[_-]?string|secret|token)\s*[:=]\s*)([^;\s,&]+)",
    re.IGNORECASE,
)
AUTHORIZATION_PATTERN = re.compile(r"(Authorization\s*:\s*(?:Bearer\s+)?)([^\s,;&]+)", re.IGNORECASE)
URL_CREDENTIAL_PATTERN = re.compile(r"\b(https?://)([^/@\s]+)@", re.IGNORECASE)
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_PATTERNS = (
    re.compile(r"^<[^>\r\n]+>$"),
    re.compile(r"^(replace-with|change-me|changeme)", re.IGNORECASE),
    re.compile(r"(^|[=:/,;])\.\.\.($|[=:/,;])"),
)
CommandRunner = Callable[[list[str], int], dict[str, Any]]
APP_ENV_PREFIX = "HAEORUM_"
SIMULATION_CONFIG_MARKER = "SIMULATED_ONLY_NOT_OPERATIONAL_EVIDENCE"
REQUIRED_MIXED_TRAFFIC_MODES = ("text", "image", "mixed")
MIN_LOAD_IMAGE_INPUTS = 3
LOAD_EVIDENCE_CHECKS: dict[str, tuple[str, Callable[[dict[str, Any]], tuple[bool, str, dict[str, Any]]]]] = {
    "load_text_100_concurrent": ("load_text_100_concurrent", check_load("text", min_concurrency=100, min_requests=100)),
    "load_image_30_concurrent": ("load_image_30_concurrent", check_load("image", min_concurrency=30, min_requests=30)),
    "load_mixed_30_concurrent": ("load_mixed_30_concurrent", check_load("mixed", min_concurrency=30, min_requests=30)),
    "load_mixed_traffic_850_active_users": (
        "load_mixed_traffic_850_active_users",
        check_load("mixed-traffic", min_concurrency=100, min_active_users=850, min_requests=850),
    ),
}
POC_SOURCE_MATCH_FIELDS = (
    ("category", "category_name"),
    ("image_url", "main_image_url"),
    ("product_url", "product_url"),
    ("mall_id", "mall_id"),
)


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("operational evidence config must be a JSON object")
    return data


def is_simulated_config(config: Mapping[str, Any]) -> bool:
    return (
        config.get("simulation_only") is True
        or config.get("not_operational_readiness") is True
        or str(config.get("simulation_marker") or "").strip() == SIMULATION_CONFIG_MARKER
    )


def load_env_file(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    target = Path(path)
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid env file line {line_number}: missing '='")
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid env file line {line_number}: invalid variable name")
        values[name] = unquote_env_value(value.strip())
    return values


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            return (
                inner.replace(r"\\", "\\")
                .replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\t", "\t")
            )
        return inner
    return value


def evidence_path(evidence_dir: str | Path, key: str) -> str:
    return str(Path(evidence_dir) / DEFAULT_EVIDENCE_FILES[key])


def optional_path(evidence_dir: str | Path, filename: str) -> str:
    return str(Path(evidence_dir) / filename)


def normalize_evidence_dir(evidence_dir: str | Path) -> Path:
    path = Path(evidence_dir)
    return path if path.is_absolute() else path.resolve()


def config_value(config: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def string_value(config: dict[str, Any], *path: str, default: str = "") -> str:
    value = config_value(config, *path, default=default)
    return str(value or "").strip()


def string_list_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]
    return [str(item or "").strip() for item in raw_items if str(item or "").strip()]


def string_list_value(config: dict[str, Any], *path: str, default: Any = None) -> list[str]:
    value = config_value(config, *path, default=default)
    return string_list_items(value)


def bool_value(config: dict[str, Any], *path: str, default: bool = False) -> bool:
    value = config_value(config, *path, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def int_value(config: dict[str, Any], *path: str, default: int) -> int:
    value = config_value(config, *path, default=default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def primary_mall_validation_context(
    config: dict[str, Any],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    api_key = resolve_secret(config, "api_key", environment)
    return {
        "expected_malls": str(int_value(config, "expected_malls", default=1700)),
        "mall_id": string_value(config, "mall_id"),
        "origin": string_value(config, "origin"),
        "api_key_hash": api_key_hash(api_key),
    }


def load_api_server_count(config: dict[str, Any], override: Mapping[str, Any]) -> int:
    value = override.get("api_server_count") or config_value(config, "load", "api_server_count", default=1) or 1
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def load_admin_metrics_base_urls(config: dict[str, Any], override: Mapping[str, Any]) -> list[str]:
    override_urls = override.get("admin_metrics_base_urls")
    if override_urls is not None:
        return string_list_items(override_urls)
    return string_list_value(config, "load", "admin_metrics_base_urls")


def load_image_files(config: dict[str, Any], override: Mapping[str, Any]) -> list[str]:
    primary = str(override.get("image_file") or string_value(config, "load", "image_file")).strip()
    configured_extra = (
        override.get("image_files")
        if override.get("image_files") not in (None, "")
        else config_value(config, "load", "image_files", default=[])
    )
    files = [primary] if primary else []
    files.extend(string_list_items(configured_extra))
    files.extend(string_list_items(override.get("additional_image_files")))
    deduped: list[str] = []
    seen: set[str] = set()
    for path in files:
        key = str(Path(path))
        if not path or key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def load_image_config_maps(image_files: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    primary = image_files[0] if image_files else ""
    additional = image_files[1:]
    config_values = {"load.image_file": primary}
    required_files = {"load.image_file": primary}
    if additional:
        config_values["load.image_files"] = ",".join(image_files)
    for index in range(2, MIN_LOAD_IMAGE_INPUTS + 1):
        label = f"load.image_files[{index}]"
        value = image_files[index - 1] if len(image_files) >= index else ""
        config_values[label] = value
        required_files[label] = value
    return config_values, required_files


def load_image_required_config_labels() -> list[str]:
    return ["load.image_file"] + [f"load.image_files[{index}]" for index in range(2, MIN_LOAD_IMAGE_INPUTS + 1)]


def load_allow_private_admin_metrics_targets(config: dict[str, Any], override: Mapping[str, Any]) -> bool:
    if "allow_private_admin_metrics_targets" in override:
        value = override.get("allow_private_admin_metrics_targets")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    return bool_value(config, "load", "allow_private_admin_metrics_targets", default=False)


def append_load_admin_metrics_options(
    command: list[str],
    *,
    api_server_count: int,
    admin_metrics_base_urls: list[str],
    allow_private_admin_metrics_targets: bool,
) -> None:
    command.extend(["--api-server-count", str(api_server_count)])
    for base_url in admin_metrics_base_urls:
        command.extend(["--admin-metrics-base-url", base_url])
    if allow_private_admin_metrics_targets:
        command.append("--allow-private-admin-metrics-targets")


def load_admin_metrics_config_value(api_server_count: int, admin_metrics_base_urls: list[str]) -> str:
    if api_server_count >= 2 and len(admin_metrics_base_urls) < api_server_count:
        return ""
    return ",".join(admin_metrics_base_urls)


def resolve_secret(config: dict[str, Any], field: str, environment: Mapping[str, str] | None = None) -> str:
    direct = string_value(config, field)
    if direct:
        return direct
    env_name = string_value(config, field + "_env")
    if env_name:
        source = os.environ if environment is None else environment
        return str(source.get(env_name, "")).strip()
    return ""


def collection_environment(env_file: str | Path | None = None) -> dict[str, str]:
    if env_file:
        return load_env_file(env_file)
    return dict(os.environ)


def child_process_environment(env_file: str | Path | None = None) -> dict[str, str] | None:
    if not env_file:
        return None
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(APP_ENV_PREFIX)}
    env.update(load_env_file(env_file))
    return env


def build_plan(
    config: dict[str, Any],
    evidence_dir: str | Path,
    environment: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    python = sys.executable
    base_url = string_value(config, "base_url")
    mall_id = string_value(config, "mall_id")
    origin = string_value(config, "origin")
    api_key = resolve_secret(config, "api_key", environment)
    admin_key = resolve_secret(config, "admin_key", environment)
    products_csv = string_value(config, "products_csv")
    poc_products_csv = string_value(config, "poc_products_csv", default=products_csv)
    mall_config = string_value(config, "mall_config")
    mall_config_source = string_value(config, "mall_config_source")
    primary_mall_context = primary_mall_validation_context(config, environment)
    marqo_url = string_value(config, "marqo", "url")
    index_name = string_value(config, "marqo", "index", default="haeorum-products")
    marqo_model = string_value(config, "marqo", "model", default=Settings.marqo_model)
    embedding_backend = string_value(config, "marqo", "embedding_backend", default=Settings.embedding_backend)
    qwen_model = string_value(config, "marqo", "qwen_model", default=Settings.qwen_model)
    qwen_embedding_url = string_value(config, "marqo", "qwen_embedding_url")
    qwen_dimensions = str(int_value(config, "marqo", "qwen_embedding_dimensions", default=Settings.qwen_embedding_dimensions))
    gemini_model = string_value(config, "marqo", "gemini_model", default=qwen_model)
    gemini_embedding_url = string_value(config, "marqo", "gemini_embedding_url", default=qwen_embedding_url)
    gemini_dimensions = str(int_value(config, "marqo", "gemini_embedding_dimensions", default=int(qwen_dimensions)))
    provider_is_gemini = str(embedding_backend or "").strip().lower() == "gemini"
    marqo_embedding_args = (
        [
            "--gemini-model",
            gemini_model,
            "--gemini-embedding-url",
            gemini_embedding_url,
            "--gemini-embedding-dimensions",
            gemini_dimensions,
        ]
        if provider_is_gemini
        else [
            "--qwen-model",
            qwen_model,
            "--qwen-embedding-url",
            qwen_embedding_url,
            "--qwen-embedding-dimensions",
            qwen_dimensions,
        ]
    )
    marqo_embedding_config_values = (
        {
            "marqo.gemini_embedding_url": gemini_embedding_url,
            "marqo.embedding_url": gemini_embedding_url,
        }
        if provider_is_gemini
        else {
            "marqo.qwen_embedding_url": qwen_embedding_url,
            "marqo.embedding_url": qwen_embedding_url,
        }
    )
    marqo_max_cpu_percent = string_value(config, "marqo", "max_cpu_percent", default="90")
    marqo_max_memory_percent = string_value(config, "marqo", "max_memory_percent", default="85")
    marqo_storage_container = string_value(config, "marqo", "storage_container", default="vespa")
    marqo_storage_path = string_value(config, "marqo", "storage_path", default="/opt/vespa/var")
    marqo_max_storage_percent = string_value(config, "marqo", "max_storage_percent", default="85")
    marqo_min_storage_available_gb = string_value(config, "marqo", "min_storage_available_gb", default="10")
    quality_cases_file = string_value(config, "quality", "cases_file")
    scale_image_files = load_image_files(config, {})
    load_image_file = scale_image_files[0] if scale_image_files else string_value(config, "load", "image_file")
    load_image_config_values, load_image_required_files = load_image_config_maps(scale_image_files)
    quality_command = [
        python,
        "scripts/quality_report.py",
        "--csv",
        poc_products_csv,
        "--strict",
        "--engine",
        "marqo",
        "--index-name",
        index_name,
        "--marqo-url",
        marqo_url,
        "--marqo-model",
        marqo_model,
        "--cases",
        quality_cases_file,
        "--mall-config",
        mall_config,
        "--min-products",
        str(int_value(config, "quality", "min_products", default=300)),
        "--max-text-ms",
        str(int_value(config, "quality", "max_text_ms", default=3000)),
        "--max-image-ms",
        str(int_value(config, "quality", "max_image_ms", default=5000)),
        "--max-mixed-ms",
        str(int_value(config, "quality", "max_mixed_ms", default=5000)),
        "--json-output",
        evidence_path(evidence_dir, "quality_report"),
        "--markdown-output",
        optional_path(evidence_dir, "quality-report.md"),
    ]
    commands = []
    if bool_value(config, "input_preparation", "enabled", default=False):
        commands.extend(input_preparation_commands(config, evidence_dir, environment=environment))
    commands.extend(
        [
        evidence_command(
            "mall_config_build",
            [
                python,
                "scripts/mall_config_builder.py",
                "--csv",
                mall_config_source,
                "--output",
                mall_config,
                "--report-output",
                evidence_path(evidence_dir, "mall_config_build"),
                "--min-count",
                str(int_value(config, "expected_malls", default=1700)),
                "--sort-by-mall-id",
            ],
            required=["mall_config_source", "mall_config"],
            config_values={"mall_config_source": mall_config_source, "mall_config": mall_config},
            required_files={"mall_config_source": mall_config_source},
            produces_files={"mall_config": mall_config},
            evidence_file=evidence_path(evidence_dir, "mall_config_build"),
            file_validation_context={"expected_malls": str(int_value(config, "expected_malls", default=1700))},
        ),
        evidence_command(
            "api_smoke",
            [
                python,
                "scripts/api_smoke_test.py",
                "--base-url",
                base_url,
                "--mall-id",
                mall_id,
                "--api-key",
                api_key,
                "--origin",
                origin,
                "--admin-key",
                admin_key,
                "--mall-config",
                mall_config,
                "--expect-click-rate-limit",
                "--click-rate-limit-probe-count",
                str(int_value(config, "click_rate_limit_probe_count", default=1)),
                "--output",
                evidence_path(evidence_dir, "api_smoke"),
            ],
            required=["base_url", "mall_id", "api_key", "origin", "admin_key", "mall_config"],
            config_values={
                "base_url": base_url,
                "mall_id": mall_id,
                "api_key": api_key,
                "origin": origin,
                "admin_key": admin_key,
                "mall_config": mall_config,
            },
            required_files={"mall_config": mall_config},
            file_validation_context=primary_mall_context,
        ),
        evidence_command(
            "mssql_view",
            [
                python,
                "scripts/mssql_view_check.py",
                "--connection-string",
                resolve_secret(config, "mssql_connection_string", environment),
                "--query",
                string_value(config, "mssql_query"),
                "--output",
                evidence_path(evidence_dir, "mssql_view"),
            ],
            required=["mssql_connection_string", "mssql_query"],
            config_values={
                "mssql_connection_string": resolve_secret(config, "mssql_connection_string", environment),
                "mssql_query": string_value(config, "mssql_query"),
            },
        ),
        evidence_command(
            "image_urls",
            [
                python,
                "scripts/image_url_check.py",
                "--csv",
                products_csv,
                "--limit",
                str(int_value(config, "image_url_check", "limit", default=100)),
                "--concurrency",
                str(int_value(config, "image_url_check", "concurrency", default=5)),
                "--min-dimension",
                str(int_value(config, "image_url_check", "min_dimension", default=16)),
                "--require-https",
                "--output",
                evidence_path(evidence_dir, "image_urls"),
            ],
            required=["products_csv"],
            config_values={"products_csv": products_csv},
            required_files={"products_csv": products_csv},
            file_validation_context={
                "min_products": str(int_value(config, "quality", "min_products", default=300)),
                "mall_config": mall_config,
            },
        ),
        evidence_command(
            "quality_report",
            quality_command,
            required=["poc_products_csv", "quality.cases_file", "marqo.url", "marqo.index", "mall_config"],
            config_values={
                "poc_products_csv": poc_products_csv,
                "quality.cases_file": quality_cases_file,
                "marqo.url": marqo_url,
                "marqo.index": index_name,
                "marqo.model": marqo_model,
                "mall_config": mall_config,
            },
            required_files={
                "poc_products_csv": poc_products_csv,
                "quality_cases_file": quality_cases_file,
                "mall_config": mall_config,
            },
            file_validation_context={
                "expected_malls": str(int_value(config, "expected_malls", default=1700)),
                "min_products": str(int_value(config, "quality", "min_products", default=300)),
                "poc_products_csv": poc_products_csv,
                "products_csv": products_csv,
                "mall_config": mall_config,
            },
        ),
        evidence_command(
            "csv_poc_index",
            [
                python,
                "scripts/csv_index.py",
                "--csv",
                poc_products_csv,
                "--engine",
                "marqo",
                "--index-name",
                index_name,
                "--marqo-url",
                marqo_url,
                "--marqo-model",
                marqo_model,
                "--mode",
                "reindex",
                "--validate-images",
                "--output",
                evidence_path(evidence_dir, "csv_poc_index"),
                "--markdown-output",
                optional_path(evidence_dir, "csv-index.md"),
            ],
            required=["poc_products_csv", "marqo.url", "marqo.index", "marqo.model"],
            config_values={
                "poc_products_csv": poc_products_csv,
                "marqo.url": marqo_url,
                "marqo.index": index_name,
                "marqo.model": marqo_model,
            },
            required_files={"poc_products_csv": poc_products_csv},
            file_validation_context={
                "min_products": str(int_value(config, "quality", "min_products", default=300)),
                "products_csv": products_csv,
                "mall_config": mall_config,
            },
        ),
        evidence_command(
            "mall_config",
            [
                python,
                "scripts/mall_config_check.py",
                "--config",
                mall_config,
                "--min-count",
                str(int_value(config, "expected_malls", default=1700)),
                "--output",
                evidence_path(evidence_dir, "mall_config"),
            ],
            required=["mall_config"],
            config_values={"mall_config": mall_config},
            required_files={"mall_config": mall_config},
            file_validation_context=primary_mall_context,
        ),
        evidence_command(
            "marqo_resource",
            [
                python,
                "scripts/marqo_resource_check.py",
                "--marqo-url",
                marqo_url,
                "--index",
                index_name,
                "--container",
                string_value(config, "marqo", "container", default="marqo-api"),
                "--storage-container",
                marqo_storage_container,
                "--storage-path",
                marqo_storage_path,
                "--expected-model",
                marqo_model,
                "--embedding-backend",
                embedding_backend,
                *marqo_embedding_args,
                "--max-cpu-percent",
                marqo_max_cpu_percent,
                "--max-memory-percent",
                marqo_max_memory_percent,
                "--max-storage-percent",
                marqo_max_storage_percent,
                "--min-storage-available-gb",
                marqo_min_storage_available_gb,
                "--output",
                evidence_path(evidence_dir, "marqo_resource"),
            ],
            required=[
                "marqo.url",
                "marqo.index",
                "marqo.model",
                "marqo.embedding_backend",
                "marqo.gemini_embedding_url" if provider_is_gemini else "marqo.qwen_embedding_url",
            ],
            config_values={
                "marqo.url": marqo_url,
                "marqo.index": index_name,
                "marqo.model": marqo_model,
                "marqo.embedding_backend": embedding_backend,
                **marqo_embedding_config_values,
                "marqo.max_cpu_percent": marqo_max_cpu_percent,
                "marqo.max_memory_percent": marqo_max_memory_percent,
                "marqo.storage_container": marqo_storage_container,
                "marqo.storage_path": marqo_storage_path,
                "marqo.max_storage_percent": marqo_max_storage_percent,
                "marqo.min_storage_available_gb": marqo_min_storage_available_gb,
            },
        ),
        evidence_command(
            "server_preflight",
            [
                python,
                "scripts/server_preflight_check.py",
                "--role",
                string_value(config, "server_preflight", "role", default="api"),
                "--require-docker",
                "--require-compose",
                "--require-pyodbc",
                "--expected-odbc-driver",
                string_value(config, "server_preflight", "expected_odbc_driver", default="ODBC Driver 18 for SQL Server"),
                "--output",
                evidence_path(evidence_dir, "server_preflight"),
            ],
            required=[],
            config_values={},
        ),
        env_preflight_command(config, evidence_dir),
        load_command(config, evidence_dir, "load_text_100_concurrent", "text", 100, 100, 3000, environment),
        load_command(config, evidence_dir, "load_image_30_concurrent", "image", 30, 30, 5000, environment),
        load_command(config, evidence_dir, "load_mixed_30_concurrent", "mixed", 30, 30, 5000, environment),
        mixed_traffic_command(config, evidence_dir, environment),
        evidence_command(
            "api_scale_comparison",
            [
                python,
                "scripts/load_compare.py",
                "--single-report",
                string_value(config, "api_scale", "single_report"),
                "--multi-report",
                string_value(config, "api_scale", "multi_report"),
                "--max-multi-p99-regression-percent",
                str(float(config_value(config, "api_scale", "max_multi_p99_regression_percent", default=25.0))),
                "--output",
                evidence_path(evidence_dir, "api_scale_comparison"),
                "--markdown-output",
                optional_path(evidence_dir, "api-scale.md"),
            ],
            required=[
                "api_scale.single_report",
                "api_scale.multi_report",
                *load_image_required_config_labels(),
                "mall_config",
            ],
            config_values={
                "api_scale.single_report": string_value(config, "api_scale", "single_report"),
                "api_scale.multi_report": string_value(config, "api_scale", "multi_report"),
                **load_image_config_values,
                "mall_config": mall_config,
            },
            required_files={
                "api_scale.single_report": string_value(config, "api_scale", "single_report"),
                "api_scale.multi_report": string_value(config, "api_scale", "multi_report"),
                **load_image_required_files,
                "mall_config": mall_config,
            },
            file_validation_context={
                "base_url": base_url,
                "origin": origin,
                "mall_id": mall_id,
                "expected_malls": str(int_value(config, "expected_malls", default=1700)),
                **load_image_config_values,
                "mall_config": mall_config,
                "single_api_server_count": "1",
                "multi_api_server_count": "2",
            },
        ),
        evidence_command(
            "representative_mall_sites",
            [
                python,
                "scripts/representative_site_check.py",
                "--sites",
                string_value(config, "representative_sites_config"),
                "--api-base-url",
                base_url,
                "--image-file",
                load_image_file,
                "--output",
                evidence_path(evidence_dir, "representative_mall_sites"),
                "--markdown-output",
                optional_path(evidence_dir, "representative-sites.md"),
            ],
            required=["representative_sites_config", "base_url", "load.image_file", "mall_config"],
            config_values={
                "representative_sites_config": string_value(config, "representative_sites_config"),
                "base_url": base_url,
                "load.image_file": load_image_file,
                "mall_config": mall_config,
            },
            required_files={
                "representative_sites_config": string_value(config, "representative_sites_config"),
                "load.image_file": load_image_file,
                "mall_config": mall_config,
            },
            file_validation_context={
                **primary_mall_context,
                "base_url": base_url,
                "api_key_available": "true" if not is_missing_required_config("api_key", api_key) else "",
                "required_sites": str(int_value(config, "required_sites", default=3)),
                "mall_config": mall_config,
                "require_saved_widget_probe_sources": "true"
                if bool_value(config, "representative_sites", "require_saved_widget_probe_sources", default=False)
                else "",
            },
        ),
        security_command(config, evidence_dir, environment=environment),
        ]
    )
    commands.append(
        evidence_command(
            "operational_readiness",
            [
                python,
                "scripts/operational_readiness.py",
                "--evidence-dir",
                str(evidence_dir),
                "--expected-malls",
                str(int_value(config, "expected_malls", default=1700)),
                "--required-sites",
                str(int_value(config, "required_sites", default=3)),
                "--output",
                evidence_path(evidence_dir, "operational_readiness"),
                "--markdown-output",
                optional_path(evidence_dir, "operational-readiness.md"),
                "--missing-commands-output",
                optional_path(evidence_dir, "missing-evidence.sh"),
                "--missing-commands-shell",
                "bash",
                "--missing-commands-project-root",
                string_value(config, "missing_commands", "project_root", default=str(ROOT)),
                "--missing-commands-evidence-dir",
                string_value(config, "missing_commands", "evidence_dir", default=str(evidence_dir)),
            ],
            required=[],
            config_values={},
        )
    )
    return commands


def input_preparation_commands(
    config: dict[str, Any],
    evidence_dir: str | Path,
    environment: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    python = sys.executable
    products_csv = string_value(config, "products_csv")
    poc_products_csv = string_value(config, "poc_products_csv", default=products_csv)
    mssql_export = config_value(config, "input_preparation", "mssql_export", default={})
    mssql_export = mssql_export if isinstance(mssql_export, dict) else {}
    poc_dataset = config_value(config, "input_preparation", "poc_dataset", default={})
    poc_dataset = poc_dataset if isinstance(poc_dataset, dict) else {}

    export_command = [
        python,
        "scripts/mssql_export_csv.py",
        "--connection-string",
        resolve_secret(config, "mssql_connection_string", environment),
        "--query",
        string_value(config, "mssql_query"),
        "--output-csv",
        products_csv,
        "--report-output",
        evidence_path(evidence_dir, "mssql_export"),
    ]
    export_limit = int_value(mssql_export, "limit", default=0)
    if export_limit > 0:
        export_command.extend(["--limit", str(export_limit)])
    export_fetch_size = int_value(mssql_export, "fetch_size", default=1000)
    if export_fetch_size > 0 and export_fetch_size != 1000:
        export_command.extend(["--fetch-size", str(export_fetch_size)])
    export_since = string_value(mssql_export, "since")
    if export_since:
        export_command.extend(["--since", export_since])
    mall_config = string_value(config, "mall_config")
    if mall_config:
        export_command.extend(["--mall-config", mall_config])
    product_id_column = string_value(mssql_export, "product_id_column")
    if product_id_column:
        export_command.extend(["--product-id-column", product_id_column])
    updated_at_column = string_value(mssql_export, "updated_at_column")
    if updated_at_column:
        export_command.extend(["--updated-at-column", updated_at_column])

    dataset_command = [
        python,
        "scripts/poc_dataset_builder.py",
        "--csv",
        products_csv,
        "--output-csv",
        poc_products_csv,
        "--report-output",
        evidence_path(evidence_dir, "poc_dataset"),
        "--target-size",
        str(int_value(poc_dataset, "target_size", default=int_value(config, "quality", "min_products", default=300))),
        "--min-products",
        str(int_value(poc_dataset, "min_products", default=int_value(config, "quality", "min_products", default=300))),
        "--min-per-category",
        str(int_value(poc_dataset, "min_per_category", default=10)),
    ]
    recommended_categories = config_value(poc_dataset, "recommended_categories", default=None)
    if isinstance(recommended_categories, list):
        categories = ",".join(str(category).strip() for category in recommended_categories if str(category).strip())
        if categories:
            dataset_command.extend(["--recommended-categories", categories])
    elif isinstance(recommended_categories, str) and recommended_categories.strip():
        dataset_command.extend(["--recommended-categories", recommended_categories.strip()])

    return [
        evidence_command(
            "mssql_export",
            export_command,
            required=["mssql_connection_string", "mssql_query", "products_csv"],
            config_values={
                "mssql_connection_string": resolve_secret(config, "mssql_connection_string", environment),
                "mssql_query": string_value(config, "mssql_query"),
                "products_csv": products_csv,
                "mall_config": mall_config,
                "input_preparation.mssql_export.fetch_size": str(export_fetch_size),
            },
            required_files={"mall_config": mall_config} if mall_config else {},
            produces_files={"products_csv": products_csv},
            evidence_file=evidence_path(evidence_dir, "mssql_export"),
            file_validation_context={"expected_malls": str(int_value(config, "expected_malls", default=1700))},
        ),
        evidence_command(
            "poc_dataset",
            dataset_command,
            required=["products_csv", "poc_products_csv"],
            config_values={"products_csv": products_csv, "poc_products_csv": poc_products_csv},
            required_files={"products_csv": products_csv},
            produces_files={"poc_products_csv": poc_products_csv},
            evidence_file=evidence_path(evidence_dir, "poc_dataset"),
            file_validation_context={
                "min_products": str(
                    int_value(
                        poc_dataset,
                        "min_products",
                        default=int_value(config, "quality", "min_products", default=300),
                    )
                ),
                "mall_config": string_value(config, "mall_config"),
            },
        ),
    ]


def load_command(
    config: dict[str, Any],
    evidence_dir: str | Path,
    name: str,
    mode: str,
    requests: int,
    concurrency: int,
    p95_ms: int,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    python = sys.executable
    base_url = string_value(config, "base_url")
    mall_id = string_value(config, "mall_id")
    origin = string_value(config, "origin")
    api_key = resolve_secret(config, "api_key", environment)
    admin_key = resolve_secret(config, "admin_key", environment)
    mall_config = string_value(config, "mall_config")
    override = config_value(config, "load", mode, default={})
    override = override if isinstance(override, dict) else {}
    image_files = load_image_files(config, override)
    image_file = image_files[0] if image_files else ""
    additional_image_files = image_files[1:]
    effective_p95_ms = int(override.get("p95_ms", p95_ms))
    effective_p99_ms = int(override.get("p99_ms", default_p99_ms(effective_p95_ms)))
    effective_request_timeout_seconds = int(
        override.get("request_timeout_seconds", default_request_timeout_seconds(effective_p99_ms))
    )
    effective_max_server_wait_avg_ms = int(
        override.get("max_server_wait_avg_ms", default_server_wait_avg_ms(effective_p95_ms))
    )
    effective_min_rps = float(
        override.get(
            "min_requests_per_second",
            default_min_rps(int(override.get("concurrency", concurrency)), effective_p95_ms),
        )
    )
    effective_max_process_rss_growth_mb = float(
        override.get("max_process_rss_growth_mb", default_max_process_rss_growth_mb())
    )
    api_server_count = load_api_server_count(config, override)
    admin_metrics_base_urls = load_admin_metrics_base_urls(config, override)
    allow_private_admin_metrics_targets = load_allow_private_admin_metrics_targets(config, override)
    command = [
        python,
        "scripts/load_test.py",
        "--base-url",
        base_url,
        "--mall-id",
        mall_id,
        "--api-key",
        api_key,
        "--origin",
        origin,
        "--admin-key",
        admin_key,
        "--mall-config",
        mall_config,
        "--mode",
        mode,
        "--requests",
        str(int(override.get("requests", requests))),
        "--concurrency",
        str(int(override.get("concurrency", concurrency))),
        "--p95-ms",
        str(effective_p95_ms),
        "--p99-ms",
        str(effective_p99_ms),
        "--request-timeout-seconds",
        str(effective_request_timeout_seconds),
        "--max-server-wait-avg-ms",
        str(effective_max_server_wait_avg_ms),
        "--min-rps",
        str(effective_min_rps),
        "--max-process-rss-growth-mb",
        str(effective_max_process_rss_growth_mb),
        "--output",
        evidence_path(evidence_dir, name),
        "--markdown-output",
        optional_path(evidence_dir, DEFAULT_EVIDENCE_FILES[name].replace(".json", ".md")),
    ]
    append_load_admin_metrics_options(
        command,
        api_server_count=api_server_count,
        admin_metrics_base_urls=admin_metrics_base_urls,
        allow_private_admin_metrics_targets=allow_private_admin_metrics_targets,
    )
    required = ["base_url", "mall_id", "api_key", "origin", "admin_key", "mall_config"]
    config_values = {
        "base_url": base_url,
        "mall_id": mall_id,
        "api_key": api_key,
        "origin": origin,
        "admin_key": admin_key,
        "mall_config": mall_config,
        "load.api_server_count": str(api_server_count),
        "load.admin_metrics_base_urls": load_admin_metrics_config_value(api_server_count, admin_metrics_base_urls),
    }
    if api_server_count >= 2:
        required.append("load.admin_metrics_base_urls")
    required_files = {"mall_config": mall_config}
    if mode in {"image", "mixed"}:
        command.extend(["--image-file", image_file])
        for additional_image_file in additional_image_files:
            command.extend(["--additional-image-file", additional_image_file])
        image_config_values, image_required_files = load_image_config_maps(image_files)
        required.extend(load_image_required_config_labels())
        config_values.update(image_config_values)
        required_files.update(image_required_files)
    return evidence_command(
        name,
        command,
        required=required,
        config_values=config_values,
        required_files=required_files,
        file_validation_context=primary_mall_validation_context(config, environment),
    )


def mixed_traffic_command(
    config: dict[str, Any],
    evidence_dir: str | Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    python = sys.executable
    base_url = string_value(config, "base_url")
    mall_id = string_value(config, "mall_id")
    origin = string_value(config, "origin")
    api_key = resolve_secret(config, "api_key", environment)
    admin_key = resolve_secret(config, "admin_key", environment)
    mall_config = string_value(config, "mall_config")
    override = config_value(config, "load", "mixed_traffic", default={})
    override = override if isinstance(override, dict) else {}
    image_files = load_image_files(config, override)
    image_file = image_files[0] if image_files else ""
    additional_image_files = image_files[1:]
    mall_sample_size = int(override.get("mall_sample_size", config_value(config, "load", "mall_sample_size", default=50)) or 0)
    effective_p95_ms = int(override.get("p95_ms", 5000))
    effective_p99_ms = int(override.get("p99_ms", default_p99_ms(effective_p95_ms)))
    effective_request_timeout_seconds = int(
        override.get("request_timeout_seconds", default_request_timeout_seconds(effective_p99_ms))
    )
    effective_max_server_wait_avg_ms = int(
        override.get("max_server_wait_avg_ms", default_server_wait_avg_ms(effective_p95_ms))
    )
    effective_min_rps = float(
        override.get(
            "min_requests_per_second",
            default_min_rps(int(override.get("concurrency", 100)), effective_p95_ms),
        )
    )
    effective_max_process_rss_growth_mb = float(
        override.get("max_process_rss_growth_mb", default_max_process_rss_growth_mb())
    )
    api_server_count = load_api_server_count(config, override)
    admin_metrics_base_urls = load_admin_metrics_base_urls(config, override)
    allow_private_admin_metrics_targets = load_allow_private_admin_metrics_targets(config, override)
    command = [
        python,
        "scripts/load_test.py",
        "--base-url",
        base_url,
        "--mall-id",
        mall_id,
        "--api-key",
        api_key,
        "--origin",
        origin,
        "--admin-key",
        admin_key,
        "--mall-config",
        mall_config,
        "--scenario",
        "mixed-traffic",
        "--active-users",
        str(int(override.get("active_users", 850))),
        "--requests",
        str(int(override.get("requests", 850))),
        "--concurrency",
        str(int(override.get("concurrency", 100))),
        "--p95-ms",
        str(effective_p95_ms),
        "--p99-ms",
        str(effective_p99_ms),
        "--request-timeout-seconds",
        str(effective_request_timeout_seconds),
        "--max-server-wait-avg-ms",
        str(effective_max_server_wait_avg_ms),
        "--min-rps",
        str(effective_min_rps),
        "--max-process-rss-growth-mb",
        str(effective_max_process_rss_growth_mb),
        "--image-file",
        image_file,
        "--output",
        evidence_path(evidence_dir, "load_mixed_traffic_850_active_users"),
        "--markdown-output",
        optional_path(evidence_dir, "load-mixed-traffic.md"),
    ]
    for additional_image_file in additional_image_files:
        command.extend(["--additional-image-file", additional_image_file])
    append_load_admin_metrics_options(
        command,
        api_server_count=api_server_count,
        admin_metrics_base_urls=admin_metrics_base_urls,
        allow_private_admin_metrics_targets=allow_private_admin_metrics_targets,
    )
    if mall_sample_size > 0:
        command.extend(["--mall-sample-size", str(mall_sample_size)])
        command.extend(["--mall-sample-strategy", "spread"])
    image_config_values, image_required_files = load_image_config_maps(image_files)
    required = [
        "base_url",
        "mall_id",
        "api_key",
        "origin",
        "admin_key",
        *load_image_required_config_labels(),
        "mall_config",
    ]
    config_values = {
        "base_url": base_url,
        "mall_id": mall_id,
        "api_key": api_key,
        "origin": origin,
        "admin_key": admin_key,
        **image_config_values,
        "mall_config": mall_config,
        "load.mixed_traffic.mall_sample_size": str(mall_sample_size),
        "load.api_server_count": str(api_server_count),
        "load.admin_metrics_base_urls": load_admin_metrics_config_value(api_server_count, admin_metrics_base_urls),
    }
    if api_server_count >= 2:
        required.append("load.admin_metrics_base_urls")
    return evidence_command(
        "load_mixed_traffic_850_active_users",
        command,
        required=required,
        config_values=config_values,
        required_files={
            **image_required_files,
            "mall_config": mall_config,
        },
        file_validation_context=primary_mall_validation_context(config, environment),
    )


def default_p99_ms(p95_ms: int) -> int:
    return max(int(p95_ms), int(round(float(p95_ms) * 1.6)))


def default_server_wait_avg_ms(p95_ms: int) -> int:
    return max(250, int(round(float(p95_ms) * 0.2)))


def default_request_timeout_seconds(p99_ms: int) -> int:
    return max(10, int(round((float(p99_ms) / 1000.0) * 2.0)))


def default_min_rps(concurrency: int, p95_ms: int) -> float:
    p95_seconds = max(float(p95_ms) / 1000.0, 0.001)
    return round(max(1.0, float(concurrency) / p95_seconds * 0.25), 2)


def default_max_process_rss_growth_mb() -> float:
    return 512.0


def service_env_values(config: dict[str, Any], environment: Mapping[str, str] | None = None) -> dict[str, str]:
    values = dict(environment or {})
    env_file = string_value(config, "env_check", "env_file")
    if is_missing_config_value(env_file):
        return values
    path = Path(env_file)
    if not path.exists():
        return values
    try:
        values.update(load_env_file(path))
    except Exception:
        return values
    return values


def sync_alert_webhook_is_configured(config: dict[str, Any], environment: Mapping[str, str] | None = None) -> bool:
    service_env = service_env_values(config, environment=environment)
    report = check_sync_alert_webhook_url(service_env.get("HAEORUM_SYNC_ALERT_WEBHOOK_URL"))
    return bool(report.get("configured") and report.get("ok"))


def security_command(
    config: dict[str, Any],
    evidence_dir: str | Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    python = sys.executable
    sync_alerting_configured = bool_value(config, "security", "sync_alerting_configured", default=False)
    sync_alert_webhook_configured = sync_alert_webhook_is_configured(config, environment=environment)
    sync_alerting_ready = sync_alerting_configured or sync_alert_webhook_configured
    env_file = string_value(config, "env_check", "env_file")
    service_env = service_env_values(config, environment=environment)
    reindex_systemd_service = string_value(config, "security", "reindex_systemd_service")
    command = [
        python,
        "scripts/security_check.py",
        "--base-url",
        string_value(config, "base_url"),
        "--nginx-config",
        string_value(config, "security", "nginx_config"),
        "--systemd-service",
        string_value(config, "security", "systemd_service"),
        "--sync-systemd-service",
        string_value(config, "security", "sync_systemd_service"),
        "--reindex-systemd-service",
        reindex_systemd_service,
        "--reindex-systemd-timer",
        string_value(config, "security", "reindex_systemd_timer"),
        "--logrotate-config",
        string_value(config, "security", "logrotate_config"),
        "--output",
        evidence_path(evidence_dir, "security"),
        "--markdown-output",
        optional_path(evidence_dir, "security.md"),
    ]
    if bool_value(config, "security", "mssql_ip_restricted", default=True):
        command.insert(4, "--mssql-ip-restricted")
    if sync_alerting_configured:
        command.insert(4, "--sync-alerting-configured")
    if env_file:
        command.extend(["--env-file", env_file])
    required = [
        "base_url",
        "security.nginx_config",
        "security.systemd_service",
        "security.sync_systemd_service",
        "security.reindex_systemd_service",
        "security.reindex_systemd_timer",
        "security.logrotate_config",
    ]
    if not sync_alerting_ready:
        required.append("security.sync_alerting_configured")
    return evidence_command(
        "security",
        command,
        required=required,
        config_values={
            "base_url": string_value(config, "base_url"),
            "security.nginx_config": string_value(config, "security", "nginx_config"),
            "security.systemd_service": string_value(config, "security", "systemd_service"),
            "security.sync_systemd_service": string_value(config, "security", "sync_systemd_service"),
            "security.reindex_systemd_service": string_value(config, "security", "reindex_systemd_service"),
            "security.reindex_systemd_timer": string_value(config, "security", "reindex_systemd_timer"),
            "security.logrotate_config": string_value(config, "security", "logrotate_config"),
            "security.sync_alerting_configured": "true" if sync_alerting_configured else "",
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL": "configured" if sync_alert_webhook_configured else "",
            "env_check.env_file": env_file,
        },
        required_files={
            "security.nginx_config": string_value(config, "security", "nginx_config"),
            "security.systemd_service": string_value(config, "security", "systemd_service"),
            "security.sync_systemd_service": string_value(config, "security", "sync_systemd_service"),
            "security.reindex_systemd_service": reindex_systemd_service,
            "security.reindex_systemd_timer": string_value(config, "security", "reindex_systemd_timer"),
            "security.logrotate_config": string_value(config, "security", "logrotate_config"),
        },
        file_validation_context={
            "image_max_mb": service_env.get("HAEORUM_MAX_IMAGE_MB", ""),
            "reindex_systemd_service": reindex_systemd_service,
        },
    )


def env_preflight_command(config: dict[str, Any], evidence_dir: str | Path) -> dict[str, Any]:
    python = sys.executable
    env_file = string_value(config, "env_check", "env_file")
    role = string_value(config, "env_check", "role", default=string_value(config, "server_preflight", "role", default="api"))
    api_server_count = int_value(config, "env_check", "api_server_count", default=2)
    return evidence_command(
        "env_preflight",
        [
            python,
            "scripts/env_check.py",
            "--env-file",
            env_file,
            "--role",
            role,
            "--api-server-count",
            str(api_server_count),
            "--output",
            evidence_path(evidence_dir, "env_preflight"),
            "--markdown-output",
            optional_path(evidence_dir, "env-check.md"),
        ],
        required=["env_check.env_file"],
        config_values={"env_check.env_file": env_file},
        required_files={"env_check.env_file": env_file},
        file_validation_context={
            "role": role,
            "api_server_count": str(api_server_count),
        },
    )


def evidence_command(
    name: str,
    command: list[str],
    required: list[str],
    config_values: dict[str, str],
    required_files: dict[str, str] | None = None,
    produces_files: dict[str, str] | None = None,
    capture_stdout_to: str | None = None,
    evidence_file: str | None = None,
    file_validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing = [field for field in required if is_missing_required_config(field, config_values.get(field, ""))]
    return {
        "name": name,
        "command": command,
        "redacted_command": redact_command(command),
        "missing_config": missing,
        "required_files": required_files or {},
        "missing_input_files": missing_input_files(required_files or {}),
        "produces_files": produces_files or {},
        "capture_stdout_to": capture_stdout_to,
        "evidence_file": evidence_file or capture_stdout_to or output_option_value(command),
        "file_validation_context": file_validation_context or {},
    }


def missing_input_files(required_files: Mapping[str, str], planned_output_paths: set[str] | None = None) -> list[str]:
    planned = planned_output_paths or set()
    missing = []
    for label, path in required_files.items():
        if is_missing_config_value(path):
            continue
        if normalized_path_key(path) in planned:
            continue
        if not Path(path).exists():
            missing.append(label)
    return missing


def input_file_validation_problems(
    item: Mapping[str, Any],
    planned_output_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    planned = planned_output_paths or set()
    required_files = item.get("required_files") if isinstance(item.get("required_files"), dict) else {}
    file_validation_context = (
        item.get("file_validation_context")
        if isinstance(item.get("file_validation_context"), dict)
        else {}
    )
    problems: list[dict[str, Any]] = []
    product_file_messages: dict[str, list[str]] = {}
    for label, path in required_files.items():
        if label not in {"products_csv", "poc_products_csv"}:
            continue
        text_path = str(path or "").strip()
        if is_missing_config_value(text_path) or normalized_path_key(text_path) in planned or not Path(text_path).exists():
            continue
        product_file_messages[label] = product_csv_file_problems(text_path, label, file_validation_context)
    pair_messages = product_csv_pair_problem_messages(required_files, file_validation_context, planned)
    if pair_messages and "poc_products_csv" in required_files:
        product_file_messages.setdefault("poc_products_csv", []).extend(pair_messages)

    for label, path in required_files.items():
        text_path = str(path or "").strip()
        if is_missing_config_value(text_path) or normalized_path_key(text_path) in planned or not Path(text_path).exists():
            continue
        messages: list[str] = []
        if label in {"products_csv", "poc_products_csv"}:
            messages = product_file_messages.get(label) or []
        elif label == "mall_config_source":
            messages = mall_config_source_file_problems(text_path, file_validation_context)
        elif label == "mall_config":
            messages = mall_config_file_problems(text_path, file_validation_context)
        elif label == "quality_cases_file":
            quality_context = file_validation_context
            if product_file_messages.get("poc_products_csv"):
                quality_context = {**file_validation_context, "skip_poc_dataset_cross_check": "true"}
            messages = quality_cases_file_problems(text_path, quality_context)
        elif label == "representative_sites_config":
            messages = representative_sites_config_problems(text_path, file_validation_context)
        elif label in {"api_scale.single_report", "api_scale.multi_report"}:
            messages = load_report_file_problems(text_path, label, file_validation_context)
        elif label == "load.image_file" or label.startswith("load.image_files["):
            messages = load_image_file_problems(text_path, file_validation_context)
        elif label == "env_check.env_file":
            messages = service_env_file_problems(text_path, file_validation_context)
        elif label.startswith("security."):
            messages = security_input_file_problems(text_path, label, file_validation_context)
        if messages:
            problems.append(
                {
                    "name": label,
                    "path": text_path,
                    "problem_count": len(messages),
                    "problems": messages[:20],
                }
            )
    return problems


def product_csv_file_problems(path: str, label: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    try:
        from app.sync import CsvProductSource
        from scripts.csv_index import summarize_products
        from scripts.sample_data_guard import builtin_sample_dataset_profile, is_local_sample_evidence

        products = CsvProductSource(Path(path)).fetch_all()
        summary = summarize_products(products)
        source_profile = builtin_sample_dataset_profile(path, (product.product_id for product in products))
    except Exception as exc:
        return [f"parse: {exc}"]

    messages: list[str] = []
    total_products = int(summary.get("total_products", 0) or 0)
    active_products = int(summary.get("active_products", 0) or 0)
    missing_active_image_count = int(summary.get("missing_active_image_count", 0) or 0)
    active_unsafe_image_url_count = int(summary.get("active_unsafe_image_url_count", 0) or 0)
    active_unsafe_image_url_ids = [
        str(product_id)
        for product_id in summary.get("active_unsafe_image_url_product_ids") or []
        if str(product_id)
    ]
    active_non_https_image_url_count = int(summary.get("active_non_https_image_url_count", 0) or 0)
    active_non_https_image_url_ids = [
        str(product_id)
        for product_id in summary.get("active_non_https_image_url_product_ids") or []
        if str(product_id)
    ]
    active_product_rows = [product for product in products if product.active]
    missing_active_product_url_ids = sorted(product.product_id for product in active_product_rows if not product.product_url)
    missing_active_mall_id_ids = sorted(product.product_id for product in active_product_rows if not product.mall_id)
    active_unsafe_product_url_count = int(summary.get("active_unsafe_product_url_count", 0) or 0)
    active_unsafe_product_url_ids = [
        str(product_id)
        for product_id in summary.get("active_unsafe_product_url_product_ids") or []
        if str(product_id)
    ]
    active_product_url_product_id_mismatch_count = int(
        summary.get("active_product_url_product_id_mismatch_count", 0) or 0
    )
    active_product_url_product_id_mismatch_ids = [
        str(product_id)
        for product_id in summary.get("active_product_url_product_id_mismatch_product_ids") or []
        if str(product_id)
    ]
    min_products = parse_positive_int(file_validation_context.get("min_products"), default=300)
    min_category_count = parse_positive_int(file_validation_context.get("min_category_count"), default=3)

    if total_products <= 0:
        messages.append("total_products: CSV must contain product rows")
    if active_products < min_products:
        messages.append(f"active_products: expected at least {min_products}, found {active_products}")
    duplicate_ids = summary.get("duplicate_product_ids") or []
    if duplicate_ids:
        messages.append(f"duplicate_product_ids: {', '.join(str(product_id) for product_id in duplicate_ids[:5])}")
    category_count = int(summary.get("category_count", 0) or 0)
    if category_count < min_category_count:
        messages.append(f"category_count: expected at least {min_category_count}, found {category_count}")
    if missing_active_image_count:
        messages.append(f"missing_active_image_count: expected 0, found {missing_active_image_count}")
    if active_unsafe_image_url_count:
        sample = ", ".join(active_unsafe_image_url_ids[:5])
        suffix = f" ({sample})" if sample else ""
        messages.append(f"active_unsafe_image_url_count: expected 0, found {active_unsafe_image_url_count}{suffix}")
    if active_non_https_image_url_count:
        sample = ", ".join(active_non_https_image_url_ids[:5])
        suffix = f" ({sample})" if sample else ""
        messages.append(f"active_non_https_image_url_count: expected 0, found {active_non_https_image_url_count}{suffix}")
    if missing_active_product_url_ids:
        messages.append(f"missing_active_product_url_count: expected 0, found {len(missing_active_product_url_ids)}")
    if missing_active_mall_id_ids:
        messages.append(f"missing_active_mall_id_count: expected 0, found {len(missing_active_mall_id_ids)}")
    if active_unsafe_product_url_count:
        sample = ", ".join(active_unsafe_product_url_ids[:5])
        suffix = f" ({sample})" if sample else ""
        messages.append(f"active_unsafe_product_url_count: expected 0, found {active_unsafe_product_url_count}{suffix}")
    if active_product_url_product_id_mismatch_count:
        sample = ", ".join(active_product_url_product_id_mismatch_ids[:5])
        suffix = f" ({sample})" if sample else ""
        messages.append(
            "active_product_url_product_id_mismatch_count: expected 0, "
            f"found {active_product_url_product_id_mismatch_count}{suffix}"
        )
    messages.extend(product_csv_mall_config_problem_messages(active_product_rows, file_validation_context))
    if is_local_sample_evidence(source_profile):
        messages.append("source: built-in sample dataset is local-only and cannot prove operational readiness")
    return messages


def product_csv_pair_problem_messages(
    required_files: Mapping[str, str],
    file_validation_context: Mapping[str, Any],
    planned_output_paths: set[str],
) -> list[str]:
    products_csv = str(required_files.get("products_csv") or file_validation_context.get("products_csv") or "").strip()
    poc_products_csv = str(
        required_files.get("poc_products_csv") or file_validation_context.get("poc_products_csv") or ""
    ).strip()
    if (
        is_missing_config_value(products_csv)
        or is_missing_config_value(poc_products_csv)
        or normalized_path_key(products_csv) in planned_output_paths
        or normalized_path_key(poc_products_csv) in planned_output_paths
        or not Path(products_csv).exists()
        or not Path(poc_products_csv).exists()
    ):
        return []

    messages: list[str] = []
    if normalized_path_key(products_csv) == normalized_path_key(poc_products_csv):
        messages.append(
            "poc_products_csv: must not point to the same file as products_csv; "
            "build a dedicated PoC CSV with poc_dataset_builder.py"
        )
        return messages

    try:
        from app.sync import CsvProductSource

        full_products = CsvProductSource(Path(products_csv)).fetch_all()
        poc_products = CsvProductSource(Path(poc_products_csv)).fetch_all()
    except Exception as exc:
        return [f"poc_products_csv: could not compare with products_csv: {exc}"]

    full_by_id = {product.product_id: product for product in full_products}
    missing_ids = sorted({product.product_id for product in poc_products if product.product_id not in full_by_id})
    active_in_poc_but_not_full = sorted(
        {
            product.product_id
            for product in poc_products
            if product.active and product.product_id in full_by_id and not full_by_id[product.product_id].active
        }
    )
    source_field_mismatches = poc_source_field_mismatches(poc_products, full_by_id)
    if missing_ids:
        messages.append(
            "poc_products_csv: "
            f"{len(missing_ids)} product IDs are not present in products_csv: {', '.join(missing_ids[:5])}"
        )
    if active_in_poc_but_not_full:
        messages.append(
            "poc_products_csv: "
            f"{len(active_in_poc_but_not_full)} active products are inactive or hidden in products_csv: "
            f"{', '.join(active_in_poc_but_not_full[:5])}"
        )
    if source_field_mismatches:
        samples = [
            f"{item['product_id']}({', '.join(item['fields'])})"
            for item in source_field_mismatches[:5]
        ]
        messages.append(
            "poc_products_csv: "
            f"{len(source_field_mismatches)} products changed source fields from products_csv: "
            f"{', '.join(samples)}"
        )
    return messages


def poc_source_field_mismatches(
    poc_products: list[Any],
    full_by_id: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for product in poc_products:
        product_id = str(getattr(product, "product_id", "") or "").strip()
        source = full_by_id.get(product_id)
        if source is None:
            continue
        changed_fields = [
            output_name
            for attr_name, output_name in POC_SOURCE_MATCH_FIELDS
            if normalized_product_source_value(getattr(product, attr_name, None))
            != normalized_product_source_value(getattr(source, attr_name, None))
        ]
        if changed_fields:
            mismatches.append({"product_id": product_id, "fields": changed_fields})
    return mismatches


def normalized_product_source_value(value: Any) -> str:
    return str(value or "").strip()


def product_csv_mall_config_problem_messages(products: list[Any], context: Mapping[str, Any]) -> list[str]:
    mall_config_path = str(context.get("mall_config") or "").strip()
    if is_missing_config_value(mall_config_path) or not Path(mall_config_path).exists():
        return []
    try:
        report = validate_mall_config(Path(mall_config_path), min_count=1)
    except Exception:
        return []

    enabled_mall_ids = {str(mall_id).strip() for mall_id in report.get("enabled_mall_ids") or [] if str(mall_id).strip()}
    product_url_prefixes = (
        report.get("enabled_mall_product_url_prefixes")
        if isinstance(report.get("enabled_mall_product_url_prefixes"), dict)
        else {}
    )
    if not enabled_mall_ids:
        return []

    unknown_mall_counts: dict[str, int] = {}
    product_url_mismatches: list[dict[str, str]] = []
    for product in products:
        mall_id = str(getattr(product, "mall_id", "") or "").strip()
        if not mall_id:
            continue
        if mall_id not in enabled_mall_ids:
            unknown_mall_counts[mall_id] = unknown_mall_counts.get(mall_id, 0) + 1
            continue
        prefix = str(product_url_prefixes.get(mall_id) or "").strip().rstrip("/")
        product_url = str(getattr(product, "product_url", "") or "").strip()
        if prefix and product_url and not url_matches_prefix(product_url, prefix):
            product_url_mismatches.append(
                {
                    "product_id": str(getattr(product, "product_id", "") or ""),
                    "mall_id": mall_id,
                    "product_url": product_url,
                    "expected_prefix": prefix,
                }
            )

    messages: list[str] = []
    if unknown_mall_counts:
        samples = [
            f"{mall_id} ({count} products)"
            for mall_id, count in sorted(unknown_mall_counts.items())[:5]
        ]
        messages.append(f"mall_id: active products reference mall_ids not enabled in mall_config: {', '.join(samples)}")
    for item in product_url_mismatches[:5]:
        messages.append(
            f"product_url: product {item['product_id']!r} for mall_id {item['mall_id']!r} does not match mall_config product_url_template prefix {item['expected_prefix']!r}"
        )
    if len(product_url_mismatches) > 5:
        messages.append(f"product_url: {len(product_url_mismatches) - 5} additional active products do not match mall_config product_url_template prefixes")
    return messages


def mall_config_source_file_problems(path: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    expected_malls = parse_positive_int(file_validation_context.get("expected_malls"), default=1700)
    try:
        report = build_mall_config_from_csv(path, sort_by_mall_id=True, min_count=expected_malls)
    except Exception as exc:
        return [f"parse: {exc}"]
    messages: list[str] = []
    if report.get("ok") is not True:
        for problem in report.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            field = str(problem.get("field") or "unknown")
            message = str(problem.get("message") or "invalid").strip()
            row = problem.get("row")
            prefix = f"row {row} " if row else ""
            messages.append(f"{field}: {prefix}{message}")
        if not messages:
            messages.append("ok: mall config builder source did not pass")
    if int(report.get("mall_count", 0) or 0) < expected_malls:
        messages.append(f"mall_count: expected at least {expected_malls}")
    if int(report.get("enabled_count", 0) or 0) < expected_malls:
        messages.append(f"enabled_count: expected at least {expected_malls}")
    if int(report.get("generated_api_key_count", 0) or 0) != 0:
        messages.append("generated_api_key_count: source must contain real API keys; do not generate fallback keys")
    return sorted(set(messages))


def mall_config_file_problems(path: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    expected_malls = parse_positive_int(file_validation_context.get("expected_malls"), default=1700)
    try:
        report = validate_mall_config(Path(path), min_count=expected_malls)
    except Exception as exc:
        return [f"parse: {exc}"]
    context_messages = mall_config_context_problem_messages(report, file_validation_context)
    if report.get("ok") is True:
        return context_messages
    messages: list[str] = []
    for problem in report.get("problems") or []:
        if not isinstance(problem, dict):
            continue
        location = []
        if "index" in problem:
            location.append(f"index {problem.get('index')}")
        if problem.get("mall_id"):
            location.append(f"mall {problem.get('mall_id')}")
        field = str(problem.get("field") or "unknown")
        message = str(problem.get("message") or "invalid mall config").strip()
        prefix = " ".join(location)
        messages.append(f"{prefix + ' ' if prefix else ''}{field}: {message}")
    if not messages:
        messages.append("mall config did not pass validation")
    messages.extend(context_messages)
    return messages


def mall_config_context_problem_messages(report: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    mall_id = str(context.get("mall_id") or "").strip()
    origin = normalized_representative_origin(context.get("origin"))
    if is_missing_config_value(mall_id):
        return []

    enabled_mall_ids = {str(value).strip() for value in report.get("enabled_mall_ids") or [] if str(value).strip()}
    enabled_origins = report.get("enabled_mall_origins") if isinstance(report.get("enabled_mall_origins"), dict) else {}
    api_key_hashes = (
        report.get("enabled_mall_api_key_hashes")
        if isinstance(report.get("enabled_mall_api_key_hashes"), dict)
        else {}
    )
    messages: list[str] = []
    if enabled_mall_ids and mall_id not in enabled_mall_ids:
        messages.append(f"configured mall_id {mall_id!r} is not enabled in mall_config")
        return messages
    allowed_origins = [str(value).strip().rstrip("/") for value in enabled_origins.get(mall_id, [])]
    if origin and allowed_origins and origin not in allowed_origins:
        messages.append(
            f"configured origin {origin!r} is not allowed for mall_id {mall_id!r}; allowed_origins={allowed_origins}"
        )
    expected_api_key_hash = str(api_key_hashes.get(mall_id) or "").strip()
    configured_api_key_hash = str(context.get("api_key_hash") or "").strip()
    if expected_api_key_hash and configured_api_key_hash and configured_api_key_hash != expected_api_key_hash:
        messages.append(f"configured api_key does not match mall_config api_key for mall_id {mall_id!r}")
    return messages


def quality_cases_file_problems(path: str, file_validation_context: Mapping[str, Any] | None = None) -> list[str]:
    try:
        cases, _source = load_quality_cases(path)
    except Exception as exc:
        return [f"parse: {exc}"]
    messages: list[str] = []
    case_summaries: list[dict[str, Any]] = []
    context = file_validation_context or {}
    max_mb = parse_positive_int(context.get("image_max_mb"), default=5)
    max_bytes = max_mb * 1024 * 1024
    min_dimension = parse_positive_int(context.get("image_min_dimension"), default=16)
    seen_image_paths: dict[str, str] = {}
    seen_image_digests: dict[str, str] = {}
    for case in cases:
        case_name = str(case.get("name") or "").strip()
        query = case.get("query") if isinstance(case.get("query"), dict) else {}
        has_text = bool(str(query.get("q") or "").strip())
        has_image = bool(query.get("image"))
        if has_text and has_image:
            query_type = "text_image"
        elif has_image:
            query_type = "image"
        else:
            query_type = "text"
        checks: list[dict[str, Any]] = []
        if case.get("expected_category"):
            checks.append({"name": "expected_category", "expected": case.get("expected_category")})
        if case.get("expected_top_product_id"):
            checks.append({"name": "expected_top_product_id", "expected": case.get("expected_top_product_id")})
        if "expected_min_results" in case:
            checks.append({"name": "expected_min_results", "expected": case.get("expected_min_results")})
        if "expected_low_confidence" in case:
            checks.append({"name": "expected_low_confidence", "expected": case.get("expected_low_confidence")})
        case_summaries.append(
            {
                "name": case.get("name"),
                "query_type": query_type,
                "tags": case.get("tags") or [],
                "checks": checks,
            }
        )
        if has_image:
            image_path = str(case.get("image_path") or "").strip()
            if not image_path:
                messages.append(f"case {case.get('name')}: image or mixed cases must use image_path")
            else:
                normalized_image_path = str(Path(image_path).resolve())
                previous_case = seen_image_paths.get(normalized_image_path)
                if previous_case:
                    messages.append(f"case {case.get('name')}: image_path is reused by case {previous_case!r}")
                else:
                    seen_image_paths[normalized_image_path] = case_name
            if image_path and not Path(image_path).exists():
                messages.append(f"case {case.get('name')}: image_path does not exist")
            elif image_path:
                try:
                    image_bytes = Path(image_path).read_bytes()
                    image = validate_image_bytes(
                        image_bytes,
                        max_bytes=max_bytes,
                        min_dimension=min_dimension,
                    )
                except Exception as exc:
                    messages.append(f"case {case.get('name')}: image_path is not a valid reference image: {exc}")
                else:
                    image_digest = hashlib.sha256(image_bytes).hexdigest()
                    previous_case = seen_image_digests.get(image_digest)
                    if previous_case:
                        messages.append(f"case {case.get('name')}: image_path content duplicates case {previous_case!r}")
                    else:
                        seen_image_digests[image_digest] = case_name
                    if image.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                        messages.append(f"case {case.get('name')}: image_path must be JPG, PNG, or WEBP")
    contract = summarize_case_contract(case_summaries)
    if contract.get("ok") is not True:
        messages.extend(quality_case_contract_problem_messages(contract))
    messages.extend(quality_case_dataset_problem_messages(cases, context))
    return messages


def quality_case_dataset_problem_messages(cases: list[dict[str, Any]], context: Mapping[str, Any]) -> list[str]:
    if str(context.get("skip_poc_dataset_cross_check") or "").strip().lower() in {"1", "true", "yes"}:
        return []
    csv_path = str(context.get("poc_products_csv") or "").strip()
    if is_missing_config_value(csv_path) or not Path(csv_path).exists():
        return []
    try:
        from app.sync import CsvProductSource

        products = CsvProductSource(Path(csv_path)).fetch_all()
    except Exception:
        return []

    active_products = [product for product in products if product.active]
    active_category_counts: dict[str, int] = {}
    configured_mall_id = str(context.get("mall_id") or "").strip()
    active_configured_mall_category_counts: dict[str, int] = {}
    for product in active_products:
        category = str(product.category or "").strip()
        if category:
            active_category_counts[category] = active_category_counts.get(category, 0) + 1
            if configured_mall_id and str(getattr(product, "mall_id", "") or "").strip() == configured_mall_id:
                active_configured_mall_category_counts[category] = (
                    active_configured_mall_category_counts.get(category, 0) + 1
                )
    all_products_by_id = {str(product.product_id): product for product in products}
    active_product_ids = {str(product.product_id) for product in active_products}

    messages: list[str] = []
    for case in cases:
        name = str(case.get("name") or "(unnamed)")
        expected_category = str(case.get("expected_category") or "").strip()
        expected_top_product_id = str(case.get("expected_top_product_id") or "").strip()
        expected_min_results = parse_positive_int(case.get("expected_min_results"), default=0)
        if expected_category:
            category_count = active_category_counts.get(expected_category, 0)
            if category_count <= 0:
                messages.append(f"case {name}: expected_category {expected_category!r} is not present in active PoC CSV")
            elif expected_min_results and category_count < expected_min_results:
                messages.append(
                    f"case {name}: expected_category {expected_category!r} has only {category_count} active products, below expected_min_results {expected_min_results}"
                )
            if configured_mall_id:
                configured_mall_category_count = active_configured_mall_category_counts.get(expected_category, 0)
                if configured_mall_category_count <= 0:
                    messages.append(
                        f"case {name}: expected_category {expected_category!r} has no active products for configured mall_id {configured_mall_id!r} in PoC CSV"
                    )
                elif expected_min_results and configured_mall_category_count < expected_min_results:
                    messages.append(
                        f"case {name}: expected_category {expected_category!r} has only {configured_mall_category_count} active products for configured mall_id {configured_mall_id!r}, below expected_min_results {expected_min_results}"
                    )
        if expected_top_product_id:
            product = all_products_by_id.get(expected_top_product_id)
            if product is None:
                messages.append(f"case {name}: expected_top_product_id {expected_top_product_id!r} is not present in PoC CSV")
            elif expected_top_product_id not in active_product_ids:
                messages.append(f"case {name}: expected_top_product_id {expected_top_product_id!r} is not active in PoC CSV")
            elif expected_category and str(product.category or "").strip() != expected_category:
                messages.append(
                    f"case {name}: expected_top_product_id {expected_top_product_id!r} category {str(product.category or '').strip()!r} does not match expected_category {expected_category!r}"
                )
            elif configured_mall_id:
                product_mall_id = str(getattr(product, "mall_id", "") or "").strip()
                if product_mall_id != configured_mall_id:
                    mall_label = product_mall_id or "(missing)"
                    messages.append(
                        f"case {name}: expected_top_product_id {expected_top_product_id!r} belongs to mall_id {mall_label!r}, not configured mall_id {configured_mall_id!r}"
                    )
    return messages


def quality_case_contract_problem_messages(contract: Mapping[str, Any]) -> list[str]:
    messages: list[str] = []
    missing_type_counts = contract.get("missing_type_counts")
    if isinstance(missing_type_counts, dict):
        for query_type, summary in missing_type_counts.items():
            actual = summary.get("actual") if isinstance(summary, dict) else None
            expected = summary.get("expected") if isinstance(summary, dict) else None
            messages.append(f"case_contract: {query_type} cases below minimum {actual}/{expected}")
    if contract.get("missing_low_confidence_case") is True:
        messages.append("case_contract: missing expected_low_confidence=true case")
    if contract.get("missing_text_variant_case") is True:
        messages.append("case_contract: missing typo_or_synonym text variant case")
    for name in contract.get("missing_expectation_cases") or []:
        messages.append(f"case_contract: {name} has no expected category/top product")
    for name in contract.get("low_min_result_cases") or []:
        messages.append(f"case_contract: {name} expected_min_results is below minimum")
    for name in contract.get("duplicate_case_names") or []:
        messages.append(f"case_contract: duplicate case name {name!r}")
    return messages


def representative_sites_config_problems(path: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    try:
        sites = load_site_configs(Path(path))
    except Exception as exc:
        return [f"parse: {exc}"]
    problems: list[str] = []
    required_sites = parse_positive_int(file_validation_context.get("required_sites"), default=3)
    if len(sites) < required_sites:
        problems.append(f"site_count: expected at least {required_sites}, found {len(sites)}")
    collection = validate_site_collection(sites)
    for problem in collection.get("problems") or []:
        for group in collection.get(problem) or []:
            site_labels = ", ".join(
                f"#{site.get('index')}:{site.get('mall_id') or site.get('name') or 'unknown'}"
                for site in group.get("sites") or []
            )
            problems.append(f"{problem}: {group.get('value')} reused by {site_labels}")
    args = argparse.Namespace(
        api_base_url=str(file_validation_context.get("base_url") or ""),
        api_key="configured-public-api-key" if file_validation_context.get("api_key_available") == "true" else "",
        skip_page=False,
        skip_api=False,
    )
    for index, site in enumerate(sites, start=1):
        check = validate_site_config(site, args)
        for problem in check.get("problems") or []:
            if not isinstance(problem, dict):
                continue
            field = str(problem.get("field") or "unknown")
            message = str(problem.get("message") or "").strip()
            problems.append(f"site {index} {field}: {message}")
    problems.extend(representative_site_mall_config_problem_messages(sites, file_validation_context))
    problems.extend(representative_site_widget_probe_problem_messages(path, sites, file_validation_context))
    return problems


def representative_site_widget_probe_problem_messages(
    config_path: str,
    sites: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> list[str]:
    base_dir = Path(config_path).resolve().parent
    configured_api_base_url = str(context.get("base_url") or "").strip()
    messages: list[str] = []
    seen_local_source_paths: dict[str, str] = {}
    seen_local_source_digests: dict[str, tuple[str, int]] = {}
    for index, site in enumerate(sites, start=1):
        source_entries = probe_source_entries(site, base_dir=base_dir)
        if context.get("require_saved_widget_probe_sources") == "true" and not has_local_explicit_probe_sources(source_entries):
            messages.append(
                f"site {index} widget_probe_sources: saved PC/mobile HTML sources are required by "
                "representative_sites.require_saved_widget_probe_sources"
            )
            continue
        if not source_entries:
            continue
        if has_local_explicit_probe_sources(source_entries):
            missing_variants = missing_pc_mobile_variants(source_entries)
            if missing_variants:
                messages.append(
                    f"site {index} widget_probe_sources: saved PC/mobile coverage incomplete; "
                    f"missing {', '.join(missing_variants)}. Use filenames with pc/mobile markers or "
                    "objects like {\"variant\":\"mobile\",\"source\":\"saved-mobile.html\"}."
                )
        mall_id = str(site.get("mall_id") or site.get("site_id") or "").strip()
        api_base_url = str(site.get("api_base_url") or configured_api_base_url).strip()
        widget_src = site_widget_src(site, api_base_url, "")
        api_key = str(site.get("api_key") or "").strip()
        page_url = str(site.get("url") or "").strip()
        for source_index, entry in enumerate(source_entries, start=1):
            source = str(entry.get("source") or "")
            if is_remote_probe_source(source):
                continue
            if has_explicit_probe_source(entry):
                source_path_key = str(Path(source).resolve()).casefold()
                source_label = f"site {index} widget_probe_source {source_index}"
                previous_source_label = seen_local_source_paths.get(source_path_key)
                if previous_source_label:
                    messages.append(
                        f"{source_label}: saved HTML source file is reused by {previous_source_label}; "
                        "provide separate saved PC/mobile HTML captures for each representative site"
                    )
                else:
                    seen_local_source_paths[source_path_key] = source_label
            else:
                source_label = f"site {index} widget_probe_source {source_index}"
            source_path = Path(source)
            if not source_path.exists():
                messages.append(f"site {index} widget_probe_source {source_index}: file not found {source}")
                continue
            try:
                source_bytes = source_path.read_bytes()
            except OSError as exc:
                messages.append(f"site {index} widget_probe_source {source_index}: read failed {exc}")
                continue
            if has_explicit_probe_source(entry):
                source_digest = hashlib.sha256(source_bytes).hexdigest()
                previous_digest = seen_local_source_digests.get(source_digest)
                if previous_digest:
                    if previous_digest[1] == index:
                        messages.append(
                            f"{source_label}: saved HTML source content duplicates {previous_digest[0]}; "
                            "provide independently captured desktop/mobile HTML for this representative site"
                        )
                    else:
                        messages.append(
                            f"{source_label}: saved HTML source content duplicates {previous_digest[0]}; "
                            "provide independently captured PC/mobile HTML for each representative site"
                        )
                elif not previous_digest:
                    seen_local_source_digests[source_digest] = (source_label, index)
            body = source_bytes.decode("utf-8", errors="replace")
            report = analyze_html_source(
                str(source_path),
                body,
                api_base_url=api_base_url,
                widget_src=widget_src,
                mall_id=mall_id,
                api_key=api_key,
                page_url=page_url,
            )
            if report.get("data_auto_init_ready") is not True:
                risks = report.get("blocking_risks") or report.get("risks") or ["not_ready"]
                messages.append(
                    f"site {index} widget_probe_source {source_index}: data_auto_init_ready=false ({', '.join(str(risk) for risk in risks)})"
                )
            elif report.get("ok") is not True:
                risks = report.get("risks") or ["unsafe_recommended_selectors"]
                messages.append(
                    f"site {index} widget_probe_source {source_index}: recommended selectors are not safe ({', '.join(str(risk) for risk in risks)})"
                )
            else:
                messages.extend(
                    representative_site_widget_probe_preview_problem_messages(
                        source_label=source_label,
                        source_path=source_path,
                        body=body,
                        report=report,
                    )
                )
    return messages


def representative_site_widget_probe_preview_problem_messages(
    *,
    source_label: str,
    source_path: Path,
    body: str,
    report: Mapping[str, Any],
) -> list[str]:
    recommendation = report.get("recommendation") if isinstance(report.get("recommendation"), Mapping) else {}
    if recommendation.get("ready") is not True:
        return []
    snippet = str(recommendation.get("snippet") or "").strip()
    if not snippet:
        return [f"{source_label}: preview validation failed (snippet_not_embedded)"]
    preview_body = inject_snippet_preview_html(body, snippet)
    validation = validate_preview_html_body(
        preview_body,
        snippet,
        name=report.get("name"),
        mall_id=str(report.get("mall_id") or ""),
        source_file=str(source_path),
    )
    if validation.get("ok") is True:
        return []
    problems = ", ".join(str(problem) for problem in validation.get("problems") or []) or "unknown"
    return [f"{source_label}: preview validation failed ({problems})"]


def representative_site_mall_config_problem_messages(
    sites: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> list[str]:
    mall_config_path = str(context.get("mall_config") or "").strip()
    if is_missing_config_value(mall_config_path) or not Path(mall_config_path).exists():
        return []
    try:
        report = validate_mall_config(Path(mall_config_path), min_count=1)
    except Exception:
        return []

    enabled_mall_ids = {str(mall_id).strip() for mall_id in report.get("enabled_mall_ids") or [] if str(mall_id).strip()}
    enabled_origins = report.get("enabled_mall_origins") if isinstance(report.get("enabled_mall_origins"), dict) else {}
    product_url_prefixes = (
        report.get("enabled_mall_product_url_prefixes")
        if isinstance(report.get("enabled_mall_product_url_prefixes"), dict)
        else {}
    )
    api_key_hashes = (
        report.get("enabled_mall_api_key_hashes")
        if isinstance(report.get("enabled_mall_api_key_hashes"), dict)
        else {}
    )
    if not enabled_mall_ids:
        return []

    messages: list[str] = []
    for index, site in enumerate(sites, start=1):
        mall_id = str(site.get("mall_id") or "").strip()
        if not mall_id:
            continue
        if mall_id not in enabled_mall_ids:
            messages.append(f"site {index} mall_id {mall_id!r} is not enabled in mall_config")
            continue

        origin = normalized_representative_origin(site.get("origin"))
        allowed_origins = [str(origin).strip().rstrip("/") for origin in enabled_origins.get(mall_id, [])]
        if origin and allowed_origins and origin not in allowed_origins:
            messages.append(
                f"site {index} origin {origin!r} is not allowed for mall_id {mall_id!r}; allowed_origins={allowed_origins}"
            )

        expected_api_key_hash = str(api_key_hashes.get(mall_id) or "").strip()
        if expected_api_key_hash:
            site_api_key = str(site.get("api_key") or "").strip()
            site_api_key_hash = api_key_hash(site_api_key)
            if not site_api_key:
                messages.append(
                    f"site {index} api_key is required in representative_sites_config to verify mall_config API key for mall_id {mall_id!r}"
                )
            elif site_api_key_hash and site_api_key_hash != expected_api_key_hash:
                messages.append(f"site {index} api_key does not match mall_config api_key for mall_id {mall_id!r}")

        mall_prefix = str(product_url_prefixes.get(mall_id) or "").strip().rstrip("/")
        if not mall_prefix:
            continue
        for prefix in representative_expected_prefixes(site):
            if prefix and not representative_prefix_matches_mall_prefix(prefix, mall_prefix):
                messages.append(
                    f"site {index} expected_product_url_prefix {prefix!r} is not compatible with mall_config product_url_template prefix {mall_prefix!r}"
                )
    return messages


def normalized_representative_origin(value: Any) -> str:
    try:
        return normalize_origin_value(str(value or ""), allow_wildcard=False, field_name="origin").rstrip("/")
    except Exception:
        return str(value or "").strip().rstrip("/")


def representative_expected_prefixes(site: Mapping[str, Any]) -> list[str]:
    prefixes = expected_list(site.get("expected_product_url_prefix"))
    if not prefixes and site.get("origin"):
        prefixes = [str(site.get("origin") or "")]
    return [prefix.strip().rstrip("/") for prefix in prefixes if prefix.strip()]


def representative_prefix_matches_mall_prefix(prefix: str, mall_prefix: str) -> bool:
    return url_matches_prefix(mall_prefix, prefix) or url_matches_prefix(prefix, mall_prefix)


def service_env_file_problems(path: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    try:
        from scripts.env_check import build_report as build_env_check_report

        args = argparse.Namespace(
            env_file=path,
            role=str(file_validation_context.get("role") or "api"),
            api_server_count=parse_positive_int(file_validation_context.get("api_server_count"), default=1),
            allow_non_production=False,
            skip_path_checks=False,
        )
        report = build_env_check_report(args)
    except Exception as exc:
        return [f"env_file: {exc}"]
    if report.get("ok") is True:
        return []
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    messages: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or check.get("ok") is True:
            continue
        name = str(check.get("name") or "env_check")
        message = str(check.get("message") or "service env file check failed").strip()
        messages.append(f"{name}: {message}")
    if not messages:
        messages.append("env_file: service env file did not pass env_check.py")
    return messages[:10]


def security_input_file_problems(path: str, label: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    try:
        from scripts.security_check import (
            check_logrotate_config,
            check_nginx_client_max_body_size,
            check_nginx_forwarded_for_safety,
            check_nginx_upstream_resilience,
            check_systemd_reindex_service,
            check_systemd_reindex_timer,
            check_systemd_restart_policy,
            check_systemd_sync_worker_service,
        )

        if label == "security.nginx_config":
            reports = [
                ("nginx_client_max_body_size", check_nginx_client_max_body_size(path, security_max_image_mb(file_validation_context))),
                ("nginx_upstream_resilience", check_nginx_upstream_resilience(path)),
                ("nginx_forwarded_for_safety", check_nginx_forwarded_for_safety(path)),
            ]
        elif label == "security.systemd_service":
            reports = [("systemd_restart_policy", check_systemd_restart_policy(path))]
        elif label == "security.sync_systemd_service":
            reports = [("systemd_sync_worker", check_systemd_sync_worker_service(path))]
        elif label == "security.reindex_systemd_service":
            reports = [("systemd_reindex_service", check_systemd_reindex_service(path))]
        elif label == "security.reindex_systemd_timer":
            expected_unit = Path(str(file_validation_context.get("reindex_systemd_service") or "")).name
            reports = [
                (
                    "systemd_reindex_timer",
                    check_systemd_reindex_timer(path, expected_unit=expected_unit or "haeorum-ai-reindex.service"),
                )
            ]
        elif label == "security.logrotate_config":
            reports = [("logrotate_config", check_logrotate_config(path))]
        else:
            return []
    except Exception as exc:
        return [f"security_file: {exc}"]

    messages: list[str] = []
    for name, report in reports:
        if not isinstance(report, dict) or report.get("ok") is True:
            continue
        message = str(report.get("message") or f"{name} check failed").strip()
        details = security_report_problem_details(report)
        messages.append(f"{name}: {message}{' (' + details + ')' if details else ''}")
    return messages


def security_max_image_mb(file_validation_context: Mapping[str, Any]) -> int:
    value = file_validation_context.get("image_max_mb")
    if value in (None, ""):
        value = file_validation_context.get("HAEORUM_MAX_IMAGE_MB")
    return parse_positive_int(value, default=5)


def security_report_problem_details(report: Mapping[str, Any]) -> str:
    for key in ("missing_or_invalid", "missing_directives", "problems", "servers_missing_failover"):
        value = report.get(key)
        if isinstance(value, list) and value:
            return f"{key}={', '.join(str(item) for item in value[:5])}"
        if isinstance(value, dict) and value:
            return f"{key}={', '.join(str(item) for item in list(value)[:5])}"
    return ""


def load_report_file_problems(
    path: str,
    label: str,
    file_validation_context: Mapping[str, Any],
) -> list[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("load report JSON root must be an object")
        summary = summarize_load_report(data)
    except Exception as exc:
        return [f"parse: {exc}"]

    messages: list[str] = []
    if summary.get("ok") is not True:
        messages.append("ok: load report ok is not true")
    if summary.get("target_validation_ok") is not True:
        messages.append("target_validation: target_validation.ok must match report base_url and origin")

    expected_base_url = str(file_validation_context.get("base_url") or "").strip()
    if expected_base_url and not is_missing_required_config("base_url", expected_base_url):
        if summary.get("base_url") != expected_base_url:
            messages.append("base_url: does not match evidence config base_url")
    for problem in operational_target_url_problems("base_url", summary.get("base_url")):
        messages.append(problem)

    expected_origin = str(file_validation_context.get("origin") or "").strip()
    if expected_origin and not is_missing_required_config("origin", expected_origin):
        if summary.get("origin") != expected_origin:
            messages.append("origin: does not match evidence config origin")
    for problem in operational_target_url_problems("origin", summary.get("origin"), origin_only=True):
        messages.append(problem)

    expected_mall_id = str(file_validation_context.get("mall_id") or "").strip()
    if expected_mall_id and not is_missing_config_value(expected_mall_id):
        if summary.get("mall_id") != expected_mall_id:
            messages.append("mall_id: does not match evidence config mall_id")
    if not str(summary.get("mall_id") or "").strip():
        messages.append("mall_id: missing")

    api_server_count = int(summary.get("api_server_count", 0) or 0)
    if label == "api_scale.single_report" and api_server_count != 1:
        messages.append(f"api_server_count: expected 1 for api_scale.single_report, found {api_server_count}")
    if label == "api_scale.multi_report" and api_server_count < 2:
        messages.append(f"api_server_count: expected 2 or more for api_scale.multi_report, found {api_server_count}")
    if api_server_count >= 2:
        admin_source_coverage = (
            summary.get("admin_metrics_source_coverage")
            if isinstance(summary.get("admin_metrics_source_coverage"), dict)
            else {}
        )
        if not admin_source_coverage:
            messages.append("admin_metrics_source_coverage: missing for multi API report")
        else:
            if admin_source_coverage.get("ok") is not True:
                problems = ", ".join(
                    str(problem)
                    for problem in (admin_source_coverage.get("problems") or [])
                    if str(problem or "").strip()
                )
                messages.append(
                    "admin_metrics_source_coverage: not ok"
                    + (f" ({problems})" if problems else "")
                )
            if parse_positive_int(admin_source_coverage.get("successful_source_count"), default=0) < api_server_count:
                messages.append("admin_metrics_source_coverage: source count below api_server_count")
            if parse_positive_int(admin_source_coverage.get("distinct_instance_count"), default=0) < api_server_count:
                messages.append("admin_metrics_source_coverage: distinct instance count below api_server_count")

    if summary.get("scenario") != "mixed-traffic":
        messages.append("scenario: expected mixed-traffic")
    if int(summary.get("active_users", 0) or 0) < 850:
        messages.append("active_users: expected at least 850")
    if int(summary.get("requests", 0) or 0) < 850:
        messages.append("requests: expected at least 850")
    if int(summary.get("concurrency", 0) or 0) < 100:
        messages.append("concurrency: expected at least 100")
    mode_counts = summary.get("mode_counts") if isinstance(summary.get("mode_counts"), dict) else {}
    missing_modes = sorted(mode for mode in REQUIRED_MIXED_TRAFFIC_MODES if int(mode_counts.get(mode, 0) or 0) <= 0)
    if missing_modes:
        messages.append(f"mode_counts: missing {', '.join(missing_modes)}")

    image_input = summary.get("image_input") if isinstance(summary.get("image_input"), dict) else {}
    if summary.get("image_source_ok") is not True:
        messages.append(
            "image_input: " + ", ".join(summary.get("image_source_problems") or ["image_input.source"])
        )
    reported_image_sha256 = str(image_input.get("sha256") or "").strip()
    if not reported_image_sha256:
        messages.append("image_input.sha256: missing")
    else:
        expected_image_file = str(file_validation_context.get("load.image_file") or "").strip()
        if expected_image_file and not is_missing_config_value(expected_image_file) and Path(expected_image_file).exists():
            max_mb = parse_positive_int(file_validation_context.get("image_max_mb"), default=10)
            try:
                expected_image = validate_image_bytes(
                    Path(expected_image_file).read_bytes(),
                    max_bytes=max_mb * 1024 * 1024,
                    min_dimension=16,
                )
            except Exception as exc:
                messages.append(f"configured load.image_file: {exc}")
            else:
                if reported_image_sha256 != expected_image.sha256:
                    messages.append("image_input.sha256: does not match configured load.image_file")
    expected_image_files = expected_load_image_files_from_context(file_validation_context)
    if len(expected_image_files) >= MIN_LOAD_IMAGE_INPUTS:
        expected_digests: list[str] = []
        max_mb = parse_positive_int(file_validation_context.get("image_max_mb"), default=10)
        for expected_image_file in expected_image_files:
            if is_missing_config_value(expected_image_file) or not Path(expected_image_file).exists():
                continue
            try:
                expected_image = validate_image_bytes(
                    Path(expected_image_file).read_bytes(),
                    max_bytes=max_mb * 1024 * 1024,
                    min_dimension=16,
                )
            except Exception:
                continue
            expected_digests.append(expected_image.sha256)
        reported_digests = reported_image_digests(image_input)
        if len(set(expected_digests)) >= MIN_LOAD_IMAGE_INPUTS and not set(expected_digests).issubset(reported_digests):
            messages.append("image_input.sha256_values: does not match configured load.image_files")

    response_engine = summary.get("response_engine") if isinstance(summary.get("response_engine"), dict) else {}
    engine_counts = response_engine.get("engine_counts") if isinstance(response_engine.get("engine_counts"), dict) else {}
    if summary.get("response_contract_ok") is not True:
        messages.append("response_contract: response_contract.ok must be true and use only Marqo responses")
    if summary.get("response_shape_ok") is not True:
        problems = summary.get("response_shape_problems") or ["response_contract.min_item_count"]
        messages.append("response_contract.shape: " + ", ".join(str(problem) for problem in problems))
    response_shape = summary.get("response_shape") if isinstance(summary.get("response_shape"), dict) else {}
    mall_config_path = str(file_validation_context.get("mall_config") or "").strip()
    if mall_config_path and not is_missing_config_value(mall_config_path) and response_shape.get("product_url_prefix_required") is not True:
        messages.append("response_contract.product_url_prefix_required")
    if summary.get("query_type_coverage_ok") is not True:
        problems = summary.get("query_type_coverage_problems") or ["response_contract.query_type_counts"]
        messages.append("response_contract.query_type_counts: " + ", ".join(str(problem) for problem in problems))
    if not engine_counts:
        messages.append("response_contract.engine_counts: missing")
    if int(response_engine.get("non_marqo_engine_responses", 0) or 0) != 0:
        messages.append("response_contract.non_marqo_engine_responses: expected 0")

    if summary.get("server_metrics_ok") is not True:
        messages.append("server_metrics.ok: expected true")
    if str(summary.get("engine_backend") or "").strip().lower() != "marqo":
        messages.append("server_metrics.after.snapshot.engine_backend: expected marqo")
    for missing in summary.get("server_metrics_missing") or []:
        messages.append(str(missing))

    return sorted(set(messages))


def expected_load_image_files_from_context(file_validation_context: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    primary = str(file_validation_context.get("load.image_file") or "").strip()
    if primary:
        values.append(primary)
    values.extend(string_list_items(file_validation_context.get("load.image_files")))
    for index in range(2, MIN_LOAD_IMAGE_INPUTS + 1):
        text = str(file_validation_context.get(f"load.image_files[{index}]") or "").strip()
        if text:
            values.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(Path(value))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def reported_image_digests(image_input: Mapping[str, Any]) -> set[str]:
    digests = set(string_list_items(image_input.get("sha256_values")))
    primary = str(image_input.get("sha256") or "").strip()
    if primary:
        digests.add(primary)
    images = image_input.get("images") if isinstance(image_input.get("images"), list) else []
    for image in images:
        if not isinstance(image, Mapping):
            continue
        digest = str(image.get("sha256") or "").strip()
        if digest:
            digests.add(digest)
    return digests


def load_image_file_problems(path: str, file_validation_context: Mapping[str, Any]) -> list[str]:
    max_mb = parse_positive_int(file_validation_context.get("image_max_mb"), default=10)
    max_bytes = max_mb * 1024 * 1024
    try:
        image = validate_image_bytes(Path(path).read_bytes(), max_bytes=max_bytes, min_dimension=16)
    except Exception as exc:
        return [f"image_file: {exc}"]
    messages: list[str] = []
    if image.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        messages.append("image_file.mime_type: expected JPG, PNG, or WEBP")
    if image.size_bytes <= 0:
        messages.append("image_file.size_bytes: image is empty")
    if image.size_bytes > max_bytes:
        messages.append(f"image_file.size_bytes: exceeds {max_mb} MiB")
    if image.width is not None and image.width < 16:
        messages.append("image_file.width: minimum dimension is 16px")
    if image.height is not None and image.height < 16:
        messages.append("image_file.height: minimum dimension is 16px")
    return messages


def parse_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def normalized_path_key(path: str | Path) -> str:
    return str(Path(path).resolve()) if str(path or "").strip() else ""


def output_option_value(command: list[str]) -> str | None:
    for option in ("--output", "--json-output"):
        if option in command:
            index = command.index(option)
            if index + 1 < len(command):
                return str(command[index + 1])
    return None


def is_missing_config_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return any(pattern.search(text) for pattern in PLACEHOLDER_PATTERNS)


def is_missing_required_config(field: str, value: Any) -> bool:
    text = str(value or "").strip()
    if is_missing_config_value(text):
        return True
    if field == "origin":
        return not is_safe_http_url_config(text, origin_only=True, require_https=True, allow_local=False)
    if field == "base_url":
        return not is_safe_http_url_config(text, require_https=True, allow_local=False)
    if field.endswith(".url") or field in {
        "marqo.gemini_embedding_url",
        "marqo.qwen_embedding_url",
        "marqo.embedding_url",
    }:
        return not is_safe_http_url_config(text)
    if field == "api_key":
        return is_placeholder_public_api_key(text)
    if field == "admin_key":
        return text.lower() in PLACEHOLDER_ADMIN_API_KEYS
    if field == "mssql_connection_string":
        try:
            validate_mssql_connection_string_value(text, field)
        except ValueError:
            return True
    if field == "mssql_query":
        try:
            validate_readonly_query(text)
        except ValueError:
            return True
    return False


def is_local_http_host(hostname: str | None) -> bool:
    return is_non_public_host(hostname)


def is_link_or_unspecified_http_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if host in {"0.0.0.0", "::"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_unspecified or address.is_link_local


def is_safe_http_url_config(
    value: str,
    origin_only: bool = False,
    require_https: bool = False,
    allow_local: bool = True,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        return False
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if require_https and parsed.scheme.lower() != "https":
        return False
    if not parsed.netloc or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if is_link_or_unspecified_http_host(parsed.hostname):
        return False
    if not allow_local and is_local_http_host(parsed.hostname):
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    if origin_only and (parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment):
        return False
    return True


def redact_command(command: list[str]) -> list[str]:
    redacted = []
    hide_next = False
    for part in command:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(part)
        if part in SENSITIVE_OPTIONS:
            hide_next = True
    return redacted


def run_command(command: list[str], timeout: int, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    sensitive_values = sensitive_values_for_command(command, environment)
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=dict(environment) if environment is not None else None,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "exit_code": None, "elapsed_ms": elapsed_ms(started), "error": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "elapsed_ms": elapsed_ms(started),
            "error": f"command timed out after {timeout}s",
            "stdout_tail": tail_text(sanitize_process_output(exc.stdout, sensitive_values)),
            "stderr_tail": tail_text(sanitize_process_output(exc.stderr, sensitive_values)),
        }
    stdout = sanitize_process_output(completed.stdout, sensitive_values)
    stderr = sanitize_process_output(completed.stderr, sensitive_values)
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "elapsed_ms": elapsed_ms(started),
        "stdout_tail": tail_text(stdout),
        "stderr_tail": tail_text(stderr),
        "stdout": stdout,
    }


def sensitive_values_for_command(command: list[str], environment: Mapping[str, str] | None = None) -> list[str]:
    values: list[str] = []
    hide_next = False
    for part in command:
        text = str(part)
        if hide_next:
            values.append(text)
            hide_next = False
            continue
        if text in SENSITIVE_OPTIONS:
            hide_next = True
    for name, value in (environment or {}).items():
        text = str(value or "")
        if text and SENSITIVE_ENV_NAME_PATTERN.search(str(name)):
            values.append(text)
    return sorted({value for value in values if len(value) >= 4}, key=len, reverse=True)


def sanitize_process_output(value: Any, sensitive_values: list[str]) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    for secret in sensitive_values:
        text = text.replace(secret, "***")
    text = SECRET_ASSIGNMENT_PATTERN.sub(lambda match: match.group(1) + "***", text)
    text = AUTHORIZATION_PATTERN.sub(lambda match: match.group(1) + "***", text)
    text = URL_CREDENTIAL_PATTERN.sub(lambda match: match.group(1) + "[redacted]@", text)
    return text


def execute_plan(
    plan: list[dict[str, Any]],
    timeout: int,
    dry_run: bool,
    stop_on_failure: bool,
    command_runner: CommandRunner = run_command,
    max_existing_evidence_age_days: int = DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS,
) -> list[dict[str, Any]]:
    results = []
    planned_output_paths: set[str] = set()
    for item in plan:
        evidence_file = str(item.get("evidence_file") or "")
        current_missing_input_files = missing_input_files(
            item.get("required_files", {}),
            planned_output_paths if dry_run else None,
        )
        current_input_file_problems = input_file_validation_problems(
            item,
            planned_output_paths if dry_run else None,
        )
        current_invalid_input_files = sorted(
            {
                str(problem.get("name") or "")
                for problem in current_input_file_problems
                if str(problem.get("name") or "")
            }
        )
        result = {
            "name": item["name"],
            "command": command_to_string(item["redacted_command"]),
            "evidence_file": evidence_file or None,
            "evidence_exists": evidence_file_exists(evidence_file),
            "missing_config": item["missing_config"],
            "missing_input_files": current_missing_input_files,
            "invalid_input_files": current_invalid_input_files,
            "input_file_problems": current_input_file_problems,
            "status": "planned" if dry_run else "pending",
            "ok": None,
        }
        if dry_run and result["evidence_exists"] is True:
            summary = existing_evidence_summary(item["name"], evidence_file, max_existing_evidence_age_days)
            if is_dry_run_aggregate_report(item["name"], summary):
                result.update(
                    {
                        "status": "planned",
                        "ok": None,
                        "ready_to_execute": True,
                        "message": "dry run only; command was not executed",
                        "existing_evidence": summary,
                    }
                )
                results.append(result)
                continue
            existing_ok = existing_evidence_is_acceptable(item["name"], summary)
            if existing_ok:
                add_existing_produced_paths(item, planned_output_paths)
            if not existing_ok and (item["missing_config"] or current_missing_input_files or current_invalid_input_files):
                result.update(
                    {
                        "status": "skipped",
                        "ok": False,
                        "message": (
                            skipped_message(item["missing_config"], current_missing_input_files, current_invalid_input_files)
                            + "; existing evidence is invalid, wrong-shape, content-invalid, dry-run, stale, simulated, local-only, or ok is not true"
                        ),
                        "existing_evidence": summary,
                    }
                )
                results.append(result)
                if stop_on_failure:
                    break
                continue
            result.update(
                {
                    "status": "existing" if existing_ok else "failed",
                    "ok": existing_ok,
                    "message": "evidence file already exists" if existing_ok else "existing evidence is invalid, wrong-shape, content-invalid, dry-run, stale, simulated, local-only, or ok is not true",
                    "existing_evidence": summary,
                }
            )
            results.append(result)
            if existing_ok is not True and stop_on_failure:
                break
            continue
        if item["missing_config"] or current_missing_input_files or current_invalid_input_files:
            result.update(
                {
                    "status": "skipped",
                    "ok": False,
                    "message": skipped_message(item["missing_config"], current_missing_input_files, current_invalid_input_files),
                }
            )
            results.append(result)
            if stop_on_failure:
                break
            continue
        if dry_run:
            result.update(
                {
                    "ok": None,
                    "ready_to_execute": True,
                    "message": "dry run only; command was not executed",
                }
            )
            add_planned_produced_paths(item, planned_output_paths)
            results.append(result)
            continue
        command_result = command_runner(item["command"], timeout)
        stdout = command_result.pop("stdout", "")
        capture_target = item.get("capture_stdout_to")
        if capture_target and command_result.get("ok") is True:
            Path(capture_target).parent.mkdir(parents=True, exist_ok=True)
            Path(capture_target).write_text(str(stdout or "") + ("\n" if stdout and not str(stdout).endswith("\n") else ""), encoding="utf-8")
        result.update(command_result)
        result["status"] = "passed" if command_result.get("ok") is True else "failed"
        result["ok"] = command_result.get("ok") is True
        result["evidence_exists"] = evidence_file_exists(evidence_file)
        missing_produced_files = missing_produced_output_files(item)
        if result["ok"] is True and missing_produced_files:
            result["status"] = "failed"
            result["ok"] = False
            result["message"] = "produced file was not created: " + ", ".join(missing_produced_files)
        if result["ok"] is True and evidence_file and result["evidence_exists"] is not True:
            result["status"] = "failed"
            result["ok"] = False
            result["message"] = "evidence file was not created"
        if result["ok"] is True and evidence_file and result["evidence_exists"] is True:
            summary = existing_evidence_summary(item["name"], evidence_file, max_existing_evidence_age_days)
            result["evidence_summary"] = summary
            if existing_evidence_is_acceptable(item["name"], summary) is not True:
                result["status"] = "failed"
                result["ok"] = False
                result["message"] = "produced evidence is invalid, wrong-shape, content-invalid, dry-run, stale, simulated, local-only, or ok is not true"
        if result["ok"] is True:
            add_existing_produced_paths(item, planned_output_paths)
        results.append(result)
        if result["ok"] is not True and stop_on_failure:
            break
    return results


def add_planned_produced_paths(item: Mapping[str, Any], output_paths: set[str]) -> None:
    for path in produced_file_paths(item):
        output_paths.add(normalized_path_key(path))


def add_existing_produced_paths(item: Mapping[str, Any], output_paths: set[str]) -> None:
    for path in produced_file_paths(item):
        if Path(path).exists():
            output_paths.add(normalized_path_key(path))


def produced_file_paths(item: Mapping[str, Any]) -> list[str]:
    produced = item.get("produces_files")
    if not isinstance(produced, dict):
        return []
    return [
        str(path)
        for path in produced.values()
        if str(path or "").strip() and not is_missing_config_value(str(path))
    ]


def missing_produced_output_files(item: Mapping[str, Any]) -> list[str]:
    return [path for path in produced_file_paths(item) if not Path(path).exists()]


def is_dry_run_aggregate_report(name: str, summary: Mapping[str, Any]) -> bool:
    return name == "operational_readiness" and summary.get("json_ok") is True and summary.get("ok") is not True


def skipped_message(
    missing_config: list[str],
    missing_files: list[str],
    invalid_files: list[str] | None = None,
) -> str:
    invalid = invalid_files or []
    if missing_config and (missing_files or invalid):
        return "required config or input file is missing or invalid"
    if missing_files:
        return "required input file is missing"
    if invalid:
        return "required input file is invalid"
    return "required config is missing"


def build_report(
    config_path: str | Path,
    evidence_dir: str | Path,
    timeout: int,
    dry_run: bool,
    stop_on_failure: bool,
    command_runner: CommandRunner = run_command,
    env_file: str | Path | None = None,
    max_existing_evidence_age_days: int = DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS,
) -> dict[str, Any]:
    config = load_config(config_path)
    environment = collection_environment(env_file)
    child_environment = child_process_environment(env_file)
    collector_env_file_permissions = check_secret_file_permissions(
        env_file,
        name="collector_env_file_permissions",
        required=bool(env_file),
        max_mode=COLLECTOR_ENV_FILE_MAX_MODE,
    )
    effective_runner = command_runner
    if command_runner is run_command:
        effective_runner = lambda command, timeout: run_command(command, timeout, environment=child_environment)
    max_existing_evidence_age_days = max(0, int(max_existing_evidence_age_days or 0))
    evidence_root = normalize_evidence_dir(evidence_dir)
    evidence_root.mkdir(parents=True, exist_ok=True)
    simulated_config = is_simulated_config(config)
    if simulated_config and not dry_run:
        return {
            "ok": False,
            "dry_run": dry_run,
            "ready_to_execute": False,
            "evidence_complete": False,
            "simulation_marker": str(config.get("simulation_marker") or SIMULATION_CONFIG_MARKER),
            "simulation_only": True,
            "not_operational_readiness": True,
            "refused_reason": "simulation-marked evidence config cannot run operational collection; rerun with --dry-run or replace it with real operational config",
            "collector_env_file_permissions": collector_env_file_permissions,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_existing_evidence_age_days": max_existing_evidence_age_days,
            "config": str(config_path),
            "env_file": str(env_file) if env_file else None,
            "evidence_dir": str(evidence_root),
            "blocking_inputs": {
                "missing_config": [],
                "missing_files": [],
                "invalid_files": [],
                "blocked_steps": [],
            },
            "execution_runbook": build_execution_runbook(config, config_path, evidence_root, env_file),
            "failed_steps": [],
            "skipped_steps": [],
            "existing_steps": [],
            "status_counts": {"passed": 0, "failed": 0, "skipped": 0, "planned": 0, "existing": 0},
            "steps": [],
        }
    plan = build_plan(config, evidence_root, environment=environment)
    results = execute_plan(
        plan,
        timeout,
        dry_run,
        stop_on_failure,
        command_runner=effective_runner,
        max_existing_evidence_age_days=max_existing_evidence_age_days,
    )
    blocking_inputs = summarize_blocking_inputs(results, plan, config)
    collector_env_file_ready = collector_env_file_permissions.get("ok") is True
    ready_to_execute = collector_env_file_ready and all(item.get("status") in {"planned", "existing"} for item in results)
    evidence_complete = not dry_run and collector_env_file_ready and all(item.get("ok") is True for item in results)
    return {
        "ok": ready_to_execute if dry_run else evidence_complete,
        "dry_run": dry_run,
        "ready_to_execute": ready_to_execute,
        "evidence_complete": evidence_complete,
        "simulation_marker": str(config.get("simulation_marker") or "") if simulated_config else None,
        "simulation_only": simulated_config,
        "not_operational_readiness": simulated_config,
        "collector_env_file_permissions": collector_env_file_permissions,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_existing_evidence_age_days": max_existing_evidence_age_days,
        "config": str(config_path),
        "env_file": str(env_file) if env_file else None,
        "evidence_dir": str(evidence_root),
        "blocking_inputs": blocking_inputs,
        "execution_runbook": build_execution_runbook(config, config_path, evidence_root, env_file),
        "failed_steps": [item["name"] for item in results if item["status"] == "failed"],
        "skipped_steps": [item["name"] for item in results if item["status"] == "skipped"],
        "existing_steps": [item["name"] for item in results if item["status"] == "existing"],
        "status_counts": {
            "passed": sum(1 for item in results if item["status"] == "passed"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
            "skipped": sum(1 for item in results if item["status"] == "skipped"),
            "planned": sum(1 for item in results if item["status"] == "planned"),
            "existing": sum(1 for item in results if item["status"] == "existing"),
        },
        "steps": results,
    }


def build_execution_runbook(
    config: dict[str, Any],
    config_path: str | Path,
    evidence_dir: str | Path,
    env_file: str | Path | None = None,
) -> dict[str, Any]:
    evidence_root = normalize_evidence_dir(evidence_dir)
    config_ref = str(Path(config_path).resolve())
    env_ref = str(Path(env_file).resolve()) if env_file else ""
    base_command = [
        "python",
        "scripts/collect_operational_evidence.py",
        "--config",
        config_ref,
        "--evidence-dir",
        str(evidence_root),
    ]
    if env_file:
        base_command.extend(["--env-file", env_ref])

    local_acceptance = optional_path(evidence_root, "local-acceptance.json")
    requirements_audit = optional_path(evidence_root, "requirements-audit.json")
    requirements_audit_markdown = optional_path(evidence_root, "requirements-audit.md")
    requirements_blockers = optional_path(evidence_root, "requirements-blockers.md")
    plan_json = optional_path(evidence_root, "evidence-collection-plan.json")
    plan_markdown = optional_path(evidence_root, "evidence-collection-plan.md")
    collection_json = optional_path(evidence_root, "evidence-collection.json")
    collection_markdown = optional_path(evidence_root, "evidence-collection.md")
    readiness_json = optional_path(evidence_root, "operational-readiness.json")
    readiness_markdown = optional_path(evidence_root, "operational-readiness.md")
    expected_malls = str(int_value(config, "expected_malls", default=1700))
    required_sites = str(int_value(config, "required_sites", default=3))
    blocker_project_root = string_value(config, "missing_commands", "project_root")
    blocker_evidence_dir = string_value(config, "missing_commands", "evidence_dir")

    dry_run_command = base_command + [
        "--dry-run",
        "--output",
        plan_json,
        "--markdown-output",
        plan_markdown,
        "--local-acceptance-report",
        local_acceptance,
        "--requirements-audit-output",
        requirements_audit,
        "--requirements-audit-markdown-output",
        requirements_audit_markdown,
        "--requirements-blocker-checklist-output",
        requirements_blockers,
    ]
    collect_command = base_command + [
        "--output",
        collection_json,
        "--markdown-output",
        collection_markdown,
        "--local-acceptance-report",
        local_acceptance,
        "--requirements-audit-output",
        requirements_audit,
        "--requirements-audit-markdown-output",
        requirements_audit_markdown,
        "--requirements-blocker-checklist-output",
        requirements_blockers,
    ]
    readiness_command = [
        "python",
        "scripts/operational_readiness.py",
        "--evidence-dir",
        str(evidence_root),
        "--expected-malls",
        expected_malls,
        "--required-sites",
        required_sites,
        "--output",
        readiness_json,
        "--markdown-output",
        readiness_markdown,
    ]
    audit_command = [
        "python",
        "scripts/requirements_audit.py",
        "--local-acceptance-report",
        local_acceptance,
        "--operational-readiness-report",
        readiness_json,
        "--evidence-collection-report",
        collection_json,
        "--output",
        requirements_audit,
        "--markdown-output",
        requirements_audit_markdown,
        "--blocker-checklist-output",
        requirements_blockers,
    ]
    if blocker_project_root:
        audit_command.extend(["--blocker-checklist-project-root", blocker_project_root])
    if blocker_evidence_dir:
        audit_command.extend(["--blocker-checklist-evidence-dir", blocker_evidence_dir])
    commands = [
        {
            "name": "dry_run",
            "when": "After filling config/env values and placing required input files, confirm the collection plan.",
            "command": command_to_string(dry_run_command),
        },
        {
            "name": "collect",
            "when": "Run only after the dry run reports ready_to_execute=true.",
            "command": command_to_string(collect_command),
        },
        {
            "name": "readiness",
            "when": "Rerun manually if evidence files are copied or regenerated outside the collector.",
            "command": command_to_string(readiness_command),
        },
        {
            "name": "audit",
            "when": "Use this as the final gate after operational-readiness.json is ok=true.",
            "command": command_to_string(audit_command),
        },
    ]
    runbook = {
        "working_directory": str(ROOT),
        "commands": commands,
    }
    if blocker_project_root and blocker_evidence_dir:
        runbook["deployment_working_directory"] = normalize_posix_path(blocker_project_root)
        runbook["deployment_evidence_dir"] = normalize_posix_path(blocker_evidence_dir)
        runbook["deployment_commands"] = build_deployment_execution_commands(
            config,
            include_env_file=bool(env_file) or config_uses_env_references(config),
            project_root=blocker_project_root,
            evidence_dir=blocker_evidence_dir,
        )
    return runbook


def config_uses_env_references(config: dict[str, Any]) -> bool:
    for field in ("api_key_env", "admin_key_env", "mssql_connection_string_env"):
        if str(config.get(field) or "").strip():
            return True
    return False


def build_deployment_execution_commands(
    config: dict[str, Any],
    include_env_file: bool,
    project_root: str | Path,
    evidence_dir: str | Path,
) -> list[dict[str, str]]:
    evidence_root = normalize_posix_path(evidence_dir).rstrip("/")
    base_command = [
        "python",
        "scripts/collect_operational_evidence.py",
        "--config",
        DEPLOYMENT_CONFIG_PATH,
        "--evidence-dir",
        evidence_root,
    ]
    if include_env_file:
        base_command.extend(["--env-file", DEPLOYMENT_ENV_PATH])

    local_acceptance = join_posix_path(evidence_root, "local-acceptance.json")
    requirements_audit = join_posix_path(evidence_root, "requirements-audit.json")
    requirements_audit_markdown = join_posix_path(evidence_root, "requirements-audit.md")
    requirements_blockers = join_posix_path(evidence_root, "requirements-blockers.md")
    plan_json = join_posix_path(evidence_root, "evidence-collection-plan.json")
    plan_markdown = join_posix_path(evidence_root, "evidence-collection-plan.md")
    collection_json = join_posix_path(evidence_root, "evidence-collection.json")
    collection_markdown = join_posix_path(evidence_root, "evidence-collection.md")
    readiness_json = join_posix_path(evidence_root, "operational-readiness.json")
    readiness_markdown = join_posix_path(evidence_root, "operational-readiness.md")
    expected_malls = str(int_value(config, "expected_malls", default=1700))
    required_sites = str(int_value(config, "required_sites", default=3))
    project_root_text = normalize_posix_path(project_root)

    dry_run_command = base_command + [
        "--dry-run",
        "--output",
        plan_json,
        "--markdown-output",
        plan_markdown,
        "--local-acceptance-report",
        local_acceptance,
        "--requirements-audit-output",
        requirements_audit,
        "--requirements-audit-markdown-output",
        requirements_audit_markdown,
        "--requirements-blocker-checklist-output",
        requirements_blockers,
    ]
    collect_command = base_command + [
        "--output",
        collection_json,
        "--markdown-output",
        collection_markdown,
        "--local-acceptance-report",
        local_acceptance,
        "--requirements-audit-output",
        requirements_audit,
        "--requirements-audit-markdown-output",
        requirements_audit_markdown,
        "--requirements-blocker-checklist-output",
        requirements_blockers,
    ]
    readiness_command = [
        "python",
        "scripts/operational_readiness.py",
        "--evidence-dir",
        evidence_root,
        "--expected-malls",
        expected_malls,
        "--required-sites",
        required_sites,
        "--output",
        readiness_json,
        "--markdown-output",
        readiness_markdown,
        "--missing-commands-output",
        join_posix_path(evidence_root, "missing-evidence.sh"),
        "--missing-commands-shell",
        "bash",
        "--missing-commands-project-root",
        project_root_text,
        "--missing-commands-evidence-dir",
        evidence_root,
    ]
    audit_command = [
        "python",
        "scripts/requirements_audit.py",
        "--local-acceptance-report",
        local_acceptance,
        "--operational-readiness-report",
        readiness_json,
        "--evidence-collection-report",
        collection_json,
        "--output",
        requirements_audit,
        "--markdown-output",
        requirements_audit_markdown,
        "--blocker-checklist-output",
        requirements_blockers,
        "--blocker-checklist-project-root",
        project_root_text,
        "--blocker-checklist-evidence-dir",
        evidence_root,
    ]
    return [
        {
            "name": "dry_run",
            "when": "After filling config/env values and placing required input files, confirm the collection plan.",
            "command": command_to_string(dry_run_command),
        },
        {
            "name": "collect",
            "when": "Run only after the dry run reports ready_to_execute=true.",
            "command": command_to_string(collect_command),
        },
        {
            "name": "readiness",
            "when": "Rerun manually if evidence files are copied or regenerated outside the collector.",
            "command": command_to_string(readiness_command),
        },
        {
            "name": "audit",
            "when": "Use this as the final gate after operational-readiness.json is ok=true.",
            "command": command_to_string(audit_command),
        },
    ]


def summarize_blocking_inputs(
    results: list[dict[str, Any]],
    plan: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    plan_by_name = {item["name"]: item for item in plan}
    config_inputs: dict[str, dict[str, Any]] = {}
    file_inputs: dict[str, dict[str, Any]] = {}
    invalid_file_inputs: dict[str, dict[str, Any]] = {}
    blocked_steps: list[str] = []
    for result in results:
        if result.get("status") != "skipped":
            continue
        step_name = str(result.get("name") or "")
        if step_name:
            blocked_steps.append(step_name)
        for field in result.get("missing_config") or []:
            entry = config_inputs.setdefault(
                field,
                {
                    "name": field,
                    "env_var": missing_config_env_var(config, field),
                    "resolution": config_resolution_hint(config, field),
                    "steps": [],
                },
            )
            entry["steps"].append(step_name)
        plan_item = plan_by_name.get(step_name, {})
        required_files = plan_item.get("required_files") if isinstance(plan_item, dict) else {}
        required_files = required_files if isinstance(required_files, dict) else {}
        for label in result.get("missing_input_files") or []:
            entry = file_inputs.setdefault(
                label,
                {
                    "name": label,
                    "path": str(required_files.get(label) or ""),
                    "resolution": file_resolution_hint(label, str(required_files.get(label) or "")),
                    "steps": [],
                },
            )
            entry["steps"].append(step_name)
        for problem in result.get("input_file_problems") or []:
            if not isinstance(problem, dict):
                continue
            label = str(problem.get("name") or "")
            if not label:
                continue
            entry = invalid_file_inputs.setdefault(
                label,
                {
                    "name": label,
                    "path": str(problem.get("path") or ""),
                    "resolution": file_resolution_hint(label, str(problem.get("path") or "")),
                    "problems": [],
                    "steps": [],
                },
            )
            entry["problems"].extend(str(item) for item in problem.get("problems") or [] if str(item))
            entry["steps"].append(step_name)
    return {
        "missing_config": sorted(normalize_input_entries(config_inputs), key=lambda item: item["name"]),
        "missing_files": sorted(normalize_input_entries(file_inputs), key=lambda item: item["name"]),
        "invalid_files": sorted(normalize_input_entries(invalid_file_inputs), key=lambda item: item["name"]),
        "blocked_steps": sorted(set(blocked_steps)),
    }


def normalize_input_entries(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items.values():
        entry = dict(item)
        entry["steps"] = sorted(set(str(step) for step in entry.get("steps") or [] if step))
        if not entry.get("env_var"):
            entry.pop("env_var", None)
        if not entry.get("path"):
            entry.pop("path", None)
        if entry.get("problems"):
            entry["problems"] = sorted(set(str(problem) for problem in entry["problems"] if str(problem)))
        normalized.append(entry)
    return normalized


def config_resolution_hint(config: dict[str, Any], field: str) -> str:
    env_name = missing_config_env_var(config, field)
    if field == "base_url":
        return (
            "Set base_url to an absolute HTTPS non-local API base URL such as https://ai-search.haeorumgift.com; "
            "omit credentials, whitespace, query strings, fragments, placeholder values, localhost, and invalid ports."
        )
    if field == "origin":
        return (
            "Set origin to the representative mall HTTPS browser origin such as https://shop001.haeorumgift.com; "
            "omit paths, query strings, fragments, credentials, whitespace, placeholder values, localhost, and invalid ports."
        )
    if field == "marqo.url":
        return (
            "Set marqo.url to the reachable absolute HTTP(S) Marqo URL. For host-run evidence use "
            "http://127.0.0.1:8882; inside Docker containers keep MARQO_URL=http://marqo-api:8882. "
            "omit credentials, whitespace, query strings, fragments, placeholder values, and invalid ports."
        )
    if field in {"marqo.gemini_embedding_url", "marqo.embedding_url"}:
        return (
            "Set marqo.gemini_embedding_url to the host-reachable Gemini embedding proxy URL. For host-run "
            "evidence use http://127.0.0.1:8098; inside Docker containers keep "
            "HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098. Omit credentials, whitespace, "
            "query strings, fragments, placeholder values, and invalid ports."
        )
    if field == "api_key":
        return secret_resolution_hint(
            env_name,
            "public mall API key for the representative mall used by API smoke and load tests",
        )
    if field == "admin_key":
        return secret_resolution_hint(
            env_name,
            "production admin key used to collect metrics, logs, sync status, and rate-limit evidence",
        )
    if field == "mssql_connection_string":
        return secret_resolution_hint(
            env_name,
            "read-only MSSQL connection string for dbo.v_ai_search_products with Encrypt=yes, "
            "TrustServerCertificate=no, and ApplicationIntent=ReadOnly",
        )
    if field == "mssql_query":
        return (
            "Set mssql_query to a single read-only SELECT or WITH query against dbo.v_ai_search_products. "
            "Do not include comments, multiple statements, INSERT/UPDATE/DELETE/MERGE, DDL, EXEC, SET, "
            "or other write/permission-changing keywords."
        )
    if field == "security.sync_alerting_configured":
        return (
            "Set security.sync_alerting_configured=true only after HAEORUM_SYNC_ALERT_WEBHOOK_URL is configured "
            "or an external monitoring rule alerts on sync_last_error, sync_product_failures, "
            "sync_batch_failures, and sync_lock_contention."
        )
    if field == "quality.cases_file":
        return (
            "Set quality.cases_file to the deployed production quality cases JSON, copied from "
            "contracts/quality_cases.example.json and filled with real PoC text, image, and mixed-search cases."
        )
    if field.startswith("load.image_files["):
        return (
            "Set load.image_files to at least two additional deployed real reference image files so image, mixed, "
            "850-user, and API scale load evidence uses three distinct image inputs instead of a single cached image."
        )
    if env_name:
        return f"Set {env_name} in the collector --env-file or provide {field} directly in the evidence config."
    return f"Provide {field} in the operational evidence config."


def secret_resolution_hint(env_name: str, description: str) -> str:
    if env_name:
        return (
            f"Set {env_name} in the collector --env-file with the {description}; "
            "do not leave replace-with, ..., sample, dummy, or dev-key placeholder values."
        )
    return f"Provide the {description} directly in the evidence config using a real non-sample value."


def file_resolution_hint(label: str, path: str) -> str:
    target = f" at {path}" if path else ""
    hints = {
        "products_csv": (
            "Export the full product set from the read-only MSSQL view with scripts/mssql_export_csv.py "
            "or point products_csv at an existing normalized export. Example: "
            f"python scripts/mssql_export_csv.py --connection-string <readonly_connection_string> "
            f"--query <readonly_select_query> --output-csv {path or '<products_csv>'} "
            "--fetch-size 1000 --report-output /var/log/haeorum-ai-search/mssql-export.json. "
            "The collector dry-run validates "
            "that the CSV is parseable, has enough active products/categories for PoC extraction, has no duplicate "
            "product IDs, is not the built-in sample dataset, and includes image URLs for active products."
        ),
        "poc_products_csv": (
            "Build a 300+ product PoC CSV with scripts/poc_dataset_builder.py from products_csv, "
            "then point poc_products_csv at that file. Example: "
            f"python scripts/poc_dataset_builder.py --csv <products_csv> --output-csv {path or '<poc_products_csv>'} "
            "--report-output /var/log/haeorum-ai-search/poc-dataset.json --target-size 300 --min-products 300. "
            "The collector dry-run validates that the PoC CSV is parseable, has 300+ active non-sample products, "
            "has category diversity, has no duplicate mall/product identities, has image URLs for every active product, and "
            "keeps category_name, main_image_url, product_url, and mall_id aligned with products_csv."
        ),
        "mall_config": (
            "Create the production mall config with 1,700 enabled mall entries, real non-sample API keys, allowed origins, "
            "and product URL templates; scripts/mall_config_builder.py can generate the base file. Example: "
            f"python scripts/mall_config_builder.py --csv <malls_csv> --output {path or '<mall_config.json>'} "
            "--report-output /var/log/haeorum-ai-search/mall-config-build.json --min-count 1700 --sort-by-mall-id."
        ),
        "mall_config_source": (
            "Provide the mall/site export CSV or XLSX used to generate malls.json. It must include every enabled mall_id, "
            "real public API key, allowed origin/domain, and product URL template or enough fields for "
            "scripts/mall_config_builder.py to derive them. Example: "
            f"python scripts/mall_config_builder.py --csv {path or '<malls_csv>'} "
            "--output /etc/haeorum-ai-search/malls.json --report-output /var/log/haeorum-ai-search/mall-config-build.json "
            "--min-count 1700 --sort-by-mall-id. The collector dry-run validates the source export before running."
        ),
        "representative_sites_config": (
            "Copy contracts/representative_sites.example.json, fill real representative site URLs, origins, "
            "API keys, and product URL rules, then point representative_sites_config at it. If the existing-site "
            "developer is unavailable, add widget_probe_source or widget_probe_sources paths to saved PC/mobile HTML "
            "files beside the config so the collector dry-run can block missing search inputs, CSP problems, or unsafe "
            "recommended selectors before the live representative_site_check run. For saved HTML, provide both desktop "
            "and mobile variants using pc/mobile filenames or widget_probe_sources objects with variant/source fields. "
            "The file must contain the required "
            "number of HTTPS non-local representative sites and no replace-with/sample placeholder values."
        ),
        "quality_cases_file": (
            "Copy contracts/quality_cases.example.json, replace the example image paths and expected results with "
            "real PoC cases, install it at the configured quality.cases_file path, and keep at least one image-only "
            "and one mixed-search case backed by a real reference image file. The collector dry-run validates the "
            "case contract, referenced image_path files, and expected category/top-product coverage for the configured "
            "mall_id before running quality_report.py."
        ),
        "load.image_file": (
            "Provide the primary real production reference image file for image, mixed, 850-user, and API scale load evidence. "
            "Use a representative JPG, PNG, or WEBP under /data/haeorum-ai-search/quality-images/ or another deployed data path. "
            "The collector dry-run validates that this file is a supported, decodable image and that the additional load.image_files entries are supported, decodable, "
            "and distinct enough to prevent a single cached image from masking embedding/image-search load."
        ),
        "env_check.env_file": (
            "Copy deploy/haeorum-ai-search.env.example to the production env path, fill production values, "
            "and keep the evidence config env_check.env_file aligned with that path. The collector dry-run "
            "runs the env_check.py contract first, including production engine, CORS, admin key, Redis scale, "
            "mall config path, and settings-load checks."
        ),
        "api_scale.single_report": (
            "Run scripts/load_test.py against the 1-API-server mixed-traffic deployment with "
            "--api-server-count 1, --active-users 850, --requests 850, --concurrency 100, --image-file, "
            "two --additional-image-file values, --mall-config, and --admin-key. Example: "
            f"python scripts/load_test.py --base-url <api_base_url> --mall-id <mall_id> --api-key <public_api_key> "
            f"--origin <mall_origin> --admin-key <admin_key> --scenario mixed-traffic --active-users 850 "
            f"--requests 850 --concurrency 100 --p95-ms 5000 --p99-ms 8000 "
            f"--request-timeout-seconds 16 --max-server-wait-avg-ms 1000 --min-rps 5.0 "
            f"--max-process-rss-growth-mb 512 --image-file <reference_image_file> "
            f"--additional-image-file <reference_image_file_2> --additional-image-file <reference_image_file_3> "
            f"--mall-config <mall_config.json> --api-server-count 1 --output {path or '<single_report>'}. "
            "The collector dry-run validates target identity, workload size, file-based image input, Marqo response "
            "engine, product URL prefix evidence from mall_config, three distinct image inputs, and admin server metrics."
        ),
        "api_scale.multi_report": (
            "Run scripts/load_test.py against the 2+ API-server mixed-traffic deployment with "
            "--api-server-count 2 or higher using the same 850 active-user workload. Example: "
            f"python scripts/load_test.py --base-url <api_base_url> --mall-id <mall_id> --api-key <public_api_key> "
            f"--origin <mall_origin> --admin-key <admin_key> --scenario mixed-traffic --active-users 850 "
            f"--requests 850 --concurrency 100 --p95-ms 5000 --p99-ms 8000 "
            f"--request-timeout-seconds 16 --max-server-wait-avg-ms 1000 --min-rps 5.0 "
            f"--max-process-rss-growth-mb 512 --image-file <reference_image_file> "
            f"--additional-image-file <reference_image_file_2> --additional-image-file <reference_image_file_3> "
            f"--mall-config <mall_config.json> --api-server-count 2 --output {path or '<multi_report>'}. "
            "Add --admin-metrics-base-url once per API instance, and add --allow-private-admin-metrics-targets "
            "when those direct admin URLs are private deployment-network addresses. "
            "The collector dry-run validates target identity, workload size, file-based image input, Marqo response "
            "engine, product URL prefix evidence from mall_config, API instance distribution, and per-instance "
            "admin server metrics source coverage, with three distinct image inputs."
        ),
        "security.nginx_config": (
            "Install or point to the active Nginx HTTPS site config; deploy/nginx/haeorum-ai-search.conf "
            "is the hardened template. The collector dry-run validates client_max_body_size, upstream failover/load "
            "balancing hints, keepalive, X-Forwarded-For/X-Real-IP overwrite, and Forwarded header sanitization "
            "before running security_check.py."
        ),
        "security.systemd_service": (
            "Install or point to the active API systemd unit; deploy/systemd/haeorum-ai-search.service "
            "is the template. The collector dry-run validates restart policy, non-root user, uvicorn ExecStart, "
            "NoNewPrivileges, and log write paths."
        ),
        "security.sync_systemd_service": (
            "Install or point to the active sync worker unit; deploy/systemd/haeorum-ai-sync.service "
            "is the template. The collector dry-run validates the continuous sync worker ExecStart, restart policy, "
            "EnvironmentFile, NoNewPrivileges, and log write paths."
        ),
        "security.reindex_systemd_service": (
            "Install or point to the active one-shot reindex unit; deploy/systemd/haeorum-ai-reindex.service "
            "is the template. The collector dry-run validates oneshot reindex ExecStart, EnvironmentFile, "
            "NoNewPrivileges, and log write paths."
        ),
        "security.reindex_systemd_timer": (
            "Install or point to the active nightly 03:00 reindex timer; "
            "deploy/systemd/haeorum-ai-reindex.timer is the template. The collector dry-run validates the 03:00 "
            "schedule, Unit target, Persistent=true, and timers.target install section."
        ),
        "security.logrotate_config": (
            "Install or point to the active logrotate rule; deploy/logrotate/haeorum-ai-search is the template. "
            "The collector dry-run validates JSONL log coverage, rotate count, and required directives."
        ),
    }
    if label.startswith("load.image_files["):
        return (
            f"Create the additional production reference image file{target} or update load.image_files in the evidence config. "
            "Image, mixed, 850-user, and API scale load evidence must use three distinct real image files."
        )
    return hints.get(label, f"Create the required input file{target} or update the evidence config to the deployed path.")


def missing_config_env_var(config: dict[str, Any], field: str) -> str:
    env_name = string_value(config, field + "_env")
    return env_name if env_name and not is_missing_config_value(env_name) else ""


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def evidence_file_exists(path: str | None) -> bool:
    return bool(path and Path(path).exists())


def is_builtin_sample_csv_value(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    return bool(text) and (text.endswith("/sample_products.csv") or text == "sample_products.csv")


def existing_evidence_is_acceptable(name: str, summary: dict[str, Any]) -> bool:
    if summary.get("json_ok") is not True or summary.get("ok") is not True:
        return False
    shape = summary.get("evidence_shape")
    if isinstance(shape, dict) and shape.get("ok") is not True:
        return False
    content = summary.get("evidence_content")
    if isinstance(content, dict) and content.get("ok") is not True:
        return False
    freshness = summary.get("evidence_freshness")
    if isinstance(freshness, dict) and freshness.get("ok") is not True:
        return False
    if (
        summary.get("local_only") is True
        or summary.get("not_operational_readiness") is True
        or summary.get("simulation_only") is True
        or summary.get("dry_run") is True
        or bool(str(summary.get("simulation_marker") or "").strip())
    ):
        return False
    if summary.get("csv_is_builtin_sample") is True or summary.get("dataset_is_builtin_sample_derived") is True:
        return False
    return True


def existing_evidence_summary(
    name: str,
    path: str | None,
    max_evidence_age_days: int = DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS,
) -> dict[str, Any]:
    if not path:
        return {"json_ok": False, "parse_error": "missing path", "ok": None}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"json_ok": False, "parse_error": str(exc), "ok": None}
    if not isinstance(data, dict):
        return {"json_ok": False, "parse_error": "JSON root must be an object", "ok": None}
    summary: dict[str, Any] = {"json_ok": True, "ok": data.get("ok")}
    if "generated_at" in data:
        summary["generated_at"] = data.get("generated_at")
    summary["evidence_shape"] = evidence_shape(name, data)
    summary["evidence_content"] = evidence_content(name, data)
    summary["evidence_freshness"] = evidence_freshness(data, max_evidence_age_days)
    for key in ["local_only", "not_operational_readiness", "simulation_only", "simulation_marker", "dry_run"]:
        if key in data:
            summary[key] = data.get(key)
    source = data.get("source") or {}
    if isinstance(source, dict):
        for key in [
            "csv_is_builtin_sample",
            "dataset_is_builtin_sample_derived",
            "builtin_sample_product_id_overlap",
            "product_id_count",
            "builtin_sample_product_id_ratio",
        ]:
            if key in source:
                summary[key] = source.get(key)
    if "csv" in data:
        summary["csv"] = data.get("csv")
        summary["csv_is_builtin_sample"] = summary.get("csv_is_builtin_sample") is True or is_builtin_sample_csv_value(data.get("csv"))
    for key in ["status_counts", "summary"]:
        value = data.get(key)
        if isinstance(value, dict):
            summary[key] = value
    return summary


def evidence_shape(name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    required = EVIDENCE_REQUIRED_KEYS.get(name, [])
    aliases = EVIDENCE_REQUIRED_ALIASES.get(name, {})
    missing = [
        key
        for key in required
        if key not in data and not any(alias in data for alias in aliases.get(key, ()))
    ]
    return {
        "ok": not missing,
        "required_keys": required,
        "required_aliases": aliases,
        "missing_keys": missing,
    }


def evidence_content(name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    if name == "mssql_export":
        problems.extend(readiness_check_content_problems("mssql_export", check_mssql_export, data))
    if name == "poc_dataset":
        problems.extend(readiness_check_content_problems("poc_dataset", check_poc_dataset, data))
    if name == "image_urls":
        problems.extend(readiness_check_content_problems("image_urls", check_image_urls, data))
    if name == "csv_poc_index":
        problems.extend(readiness_check_content_problems("csv_poc_index", check_csv_index, data))
    if name == "mall_config_build":
        problems.extend(readiness_check_content_problems("mall_config_build", check_mall_config_build(1700), data))
    if name == "mall_config":
        problems.extend(readiness_check_content_problems("mall_config", check_mall_config(1700), data))
    if name == "marqo_resource":
        problems.extend(readiness_check_content_problems("marqo_resource", check_marqo_resource, data))
    if name in LOAD_EVIDENCE_CHECKS:
        prefix, checker = LOAD_EVIDENCE_CHECKS[name]
        problems.extend(load_evidence_content_problems(prefix, checker, data))
    if name in IMAGE_FILE_EVIDENCE_STEPS:
        min_unique_files = MIN_LOAD_IMAGE_INPUTS if name.startswith("load_") else 1
        problems.extend(image_input_file_source_problems(data.get("image_input"), min_unique_files=min_unique_files))
    if name == "quality_report":
        problems.extend(quality_report_operational_content_problems(data))
    if name == "api_scale_comparison":
        for side in ("single", "multi"):
            summary = data.get(side)
            if not isinstance(summary, Mapping):
                problems.append(f"{side}")
                continue
            if summary.get("image_source_ok") is not True:
                problems.append(f"{side}.image_source_ok")
            problems.extend(
                f"{side}.{problem}"
                for problem in image_input_file_source_problems(
                    summary.get("image_input"),
                    min_unique_files=MIN_LOAD_IMAGE_INPUTS,
                )
            )
    return {
        "ok": not problems,
        "problems": sorted(set(problems)),
    }


def readiness_check_content_problems(
    prefix: str,
    checker: Callable[[dict[str, Any]], tuple[bool, str, dict[str, Any]]],
    data: Mapping[str, Any],
) -> list[str]:
    try:
        ok, _message, details = checker(dict(data))
    except Exception:
        return [f"{prefix}.checker_error"]
    raw_problems = details.get("problems") if isinstance(details, dict) else None
    if ok is True and not raw_problems:
        return []
    raw_missing_or_false = details.get("missing_or_false") if isinstance(details, dict) else None
    if isinstance(raw_problems, list) and raw_problems:
        problems = raw_problems
    elif isinstance(raw_missing_or_false, list) and raw_missing_or_false:
        problems = raw_missing_or_false
    else:
        problems = ["ok"]
    return [f"{prefix}.{str(problem)}" for problem in problems if str(problem).strip()]


def load_evidence_content_problems(
    prefix: str,
    checker: Callable[[dict[str, Any]], tuple[bool, str, dict[str, Any]]],
    data: Mapping[str, Any],
) -> list[str]:
    try:
        ok, _message, details = checker(dict(data))
    except Exception:
        return [f"{prefix}.checker_error"]
    if ok is True:
        return []
    problems: list[str] = []
    if data.get("ok") is not True:
        problems.append("ok")
    for key in ["requests", "concurrency", "active_users"]:
        if parse_count(details.get(key)) < parse_count(details.get(f"required_{key}")):
            problems.append(key)
    if details.get("thresholds_ok") is not True:
        problems.extend(str(problem) for problem in details.get("threshold_problems") or ["thresholds"])
    if details.get("image_source_ok") is not True:
        problems.extend(str(problem) for problem in details.get("image_source_problems") or ["image_input"])
    if details.get("response_contract_ok") is not True:
        problems.append("response_contract")
    if details.get("response_engine_ok") is not True:
        problems.append("response_engine")
    if details.get("response_shape_ok") is not True:
        problems.extend(str(problem) for problem in details.get("response_shape_problems") or ["response_shape"])
    if details.get("response_mall_identity_ok") is not True:
        problems.extend(
            str(problem)
            for problem in details.get("response_mall_identity_problems") or ["response_mall_identity"]
        )
    if details.get("query_type_coverage_ok") is not True:
        problems.extend(str(problem) for problem in details.get("query_type_coverage_problems") or ["query_type_coverage"])
    if details.get("target_validation_ok") is not True:
        problems.append("target_validation")
    problems.extend(str(problem) for problem in details.get("url_problems") or [])
    if details.get("server_metrics_ok") is not True:
        problems.extend(str(problem) for problem in details.get("server_metrics_missing") or ["server_metrics"])
    expected_mode = {
        "load_text_100_concurrent": "text",
        "load_image_30_concurrent": "image",
        "load_mixed_30_concurrent": "mixed",
    }.get(prefix)
    if expected_mode and data.get("mode") != expected_mode:
        problems.append("mode")
    if prefix == "load_mixed_traffic_850_active_users" and data.get("scenario") != "mixed-traffic":
        problems.append("scenario")
    return [f"{prefix}.{problem}" for problem in sorted(set(problems)) if problem]


def quality_report_operational_content_problems(data: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    operational_quality = data.get("operational_quality")
    if not isinstance(operational_quality, Mapping) or operational_quality.get("ok") is not True:
        problems.append("quality_report.operational_quality")
    if data.get("custom_cases") is not True:
        problems.append("quality_report.custom_cases")
    case_source = str(data.get("case_source") or "").strip()
    if not case_source or case_source == "builtin":
        problems.append("quality_report.case_source")
    if not fingerprint_report_ready(data.get("case_source_fingerprint")):
        problems.append("quality_report.case_source_fingerprint")
    if not is_exact_count(data.get("skipped_case_checks"), 0):
        problems.append("quality_report.skipped_case_checks")
    if not is_minimum_count(data.get("image_cases_with_file_source"), 1):
        problems.append("quality_report.image_cases_with_file_source")
    if not is_minimum_count(data.get("mixed_cases_with_file_source"), 1):
        problems.append("quality_report.mixed_cases_with_file_source")
    for problem in quality_report_case_image_fingerprint_problems(data):
        if problem == "case_image_fingerprints":
            problems.append("quality_report.case_image_fingerprints")
        else:
            problems.append("quality_report." + problem)
    source = data.get("source")
    source_engine = source.get("engine") if isinstance(source, Mapping) else None
    if source_engine != "marqo":
        problems.append("quality_report.source.engine")
    case_result_evidence = summarize_quality_case_result_evidence(dict(data))
    if case_result_evidence.get("ok") is not True:
        problems.extend(
            "quality_report.case_result_evidence." + str(problem)
            for problem in case_result_evidence.get("problems") or ["ok"]
        )
    result_contract_evidence = summarize_quality_result_contract_evidence(dict(data))
    if result_contract_evidence.get("ok") is not True:
        problems.extend(
            "quality_report.result_contract_evidence." + str(problem)
            for problem in result_contract_evidence.get("problems") or ["ok"]
        )
    return problems


def quality_report_case_image_fingerprint_problems(data: Mapping[str, Any]) -> list[str]:
    expected_count = parse_count(data.get("image_cases_with_file_source")) + parse_count(
        data.get("mixed_cases_with_file_source")
    )
    if expected_count <= 0:
        return []
    entries = data.get("case_image_fingerprints")
    if not isinstance(entries, list):
        return ["case_image_fingerprints"]
    problems: list[str] = []
    if len(entries) < expected_count:
        problems.append("case_image_fingerprints.count")
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    seen_digests: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"case_image_fingerprints[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(prefix)
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            problems.append(prefix + ".name")
        elif name in seen_names:
            problems.append(prefix + ".name_duplicate")
        if name:
            seen_names.add(name)
        if str(entry.get("query_type") or "") not in {"image", "text_image"}:
            problems.append(prefix + ".query_type")
        if entry.get("image_source") != "case_file":
            problems.append(prefix + ".image_source")
        fingerprint = entry.get("fingerprint")
        if not fingerprint_report_ready(fingerprint):
            problems.append(prefix + ".fingerprint")
            continue
        path = str(fingerprint.get("path") or "").strip()
        digest = str(fingerprint.get("digest") or "").strip().lower()
        if path in seen_paths:
            problems.append(prefix + ".fingerprint.path_duplicate")
        elif path:
            seen_paths.add(path)
        if digest in seen_digests:
            problems.append(prefix + ".fingerprint.digest_duplicate")
        elif digest:
            seen_digests.add(digest)
    return problems


def image_input_file_source_problems(value: Any, *, min_unique_files: int = 1) -> list[str]:
    image_input = value if isinstance(value, Mapping) else {}
    problems: list[str] = []
    source = image_input.get("source")
    min_unique_files = max(1, int(min_unique_files or 1))
    if source not in {"file", "files"} or (min_unique_files > 1 and source != "files"):
        problems.append("image_input.source")
    if not str(image_input.get("file") or "").strip():
        problems.append("image_input.file")
    if not is_positive_number(image_input.get("size_bytes")):
        problems.append("image_input.size_bytes")
    if not is_minimum_dimension(image_input.get("width")):
        problems.append("image_input.width")
    if not is_minimum_dimension(image_input.get("height")):
        problems.append("image_input.height")
    if not str(image_input.get("sha256") or "").strip():
        problems.append("image_input.sha256")
    if source == "files":
        files = image_input.get("files") if isinstance(image_input.get("files"), list) else []
        images = image_input.get("images") if isinstance(image_input.get("images"), list) else []
        file_count = parse_count(image_input.get("file_count"))
        unique_sha256_count = parse_count(image_input.get("unique_sha256_count"))
        if file_count < min_unique_files:
            problems.append("image_input.file_count")
        if len(files) != file_count:
            problems.append("image_input.files")
        if unique_sha256_count < min_unique_files:
            problems.append("image_input.unique_sha256_count")
        if images and len(images) < min(file_count, 50):
            problems.append("image_input.images")
        for index, image in enumerate(images[:50]):
            if not isinstance(image, Mapping):
                problems.append(f"image_input.images[{index}]")
                continue
            if not str(image.get("file") or "").strip():
                problems.append(f"image_input.images[{index}].file")
            if not is_positive_number(image.get("size_bytes")):
                problems.append(f"image_input.images[{index}].size_bytes")
            if not is_minimum_dimension(image.get("width")):
                problems.append(f"image_input.images[{index}].width")
            if not is_minimum_dimension(image.get("height")):
                problems.append(f"image_input.images[{index}].height")
            if not str(image.get("sha256") or "").strip():
                problems.append(f"image_input.images[{index}].sha256")
    return problems


def is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def is_minimum_count(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def parse_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_exact_count(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def fingerprint_report_ready(value: Any) -> bool:
    digest = str(value.get("digest") or "").strip().lower() if isinstance(value, Mapping) else ""
    return (
        isinstance(value, Mapping)
        and value.get("algorithm") == "sha256"
        and value.get("exists") is True
        and bool(str(value.get("path") or "").strip())
        and is_positive_number(value.get("size_bytes"))
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )


def is_minimum_dimension(value: Any, minimum: int = 16) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) >= minimum


def evidence_freshness(
    data: Mapping[str, Any],
    max_age_days: int = DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = str(data.get("generated_at") or "").strip()
    checked_at = now or datetime.now(timezone.utc)
    details: dict[str, Any] = {
        "ok": False,
        "generated_at": generated_at or None,
        "checked_at": checked_at.isoformat(),
        "max_age_days": max_age_days,
        "future_skew_seconds": EVIDENCE_FUTURE_SKEW_SECONDS,
        "age_seconds": None,
        "problems": [],
    }
    if max_age_days <= 0:
        details["ok"] = True
        return details
    if not generated_at:
        details["problems"] = ["generated_at"]
        return details
    try:
        parsed = parse_evidence_datetime(generated_at)
    except ValueError:
        details["problems"] = ["generated_at_format"]
        return details
    age = checked_at - parsed
    details["age_seconds"] = round(age.total_seconds(), 3)
    problems: list[str] = []
    if parsed > checked_at + timedelta(seconds=EVIDENCE_FUTURE_SKEW_SECONDS):
        problems.append("generated_at_future")
    if age > timedelta(days=max_age_days):
        problems.append("generated_at_stale")
    details["problems"] = problems
    details["ok"] = not problems
    return details


def parse_evidence_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid generated_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def tail_text(value: Any, max_chars: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def command_to_string(command: list[str]) -> str:
    return " ".join(quote_command_part(part) for part in command)


def quote_command_part(value: Any) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def to_markdown(report: dict[str, Any]) -> str:
    runbook = report.get("execution_runbook") if isinstance(report.get("execution_runbook"), dict) else {}
    deployment_evidence_dir = str(runbook.get("deployment_evidence_dir") or "").strip()
    display_evidence_dir = deployment_evidence_dir or str(report["evidence_dir"])
    lines = [
        "# Haeorum AI Search Operational Evidence Collection",
        "",
        f"- OK: `{report['ok']}`",
        f"- Dry run: `{report['dry_run']}`",
        f"- Ready to execute: `{report.get('ready_to_execute')}`",
        f"- Evidence complete: `{report.get('evidence_complete')}`",
        f"- Evidence dir: `{display_evidence_dir}`",
        f"- Max existing evidence age days: `{report.get('max_existing_evidence_age_days')}`",
        "",
    ]
    if report.get("simulation_only") or report.get("simulation_marker"):
        lines.extend(
            [
                f"- Simulation marker: `{report.get('simulation_marker')}`",
                f"- Simulation only: `{report.get('simulation_only')}`",
                f"- Refused reason: `{report.get('refused_reason') or ''}`",
                "",
            ]
        )
    env_permissions = report.get("collector_env_file_permissions")
    if isinstance(env_permissions, dict):
        lines.extend(
            [
                f"- Collector env file permissions: `{env_permissions.get('ok')}`",
                f"- Collector env file mode: `{env_permissions.get('mode')}`",
                "",
            ]
        )
    blocking_inputs = report.get("blocking_inputs") if isinstance(report.get("blocking_inputs"), dict) else {}
    missing_config = blocking_inputs.get("missing_config") if isinstance(blocking_inputs, dict) else []
    missing_files = blocking_inputs.get("missing_files") if isinstance(blocking_inputs, dict) else []
    invalid_files = blocking_inputs.get("invalid_files") if isinstance(blocking_inputs, dict) else []
    if missing_config or missing_files or invalid_files:
        lines.extend(
            [
                "## Blocking Inputs",
                "",
                "| Type | Name | Env/Path | Resolution | Blocked Steps |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in missing_config or []:
            lines.append(
                "| config | {name} | {source} | {resolution} | {steps} |".format(
                    name=item.get("name", ""),
                    source=item.get("env_var", ""),
                    resolution=escape_markdown_cell(item.get("resolution", "")),
                    steps=", ".join(item.get("steps") or []),
                )
            )
        for item in missing_files or []:
            lines.append(
                "| file | {name} | {source} | {resolution} | {steps} |".format(
                    name=item.get("name", ""),
                    source=escape_markdown_cell(item.get("path", "")),
                    resolution=escape_markdown_cell(item.get("resolution", "")),
                    steps=", ".join(item.get("steps") or []),
                )
            )
        for item in invalid_files or []:
            problem_summary = "; ".join(item.get("problems") or [])
            resolution = str(item.get("resolution") or "")
            if problem_summary:
                resolution = f"Problems: {problem_summary}. Resolution: {resolution}"
            lines.append(
                "| invalid file | {name} | {source} | {resolution} | {steps} |".format(
                    name=item.get("name", ""),
                    source=escape_markdown_cell(item.get("path", "")),
                    resolution=escape_markdown_cell(resolution),
                    steps=", ".join(item.get("steps") or []),
                )
            )
        lines.append("")
    commands = runbook.get("deployment_commands") or runbook.get("commands") if isinstance(runbook, dict) else []
    if commands:
        using_deployment_commands = bool(runbook.get("deployment_commands"))
        working_directory = runbook.get("deployment_working_directory") if using_deployment_commands else runbook.get("working_directory")
        lines.extend(
            [
                "## Execution Runbook",
                "",
                f"- Working directory: `{working_directory or ''}`",
                "- Resolve the blocking inputs above before running the full collection command.",
                "- Treat `ready_to_execute=true` as permission to run the collection, not as completed evidence.",
                "",
            ]
        )
        for command in commands:
            code_fence = "bash" if using_deployment_commands else "powershell"
            lines.extend(
                [
                    f"### {command.get('name', '')}",
                    "",
                    str(command.get("when") or ""),
                    "",
                    f"```{code_fence}",
                    str(command.get("command") or ""),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "| Step | Status | Missing Inputs | Command |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in report["steps"]:
        missing = list(item.get("missing_config") or [])
        missing.extend(f"file:{name}" for name in item.get("missing_input_files") or [])
        evidence = item.get("evidence_file")
        command_text = str(item.get("command") or "")
        if deployment_evidence_dir:
            command_text = render_collection_markdown_text(command_text, report["evidence_dir"], deployment_evidence_dir)
            evidence = render_collection_markdown_text(evidence, report["evidence_dir"], deployment_evidence_dir)
        evidence_note = f" Evidence: {evidence} ({'exists' if item.get('evidence_exists') else 'missing'})." if evidence else ""
        lines.append(
            "| {name} | `{status}` | {missing} | `{command}` |".format(
                name=item["name"],
                status=item["status"],
                missing=", ".join(missing),
                command=escape_markdown_cell(command_text + evidence_note),
            )
        )
    return "\n".join(lines) + "\n"


def render_collection_markdown_text(value: Any, local_evidence_dir: str | Path, deployment_evidence_dir: str | Path) -> str:
    text = render_blocker_text(value, deployment_evidence_dir)
    return replace_evidence_dir_prefix(text, local_evidence_dir, deployment_evidence_dir)


def sanitize_collection_report_for_deployment(
    report: dict[str, Any],
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> dict[str, Any]:
    sanitized = sanitize_handoff_report(
        report,
        deployment_project_root=deployment_project_root,
        deployment_evidence_dir=deployment_evidence_dir,
    )
    local_evidence_dir = str(report.get("evidence_dir") or "").strip()
    if local_evidence_dir and str(deployment_evidence_dir or "").strip():
        sanitized_local_evidence_dir = sanitize_handoff_report(
            local_evidence_dir,
            deployment_project_root=deployment_project_root,
            deployment_evidence_dir=deployment_evidence_dir,
        )
        for candidate in [local_evidence_dir, sanitized_local_evidence_dir]:
            sanitized = rewrite_collection_evidence_dir(sanitized, candidate, deployment_evidence_dir)
    return sanitized


def rewrite_collection_evidence_dir(value: Any, local_evidence_dir: str | Path, deployment_evidence_dir: str | Path) -> Any:
    if isinstance(value, dict):
        return {
            key: rewrite_collection_evidence_dir(item, local_evidence_dir, deployment_evidence_dir)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [rewrite_collection_evidence_dir(item, local_evidence_dir, deployment_evidence_dir) for item in value]
    if not isinstance(value, str):
        return value
    return replace_evidence_dir_prefix(value, local_evidence_dir, deployment_evidence_dir)


def replace_evidence_dir_prefix(text: str, local_evidence_dir: str | Path, deployment_evidence_dir: str | Path) -> str:
    deployment_text = normalize_posix_path(deployment_evidence_dir).rstrip("/")
    if not deployment_text:
        return text
    local_text = str(local_evidence_dir).rstrip("/\\")
    result = text
    candidates = sorted({local_text, local_text.replace("\\", "/")}, key=len, reverse=True)
    for candidate in candidates:
        if not candidate:
            continue
        result = re.sub(re.escape(candidate) + r"(?=$|[\s\\/'\"])", deployment_text, result)
    return result.replace(deployment_text + "\\", deployment_text + "/")


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def write_requirements_audit_report(
    local_acceptance_report: str | Path,
    operational_readiness_report: str | Path,
    evidence_collection_report: str | Path,
    output: str | Path,
    markdown_output: str | Path | None = None,
    blocker_checklist_output: str | Path | None = None,
    blocker_checklist_project_root: str | Path = "",
    blocker_checklist_evidence_dir: str | Path = "",
) -> dict[str, Any]:
    args = argparse.Namespace(
        local_acceptance_report=str(local_acceptance_report),
        operational_readiness_report=str(operational_readiness_report),
        evidence_collection_report=str(evidence_collection_report),
        output="",
        markdown_output="",
        blocker_checklist_project_root=str(blocker_checklist_project_root),
        blocker_checklist_evidence_dir=str(blocker_checklist_evidence_dir),
    )
    report = build_requirements_audit_report(args)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_output:
        markdown_path = Path(markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            requirements_audit_to_markdown(
                report,
                deployment_project_root=blocker_checklist_project_root,
                deployment_evidence_dir=blocker_checklist_evidence_dir,
            ),
            encoding="utf-8",
        )
    if blocker_checklist_output:
        blocker_path = Path(blocker_checklist_output)
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(
            requirements_audit_to_blocker_checklist(
                report,
                deployment_project_root=blocker_checklist_project_root,
                deployment_evidence_dir=blocker_checklist_evidence_dir,
            ),
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Haeorum AI Search operational evidence from one config file.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--env-file", default="", help="Optional KEY=VALUE file for *_env secret references.")
    parser.add_argument(
        "--max-existing-evidence-age-days",
        type=int,
        default=DEFAULT_MAX_EXISTING_EVIDENCE_AGE_DAYS,
        help="Reject existing dry-run evidence reports older than this many days. Use 0 only for local debugging.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument(
        "--local-acceptance-report",
        default="",
        help="Local acceptance report used when also writing a requirements audit.",
    )
    parser.add_argument(
        "--operational-readiness-report",
        default="",
        help="Operational readiness report used when also writing a requirements audit.",
    )
    parser.add_argument(
        "--requirements-audit-output",
        default="",
        help="Also write a final requirements audit JSON after the evidence collection report is saved.",
    )
    parser.add_argument(
        "--requirements-audit-markdown-output",
        default="",
        help="Optional Markdown output for --requirements-audit-output.",
    )
    parser.add_argument(
        "--requirements-blocker-checklist-output",
        default="",
        help="Optional blocker checklist Markdown output for --requirements-audit-output.",
    )
    args = parser.parse_args()

    report = build_report(
        args.config,
        args.evidence_dir,
        timeout=args.timeout,
        dry_run=args.dry_run,
        stop_on_failure=args.stop_on_failure,
        env_file=args.env_file or None,
        max_existing_evidence_age_days=args.max_existing_evidence_age_days,
    )
    config = load_config(args.config)
    deployment_project_root = string_value(config, "missing_commands", "project_root")
    deployment_evidence_dir = string_value(config, "missing_commands", "evidence_dir")
    output_report = sanitize_collection_report_for_deployment(
        report,
        deployment_project_root=deployment_project_root,
        deployment_evidence_dir=deployment_evidence_dir,
    )
    text = json.dumps(output_report, ensure_ascii=False, indent=2)
    print(text)
    collection_report_path = Path(args.output) if args.output else normalize_evidence_dir(args.evidence_dir) / "evidence-collection-plan.json"
    if args.output:
        collection_report_path.parent.mkdir(parents=True, exist_ok=True)
        collection_report_path.write_text(text + "\n", encoding="utf-8")
    elif args.requirements_audit_output:
        collection_report_path.parent.mkdir(parents=True, exist_ok=True)
        collection_report_path.write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(to_markdown(output_report), encoding="utf-8")
    audit_ok = True
    if args.requirements_audit_output:
        evidence_root = normalize_evidence_dir(args.evidence_dir)
        audit_report = write_requirements_audit_report(
            args.local_acceptance_report or evidence_root / "local-acceptance.json",
            args.operational_readiness_report or evidence_root / DEFAULT_EVIDENCE_FILES["operational_readiness"],
            collection_report_path,
            args.requirements_audit_output,
            args.requirements_audit_markdown_output or None,
            args.requirements_blocker_checklist_output or None,
            string_value(config, "missing_commands", "project_root"),
            string_value(config, "missing_commands", "evidence_dir"),
        )
        audit_ok = audit_report.get("ok") is True
    return 0 if report["ok"] and audit_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
