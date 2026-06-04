from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]

SOURCE_NOTES = [
    {
        "name": "Gemini API rate limits",
        "url": "https://ai.google.dev/gemini-api/docs/rate-limits",
        "operational_lesson": "Gemini limits are project-level and include RPM, TPM, and RPD; exceeding any one can return a rate limit error.",
    },
    {
        "name": "OWASP API4:2023 Unrestricted Resource Consumption",
        "url": "https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/",
        "operational_lesson": "Production APIs need explicit limits for timeouts, upload size, records returned, file descriptors, and third-party spending.",
    },
    {
        "name": "Google SRE Handling Overload",
        "url": "https://sre.google/sre-book/handling-overload/",
        "operational_lesson": "Backends and clients should handle overload with fast rejection, bounded retries, degraded paths, and per-customer/request limits.",
    },
    {
        "name": "Google SRE Monitoring Distributed Systems",
        "url": "https://sre.google/sre-book/monitoring-distributed-systems/",
        "operational_lesson": "Human-facing alerts should focus on user-impacting symptoms, while dashboards still expose query, error, latency, and saturation indicators.",
    },
    {
        "name": "Google Cloud Billing budgets",
        "url": "https://cloud.google.com/billing/docs/how-to/budgets",
        "operational_lesson": "Cloud Billing budgets can alert on actual and forecast spend, but billing data is delayed; production cutover should use conservative alert thresholds.",
    },
    {
        "name": "Docker json-file logging driver",
        "url": "https://docs.docker.com/engine/logging/drivers/json-file/",
        "operational_lesson": "Docker json-file logs are file-backed; max-size and max-file log rotation must be configured before containers are recreated.",
    },
    {
        "name": "OWASP API7:2023 Server Side Request Forgery",
        "url": "https://owasp.org/API-Security/editions/2023/en/0xa7-server-side-request-forgery/",
        "operational_lesson": "APIs that fetch user or data-source URLs must validate schemes, hosts, redirects, and private network targets before issuing outbound requests.",
    },
    {
        "name": "OWASP API10:2023 Unsafe Consumption of APIs",
        "url": "https://owasp.org/API-Security/editions/2023/en/0xaa-unsafe-consumption-of-apis/",
        "operational_lesson": "External API and URL responses need timeouts, response size limits, schema validation, and defensive retry behavior.",
    },
]

OPERATOR_SURFACE_FILES = [
    "README.md",
    "OPERATIONS.md",
    "REQUIREMENTS_TRACE.md",
    "admin_dashboard.html",
    "marqo_gemini_exact_demo.html",
    "deploy/runtime-stack-gemini-marqo.md",
    "deploy/production-handoff-checklist.md",
    "deploy/server82-runbook.md",
    "deploy/go-live-failure-scenarios.md",
    "deploy/production-incident-runbook.md",
    "deploy/operational-risk-register.md",
    "deploy/server-db-request.ko.md",
    "deploy/server-db-intake.md",
]

OPERATOR_SURFACE_FORBIDDEN_TOKENS = [
    "../ImageSearchDemo/" + "qw" + "en",
    "HAEORUM_EMBEDDING_BACKEND=" + "qw" + "en",
    "HAEORUM_EMBEDDING_BACKEND:-" + "qw" + "en",
    "QWEN_MODEL",
    "Qw" + "en/",
    "Qw" + "en ",
    "qw" + "en",
    "JSON" + "+NumPy",
    "NumPy " + "검색",
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "abusive_or_accidental_traffic_spike",
        "risk": "검색/이미지 요청 폭주, 악의적 호출, API 비용/CPU 고갈",
        "required_tokens": {
            "app/main.py": [
                "public_api_key_field_names(request.query_params)",
                "validate_multipart_content_length",
                "read_upload_bytes_limited",
                "SearchQueueFull",
                "ImageSearchQueueFull",
            ],
            "app/config.py": [
                "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE",
                "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE",
                "HAEORUM_SEARCH_MAX_CONCURRENCY",
                "HAEORUM_RATE_LIMIT_MAX_BUCKETS",
            ],
            "deploy/haeorum-ai-search.env.example": [
                "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=",
                "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=",
                "HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS=",
            ],
            "scripts/load_test.py": [
                "rate_limited_events",
                "search_queue_full",
                "image_queue_full",
            ],
        },
    },
    {
        "id": "gemini_quota_429_or_cost_runaway",
        "risk": "Gemini embedding quota/RPM/TPM 초과, 429, 외부 API 비용 증가",
        "required_tokens": {
            "app/gemini_embedding_proxy.py": [
                "GEMINI_PROXY_RATE_LIMIT_RPM",
                "GEMINI_PROXY_API_KEY",
                "proxy_auth_configured",
                "verify_proxy_api_key",
                "GEMINI_PROXY_MAX_CONCURRENT_CALLS",
                "rate_limited_total",
                "provider_call_total",
                "load_proxy_status",
                "validate_gemini_auth_settings",
                "max_response_bytes",
            ],
            "app/gemini_embeddings.py": [
                "DEFAULT_GEMINI_MAX_RESPONSE_BYTES",
                "read_gemini_response_limited",
                "Gemini embedding API response exceeds",
            ],
            "scripts/env_check.py": [
                "gemini_proxy_provider_config",
                "GEMINI_AUTH_MODES",
                "proxy_api_key_configured",
                "GEMINI_PROXY_RATE_LIMIT_RPM",
                "GEMINI_MAX_RESPONSE_BYTES",
            ],
            "app/engine.py": [
                "gemini_query_embedding_cache",
                "X-Embedding-Proxy-Key",
                "Retry-After",
                "circuit_breaker",
            ],
            "admin_dashboard.html": [
                "Gemini 사용량",
                "Proxy auth",
                "provider_call_total",
                "Rate limited",
            ],
            "deploy/server-db-intake.md": [
                "Internal Gemini proxy key delivery method",
                "Gemini quota page checked for `gemini-embedding-2`",
                "Budget alert configured",
            ],
        },
    },
    {
        "id": "backend_overload_retry_explosion",
        "risk": "Marqo/Gemini 장애 시 재시도 폭증, threadpool 고갈, tail latency 급증",
        "required_tokens": {
            "app/config.py": [
                "HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS",
                "HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS",
                "HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS",
            ],
            "app/engine.py": [
                "BackendCircuitOpenError",
                "circuit_breaker",
                "connection_acquire_wait_timeouts",
                "circuit_short_circuits",
            ],
            "app/metrics.py": [
                "haeorum_backend_http_circuit_open",
                "haeorum_backend_http_connection_acquire_wait_timeouts",
            ],
            "OPERATIONS.md": [
                "Retry-After",
                "circuit breaker",
                "fail-fast",
            ],
        },
    },
    {
        "id": "disk_or_log_exhaustion",
        "risk": "Docker/app 로그 누적으로 디스크 100%, 서비스 중단",
        "required_tokens": {
            "compose-haeorum-marqo.yaml": [
                "x-haeorum-logging",
                "max-size",
                "max-file",
            ],
            "deploy/logrotate/haeorum-ai-search": [
                "/var/log/haeorum-ai-search/*.jsonl",
                "rotate 14",
                "copytruncate",
            ],
            "app/metrics.py": [
                "disk_usage_high",
                "haeorum_disk_used_percent",
            ],
            "scripts/server_db_intake_check.py": [
                "Docker log rotation values must include max-size and max-file",
            ],
        },
    },
    {
        "id": "internal_port_exposure",
        "risk": "AI API/Marqo/embedding 내부 포트가 인터넷에 직접 노출",
        "required_tokens": {
            "compose-haeorum-marqo.yaml": [
                "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000",
                "127.0.0.1:${MARQO_PORT:-8882}:8882",
            ],
            "compose-haeorum-gemini.yaml": [
                "127.0.0.1:${GEMINI_EMBEDDING_PORT:-8098}:8098",
            ],
            "scripts/compose_exposure_check.py": [
                "protected_ports_loopback_only",
                "embedding_proxy_loopback_only",
            ],
            "deploy/nginx/haeorum-ai-search.conf": [
                "proxy_pass http://haeorum_ai_search_api",
                "proxy_set_header X-Forwarded-For $remote_addr",
            ],
        },
    },
    {
        "id": "db_view_drift_or_stale_index",
        "risk": "MSSQL View 컬럼 변경, 삭제/비노출 상품 미반영, stale Marqo 문서",
        "required_tokens": {
            "scripts/mssql_view_check.py": [
                "validate_readonly_query",
                "updated_at",
                "readonly=True",
            ],
            "scripts/mssql_export_csv.py": [
                "batched_fetch",
                "fetchmany",
                "inactive_products",
            ],
            "app/sync.py": [
                "SyncOperationLock",
                "updated_at_column",
                "source_product_missing",
                "delete_from_index",
            ],
            "deploy/server-db-intake.md": [
                "Product deletion/hidden/sold-out rules",
                "Incremental sync timestamp column",
            ],
        },
    },
    {
        "id": "credential_cors_or_mall_config_misuse",
        "risk": "API key 유출, query/body key 허용, CORS wildcard, 가맹점 설정 오염",
        "required_tokens": {
            "app/main.py": [
                "api_key query parameter is not supported",
                "reject_body_api_key_fields",
                "validate_mall_access",
            ],
            "scripts/env_check.py": [
                "HAEORUM_CORS_ORIGINS",
                "wildcard",
                "placeholder",
                "allowed_origins",
            ],
            "scripts/security_check.py": [
                "cors_restricted",
                "service_env_file_permissions",
                "mall_api_key_strength",
            ],
        },
    },
    {
        "id": "malformed_query_encoding_or_charset_drift",
        "risk": "브라우저/프록시/운영자 테스트 환경의 문자셋 문제로 한글 검색어가 `?? ??` 또는 replacement 문자로 깨져 엉뚱한 상품이 노출됨",
        "required_tokens": {
            "app/models.py": [
                "MALFORMED_QUERY_MESSAGE",
                "looks_like_malformed_query_text",
                "question_marks / len(compact)",
            ],
            "tests/test_search_service.py": [
                "test_search_request_rejects_malformed_query_encoding",
                "malformed or incorrectly encoded",
            ],
            "contracts/openapi.json": [
                "UTF-8",
            ],
            "deploy/go-live-failure-scenarios.md": [
                "malformed_query_encoding_or_charset_drift",
            ],
        },
    },
    {
        "id": "widget_integration_or_rollback_failure",
        "risk": "기존 쇼핑몰 검색창/모바일/CSP와 충돌하거나 장애 시 기존 검색으로 못 돌아감",
        "required_tokens": {
            "scripts/widget_integration_probe.py": [
                "data-hai-auto-init",
                "fallback_floating",
                "external_widget_src_blocked_or_risky",
                "api_connect_src_blocked_or_risky",
            ],
            "deploy/server-db-intake.md": [
                "Fallback behavior if AI API is down",
                "rollback test confirmation",
                "Admin contact for rollback",
            ],
            "widget/widget.js": [
                "fallbackFloating",
                "data-hai-auto-init",
            ],
        },
    },
    {
        "id": "multi_api_scale_state_split",
        "risk": "API 서버 2대 이상에서 캐시/rate limit이 분리되어 품질·방어 기준이 흔들림",
        "required_tokens": {
            "scripts/env_check.py": [
                "redis_required_for_scale",
                "HAEORUM_REDIS_URL",
            ],
            "scripts/load_test.py": [
                "admin_metrics_source_coverage",
                "distinct_instance_count",
                "api_server_count",
            ],
            "scripts/load_compare.py": [
                "admin_metrics_source_coverage",
                "compare_runtime_identity",
                "api_server_count",
            ],
        },
    },
    {
        "id": "observability_alerting_gap",
        "risk": "장애가 발생해도 운영자가 증상, 병목, 알림 상태를 바로 못 봄",
        "required_tokens": {
            "app/metrics.py": [
                "haeorum_backend_http_circuit_open",
                "haeorum_disk_used_percent",
                "haeorum_search_queue_full_events",
                "haeorum_operational_alerts",
            ],
            "app/gemini_embedding_proxy.py": [
                "@app.get(\"/metrics\")",
                "load_proxy_status",
                "max_response_bytes",
            ],
            "admin_dashboard.html": [
                "Gemini 사용량",
                "provider_call_total",
                "Rate limited",
            ],
            "scripts/security_check.py": [
                "sync_failure_alerting",
                "sync_alert_webhook_valid",
            ],
            "deploy/production-incident-runbook.md": [
                "First 10 Minutes",
                "Required Pre-Signoff Alerts",
            ],
        },
    },
    {
        "id": "unsafe_external_url_or_image_source",
        "risk": "상품 이미지/대표 사이트 URL로 SSRF, private IP 접근, redirect loop, 외부 응답 hang 발생",
        "required_tokens": {
            "app/url_safety.py": [
                "validate_http_url_resolves_to_public_network",
                "SafePublicHTTPRedirectHandler",
                "UnsafePublicHttpTargetError",
                "open_public_http_request",
            ],
            "app/gemini_embeddings.py": [
                "safe_absolute_http_url",
                "open_public_http_request",
            ],
            "scripts/image_url_check.py": [
                "--require-https",
                "safe_absolute_http_url",
            ],
            "scripts/representative_site_check.py": [
                "open_public_http_request",
            ],
            "deploy/production-incident-runbook.md": [
                "Unsafe External URL Or Image Source",
            ],
        },
    },
    {
        "id": "deployment_restart_or_rollback_gap",
        "risk": "배포/재시작 실패 후 502/504가 지속되거나 이전 경로로 못 되돌림",
        "required_tokens": {
            "compose-haeorum-marqo.yaml": [
                "healthcheck:",
                "restart: unless-stopped",
            ],
            "deploy/systemd/haeorum-ai-search.service": [
                "Restart=always",
                "NoNewPrivileges=true",
            ],
            "deploy/nginx/haeorum-ai-search.conf": [
                "upstream haeorum_ai_search_api",
                "max_fails=3",
                "fail_timeout=10s",
            ],
            "deploy/production-incident-runbook.md": [
                "Deployment Or Restart Failure",
                "Recovery Exit Criteria",
            ],
        },
    },
    {
        "id": "index_rebuild_or_sync_recovery_gap",
        "risk": "DB View 변경/색인 오염 후 재색인, 삭제 반영, 캐시 무효화 복구 경로가 불명확함",
        "required_tokens": {
            "compose-haeorum-marqo.yaml": [
                "profiles: [\"reindex\"]",
                "\"--mode\", \"reindex\", \"--once\"",
            ],
            "deploy/systemd/haeorum-ai-reindex.service": [
                "--mode reindex --once",
            ],
            "deploy/systemd/haeorum-ai-reindex.timer": [
                "OnCalendar=*-*-* 03:00:00",
                "Persistent=true",
            ],
            "app/sync.py": [
                "SyncOperationLock",
                "delete_from_index",
                "search_cache_cleared",
            ],
            "deploy/production-incident-runbook.md": [
                "DB View Drift Or Stale Index",
            ],
        },
    },
    {
        "id": "cost_budget_notification_gap",
        "risk": "Gemini 사용량이 늘어도 quota/budget 알림이 없어 비용 또는 크레딧 소모를 늦게 발견함",
        "required_tokens": {
            "deploy/server-db-intake.md": [
                "Internal Gemini proxy key delivery method",
                "Gemini quota page checked for `gemini-embedding-2`",
                "Budget alert configured",
            ],
            "admin_dashboard.html": [
                "provider_call_total",
                "Rate limited",
            ],
            "deploy/production-incident-runbook.md": [
                "Cloud Billing budget",
                "Cost: Gemini quota usage",
            ],
        },
    },
]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        path = Path(args.markdown_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    if args.print_summary:
        print(
            f"go-live scenario check: ok={report['ok']} "
            f"passed={report['summary']['passed']}/{report['summary']['total']} "
            f"runtime={report['summary']['runtime_checks']}"
        )
    return 0 if report["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check go-live failure scenario coverage for Haeorum AI Search.")
    parser.add_argument("--base-url", default="", help="Optional running API base URL for runtime checks.")
    parser.add_argument("--admin-key", default="", help="Admin key used when --base-url is set.")
    parser.add_argument(
        "--admin-key-env",
        default="",
        help="Environment variable containing the admin key. Used when --admin-key is empty.",
    )
    parser.add_argument("--mall-id", default="", help="Optional public mall_id for runtime search checks.")
    parser.add_argument("--origin", default="", help="Optional Origin header for runtime public search checks.")
    parser.add_argument("--public-api-key", default="", help="Public mall API key for runtime search checks.")
    parser.add_argument(
        "--public-api-key-env",
        default="",
        help="Environment variable containing the public mall API key. Used when --public-api-key is empty.",
    )
    parser.add_argument("--runtime-timeout", type=float, default=8.0)
    parser.add_argument("--output", default="logs/go-live-scenario-check.json")
    parser.add_argument("--markdown-output", default="logs/go-live-scenario-check.md")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    scenario_checks = [check_scenario(scenario) for scenario in SCENARIOS]
    operator_surface_checks = [check_operator_surface()]
    runtime_checks = build_runtime_checks(args)
    checks = scenario_checks + operator_surface_checks + runtime_checks
    failed = [check["id"] for check in checks if check.get("ok") is not True]
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "source_notes": SOURCE_NOTES,
        "failed_checks": failed,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "scenario_checks": len(scenario_checks),
            "operator_surface_checks": len(operator_surface_checks),
            "runtime_checks": len(runtime_checks),
        },
        "checks": checks,
    }


def check_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    for relative, tokens in scenario["required_tokens"].items():
        text = read_text(ROOT / relative)
        if not text:
            missing.append({"file": relative, "token": "<file missing or unreadable>"})
            continue
        for token in tokens:
            if token not in text:
                missing.append({"file": relative, "token": token})
    return {
        "id": scenario["id"],
        "category": "scenario",
        "ok": not missing,
        "risk": scenario["risk"],
        "missing": missing,
        "evidence_files": sorted(scenario["required_tokens"]),
    }


def check_operator_surface() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for relative in OPERATOR_SURFACE_FILES:
        text = read_text(ROOT / relative)
        if not text:
            findings.append({"file": relative, "token": "<file missing or unreadable>", "line": None})
            continue
        lowered = text.lower()
        for token in OPERATOR_SURFACE_FORBIDDEN_TOKENS:
            if token.lower() not in lowered:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if token.lower() in line.lower():
                    findings.append({"file": relative, "token": token, "line": line_no})
    return {
        "id": "operator_surface_gemini_only",
        "category": "operator_surface",
        "ok": not findings,
        "risk": "운영자에게 레거시 provider/임시 벡터 데모 표현이 다시 노출되어 실제 Gemini+Marqo 반입 경로가 헷갈림",
        "forbidden_token_count": len(OPERATOR_SURFACE_FORBIDDEN_TOKENS),
        "scanned_files": OPERATOR_SURFACE_FILES,
        "findings": findings,
    }


def build_runtime_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    if not args.base_url:
        return []
    checks: list[dict[str, Any]] = []
    admin_key = args.admin_key or (os.environ.get(args.admin_key_env) if args.admin_key_env else "")
    health = fetch_json(urljoin(ensure_slash(args.base_url), "health"), timeout=args.runtime_timeout)
    health_data = health.get("data") if isinstance(health.get("data"), dict) else {}
    health_gemini = health_data.get("gemini") if isinstance(health_data.get("gemini"), dict) else {}
    checks.append(
        {
            "id": "runtime_health_gemini_marqo",
            "category": "runtime",
            "ok": health_data.get("ready") is True
            and str(health_data.get("engine") or "").lower() == "marqo"
            and str(health_data.get("embedding_backend") or "").lower() == "gemini"
            and health_data.get("gemini_ready") is True
            and health_gemini.get("proxy_auth_configured") is True,
            "details": {
                "error": health.get("error"),
                "engine": health_data.get("engine"),
                "embedding_backend": health_data.get("embedding_backend"),
                "gemini_ready": health_data.get("gemini_ready"),
                "gemini_proxy_auth_configured": health_gemini.get("proxy_auth_configured"),
                "numberOfDocuments": ((health_data.get("stats") or {}) if isinstance(health_data, dict) else {}).get(
                    "numberOfDocuments"
                ),
            },
        }
    )
    if not admin_key:
        checks.append(
            {
                "id": "runtime_admin_metrics",
                "category": "runtime",
                "ok": False,
                "details": {"error": "--admin-key or --admin-key-env is required with --base-url"},
            }
        )
        return checks
    metrics = fetch_json(
        urljoin(ensure_slash(args.base_url), "admin/metrics"),
        timeout=args.runtime_timeout,
        headers={"X-Admin-Key": admin_key},
    )
    metrics_data = metrics.get("data") if isinstance(metrics.get("data"), dict) else {}
    engine = metrics_data.get("engine") if isinstance(metrics_data.get("engine"), dict) else {}
    transport = engine.get("transport") if isinstance(engine.get("transport"), dict) else {}
    gemini = engine.get("gemini") if isinstance(engine.get("gemini"), dict) else {}
    alerts = metrics_data.get("alerts") if isinstance(metrics_data.get("alerts"), list) else []
    critical_alerts = [
        alert
        for alert in alerts
        if str((alert or {}).get("level") or "").lower() in {"critical", "error", "fatal"}
    ]
    checks.extend(
        [
            {
                "id": "runtime_backend_observability",
                "category": "runtime",
                "ok": "marqo" in transport
                and "gemini" in transport
                and "gemini_query_embedding_cache" in engine
                and gemini.get("proxy_auth_configured") is True,
                "details": {
                    "transport_services": sorted(transport),
                    "gemini_query_embedding_cache": "gemini_query_embedding_cache" in engine,
                    "gemini_proxy_auth_configured": gemini.get("proxy_auth_configured"),
                    "error": metrics.get("error"),
                },
            },
            {
                "id": "runtime_abuse_and_queue_observability",
                "category": "runtime",
                "ok": all(key in metrics_data for key in ["rate_limit", "search_queue", "image_queue", "cache"]),
                "details": {
                    "has_rate_limit": "rate_limit" in metrics_data,
                    "has_search_queue": "search_queue" in metrics_data,
                    "has_image_queue": "image_queue" in metrics_data,
                    "has_cache": "cache" in metrics_data,
                },
            },
            {
                "id": "runtime_critical_alerts_absent",
                "category": "runtime",
                "ok": not critical_alerts,
                "details": {
                    "critical_alerts": critical_alerts,
                    "warning_count": len(alerts) - len(critical_alerts),
                },
            },
        ]
    )
    public_api_key = args.public_api_key or (os.environ.get(args.public_api_key_env) if args.public_api_key_env else "")
    if args.mall_id and public_api_key:
        checks.extend(build_public_search_runtime_checks(args, public_api_key))
    return checks


def build_public_search_runtime_checks(args: argparse.Namespace, public_api_key: str) -> list[dict[str, Any]]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-API-Key": public_api_key,
    }
    if args.origin:
        headers["Origin"] = args.origin
    base_url = ensure_slash(args.base_url)
    good_query = post_json(
        urljoin(base_url, "api/ai-search"),
        {"mall_id": args.mall_id, "q": "검은 우산", "limit": 1},
        timeout=args.runtime_timeout,
        headers=headers,
    )
    good_data = good_query.get("data") if isinstance(good_query.get("data"), dict) else {}
    good_meta = good_data.get("meta") if isinstance(good_data.get("meta"), dict) else {}
    malformed_query = post_json(
        urljoin(base_url, "api/ai-search"),
        {"mall_id": args.mall_id, "q": "?? ??", "limit": 1},
        timeout=args.runtime_timeout,
        headers=headers,
    )
    malformed_detail = json.dumps(
        malformed_query.get("data") or malformed_query.get("error") or "",
        ensure_ascii=False,
    ).lower()
    return [
        {
            "id": "runtime_utf8_korean_query_accepted",
            "category": "runtime",
            "ok": good_query.get("status") == 200
            and good_meta.get("query_type") == "text"
            and str(good_meta.get("engine") or "").lower() == "marqo"
            and str(good_meta.get("embedding_backend") or "").lower() == "gemini",
            "details": {
                "status": good_query.get("status"),
                "error": good_query.get("error"),
                "query_type": good_meta.get("query_type"),
                "engine": good_meta.get("engine"),
                "embedding_backend": good_meta.get("embedding_backend"),
            },
        },
        {
            "id": "runtime_malformed_query_rejected",
            "category": "runtime",
            "ok": malformed_query.get("status") in {400, 422}
            and "malformed" in malformed_detail
            and "utf-8" in malformed_detail,
            "details": {
                "status": malformed_query.get("status"),
                "error": malformed_query.get("error"),
                "detail": malformed_query.get("data"),
            },
        },
    ]


def fetch_json(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        with urlopen(Request(url, headers=dict(headers or {}), method="GET"), timeout=timeout) as response:
            return {"status": response.status, "data": json.loads(response.read().decode("utf-8"))}
    except HTTPError as exc:
        return {"error": safe_error_body(exc), "status": exc.code}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}


def post_json(url: str, payload: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json; charset=utf-8", **dict(headers or {})}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(Request(url, data=body, headers=request_headers, method="POST"), timeout=timeout) as response:
            return {"status": response.status, "data": json.loads(response.read().decode("utf-8"))}
    except HTTPError as exc:
        text = safe_error_body(exc)
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        return {"error": text, "status": exc.code, "data": parsed}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}


def safe_error_body(exc: HTTPError) -> str:
    try:
        return exc.read(512).decode("utf-8", errors="replace")
    except Exception:
        return str(exc)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def ensure_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Go-Live Failure Scenario Check",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Passed: `{(report.get('summary') or {}).get('passed')} / {(report.get('summary') or {}).get('total')}`",
        f"- Failed: `{(report.get('summary') or {}).get('failed')}`",
        "",
        "## Scenarios",
        "",
    ]
    for check in report.get("checks", []):
        status = "PASS" if check.get("ok") is True else "FAIL"
        risk = check.get("risk") or check.get("id")
        lines.append(f"- {status}: `{check.get('id')}` - {risk}")
        missing = check.get("missing") or []
        for item in missing:
            lines.append(f"  - Missing `{item.get('token')}` in `{item.get('file')}`")
    lines.extend(["", "## Source Notes", ""])
    for note in report.get("source_notes", []):
        lines.append(f"- `{note.get('name')}`: {note.get('operational_lesson')} ({note.get('url')})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
