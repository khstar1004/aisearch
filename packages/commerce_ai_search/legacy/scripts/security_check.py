from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NGINX_CONFIG_PATH = Path("/etc/nginx/sites-enabled/haeorum-ai-search.conf")
DEFAULT_LOGROTATE_CONFIG_PATH = Path("/etc/logrotate.d/haeorum-ai-search")
DEFAULT_SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/haeorum-ai-search.service")
DEFAULT_SYNC_SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/haeorum-ai-sync.service")
DEFAULT_REINDEX_SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/haeorum-ai-reindex.service")
DEFAULT_REINDEX_SYSTEMD_TIMER_PATH = Path("/etc/systemd/system/haeorum-ai-reindex.timer")
MIN_SYSTEMD_NOFILE = 65535
NGINX_BODY_SIZE_PATTERN = re.compile(r"\bclient_max_body_size\s+([^;]+);", re.IGNORECASE)
NGINX_UPSTREAM_PATTERN = re.compile(r"upstream\s+haeorum_ai_search_api\s*\{(?P<body>.*?)\}", re.DOTALL | re.IGNORECASE)
LOGROTATE_PATTERN = re.compile(r"(?P<paths>[^\{\n]+)\{(?P<body>.*?)\}", re.DOTALL)
sys.path.insert(0, str(ROOT))

from app.config import (
    PLACEHOLDER_ADMIN_API_KEYS,
    PRODUCTION_ENVIRONMENTS,
    PRODUCTION_SEARCH_ENGINES,
    Settings,
    check_sync_alert_webhook_url,
    is_placeholder_public_api_key,
    is_weak_public_api_key,
    load_settings,
    mall_origins_missing_from_cors,
    origin_uses_safe_public_url,
    product_url_template_uses_safe_public_url,
    product_url_template_uses_https,
)

try:
    from scripts.collect_operational_evidence import load_env_file
    from scripts.env_file_security import SERVICE_ENV_FILE_MAX_MODE, check_secret_file_permissions
except ModuleNotFoundError:  # pragma: no cover - direct script execution from scripts/
    from collect_operational_evidence import load_env_file
    from env_file_security import SERVICE_ENV_FILE_MAX_MODE, check_secret_file_permissions


@contextmanager
def patched_environ(values: Mapping[str, str], *, clear: bool = False) -> Iterator[None]:
    original = dict(os.environ)
    if clear:
        os.environ.clear()
    os.environ.update({name: str(value) for name, value in values.items()})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def build_security_report(
    settings: Settings,
    base_url: str,
    mssql_ip_restricted: bool,
    require_production: bool = True,
    sync_alerting_configured: bool = False,
    nginx_config_path: str | Path | None = DEFAULT_NGINX_CONFIG_PATH,
    logrotate_config_path: str | Path | None = DEFAULT_LOGROTATE_CONFIG_PATH,
    systemd_service_path: str | Path | None = DEFAULT_SYSTEMD_SERVICE_PATH,
    sync_systemd_service_path: str | Path | None = DEFAULT_SYNC_SYSTEMD_SERVICE_PATH,
    reindex_systemd_service_path: str | Path | None = DEFAULT_REINDEX_SYSTEMD_SERVICE_PATH,
    reindex_systemd_timer_path: str | Path | None = DEFAULT_REINDEX_SYSTEMD_TIMER_PATH,
    service_env_file_path: str | Path | None = None,
) -> dict[str, Any]:
    enabled_malls = [mall for mall in settings.malls.values() if mall.enabled]
    admin_key = str(settings.admin_api_key or "")
    malls_with_wildcard_allowed_origins = sorted(
        mall.mall_id for mall in enabled_malls if "*" in mall.allowed_origins
    )
    malls_with_non_https_allowed_origins = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not str(origin).lower().startswith("https://")]
        for mall in enabled_malls
    }
    malls_with_non_https_allowed_origins = {
        mall_id: origins for mall_id, origins in malls_with_non_https_allowed_origins.items() if origins
    }
    malls_with_placeholder_api_keys = sorted(
        mall.mall_id for mall in enabled_malls if mall.api_key and is_placeholder_public_api_key(mall.api_key)
    )
    malls_with_weak_api_keys = sorted(
        mall.mall_id
        for mall in enabled_malls
        if mall.api_key and not is_placeholder_public_api_key(mall.api_key) and is_weak_public_api_key(mall.api_key)
    )
    non_https_cors_origins = [
        origin for origin in settings.cors_origins if origin != "*" and not str(origin).lower().startswith("https://")
    ]
    unsafe_cors_origins = [
        origin for origin in settings.cors_origins if origin != "*" and not origin_uses_safe_public_url(origin)
    ]
    malls_with_unsafe_allowed_origins = {
        mall.mall_id: [origin for origin in mall.allowed_origins if not origin_uses_safe_public_url(origin)]
        for mall in enabled_malls
    }
    malls_with_unsafe_allowed_origins = {
        mall_id: origins for mall_id, origins in malls_with_unsafe_allowed_origins.items() if origins
    }
    global_product_url_template_https = safe_product_url_template_uses_https(
        settings.product_url_template,
        mall_id="www",
    )
    global_product_url_template_safe_public = safe_product_url_template_uses_safe_public_url(
        settings.product_url_template,
        mall_id="www",
    )
    malls_with_non_https_product_url_templates = sorted(
        mall.mall_id
        for mall in enabled_malls
        if not safe_product_url_template_uses_https(mall.product_url_template, mall_id=mall.mall_id)
    )
    malls_with_unsafe_product_url_templates = sorted(
        mall.mall_id
        for mall in enabled_malls
        if not safe_product_url_template_uses_safe_public_url(mall.product_url_template, mall_id=mall.mall_id)
    )
    malls_missing_cors_origins = mall_origins_missing_from_cors(settings)
    nginx_body_limit = check_nginx_client_max_body_size(nginx_config_path, settings.max_image_mb)
    nginx_upstream = check_nginx_upstream_resilience(nginx_config_path)
    nginx_forwarded_for = check_nginx_forwarded_for_safety(nginx_config_path)
    logrotate = check_logrotate_config(logrotate_config_path)
    systemd_restart = check_systemd_restart_policy(systemd_service_path)
    sync_worker = check_systemd_sync_worker_service(sync_systemd_service_path)
    reindex_service = check_systemd_reindex_service(reindex_systemd_service_path)
    reindex_timer = check_systemd_reindex_timer(
        reindex_systemd_timer_path,
        expected_unit=Path(str(reindex_systemd_service_path or DEFAULT_REINDEX_SYSTEMD_SERVICE_PATH)).name,
    )
    service_env_file_permissions = check_secret_file_permissions(
        service_env_file_path,
        name="service_env_file_permissions",
        required=bool(service_env_file_path),
        max_mode=SERVICE_ENV_FILE_MAX_MODE,
    )
    public_base_url = check_public_base_url(base_url)
    sync_alert_webhook = check_sync_alert_webhook_url(settings.sync_alert_webhook_url)
    sync_alert_webhook_configured = bool(sync_alert_webhook["configured"])
    sync_alert_webhook_valid = bool(sync_alert_webhook["ok"])
    external_sync_alerting_confirmed = bool(sync_alerting_configured)
    checks = {
        "https": public_base_url["scheme"] == "https",
        "public_base_url": public_base_url["ok"],
        "cors_restricted": bool(settings.cors_origins) and "*" not in settings.cors_origins,
        "cors_origins_https": (not require_production) or not non_https_cors_origins,
        "cors_origins_safe_public": (not require_production) or not unsafe_cors_origins,
        "cors_covers_allowed_origins": not malls_missing_cors_origins,
        "allowed_origins": bool(enabled_malls)
        and all(bool(mall.allowed_origins) for mall in enabled_malls)
        and not malls_with_wildcard_allowed_origins
        and not malls_with_non_https_allowed_origins,
        "allowed_origins_safe_public": bool(enabled_malls) and not malls_with_unsafe_allowed_origins,
        "product_url_templates_https": (not require_production)
        or (global_product_url_template_https and not malls_with_non_https_product_url_templates),
        "product_url_templates_safe_public": (not require_production)
        or (global_product_url_template_safe_public and not malls_with_unsafe_product_url_templates),
        "admin_key": admin_key.lower() not in PLACEHOLDER_ADMIN_API_KEYS and len(admin_key) >= 16,
        "mall_api_key": bool(enabled_malls)
        and all(bool(mall.api_key) for mall in enabled_malls)
        and not malls_with_placeholder_api_keys
        and not malls_with_weak_api_keys,
        "mall_api_key_strength": bool(enabled_malls) and not malls_with_weak_api_keys,
        "mssql_ip_restricted": bool(mssql_ip_restricted),
        "production_env": (settings.environment in PRODUCTION_ENVIRONMENTS) if require_production else True,
        "production_search_engine": (settings.engine_backend in PRODUCTION_SEARCH_ENGINES) if require_production else True,
        "sync_interval_hourly": 0 < settings.sync_interval_seconds <= 3600,
        "sync_alert_webhook_valid": sync_alert_webhook_valid,
        "sync_failure_alerting": (sync_alert_webhook_configured and sync_alert_webhook_valid)
        or external_sync_alerting_confirmed,
        "nginx_client_max_body_size": nginx_body_limit["ok"],
        "nginx_upstream_resilience": nginx_upstream["ok"],
        "nginx_forwarded_for_safety": nginx_forwarded_for["ok"],
        "systemd_restart_policy": systemd_restart["ok"],
        "systemd_sync_worker": sync_worker["ok"],
        "systemd_reindex_service": reindex_service["ok"],
        "systemd_reindex_timer": reindex_timer["ok"],
        "logrotate_config": logrotate["ok"],
        "service_env_file_permissions": service_env_file_permissions["ok"],
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **checks,
        "failed_checks": failed,
        "base_url": base_url,
        "public_base_url_report": public_base_url,
        "environment": settings.environment,
        "engine_backend": settings.engine_backend,
        "sync_interval_seconds": settings.sync_interval_seconds,
        "sync_alert_webhook_configured": sync_alert_webhook_configured,
        "sync_alert_webhook_valid": sync_alert_webhook_valid,
        "sync_alert_webhook": sync_alert_webhook,
        "external_sync_alerting_confirmed": external_sync_alerting_confirmed,
        "cors_origins": list(settings.cors_origins),
        "non_https_cors_origins": sorted(non_https_cors_origins),
        "unsafe_cors_origins": sorted(unsafe_cors_origins),
        "enabled_mall_count": len(enabled_malls),
        "malls_without_allowed_origins": sorted(mall.mall_id for mall in enabled_malls if not mall.allowed_origins),
        "malls_with_wildcard_allowed_origins": malls_with_wildcard_allowed_origins,
        "malls_with_non_https_allowed_origins": malls_with_non_https_allowed_origins,
        "malls_with_unsafe_allowed_origins": malls_with_unsafe_allowed_origins,
        "malls_missing_cors_origins": malls_missing_cors_origins,
        "malls_without_api_key": sorted(mall.mall_id for mall in enabled_malls if not mall.api_key),
        "malls_with_placeholder_api_keys": malls_with_placeholder_api_keys,
        "malls_with_weak_api_keys": malls_with_weak_api_keys,
        "global_product_url_template_https": global_product_url_template_https,
        "global_product_url_template_safe_public": global_product_url_template_safe_public,
        "malls_with_non_https_product_url_templates": malls_with_non_https_product_url_templates,
        "malls_with_unsafe_product_url_templates": malls_with_unsafe_product_url_templates,
        "nginx": nginx_body_limit,
        "nginx_upstream": nginx_upstream,
        "nginx_forwarded_for": nginx_forwarded_for,
        "systemd": systemd_restart,
        "sync_systemd": sync_worker,
        "reindex_systemd": reindex_service,
        "reindex_timer": reindex_timer,
        "logrotate": logrotate,
        "service_env_file_permissions_report": service_env_file_permissions,
    }


def safe_product_url_template_uses_https(value: object, mall_id: str) -> bool:
    try:
        return product_url_template_uses_https(value, mall_id=mall_id)
    except ValueError:
        return False


def safe_product_url_template_uses_safe_public_url(value: object, mall_id: str) -> bool:
    try:
        return product_url_template_uses_safe_public_url(value, mall_id=mall_id)
    except ValueError:
        return False


def is_local_public_base_url_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if host in {"localhost", "0.0.0.0", "::", "::1"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified or address.is_link_local


def check_public_base_url(base_url: str) -> dict[str, Any]:
    text = str(base_url or "").strip()
    problems: list[str] = []
    if not text:
        problems.append("base_url")
        return {"ok": False, "url": text, "scheme": None, "host": None, "problems": problems}
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in text):
        problems.append("base_url_format")
        return {"ok": False, "url": text, "scheme": None, "host": None, "problems": problems}
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError:
        problems.append("base_url_port")
        return {
            "ok": False,
            "url": text,
            "scheme": parsed.scheme.lower() or None,
            "host": parsed.hostname,
            "problems": problems,
        }
    scheme = parsed.scheme.lower()
    if scheme != "https":
        problems.append("base_url_https")
    if not parsed.netloc or not parsed.hostname:
        problems.append("base_url_host")
    if parsed.username is not None or parsed.password is not None:
        problems.append("base_url_credentials")
    if parsed.hostname and is_local_public_base_url_host(parsed.hostname):
        problems.append("base_url_non_local")
    if parsed.params or parsed.query or parsed.fragment:
        problems.append("base_url_clean_url")
    return {
        "ok": not problems,
        "url": text,
        "scheme": scheme or None,
        "host": parsed.hostname,
        "path": parsed.path,
        "problems": sorted(set(problems)),
    }


def parse_nginx_size_to_bytes(value: str) -> int:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+)([kKmMgG]?)", text)
    if not match:
        raise ValueError(f"invalid nginx size: {value}")
    amount = int(match.group(1))
    suffix = match.group(2).lower()
    multiplier = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[suffix]
    return amount * multiplier


def check_nginx_client_max_body_size(config_path: str | Path | None, max_image_mb: int) -> dict[str, Any]:
    min_body_bytes = (max(1, int(max_image_mb)) + 1) * 1024 * 1024
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "configured": None,
            "configured_bytes": None,
            "minimum_bytes": min_body_bytes,
            "message": "nginx config path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "configured": None,
            "configured_bytes": None,
            "minimum_bytes": min_body_bytes,
            "message": "nginx config file is missing",
        }
    text = path.read_text(encoding="utf-8")
    matches = NGINX_BODY_SIZE_PATTERN.findall(text)
    if not matches:
        return {
            "ok": False,
            "path": str(path),
            "configured": None,
            "configured_bytes": None,
            "minimum_bytes": min_body_bytes,
            "message": "client_max_body_size is missing",
        }
    configured = matches[-1].strip()
    try:
        configured_bytes = parse_nginx_size_to_bytes(configured)
    except ValueError as exc:
        return {
            "ok": False,
            "path": str(path),
            "configured": configured,
            "configured_bytes": None,
            "minimum_bytes": min_body_bytes,
            "message": str(exc),
        }
    ok = configured_bytes >= min_body_bytes
    return {
        "ok": ok,
        "path": str(path),
        "configured": configured,
        "configured_bytes": configured_bytes,
        "minimum_bytes": min_body_bytes,
        "message": "client_max_body_size covers image uploads" if ok else "client_max_body_size is below image upload limit plus form overhead",
    }


def check_nginx_upstream_resilience(config_path: str | Path | None) -> dict[str, Any]:
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "server_count": 0,
            "has_load_balancing": False,
            "has_keepalive": False,
            "servers_missing_failover": [],
            "message": "nginx config path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "server_count": 0,
            "has_load_balancing": False,
            "has_keepalive": False,
            "servers_missing_failover": [],
            "message": "nginx config file is missing",
        }
    text = strip_nginx_comments(path.read_text(encoding="utf-8"))
    match = NGINX_UPSTREAM_PATTERN.search(text)
    if not match:
        return {
            "ok": False,
            "path": str(path),
            "server_count": 0,
            "has_load_balancing": False,
            "has_keepalive": False,
            "servers_missing_failover": [],
            "message": "upstream haeorum_ai_search_api is missing",
        }
    body = match.group("body")
    servers = [line.strip() for line in body.splitlines() if line.strip().startswith("server ")]
    servers_missing_failover = [
        server for server in servers if "max_fails=" not in server or "fail_timeout=" not in server
    ]
    has_load_balancing = any(token in body for token in ["least_conn", "ip_hash", "hash "])
    has_keepalive = bool(re.search(r"\bkeepalive\s+\d+\s*;", body))
    ok = bool(servers) and not servers_missing_failover and has_load_balancing and has_keepalive
    return {
        "ok": ok,
        "path": str(path),
        "server_count": len(servers),
        "has_load_balancing": has_load_balancing,
        "has_keepalive": has_keepalive,
        "servers_missing_failover": servers_missing_failover,
        "message": "nginx upstream has load balancing and failover hints" if ok else "nginx upstream resilience is incomplete",
    }


def check_nginx_forwarded_for_safety(config_path: str | Path | None) -> dict[str, Any]:
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "safe_header_count": 0,
            "unsafe_header_count": 0,
            "x_real_ip_safe_header_count": 0,
            "x_real_ip_unsafe_header_count": 0,
            "forwarded_header_sanitized_count": 0,
            "forwarded_header_unsafe_count": 0,
            "missing_or_invalid": ["path"],
            "message": "nginx config path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "safe_header_count": 0,
            "unsafe_header_count": 0,
            "x_real_ip_safe_header_count": 0,
            "x_real_ip_unsafe_header_count": 0,
            "forwarded_header_sanitized_count": 0,
            "forwarded_header_unsafe_count": 0,
            "missing_or_invalid": ["path"],
            "message": "nginx config file is missing",
        }
    text = strip_nginx_comments(path.read_text(encoding="utf-8"))
    safe_headers = re.findall(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", text, flags=re.IGNORECASE)
    unsafe_headers = re.findall(
        r"proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for\s*;",
        text,
        flags=re.IGNORECASE,
    )
    x_real_ip_safe_headers = re.findall(r"proxy_set_header\s+X-Real-IP\s+\$remote_addr\s*;", text, flags=re.IGNORECASE)
    x_real_ip_unsafe_headers = [
        value.strip()
        for value in re.findall(r"proxy_set_header\s+X-Real-IP\s+([^;]+);", text, flags=re.IGNORECASE)
        if value.strip() != "$remote_addr"
    ]
    forwarded_sanitized_headers = re.findall(
        r"proxy_set_header\s+Forwarded\s+(?:\"\"|''|for=\$remote_addr)\s*;",
        text,
        flags=re.IGNORECASE,
    )
    forwarded_unsafe_headers = [
        value.strip()
        for value in re.findall(r"proxy_set_header\s+Forwarded\s+([^;]+);", text, flags=re.IGNORECASE)
        if value.strip() not in {'""', "''", "for=$remote_addr"}
    ]
    missing_or_invalid = []
    if not safe_headers:
        missing_or_invalid.append("X-Forwarded-For")
    if unsafe_headers:
        missing_or_invalid.append("X-Forwarded-For:append")
    if not x_real_ip_safe_headers:
        missing_or_invalid.append("X-Real-IP")
    if x_real_ip_unsafe_headers:
        missing_or_invalid.append("X-Real-IP:unsafe")
    if not forwarded_sanitized_headers:
        missing_or_invalid.append("Forwarded")
    if forwarded_unsafe_headers:
        missing_or_invalid.append("Forwarded:unsafe")
    ok = not missing_or_invalid
    return {
        "ok": ok,
        "path": str(path),
        "safe_header_count": len(safe_headers),
        "unsafe_header_count": len(unsafe_headers),
        "x_real_ip_safe_header_count": len(x_real_ip_safe_headers),
        "x_real_ip_unsafe_header_count": len(x_real_ip_unsafe_headers),
        "forwarded_header_sanitized_count": len(forwarded_sanitized_headers),
        "forwarded_header_unsafe_count": len(forwarded_unsafe_headers),
        "missing_or_invalid": missing_or_invalid,
        "message": "nginx overwrites forwarded client IP headers with remote_addr or clears them"
        if ok
        else "nginx must overwrite X-Forwarded-For/X-Real-IP and clear or overwrite Forwarded",
    }


def check_logrotate_config(config_path: str | Path | None, min_rotate: int = 14) -> dict[str, Any]:
    required_directives = {"missingok", "notifempty", "compress", "copytruncate"}
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "matched_paths": [],
            "rotate": None,
            "missing_directives": sorted(required_directives),
            "message": "logrotate config path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "matched_paths": [],
            "rotate": None,
            "missing_directives": sorted(required_directives),
            "message": "logrotate config file is missing",
        }
    text = strip_logrotate_comments(path.read_text(encoding="utf-8"))
    for match in LOGROTATE_PATTERN.finditer(text):
        raw_paths = match.group("paths").strip()
        body = match.group("body")
        matched_paths = [item for item in raw_paths.split() if item]
        if not covers_haeorum_jsonl_logs(matched_paths):
            continue
        directives = parse_logrotate_directives(body)
        rotate = parse_logrotate_rotate(directives.get("rotate"))
        missing = sorted(name for name in required_directives if name not in directives)
        if rotate is None or rotate < min_rotate:
            missing.append("rotate")
        ok = not missing
        return {
            "ok": ok,
            "path": str(path),
            "matched_paths": matched_paths,
            "rotate": rotate,
            "minimum_rotate": min_rotate,
            "missing_directives": missing,
            "message": "logrotate covers JSONL application logs" if ok else "logrotate config is incomplete",
        }
    return {
        "ok": False,
        "path": str(path),
        "matched_paths": [],
        "rotate": None,
        "minimum_rotate": min_rotate,
        "missing_directives": sorted(required_directives),
        "message": "logrotate config does not cover /var/log/haeorum-ai-search JSONL logs",
    }


def check_systemd_restart_policy(config_path: str | Path | None) -> dict[str, Any]:
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "restart": None,
            "restart_sec": None,
            "user": None,
            "missing_or_invalid": ["path"],
            "message": "systemd service path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "restart": None,
            "restart_sec": None,
            "user": None,
            "missing_or_invalid": ["path"],
            "message": "systemd service file is missing",
        }
    directives = parse_systemd_directives(path.read_text(encoding="utf-8"))
    restart = directives.get("Restart", "").lower()
    restart_sec = parse_systemd_seconds(directives.get("RestartSec"))
    user = directives.get("User")
    exec_start = directives.get("ExecStart", "")
    limit_nofile = parse_systemd_limit(directives.get("LimitNOFILE"))
    missing_or_invalid = []
    if restart not in {"always", "on-failure"}:
        missing_or_invalid.append("Restart")
    if restart_sec is None or restart_sec > 30:
        missing_or_invalid.append("RestartSec")
    if not user or user == "root":
        missing_or_invalid.append("User")
    if "uvicorn" not in exec_start or "app.main:app" not in exec_start:
        missing_or_invalid.append("ExecStart")
    if directives.get("NoNewPrivileges", "").lower() != "true":
        missing_or_invalid.append("NoNewPrivileges")
    if limit_nofile is None or limit_nofile < MIN_SYSTEMD_NOFILE:
        missing_or_invalid.append("LimitNOFILE")
    ok = not missing_or_invalid
    return {
        "ok": ok,
        "path": str(path),
        "restart": directives.get("Restart"),
        "restart_sec": restart_sec,
        "user": user,
        "exec_start": exec_start,
        "limit_nofile": directives.get("LimitNOFILE"),
        "limit_nofile_value": limit_nofile,
        "minimum_limit_nofile": MIN_SYSTEMD_NOFILE,
        "missing_or_invalid": missing_or_invalid,
        "message": "systemd service restarts API safely" if ok else "systemd restart policy is incomplete",
    }


def check_systemd_sync_worker_service(config_path: str | Path | None) -> dict[str, Any]:
    base = read_systemd_unit(config_path, "sync worker systemd service")
    if base.get("ok") is False:
        return base
    directives = base["directives"]
    restart = directives.get("Restart", "").lower()
    restart_sec = parse_systemd_seconds(directives.get("RestartSec"))
    exec_start = directives.get("ExecStart", "")
    missing_or_invalid = common_service_hardening_problems(directives)
    if "-m app.sync_worker" not in exec_start or "--mode sync" not in exec_start:
        missing_or_invalid.append("ExecStart")
    if "--once" in exec_start:
        missing_or_invalid.append("ExecStart:continuous")
    if restart not in {"always", "on-failure"}:
        missing_or_invalid.append("Restart")
    if restart_sec is None or restart_sec > 60:
        missing_or_invalid.append("RestartSec")
    ok = not missing_or_invalid
    return {
        "ok": ok,
        "path": base["path"],
        "restart": directives.get("Restart"),
        "restart_sec": restart_sec,
        "user": directives.get("User"),
        "exec_start": exec_start,
        "limit_nofile": directives.get("LimitNOFILE"),
        "limit_nofile_value": parse_systemd_limit(directives.get("LimitNOFILE")),
        "minimum_limit_nofile": MIN_SYSTEMD_NOFILE,
        "missing_or_invalid": missing_or_invalid,
        "message": "systemd sync worker runs continuous changed-product sync"
        if ok
        else "systemd sync worker service is incomplete",
    }


def check_systemd_reindex_service(config_path: str | Path | None) -> dict[str, Any]:
    base = read_systemd_unit(config_path, "reindex systemd service")
    if base.get("ok") is False:
        return base
    directives = base["directives"]
    exec_start = directives.get("ExecStart", "")
    missing_or_invalid = common_service_hardening_problems(directives)
    if directives.get("Type", "").lower() != "oneshot":
        missing_or_invalid.append("Type")
    if "-m app.sync_worker" not in exec_start or "--mode reindex" not in exec_start or "--once" not in exec_start:
        missing_or_invalid.append("ExecStart")
    ok = not missing_or_invalid
    return {
        "ok": ok,
        "path": base["path"],
        "type": directives.get("Type"),
        "user": directives.get("User"),
        "exec_start": exec_start,
        "limit_nofile": directives.get("LimitNOFILE"),
        "limit_nofile_value": parse_systemd_limit(directives.get("LimitNOFILE")),
        "minimum_limit_nofile": MIN_SYSTEMD_NOFILE,
        "missing_or_invalid": missing_or_invalid,
        "message": "systemd reindex service runs one-shot full reindex"
        if ok
        else "systemd reindex service is incomplete",
    }


def check_systemd_reindex_timer(config_path: str | Path | None, expected_unit: str = "haeorum-ai-reindex.service") -> dict[str, Any]:
    base = read_systemd_unit(config_path, "reindex systemd timer")
    if base.get("ok") is False:
        return base
    directives = base["directives"]
    on_calendar = directives.get("OnCalendar", "")
    unit = directives.get("Unit", "")
    persistent = directives.get("Persistent", "").lower()
    wanted_by = directives.get("WantedBy", "")
    missing_or_invalid = []
    if not is_nightly_three_am_schedule(on_calendar):
        missing_or_invalid.append("OnCalendar")
    if unit != expected_unit:
        missing_or_invalid.append("Unit")
    if persistent != "true":
        missing_or_invalid.append("Persistent")
    if wanted_by != "timers.target":
        missing_or_invalid.append("WantedBy")
    ok = not missing_or_invalid
    return {
        "ok": ok,
        "path": base["path"],
        "on_calendar": on_calendar,
        "unit": unit,
        "persistent": directives.get("Persistent"),
        "wanted_by": wanted_by,
        "missing_or_invalid": missing_or_invalid,
        "message": "systemd timer schedules nightly full reindex"
        if ok
        else "systemd reindex timer is incomplete",
    }


def read_systemd_unit(config_path: str | Path | None, description: str) -> dict[str, Any]:
    if not config_path:
        return {
            "ok": False,
            "path": None,
            "directives": {},
            "missing_or_invalid": ["path"],
            "message": f"{description} path is required",
        }
    path = Path(config_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "directives": {},
            "missing_or_invalid": ["path"],
            "message": f"{description} file is missing",
        }
    return {"ok": True, "path": str(path), "directives": parse_systemd_directives(path.read_text(encoding="utf-8"))}


def common_service_hardening_problems(directives: dict[str, str]) -> list[str]:
    missing_or_invalid = []
    user = directives.get("User")
    exec_start = directives.get("ExecStart", "")
    read_write_paths = directives.get("ReadWritePaths", "")
    if not user or user == "root":
        missing_or_invalid.append("User")
    if not exec_start:
        missing_or_invalid.append("ExecStart")
    if directives.get("NoNewPrivileges", "").lower() != "true":
        missing_or_invalid.append("NoNewPrivileges")
    limit_nofile = parse_systemd_limit(directives.get("LimitNOFILE"))
    if limit_nofile is None or limit_nofile < MIN_SYSTEMD_NOFILE:
        missing_or_invalid.append("LimitNOFILE")
    if "haeorum-ai-search.env" not in directives.get("EnvironmentFile", ""):
        missing_or_invalid.append("EnvironmentFile")
    if not directives.get("WorkingDirectory"):
        missing_or_invalid.append("WorkingDirectory")
    if "/var/log/haeorum-ai-search" not in read_write_paths:
        missing_or_invalid.append("ReadWritePaths")
    return missing_or_invalid


def is_nightly_three_am_schedule(value: str) -> bool:
    text = " ".join(str(value or "").strip().split()).lower()
    return bool(re.search(r"(^|[\s*-])03:00(?::00)?($|\s)", text))


def parse_systemd_directives(text: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        directives[key.strip()] = value.strip()
    return directives


def parse_systemd_seconds(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+)(s|sec|seconds)?", text)
    if not match:
        return None
    return int(match.group(1))


def parse_systemd_limit(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"infinity", "infinite", "unlimited"}:
        return 10**18
    try:
        return int(text)
    except ValueError:
        return None


def strip_nginx_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("#", 1)[0])
    return "\n".join(lines)


def strip_logrotate_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def covers_haeorum_jsonl_logs(paths: list[str]) -> bool:
    normalized = {path.strip() for path in paths}
    if "/var/log/haeorum-ai-search/*.jsonl" in normalized:
        return True
    required = {
        "/var/log/haeorum-ai-search/search.jsonl",
        "/var/log/haeorum-ai-search/error.jsonl",
        "/var/log/haeorum-ai-search/sync.jsonl",
    }
    return required.issubset(normalized)


def parse_logrotate_directives(body: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for line in body.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        directives[parts[0]] = " ".join(parts[1:])
    return directives


def parse_logrotate_rotate(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).split()[0])
    except (IndexError, ValueError):
        return None


def failed_load_report(error: Exception, base_url: str, mssql_ip_restricted: bool, require_production: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_loaded": False,
        "error": str(error),
        "base_url": base_url,
        "mssql_ip_restricted": bool(mssql_ip_restricted),
        "require_production": bool(require_production),
        "failed_checks": ["config_loaded"],
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Security Check",
        "",
        f"- OK: `{report['ok']}`",
        f"- Base URL: `{report.get('base_url')}`",
        f"- Environment: `{report.get('environment')}`",
        f"- Engine: `{report.get('engine_backend')}`",
        f"- Sync interval seconds: `{report.get('sync_interval_seconds')}`",
        f"- Sync alert webhook configured: `{report.get('sync_alert_webhook_configured')}`",
        f"- Sync alert webhook valid: `{report.get('sync_alert_webhook_valid')}`",
        f"- External sync alerting confirmed: `{report.get('external_sync_alerting_confirmed')}`",
        "",
        "| Check | Passed |",
        "| --- | --- |",
    ]
    for name in [
        "https",
        "public_base_url",
        "cors_restricted",
        "cors_origins_https",
        "cors_origins_safe_public",
        "cors_covers_allowed_origins",
        "allowed_origins",
        "allowed_origins_safe_public",
        "product_url_templates_https",
        "product_url_templates_safe_public",
        "admin_key",
        "mall_api_key",
        "mall_api_key_strength",
        "mssql_ip_restricted",
        "production_env",
        "production_search_engine",
        "sync_interval_hourly",
        "sync_alert_webhook_valid",
        "sync_failure_alerting",
        "nginx_client_max_body_size",
        "nginx_upstream_resilience",
        "nginx_forwarded_for_safety",
        "systemd_restart_policy",
        "systemd_sync_worker",
        "systemd_reindex_service",
        "systemd_reindex_timer",
        "logrotate_config",
        "service_env_file_permissions",
    ]:
        lines.append(f"| {name} | `{report.get(name)}` |")
    if report.get("nginx"):
        nginx = report["nginx"]
        lines.extend(
            [
                "",
                f"- Nginx config: `{nginx.get('path')}`",
                f"- Nginx client_max_body_size: `{nginx.get('configured')}`",
                f"- Nginx body size message: `{nginx.get('message')}`",
            ]
        )
    if report.get("nginx_upstream"):
        upstream = report["nginx_upstream"]
        lines.extend(
            [
                "",
                f"- Nginx upstream servers: `{upstream.get('server_count')}`",
                f"- Nginx upstream message: `{upstream.get('message')}`",
            ]
        )
    if report.get("nginx_forwarded_for"):
        forwarded_for = report["nginx_forwarded_for"]
        lines.extend(
            [
                "",
                f"- Nginx X-Forwarded-For safe headers: `{forwarded_for.get('safe_header_count')}`",
                f"- Nginx X-Real-IP safe headers: `{forwarded_for.get('x_real_ip_safe_header_count')}`",
                f"- Nginx Forwarded sanitized headers: `{forwarded_for.get('forwarded_header_sanitized_count')}`",
                f"- Nginx forwarded header problems: `{forwarded_for.get('missing_or_invalid')}`",
                f"- Nginx X-Forwarded-For message: `{forwarded_for.get('message')}`",
            ]
        )
    if report.get("sync_alert_webhook"):
        webhook = report["sync_alert_webhook"]
        lines.extend(
            [
                "",
                f"- Sync alert webhook message: `{webhook.get('message')}`",
                f"- Sync alert webhook host: `{webhook.get('host')}`",
            ]
        )
    if report.get("systemd"):
        systemd = report["systemd"]
        lines.extend(
            [
                "",
                f"- Systemd service: `{systemd.get('path')}`",
                f"- Systemd restart: `{systemd.get('restart')}`",
                f"- Systemd message: `{systemd.get('message')}`",
            ]
        )
    if report.get("sync_systemd"):
        sync_systemd = report["sync_systemd"]
        lines.extend(
            [
                "",
                f"- Sync systemd service: `{sync_systemd.get('path')}`",
                f"- Sync systemd message: `{sync_systemd.get('message')}`",
            ]
        )
    if report.get("reindex_systemd"):
        reindex_systemd = report["reindex_systemd"]
        lines.extend(
            [
                "",
                f"- Reindex systemd service: `{reindex_systemd.get('path')}`",
                f"- Reindex systemd message: `{reindex_systemd.get('message')}`",
            ]
        )
    if report.get("reindex_timer"):
        reindex_timer = report["reindex_timer"]
        lines.extend(
            [
                "",
                f"- Reindex systemd timer: `{reindex_timer.get('path')}`",
                f"- Reindex schedule: `{reindex_timer.get('on_calendar')}`",
                f"- Reindex timer message: `{reindex_timer.get('message')}`",
            ]
        )
    if report.get("logrotate"):
        logrotate = report["logrotate"]
        lines.extend(
            [
                "",
                f"- Logrotate config: `{logrotate.get('path')}`",
                f"- Logrotate matched paths: `{', '.join(logrotate.get('matched_paths') or [])}`",
                f"- Logrotate message: `{logrotate.get('message')}`",
            ]
        )
    if report.get("service_env_file_permissions_report"):
        env_permissions = report["service_env_file_permissions_report"]
        lines.extend(
            [
                "",
                f"- Service env file: `{env_permissions.get('path')}`",
                f"- Service env file mode: `{env_permissions.get('mode')}`",
                f"- Service env file permission message: `{env_permissions.get('message')}`",
            ]
        )
    if report.get("failed_checks"):
        lines.extend(["", f"- Failed checks: `{', '.join(report['failed_checks'])}`"])
    if report.get("error"):
        lines.extend(["", f"- Error: `{report['error']}`"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build security evidence for Haeorum AI Search operational readiness.")
    parser.add_argument("--base-url", required=True, help="Public API base URL, expected to be https:// in production.")
    parser.add_argument(
        "--mssql-ip-restricted",
        action="store_true",
        help="Set only after the DB/firewall rule limits MSSQL access to approved AI search servers.",
    )
    parser.add_argument(
        "--allow-non-production",
        action="store_true",
        help="Do not require HAEORUM_ENV=production. Useful only for local dry-runs.",
    )
    parser.add_argument(
        "--sync-alerting-configured",
        action="store_true",
        help=(
            "Set only after an external monitoring or alerting rule catches sync_last_error, "
            "sync_product_failures, sync_batch_failures, or sync_lock_contention when "
            "HAEORUM_SYNC_ALERT_WEBHOOK_URL is not used."
        ),
    )
    parser.add_argument(
        "--nginx-config",
        default=str(DEFAULT_NGINX_CONFIG_PATH),
        help="Nginx site config path used to verify client_max_body_size covers image upload limits.",
    )
    parser.add_argument(
        "--logrotate-config",
        default=str(DEFAULT_LOGROTATE_CONFIG_PATH),
        help="Logrotate config path used to verify application JSONL logs are rotated.",
    )
    parser.add_argument(
        "--systemd-service",
        default=str(DEFAULT_SYSTEMD_SERVICE_PATH),
        help="Systemd API service unit path used to verify restart and hardening directives.",
    )
    parser.add_argument(
        "--sync-systemd-service",
        default=str(DEFAULT_SYNC_SYSTEMD_SERVICE_PATH),
        help="Systemd sync worker service unit path used to verify hourly changed-product sync deployment.",
    )
    parser.add_argument(
        "--reindex-systemd-service",
        default=str(DEFAULT_REINDEX_SYSTEMD_SERVICE_PATH),
        help="Systemd one-shot reindex service path used by the nightly timer.",
    )
    parser.add_argument(
        "--reindex-systemd-timer",
        default=str(DEFAULT_REINDEX_SYSTEMD_TIMER_PATH),
        help="Systemd timer path used to verify the nightly full reindex schedule.",
    )
    parser.add_argument("--env-file", default="", help="Optional service env file to load before reading app settings.")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()

    require_production = not args.allow_non_production
    try:
        env_values = load_env_file(args.env_file) if args.env_file else {}
        with patched_environ(env_values, clear=bool(args.env_file)):
            report = build_security_report(
                load_settings(),
                base_url=args.base_url,
                mssql_ip_restricted=args.mssql_ip_restricted,
                require_production=require_production,
                sync_alerting_configured=args.sync_alerting_configured,
                nginx_config_path=args.nginx_config,
                logrotate_config_path=args.logrotate_config,
                systemd_service_path=args.systemd_service,
                sync_systemd_service_path=args.sync_systemd_service,
                reindex_systemd_service_path=args.reindex_systemd_service,
                reindex_systemd_timer_path=args.reindex_systemd_timer,
                service_env_file_path=args.env_file or None,
            )
    except Exception as exc:
        report = failed_load_report(exc, args.base_url, args.mssql_ip_restricted, require_production)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
