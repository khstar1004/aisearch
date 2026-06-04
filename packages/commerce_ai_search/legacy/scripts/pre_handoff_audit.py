from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8120"
DEFAULT_DEMO_URL = "http://127.0.0.1:3000/marqo_gemini_exact_demo.html"
DEFAULT_QUERIES = ("텀블러", "개업 답례품 수건", "USB 32GB", "여름 판촉 부채")
REQUIRED_FILES = (
    "compose-haeorum-marqo.yaml",
    "compose-haeorum-gemini.yaml",
    "compose-haeorum-existing-8gb.yaml",
    "deploy/haeorum-ai-search.env.example",
    "deploy/operational-risk-register.md",
    "deploy/go-live-failure-scenarios.md",
    "deploy/production-incident-runbook.md",
    "deploy/production-handoff-checklist.md",
    "deploy/runtime-stack-gemini-marqo.md",
    "deploy/server-db-request.ko.md",
    "deploy/server82-runbook.md",
    "deploy/nginx/haeorum-ai-search.conf",
    "deploy/logrotate/haeorum-ai-search",
    "contracts/operational_evidence.config.example.json",
    "app/gemini_embedding_proxy.py",
    "app/gemini_embeddings.py",
    "admin_dashboard.html",
    "marqo_gemini_exact_demo.html",
    "widget/widget.js",
    "scripts/server_db_intake_check.py",
    "scripts/compose_exposure_check.py",
    "scripts/go_live_scenario_check.py",
)
COMPOSE_FORBIDDEN_TOKENS = (
    "../ImageSearchDemo/qwen",
    "QWEN_MODEL_NAME",
    "HAEORUM_EMBEDDING_BACKEND:-qwen",
)
EXPECTED_HAEORUM_CONTAINERS = (
    "haeorum-ai-search-marqo-ai-search-1",
    "haeorum-ai-search-marqo-gemini-embedding-1",
    "haeorum-ai-search-marqo-marqo-api-1",
    "haeorum-ai-search-marqo-mioc-1",
    "haeorum-ai-search-marqo-vespa-1",
)
EXTERNAL_INPUTS = (
    "server82 SSH host/port/user/sudo policy",
    "production domain, DNS target, and TLS method",
    "MSSQL read-only connection string and View/query",
    "real mall CORS origins and public API keys",
    "production Gemini auth choice: API key or ADC quota project",
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    write_output(Path(args.output), report)
    if args.markdown_output:
        write_output(Path(args.markdown_output), render_markdown(report))
    if args.print_summary:
        print(render_console_summary(report))
    return 0 if report["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the Haeorum Gemini/Marqo handoff state.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--demo-url", default=DEFAULT_DEMO_URL)
    parser.add_argument("--mall-id", default="shop001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--origin", default="http://127.0.0.1:3000")
    parser.add_argument("--admin-key", default="")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--min-products", type=int, default=300)
    parser.add_argument("--max-text-p95-ms", type=float, default=3000.0)
    parser.add_argument("--runtime-timeout", type=float, default=8.0)
    parser.add_argument("--require-runtime", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--output", default="logs/pre-handoff-audit.json")
    parser.add_argument("--markdown-output", default="logs/pre-handoff-audit.md")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    checks = [
        check_required_files(),
        check_operator_visible_docs(),
        check_compose_defaults(),
        check_contract_defaults(),
        check_security_defaults(),
    ]
    if not args.skip_docker:
        checks.append(check_docker_containers())
    checks.extend(check_runtime(args))
    failed = [check["name"] for check in checks if check.get("ok") is not True]
    runtime_checks = [check for check in checks if check.get("category") == "runtime"]
    runtime_missing = [check["name"] for check in runtime_checks if check.get("skipped") is True]
    return {
        "ok": not failed,
        "status": "ready_for_server_db_inputs" if not failed else "needs_fix_before_handoff",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "runtime_required": bool(args.require_runtime),
        "runtime_missing": runtime_missing,
        "remaining_external_inputs": list(EXTERNAL_INPUTS),
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "skipped": len([check for check in checks if check.get("skipped") is True]),
        },
        "failed_checks": failed,
        "checks": checks,
    }


def check_required_files() -> dict[str, Any]:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    return {
        "name": "required_handoff_files",
        "category": "static",
        "ok": not missing,
        "details": {"missing": missing, "required_count": len(REQUIRED_FILES)},
    }


def check_operator_visible_docs() -> dict[str, Any]:
    visible_paths = [
        ROOT / "README.md",
        ROOT / "OPERATIONS.md",
        ROOT / "REQUIREMENTS_TRACE.md",
        ROOT / "deploy" / "operational-risk-register.md",
        ROOT / "deploy" / "go-live-failure-scenarios.md",
        ROOT / "deploy" / "production-incident-runbook.md",
        ROOT / "deploy" / "production-handoff-checklist.md",
        ROOT / "deploy" / "runtime-stack-gemini-marqo.md",
        ROOT / "deploy" / "server82-runbook.md",
    ]
    findings: list[dict[str, Any]] = []
    for path in visible_paths:
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "qwen" in line.lower():
                findings.append({"file": relative(path), "line": line_no})
    return {
        "name": "operator_visible_docs_gemini_only",
        "category": "static",
        "ok": not findings,
        "details": {"legacy_embedding_references": findings},
    }


def check_compose_defaults() -> dict[str, Any]:
    compose_files = [
        ROOT / "compose-haeorum-marqo.yaml",
        ROOT / "compose-haeorum-gemini.yaml",
        ROOT / "compose-haeorum-existing-8gb.yaml",
        ROOT / "compose-haeorum-marqo-gemini-localtest.yaml",
        ROOT / "compose-haeorum-demo.yaml",
    ]
    findings: list[dict[str, str]] = []
    for path in compose_files:
        text = read_text(path)
        for token in COMPOSE_FORBIDDEN_TOKENS:
            if token in text:
                findings.append({"file": relative(path), "token": token})
    base_text = read_text(ROOT / "compose-haeorum-marqo.yaml")
    demo_text = read_text(ROOT / "compose-haeorum-demo.yaml")
    gemini_text = read_text(ROOT / "compose-haeorum-gemini.yaml")
    localtest_text = read_text(ROOT / "compose-haeorum-marqo-gemini-localtest.yaml")
    expected_tokens = (
        "HAEORUM_EMBEDDING_BACKEND: \"${HAEORUM_EMBEDDING_BACKEND:-gemini}\"",
        "GEMINI_EMBEDDING_MODEL",
        "GEMINI_PROXY_API_KEY: \"${GEMINI_PROXY_API_KEY:-}\"",
        "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY: \"${GEMINI_PROXY_API_KEY:-}\"",
        "app.gemini_embedding_proxy:app",
        "logging: *haeorum-logging",
    )
    missing_expected = [token for token in expected_tokens if token not in base_text]
    gemini_override_problems = []
    if "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000" not in base_text:
        gemini_override_problems.append("ai-search host port must bind to 127.0.0.1 by default")
    if "127.0.0.1:${MARQO_PORT:-8882}:8882" not in base_text:
        gemini_override_problems.append("marqo host port must bind to 127.0.0.1 by default")
    if '"${HAEORUM_AI_SEARCH_PORT:-8000}:8000"' in base_text or '"${MARQO_PORT:-8882}:8882"' in base_text:
        gemini_override_problems.append("base compose must not publish AI/Marqo ports on all interfaces")
    if "127.0.0.1:${GEMINI_EMBEDDING_PORT:-8098}:8098" not in gemini_text:
        gemini_override_problems.append("Gemini embedding proxy host port must bind to 127.0.0.1 by default")
    if '"${GEMINI_EMBEDDING_PORT:-8098}:8098"' in gemini_text:
        gemini_override_problems.append("Gemini embedding proxy must not publish on all interfaces")
    if "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000" not in demo_text:
        gemini_override_problems.append("demo compose API host port must bind to 127.0.0.1 by default")
    if '"${HAEORUM_AI_SEARCH_PORT:-8000}:8000"' in demo_text:
        gemini_override_problems.append("demo compose must not publish API port on all interfaces")
    if 'GEMINI_AUTH_MODE: "${GEMINI_AUTH_MODE:-api_key}"' not in gemini_text:
        gemini_override_problems.append("compose-haeorum-gemini.yaml must default to api_key auth")
    quota_project_lines = [
        line.strip()
        for line in gemini_text.splitlines()
        if line.strip().startswith("GEMINI_QUOTA_PROJECT:")
    ]
    allowed_quota_project_lines = {
        'GEMINI_QUOTA_PROJECT: "${GEMINI_QUOTA_PROJECT:-}"',
        "GEMINI_QUOTA_PROJECT: ${GEMINI_QUOTA_PROJECT:-}",
    }
    if any(line not in allowed_quota_project_lines for line in quota_project_lines):
        gemini_override_problems.append("compose-haeorum-gemini.yaml must not hardcode a Google project")
    if "/gcp/application_default_credentials.json" in gemini_text:
        gemini_override_problems.append("generic Gemini compose must not require ADC file mounts")
    if 'GEMINI_AUTH_MODE: "${GEMINI_AUTH_MODE:-api_key}"' not in localtest_text:
        gemini_override_problems.append("local Gemini test compose must default to api_key auth")
    if "GEMINI_API_KEY: \"${GEMINI_API_KEY:?" not in localtest_text:
        gemini_override_problems.append("local Gemini test compose must require GEMINI_API_KEY explicitly")
    if "/gcp/application_default_credentials.json" in localtest_text:
        gemini_override_problems.append("local Gemini test compose must not require ADC file mounts")
    return {
        "name": "gemini_compose_defaults",
        "category": "static",
        "ok": not findings and not missing_expected and not gemini_override_problems,
        "details": {
            "forbidden_tokens": findings,
            "missing_expected_tokens": missing_expected,
            "gemini_override_problems": gemini_override_problems,
        },
    }


def check_contract_defaults() -> dict[str, Any]:
    config_path = ROOT / "contracts" / "operational_evidence.config.example.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "name": "gemini_contract_defaults",
            "category": "static",
            "ok": False,
            "details": {"error": str(exc)},
        }
    marqo = data.get("marqo") if isinstance(data, dict) else {}
    problems = []
    if not isinstance(marqo, dict):
        problems.append("marqo object missing")
    else:
        if marqo.get("embedding_backend") != "gemini":
            problems.append("marqo.embedding_backend must be gemini")
        if "gemini_embedding_url" not in marqo:
            problems.append("marqo.gemini_embedding_url missing")
        if marqo.get("gemini_model") != "gemini-embedding-2":
            problems.append("marqo.gemini_model must be gemini-embedding-2")
        if int(marqo.get("gemini_embedding_dimensions") or 0) != 1536:
            problems.append("marqo.gemini_embedding_dimensions must be 1536")
    return {
        "name": "gemini_contract_defaults",
        "category": "static",
        "ok": not problems,
        "details": {"problems": problems},
    }


def check_security_defaults() -> dict[str, Any]:
    problems: list[str] = []
    root_env_text = read_text(ROOT / ".env.example")
    env_text = read_text(ROOT / "deploy" / "haeorum-ai-search.env.example")
    nginx_text = read_text(ROOT / "deploy" / "nginx" / "haeorum-ai-search.conf")
    intake_text = read_text(ROOT / "deploy" / "server-db-intake.md")

    required_env_tokens = (
        "MARQO_URL=http://marqo-api:8882",
        "HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098",
        "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
        "HAEORUM_ADMIN_API_KEY=replace-with-admin-key",
        "GEMINI_AUTH_MODE=api_key",
        "GEMINI_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
        "GEMINI_API_KEY=replace-with-protected-gemini-api-key",
        "# GEMINI_QUOTA_PROJECT=replace-with-google-cloud-project-id",
        "HAEORUM_TRUSTED_PROXY_IPS=",
        "HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=",
        "HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=",
        "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly",
    )
    for token in required_env_tokens:
        if token not in env_text:
            problems.append(f"env example missing {token}")
    for token in (
        "MARQO_URL=http://marqo-api:8882",
        "HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098",
        "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
        "GEMINI_AUTH_MODE=api_key",
        "GEMINI_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
        "GEMINI_API_KEY=replace-with-protected-gemini-api-key",
    ):
        if token not in root_env_text:
            problems.append(f"root env example missing {token}")
    forbidden_env_tokens = (
        "AIza",
        "AQ.",
        "root-password-placeholder",
        "root /",
    )
    for token in forbidden_env_tokens:
        if token in env_text:
            problems.append(f"env example may contain a secret token: {token}")
    for relative_path in (
        ".env.example",
        "compose-haeorum-gemini.yaml",
        "deploy/haeorum-ai-search.env.example",
        "deploy/production-handoff-checklist.md",
        "deploy/server82-runbook.md",
        "deploy/production-incident-runbook.md",
        "README.md",
        "OPERATIONS.md",
    ):
        text = read_text(ROOT / relative_path)
        for token in ("replace-with-real-google-project-id", "replace-with-real-google-account-number"):
            if token in text:
                problems.append(f"{relative_path} must not hardcode Google project/account id {token}")

    required_nginx_tokens = (
        "proxy_set_header X-Forwarded-For $remote_addr;",
        'proxy_set_header Forwarded "";',
        "client_max_body_size",
        "proxy_pass http://haeorum_ai_search_api",
    )
    for token in required_nginx_tokens:
        if token not in nginx_text:
            problems.append(f"nginx config missing {token}")
    if "X-Forwarded-For $proxy_add_x_forwarded_for" in nginx_text:
        problems.append("nginx config must overwrite, not append, X-Forwarded-For")

    required_intake_tokens = (
        "SSH host",
        "MSSQL",
        "Read-only View",
        "Product deletion/hidden/sold-out rules",
        "Production auth method",
        "Internal Gemini proxy key delivery method",
        "Fallback behavior if AI API is down",
        "rollback test confirmation",
        "server_db_intake_check.py",
        "ready_for_env_and_server_preflight",
    )
    for token in required_intake_tokens:
        if token not in intake_text:
            problems.append(f"server-db intake missing {token}")

    return {
        "name": "security_handoff_defaults",
        "category": "static",
        "ok": not problems,
        "details": {"problems": problems},
    }


def check_docker_containers() -> dict[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        return {
            "name": "docker_haeorum_containers",
            "category": "runtime",
            "ok": True,
            "skipped": True,
            "details": {"reason": "docker not installed"},
        }
    try:
        completed = subprocess.run(
            [docker, "ps", "--format", "{{.Names}}\t{{.Status}}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return {
            "name": "docker_haeorum_containers",
            "category": "runtime",
            "ok": False,
            "details": {"error": str(exc)},
        }
    if completed.returncode != 0:
        return {
            "name": "docker_haeorum_containers",
            "category": "runtime",
            "ok": False,
            "details": {"exit_code": completed.returncode, "stderr": completed.stderr.strip()},
        }
    containers = parse_docker_ps(completed.stdout)
    haeorum = {name: status for name, status in containers.items() if name.startswith("haeorum-ai-search-marqo-")}
    missing = [name for name in EXPECTED_HAEORUM_CONTAINERS if name not in haeorum]
    forbidden_embedding_running = [name for name in haeorum if "qwen" in name.lower()]
    port_bindings = inspect_handoff_port_bindings(docker, [name for name in EXPECTED_HAEORUM_CONTAINERS if name in haeorum])
    port_problems = docker_port_binding_problems(port_bindings)
    return {
        "name": "docker_haeorum_containers",
        "category": "runtime",
        "ok": not missing and not forbidden_embedding_running and not port_problems,
        "details": {
            "expected": list(EXPECTED_HAEORUM_CONTAINERS),
            "running": haeorum,
            "missing": missing,
            "forbidden_embedding_running": forbidden_embedding_running,
            "port_binding_problems": port_problems,
            "port_bindings": port_bindings,
        },
    }


def check_runtime(args: argparse.Namespace) -> list[dict[str, Any]]:
    checks = []
    health = fetch_json(urljoin(ensure_slash(args.base_url), "health"), timeout=args.runtime_timeout)
    if health.get("skipped"):
        checks.append(runtime_skip_or_fail("api_health", args, health))
        return checks
    checks.append(check_health_payload(health, args))
    if args.admin_key:
        checks.append(check_admin_metrics(args))
    else:
        checks.append(runtime_skip_or_fail("admin_metrics", args, {"reason": "--admin-key not provided"}))
    if args.api_key:
        checks.append(check_searches(args))
    else:
        checks.append(runtime_skip_or_fail("public_search_smoke", args, {"reason": "--api-key not provided"}))
    checks.append(check_demo(args))
    return checks


def check_health_payload(result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    data = result.get("data") or {}
    stats = data.get("stats") if isinstance(data, dict) else {}
    problems = []
    if data.get("ok") is not True and data.get("ready") is not True:
        problems.append("health is not ok/ready")
    if str(data.get("embedding_backend") or "").lower() != "gemini":
        problems.append("embedding_backend is not gemini")
    if data.get("gemini_model") != "gemini-embedding-2":
        problems.append("gemini_model is not gemini-embedding-2")
    if int(data.get("gemini_embedding_dimensions") or 0) != 1536:
        problems.append("gemini_embedding_dimensions is not 1536")
    gemini = data.get("gemini") if isinstance(data.get("gemini"), dict) else {}
    if gemini and gemini.get("proxy_auth_configured") is not True:
        problems.append("Gemini proxy auth is not configured")
    doc_count = int((stats or {}).get("numberOfDocuments") or 0)
    if doc_count < int(args.min_products):
        problems.append(f"indexed product count below {args.min_products}")
    qwen_fields = [
        key
        for key in ("qwen", "qwen_ready", "qwen_health_problems")
        if data.get(key) not in (None, [], {})
    ]
    if qwen_fields:
        problems.append(f"legacy embedding runtime fields visible in Gemini health: {', '.join(qwen_fields)}")
    return {
        "name": "api_health",
        "category": "runtime",
        "ok": not problems,
        "details": {
            "base_url": args.base_url,
            "embedding_backend": data.get("embedding_backend"),
            "gemini_model": data.get("gemini_model"),
            "gemini_embedding_dimensions": data.get("gemini_embedding_dimensions"),
            "gemini_proxy_auth_configured": gemini.get("proxy_auth_configured"),
            "numberOfDocuments": doc_count,
            "problems": problems,
        },
    }


def check_admin_metrics(args: argparse.Namespace) -> dict[str, Any]:
    url = urljoin(ensure_slash(args.base_url), "admin/metrics")
    result = fetch_json(url, timeout=args.runtime_timeout, headers={"X-Admin-Key": args.admin_key})
    if result.get("skipped"):
        return runtime_skip_or_fail("admin_metrics", args, result)
    data = result.get("data") or {}
    engine = data.get("engine") if isinstance(data, dict) else {}
    problems = []
    if str((engine or {}).get("embedding_backend") or "").lower() != "gemini":
        problems.append("admin metrics engine.embedding_backend is not gemini")
    if "gemini_query_embedding_cache" not in (engine or {}):
        problems.append("admin metrics missing gemini_query_embedding_cache")
    if "gemini" not in ((engine or {}).get("transport") or {}):
        problems.append("admin metrics missing engine.transport.gemini")
    gemini = (engine or {}).get("gemini") if isinstance((engine or {}).get("gemini"), dict) else {}
    if gemini and gemini.get("proxy_auth_configured") is not True:
        problems.append("admin metrics Gemini proxy auth is not configured")
    prom_result = fetch_text(
        urljoin(ensure_slash(args.base_url), "admin/metrics.prom"),
        timeout=args.runtime_timeout,
        headers={"X-Admin-Key": args.admin_key},
    )
    prom_text = prom_result.get("text") or ""
    if prom_result.get("skipped"):
        problems.append("admin metrics prometheus endpoint unavailable")
    else:
        if "haeorum_gemini_query_vector_" not in prom_text:
            problems.append("Prometheus metrics missing haeorum_gemini_query_vector_*")
        if "haeorum_qwen_query_vector_" in prom_text:
            problems.append("Prometheus metrics still expose qwen query vector names in Gemini mode")
    return {
        "name": "admin_metrics",
        "category": "runtime",
        "ok": not problems,
        "details": {"problems": problems},
    }


def check_searches(args: argparse.Namespace) -> dict[str, Any]:
    queries = tuple(args.query or DEFAULT_QUERIES)
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": args.api_key,
        "Origin": args.origin,
    }
    latencies = []
    failures = []
    samples = []
    for query in queries:
        started = time.perf_counter()
        result = fetch_json(
            urljoin(ensure_slash(args.base_url), "api/ai-search"),
            timeout=max(args.runtime_timeout, 20.0),
            headers=headers,
            payload={"mall_id": args.mall_id, "q": query, "limit": 5},
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        latencies.append(elapsed_ms)
        if result.get("skipped"):
            failures.append({"query": query, "error": result})
            continue
        data = result.get("data") or {}
        meta = data.get("meta") if isinstance(data, dict) else {}
        items = data.get("items") if isinstance(data, dict) else []
        if str((meta or {}).get("engine") or "").lower() != "marqo":
            failures.append({"query": query, "reason": "meta.engine is not marqo"})
        if str((meta or {}).get("embedding_backend") or "").lower() != "gemini":
            failures.append({"query": query, "reason": "meta.embedding_backend is not gemini"})
        if not items:
            failures.append({"query": query, "reason": "empty result"})
        samples.append(
            {
                "query": query,
                "elapsed_ms": elapsed_ms,
                "top_product": (items[0] or {}).get("name") if items else None,
            }
        )
    p95 = percentile(latencies, 0.95)
    if p95 > float(args.max_text_p95_ms):
        failures.append({"reason": f"p95 {p95}ms exceeds {args.max_text_p95_ms}ms"})
    return {
        "name": "public_search_smoke",
        "category": "runtime",
        "ok": not failures,
        "details": {
            "mall_id": args.mall_id,
            "query_count": len(queries),
            "p95_ms": p95,
            "max_text_p95_ms": args.max_text_p95_ms,
            "samples": samples,
            "failures": failures,
        },
    }


def check_demo(args: argparse.Namespace) -> dict[str, Any]:
    result = fetch_text(args.demo_url, timeout=args.runtime_timeout)
    if result.get("skipped"):
        return runtime_skip_or_fail("demo_page", args, result)
    text = result.get("text") or ""
    problems = []
    if "Gemini" not in text and "AI 검색" not in text:
        problems.append("demo does not identify Gemini or the public AI search page")
    if "qwen" in text.lower():
        problems.append("demo still contains qwen text")
    return {
        "name": "demo_page",
        "category": "runtime",
        "ok": not problems,
        "details": {"url": args.demo_url, "problems": problems},
    }


def runtime_skip_or_fail(name: str, args: argparse.Namespace, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "category": "runtime",
        "ok": not args.require_runtime,
        "skipped": not args.require_runtime,
        "details": details,
    }


def fetch_json(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=body, headers=request_headers, method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return {"data": json.loads(response.read().decode("utf-8"))}
    except HTTPError as exc:
        return {"skipped": True, "status": exc.code, "error": safe_error_body(exc)}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"skipped": True, "error": str(exc)}


def fetch_text(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        with urlopen(Request(url, headers=dict(headers or {}), method="GET"), timeout=timeout) as response:
            return {"text": response.read().decode("utf-8", errors="replace")}
    except HTTPError as exc:
        return {"skipped": True, "status": exc.code, "error": safe_error_body(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"skipped": True, "error": str(exc)}


def safe_error_body(exc: HTTPError) -> str:
    try:
        return exc.read(512).decode("utf-8", errors="replace")
    except Exception:
        return str(exc)


def parse_docker_ps(text: str) -> dict[str, str]:
    containers: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        name, status = line.split("\t", 1)
        containers[name.strip()] = status.strip()
    return containers


def inspect_handoff_port_bindings(docker: str, container_names: list[str]) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for name in container_names:
        try:
            completed = subprocess.run(
                [docker, "inspect", name, "--format", "{{json .HostConfig.PortBindings}}"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception as exc:
            bindings[name] = {"__inspect_error__": str(exc)}
            continue
        if completed.returncode != 0:
            bindings[name] = {"__inspect_error__": completed.stderr.strip()}
            continue
        try:
            raw = json.loads(completed.stdout.strip() or "{}")
            bindings[name] = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError as exc:
            bindings[name] = {"__inspect_error__": str(exc)}
    return bindings


def docker_port_binding_problems(port_bindings: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    expected_loopback = {
        "haeorum-ai-search-marqo-ai-search-1": "8000/tcp",
        "haeorum-ai-search-marqo-marqo-api-1": "8882/tcp",
        "haeorum-ai-search-marqo-gemini-embedding-1": "8098/tcp",
    }
    safe_host_ips = {"127.0.0.1", "::1", "localhost"}
    unsafe_host_ips = {"", "0.0.0.0", "::"}
    for container, target_port in expected_loopback.items():
        bindings = port_bindings.get(container) or {}
        if "__inspect_error__" in bindings:
            problems.append(f"{container}: docker inspect failed")
            continue
        entries = bindings.get(target_port) or []
        if not entries:
            problems.append(f"{container}: {target_port} is not published on loopback")
            continue
        for entry in entries:
            host_ip = str((entry or {}).get("HostIp") or "").strip()
            if host_ip in unsafe_host_ips or host_ip not in safe_host_ips:
                problems.append(f"{container}: {target_port} published on unsafe HostIp {host_ip or '<all>'}")
    return problems


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 3)


def ensure_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def write_output(path: Path, data: Any) -> None:
    target = path if path.is_absolute() else ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        target.write_text(data, encoding="utf-8")
    else:
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_console_summary(report: dict[str, Any]) -> str:
    return (
        f"pre-handoff audit: ok={report['ok']} status={report['status']} "
        f"passed={report['summary']['passed']}/{report['summary']['total']} "
        f"failed={report['summary']['failed']}"
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pre-Handoff Audit",
        "",
        f"- Status: `{report['status']}`",
        f"- OK: `{report['ok']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{report['summary']['passed']} / {report['summary']['total']}`",
        f"- Failed: `{report['summary']['failed']}`",
        f"- Skipped: `{report['summary']['skipped']}`",
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        state = "PASS" if check.get("ok") is True else "SKIP" if check.get("skipped") else "FAIL"
        lines.append(f"- {state}: `{check['name']}`")
    lines.extend(["", "## Remaining External Inputs", ""])
    for item in report["remaining_external_inputs"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Failed Details", ""])
    if not report["failed_checks"]:
        lines.append("- None")
    else:
        for check in report["checks"]:
            if check.get("ok") is True:
                continue
            lines.append(f"- `{check['name']}`: `{json.dumps(check.get('details'), ensure_ascii=False)}`")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
