from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.url_safety import is_non_public_host
from scripts.local_acceptance import build_source_fingerprint
from scripts.prepare_operational_bundle import TEMPLATE_FILES, build_bundle


PROHIBITED_OPERATIONAL_TOKENS = (
    "public-shop001-dev-key",
    "public-shop002-dev-key",
    "dev-admin-key",
)
HANDOFF_SECRET_SCAN_FILES = (
    "requirements-blockers.md",
    "missing-evidence.sh",
    "missing-evidence.ps1",
    "server-db-intake-check.json",
    "server-db-intake-check.md",
    "local-acceptance.json",
    "local-acceptance.md",
    "requirements-audit.json",
    "requirements-audit.md",
    "operational-readiness.json",
    "operational-readiness.md",
    "evidence-collection-plan.json",
    "evidence-collection-plan.md",
)
SECRET_ASSIGNMENT_EXPOSURE_PATTERN = re.compile(
    r"\b(?P<key>password|passwd|pwd|api[_-]?key|admin[_-]?key|x[_-]?api[_-]?key|"
    r"x[_-]?admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|mssql[_-]?connection[_-]?string|connection[_-]?string|secret|token)"
    r"\s*=\s*(?P<value>[^;\s,&]+)",
    re.IGNORECASE,
)
QUOTED_SECRET_FIELD_EXPOSURE_PATTERN = re.compile(
    r"[\"'](?P<key>password|passwd|pwd|api[_-]?key|admin[_-]?key|x[_-]?api[_-]?key|"
    r"x[_-]?admin[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|mssql[_-]?connection[_-]?string|connection[_-]?string|secret|token)"
    r"[\"']\s*:\s*[\"'](?P<value>[^\"']+)[\"']",
    re.IGNORECASE,
)
CLI_SECRET_OPTION_EXPOSURE_PATTERN = re.compile(
    r"(?P<option>--(?:api-key|admin-key|connection-string))\s+(?P<value>\"[^\"]+\"|'[^']+'|[^\s]+)",
    re.IGNORECASE,
)
URL_CREDENTIAL_EXPOSURE_PATTERN = re.compile(r"\bhttps?://[^/@\s]+@", re.IGNORECASE)
DEPLOYMENT_PROJECT_ROOT = "/opt/haeorum-ai-search"
DEPLOYMENT_EVIDENCE_DIR = "/var/log/haeorum-ai-search"
UNRESOLVED_OPERATIONAL_PLACEHOLDERS = (
    "<quality_report.md>",
    "<csv_index.md>",
    "<env_check.md>",
    "<load_text.md>",
    "<load_image.md>",
    "<load_mixed.md>",
    "<load_mixed_traffic.md>",
    "<api_scale.md>",
    "<representative_sites.md>",
    "<security.md>",
)
PROHIBITED_LOCAL_PATH_PATTERNS = {
    "windows_drive_path": re.compile(r"\b[A-Za-z]:[\\/]+"),
    "local_example_project_path": re.compile(r"examples[\\/]+HaeorumAISearch"),
}
PROHIBITED_LEGACY_PROVIDER_PATTERNS = {
    "legacy_qwen_provider": re.compile(r"\bqwen\b", re.IGNORECASE),
    "legacy_json_numpy_demo": re.compile(r"JSON\+NumPy|NumPy 검색", re.IGNORECASE),
}
REQUIRED_LOCAL_ACCEPTANCE_CHECKS = (
    "contract_check",
    "unit_tests",
    "acceptance_check",
    "quality_report",
    "csv_index_dry_run",
    "operational_bundle_check",
    "widget_dom_check",
    "widget_js_syntax",
    "widget_dom_check_syntax",
)
REQUIRED_ACCEPTANCE_INNER_CHECKS = (
    "check_contract_fixtures",
    "check_fastapi_runtime_routes",
    "check_fastapi_admin_runtime_routes",
    "check_text_search",
    "check_text_typo_and_synonym_quality",
    "check_image_search",
    "check_image_upload_validation_and_preprocessing",
    "check_product_image_probe_and_sync_image_validation",
    "check_mixed_search",
    "check_multipart_search_contract",
    "check_search_execution_queue_metrics",
    "check_image_queue_metrics",
    "check_rate_limit_controls_and_metrics",
    "check_search_cache_and_sync_invalidation",
    "check_marqo_engine_adapter_and_reserved_backends",
    "check_low_confidence_notice",
    "check_no_result_notice",
    "check_top3_categories_and_items",
    "check_related_items_pagination",
    "check_active_filter_and_product_url",
    "check_product_click_log_and_url_safety",
    "check_domain_filters_price_quantity_delivery",
    "check_public_mall_config",
    "check_production_env_fail_closed_and_access_policy",
    "check_api_smoke_security_and_admin_contracts",
    "check_security_and_server_preflight_contracts",
    "check_mall_price_and_visibility_policy",
    "check_scaled_mall_config_1700",
    "check_representative_site_checker_contract",
    "check_mssql_readonly_view_and_incremental_contract",
    "check_sync_status_and_logs",
    "check_sync_worker_schedule_and_alerting_contract",
    "check_search_insights_quality_loop",
    "check_load_and_scale_evidence_contracts",
    "check_requirements_audit_completion_gate_contract",
    "check_local_performance_smoke",
)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    bundle_dir_arg = str(getattr(args, "bundle_dir", "") or "").strip()
    if bundle_dir_arg:
        bundle_dir = Path(bundle_dir_arg)
        generation_report = None
        checks = validate_bundle(bundle_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="haeorum-operational-bundle-") as temp_dir:
            bundle_dir = Path(temp_dir)
            generation_report = build_bundle(bundle_dir, force=True)
            checks = validate_bundle(bundle_dir)

    if generation_report is not None:
        checks.insert(
            0,
            {
                "name": "bundle_generation",
                "ok": generation_report.get("ok") is True
                and len(generation_report.get("copied") or []) >= len(TEMPLATE_FILES),
                "details": {
                    "copied_count": len(generation_report.get("copied") or []),
                    "skipped_count": len(generation_report.get("skipped") or []),
                },
            },
        )

    failed = [check["name"] for check in checks if check.get("ok") is not True]
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "bundle_dir": str(bundle_dir_arg or "<temporary>"),
        "failed_checks": failed,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
        "checks": checks,
    }


def validate_bundle(bundle_dir: Path) -> list[dict[str, Any]]:
    checks = [
        check_required_files(bundle_dir),
        check_checklist_install_commands(bundle_dir),
        check_checklist_required_edits(bundle_dir),
        check_mall_template(bundle_dir),
        check_secret_placeholders(bundle_dir),
        check_representative_sites_template(bundle_dir),
        check_env_config_alignment(bundle_dir),
        check_operational_url_config(bundle_dir),
        check_no_local_demo_keys(bundle_dir),
        check_evidence_commands(bundle_dir),
        check_input_preparation_config(bundle_dir),
        check_marqo_quality_config(bundle_dir),
        check_quality_cases_template(bundle_dir),
        check_deployment_reference_files(bundle_dir),
        check_intake_runtime_handoff_docs(bundle_dir),
        check_blocker_checklist_paths(bundle_dir),
        check_blocker_resolution_commands(bundle_dir),
        check_missing_evidence_script_paths(bundle_dir),
        check_local_acceptance_report(bundle_dir),
        check_optional_handoff_reports(bundle_dir),
        check_handoff_secret_exposure(bundle_dir),
    ]
    return checks


def check_required_files(bundle_dir: Path) -> dict[str, Any]:
    required = [Path(item["target"]).as_posix() for item in TEMPLATE_FILES]
    required.append("CHECKLIST.md")
    missing = [path for path in required if not (bundle_dir / path).exists()]
    return {
        "name": "required_files",
        "ok": not missing,
        "details": {
            "required_count": len(required),
            "missing": missing,
        },
    }


def check_checklist_install_commands(bundle_dir: Path) -> dict[str, Any]:
    checklist = read_text(bundle_dir / "CHECKLIST.md")
    required_tokens = [
        "sudo install -m 0600 operational-evidence.env /etc/haeorum-ai-search/operational-evidence.env",
        "sudo install -m 0640 operational-evidence.config.json /etc/haeorum-ai-search/operational-evidence.config.json",
        "sudo install -m 0640 haeorum-ai-search.env /etc/haeorum-ai-search/haeorum-ai-search.env",
        "sudo install -m 0640 malls.json /etc/haeorum-ai-search/malls.json",
        "sudo install -m 0640 quality-cases.json /etc/haeorum-ai-search/quality-cases.json",
        "sudo install -m 0640 query-synonyms.json /etc/haeorum-ai-search/query-synonyms.json",
    ]
    missing = [token for token in required_tokens if token not in checklist]
    return {
        "name": "install_commands",
        "ok": not missing,
        "details": {"missing": missing},
    }


def check_checklist_required_edits(bundle_dir: Path) -> dict[str, Any]:
    checklist = read_text(bundle_dir / "CHECKLIST.md")
    required_tokens = [
        "replace-with",
        "sample",
        "dummy",
        "dev-key",
        "1,700 enabled production malls",
        "quality-cases.json",
        "real PoC text, image, and mixed-search cases",
        "real reference image files",
        "representative-sites.config.json",
        "products-full.csv",
        "poc-products.csv",
        "input_preparation.enabled=true",
        "mssql-export.json",
        "poc-dataset.json",
        "0600",
        "0640",
        "api_key_env",
        "admin_key_env",
        "mssql_connection_string_env",
        "base_url",
        "origin",
        "marqo.url",
        "absolute HTTPS non-local API URLs",
        "without credentials",
        "query strings",
        "fragments",
        "quality.min_products>=300",
        "MVP targets",
        "server-db-intake.md",
        "server_db_intake_check.py",
        "ready_for_env_and_server_preflight",
        "Marqo + Gemini embedding API",
    ]
    missing = [token for token in required_tokens if token not in checklist]
    return {
        "name": "required_edits",
        "ok": not missing,
        "details": {"missing": missing},
    }


def check_mall_template(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "malls.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "mall_template", "ok": False, "details": {"error": str(exc)}}
    malls = data.get("malls") if isinstance(data, dict) else data
    if not isinstance(malls, list):
        return {"name": "mall_template", "ok": False, "details": {"error": "malls must be a list"}}

    enabled = [mall for mall in malls if isinstance(mall, dict) and mall.get("enabled", True) is not False]
    dev_key_malls = [
        str(mall.get("mall_id") or "")
        for mall in enabled
        if contains_local_demo_token(str(mall.get("api_key") or ""))
    ]
    missing_fields = [
        str(mall.get("mall_id") or "")
        for mall in enabled
        if not mall.get("api_key") or not mall.get("allowed_origins") or not mall.get("product_url_template")
    ]
    return {
        "name": "mall_template",
        "ok": bool(enabled) and not dev_key_malls and not missing_fields,
        "details": {
            "mall_count": len(malls),
            "enabled_count": len(enabled),
            "dev_key_malls": dev_key_malls,
            "missing_required_fields": missing_fields,
        },
    }


def check_secret_placeholders(bundle_dir: Path) -> dict[str, Any]:
    env_text = read_text(bundle_dir / "operational-evidence.env")
    representative_text = read_text(bundle_dir / "representative-sites.config.json")
    required_tokens = [
        "HAEORUM_PUBLIC_API_KEY=replace-with-public-api-key",
        "HAEORUM_ADMIN_API_KEY=replace-with-admin-key",
        "HAEORUM_MSSQL_READONLY_CONNECTION_STRING=",
        "GEMINI_PROXY_API_KEY=replace-with-internal-gemini-proxy-key",
        "replace-with-shop001-public-key",
        "replace-with-shop002-public-key",
    ]
    combined = env_text + "\n" + representative_text
    missing = [token for token in required_tokens if token not in combined]
    return {
        "name": "secret_placeholders",
        "ok": not missing,
        "details": {"missing": missing},
    }


def check_representative_sites_template(bundle_dir: Path) -> dict[str, Any]:
    config_path = bundle_dir / "operational-evidence.config.json"
    sites_path = bundle_dir / "representative-sites.config.json"
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        site_data = json.loads(sites_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "representative_sites_template", "ok": False, "details": {"error": str(exc)}}

    required_sites = 3
    if isinstance(config_data, dict):
        try:
            required_sites = max(1, int(config_data.get("required_sites", required_sites)))
        except (TypeError, ValueError):
            required_sites = 3
    sites = site_data.get("sites") if isinstance(site_data, dict) else site_data
    if not isinstance(sites, list):
        return {
            "name": "representative_sites_template",
            "ok": False,
            "details": {"error": "representative-sites.config.json must be a list or an object with a sites list"},
        }

    problems = []
    identifiers = []
    site_id_alias_count = 0
    required_markers = ["HaeorumAISearch.init", "haeorum-ai-search"]
    for index, site in enumerate(sites, start=1):
        if not isinstance(site, dict):
            problems.append({"site": index, "field": "site", "message": "site entry must be an object"})
            continue
        name = str(site.get("name") or "").strip()
        mall_id = str(site.get("mall_id") or "").strip()
        site_id = str(site.get("site_id") or "").strip()
        identifier = mall_id or site_id
        if site_id:
            site_id_alias_count += 1
        if not name or quality_case_has_placeholder(name):
            problems.append({"site": index, "field": "name", "message": "site name is required and must not be a placeholder"})
        if not identifier or quality_case_has_placeholder(identifier):
            problems.append({"site": name or index, "field": "mall_id", "message": "mall_id or site_id is required and must not be a placeholder"})
        else:
            identifiers.append(identifier)

        for field, origin_only in (("url", False), ("origin", True), ("api_base_url", False)):
            if not is_safe_bundle_http_url(
                str(site.get(field) or ""),
                origin_only=origin_only,
                require_https=True,
                allow_local=False,
            ):
                problems.append(
                    {
                        "site": name or index,
                        "field": field,
                        "message": f"{field} must be an absolute HTTPS non-local URL without credentials, placeholders, query strings, or fragments",
                    }
                )

        api_key = str(site.get("api_key") or "").strip()
        if not api_key or "replace-with" not in api_key.lower() or contains_local_demo_token(api_key):
            problems.append(
                {
                    "site": name or index,
                    "field": "api_key",
                    "message": "bundle template must carry a replace-with... public API key placeholder and no local demo key",
                }
            )

        prefixes = site.get("expected_product_url_prefix")
        prefix_values = prefixes if isinstance(prefixes, list) else [prefixes]
        prefix_values = [str(value or "").strip() for value in prefix_values if str(value or "").strip()]
        if not prefix_values:
            problems.append(
                {
                    "site": name or index,
                    "field": "expected_product_url_prefix",
                    "message": "expected_product_url_prefix is required for product URL rule evidence",
                }
            )
        for prefix in prefix_values:
            if not is_safe_bundle_http_url(prefix, require_https=True, allow_local=False):
                problems.append(
                    {
                        "site": name or index,
                        "field": "expected_product_url_prefix",
                        "message": "expected_product_url_prefix must be an absolute HTTPS non-local URL without credentials, query strings, or fragments",
                    }
                )

        markers = site.get("required_markers")
        if not isinstance(markers, list):
            markers = []
        marker_text = {str(marker).strip() for marker in markers}
        missing_markers = [marker for marker in required_markers if marker not in marker_text]
        if missing_markers:
            problems.append(
                {
                    "site": name or index,
                    "field": "required_markers",
                    "message": "required widget markers are missing",
                    "missing": missing_markers,
                }
            )
        probe_sources = site.get("widget_probe_sources")
        if not isinstance(probe_sources, list) or len(probe_sources) < 2:
            problems.append(
                {
                    "site": name or index,
                    "field": "widget_probe_sources",
                    "message": "PC and mobile saved HTML probe sources are required in the representative template",
                }
            )
        else:
            variants = {
                str(source.get("variant") or "").strip().lower()
                for source in probe_sources
                if isinstance(source, dict)
            }
            source_paths = [
                str(source.get("source") or "").strip()
                for source in probe_sources
                if isinstance(source, dict)
            ]
            if not {"pc", "mobile"}.issubset(variants):
                problems.append(
                    {
                        "site": name or index,
                        "field": "widget_probe_sources",
                        "message": "widget_probe_sources must include pc and mobile variants",
                    }
                )
            if any(not path or quality_case_has_placeholder(path) or not path.lower().endswith(".html") for path in source_paths):
                problems.append(
                    {
                        "site": name or index,
                        "field": "widget_probe_sources",
                        "message": "widget_probe_sources must point to saved HTML file paths",
                    }
                )

    duplicate_identifiers = sorted(identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1)
    for identifier in duplicate_identifiers:
        problems.append({"field": "mall_id", "message": "duplicate representative site identifier", "identifier": identifier})
    if len(sites) < required_sites:
        problems.append(
            {
                "field": "sites",
                "message": f"at least {required_sites} representative site template entries are required",
                "actual": len(sites),
            }
        )
    if site_id_alias_count < 1:
        problems.append(
            {
                "field": "site_id",
                "message": "at least one representative template entry must exercise the site_id alias path",
                "actual": site_id_alias_count,
            }
        )

    return {
        "name": "representative_sites_template",
        "ok": not problems,
        "details": {
            "site_count": len(sites),
            "required_sites": required_sites,
            "site_id_alias_count": site_id_alias_count,
            "identifiers": identifiers,
            "duplicate_identifiers": duplicate_identifiers,
            "problems": problems,
        },
    }


def check_env_config_alignment(bundle_dir: Path) -> dict[str, Any]:
    config_path = bundle_dir / "operational-evidence.config.json"
    env_path = bundle_dir / "operational-evidence.env"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "env_config_alignment", "ok": False, "details": {"error": str(exc)}}
    if not isinstance(config, dict):
        return {"name": "env_config_alignment", "ok": False, "details": {"error": "config must be an object"}}

    env_entries = parse_env_template(read_text(env_path))
    required_fields = (
        "api_key_env",
        "admin_key_env",
        "mssql_connection_string_env",
    )
    problems = []
    required_env_vars = {}
    for field in required_fields:
        env_name = str(config.get(field) or "").strip()
        required_env_vars[field] = env_name
        if not env_name:
            problems.append({"field": field, "message": "config field must name an env var"})
            continue
        if env_name not in env_entries:
            problems.append({"field": field, "env_var": env_name, "message": "env var is missing from operational-evidence.env"})
        elif not env_entries[env_name].strip():
            problems.append({"field": field, "env_var": env_name, "message": "env var value must not be empty"})

    present_required_env_vars = [
        env_name
        for env_name in required_env_vars.values()
        if env_name and env_name in env_entries
    ]
    missing_env_vars = [
        env_name
        for env_name in required_env_vars.values()
        if env_name and env_name not in env_entries
    ]
    return {
        "name": "env_config_alignment",
        "ok": not problems,
        "details": {
            "required_fields": list(required_fields),
            "required_env_vars": required_env_vars,
            "present_required_env_vars": present_required_env_vars,
            "missing_env_vars": missing_env_vars,
            "env_value_kinds": {
                env_name: classify_env_value(env_entries[env_name])
                for env_name in present_required_env_vars
            },
            "problems": problems,
        },
    }


def check_operational_url_config(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "operational-evidence.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "operational_url_config", "ok": False, "details": {"error": str(exc)}}
    if not isinstance(data, dict):
        return {"name": "operational_url_config", "ok": False, "details": {"error": "config must be an object"}}

    marqo = data.get("marqo") if isinstance(data.get("marqo"), dict) else {}
    url_fields = {
        "base_url": str(data.get("base_url") or "").strip(),
        "origin": str(data.get("origin") or "").strip(),
        "marqo.url": str(marqo.get("url") or "").strip(),
        "marqo.gemini_embedding_url": str(marqo.get("gemini_embedding_url") or "").strip(),
    }
    problems = []
    if not is_safe_bundle_http_url(url_fields["base_url"], require_https=True, allow_local=False):
        problems.append(
            {
                "field": "base_url",
                "message": "base_url must be an absolute HTTPS non-local API base URL without credentials, query strings, fragments, placeholders, localhost, or invalid ports",
            }
        )
    if not is_safe_bundle_http_url(url_fields["origin"], origin_only=True, require_https=True, allow_local=False):
        problems.append(
            {
                "field": "origin",
                "message": "origin must be an HTTPS non-local browser origin with no path, query string, fragment, credentials, placeholder, localhost, or invalid port",
            }
        )
    if not is_safe_bundle_http_url(url_fields["marqo.url"]):
        problems.append(
            {
                "field": "marqo.url",
                "message": "marqo.url must be a reachable absolute HTTP(S) endpoint without credentials, query strings, fragments, placeholders, or invalid ports",
            }
        )
    if not is_safe_bundle_http_url(url_fields["marqo.gemini_embedding_url"]):
        problems.append(
            {
                "field": "marqo.gemini_embedding_url",
                "message": "marqo.gemini_embedding_url must be a reachable absolute HTTP(S) endpoint without credentials, query strings, fragments, placeholders, or invalid ports",
            }
        )
    return {
        "name": "operational_url_config",
        "ok": not problems,
        "details": {
            **url_fields,
            "problems": problems,
        },
    }


def check_no_local_demo_keys(bundle_dir: Path) -> dict[str, Any]:
    inspected = [
        "operational-evidence.config.json",
        "operational-evidence.env",
        "representative-sites.config.json",
        "haeorum-ai-search.env",
        "malls.json",
        "widget_init.example.html",
        "CHECKLIST.md",
    ]
    findings = []
    for relative in inspected:
        text = read_text(bundle_dir / relative)
        for token in PROHIBITED_OPERATIONAL_TOKENS:
            if token in text:
                findings.append({"file": relative, "token": token})
    return {
        "name": "no_local_demo_keys",
        "ok": not findings,
        "details": {"findings": findings},
    }


def check_evidence_commands(bundle_dir: Path) -> dict[str, Any]:
    checklist = read_text(bundle_dir / "CHECKLIST.md")
    required_tokens = [
        "collect_operational_evidence.py",
        "server_db_intake_check.py",
        "--dry-run",
        "--env-file /etc/haeorum-ai-search/operational-evidence.env",
        "--evidence-dir /var/log/haeorum-ai-search",
        "--requirements-audit-output /var/log/haeorum-ai-search/requirements-audit.json",
    ]
    missing = [token for token in required_tokens if token not in checklist]
    return {
        "name": "evidence_commands",
        "ok": not missing,
        "details": {"missing": missing},
    }


def check_input_preparation_config(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "operational-evidence.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "input_preparation_config", "ok": False, "details": {"error": str(exc)}}
    if not isinstance(data, dict):
        return {"name": "input_preparation_config", "ok": False, "details": {"error": "config must be an object"}}

    problems = []
    input_preparation = data.get("input_preparation")
    if not isinstance(input_preparation, dict):
        problems.append({"field": "input_preparation", "message": "input_preparation object is required"})
        input_preparation = {}
    if input_preparation.get("enabled") is not True:
        problems.append({"field": "input_preparation.enabled", "message": "input_preparation.enabled must be true in the handoff template"})
    if data.get("products_csv") != "/data/haeorum-ai-search/products-full.csv":
        problems.append({"field": "products_csv", "message": "products_csv must use the standard deployed full CSV path"})
    if data.get("poc_products_csv") != "/data/haeorum-ai-search/poc-products.csv":
        problems.append({"field": "poc_products_csv", "message": "poc_products_csv must use the standard deployed PoC CSV path"})
    load_config = data.get("load")
    if not isinstance(load_config, dict):
        problems.append({"field": "load", "message": "load config object is required"})
        load_config = {}
    load_image_file = str(load_config.get("image_file") or "").strip()
    if not load_image_file.startswith("/data/haeorum-ai-search/") or quality_case_has_placeholder(load_image_file):
        problems.append(
            {
                "field": "load.image_file",
                "message": "load.image_file must point to a deployed non-placeholder reference image under /data/haeorum-ai-search/",
            }
        )
    security_config = data.get("security")
    if not isinstance(security_config, dict):
        problems.append({"field": "security", "message": "security config object is required"})
        security_config = {}
    if not isinstance(security_config.get("sync_alerting_configured"), bool):
        problems.append(
            {
                "field": "security.sync_alerting_configured",
                "message": "sync_alerting_configured must be an explicit boolean handoff confirmation",
            }
        )

    export_config = input_preparation.get("mssql_export")
    if not isinstance(export_config, dict):
        problems.append({"field": "input_preparation.mssql_export", "message": "mssql_export config is required"})
        export_config = {}
    if export_config.get("product_id_column") != "product_id":
        problems.append({"field": "input_preparation.mssql_export.product_id_column", "message": "product_id_column must default to product_id"})
    if export_config.get("updated_at_column") != "updated_at":
        problems.append({"field": "input_preparation.mssql_export.updated_at_column", "message": "updated_at_column must default to updated_at"})

    dataset_config = input_preparation.get("poc_dataset")
    if not isinstance(dataset_config, dict):
        problems.append({"field": "input_preparation.poc_dataset", "message": "poc_dataset config is required"})
        dataset_config = {}
    for field in ("target_size", "min_products"):
        try:
            value = int(dataset_config.get(field, 0))
        except (TypeError, ValueError):
            value = 0
        if value < 300:
            problems.append({"field": f"input_preparation.poc_dataset.{field}", "message": f"{field} must be at least 300"})

    return {
        "name": "input_preparation_config",
        "ok": not problems,
        "details": {
            "enabled": input_preparation.get("enabled"),
            "products_csv": data.get("products_csv"),
            "poc_products_csv": data.get("poc_products_csv"),
            "load_image_file": load_image_file,
            "sync_alerting_configured": security_config.get("sync_alerting_configured"),
            "problems": problems,
        },
    }


def check_marqo_quality_config(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "operational-evidence.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "marqo_quality_config", "ok": False, "details": {"error": str(exc)}}
    if not isinstance(data, dict):
        return {"name": "marqo_quality_config", "ok": False, "details": {"error": "config must be an object"}}

    problems = []
    marqo = data.get("marqo")
    if not isinstance(marqo, dict):
        problems.append({"field": "marqo", "message": "marqo config object is required"})
        marqo = {}
    marqo_url = str(marqo.get("url") or "").strip()
    gemini_embedding_url = str(marqo.get("gemini_embedding_url") or "").strip()
    marqo_index = str(marqo.get("index") or "").strip()
    marqo_container = str(marqo.get("container") or "").strip()
    if not is_safe_bundle_http_url(marqo_url):
        problems.append({"field": "marqo.url", "message": "marqo.url must be a concrete HTTP(S) endpoint"})
    if not is_safe_bundle_http_url(gemini_embedding_url):
        problems.append(
            {
                "field": "marqo.gemini_embedding_url",
                "message": "marqo.gemini_embedding_url must be a concrete HTTP(S) endpoint",
            }
        )
    if not marqo_index or "replace-with" in marqo_index or "..." in marqo_index:
        problems.append({"field": "marqo.index", "message": "marqo.index must be a concrete index name"})
    if not marqo_container or "replace-with" in marqo_container or "..." in marqo_container:
        problems.append({"field": "marqo.container", "message": "marqo.container must identify the deployed Marqo container"})

    quality = data.get("quality")
    if not isinstance(quality, dict):
        problems.append({"field": "quality", "message": "quality config object is required"})
        quality = {}
    cases_file = str(quality.get("cases_file") or "").strip()
    if cases_file != "/etc/haeorum-ai-search/quality-cases.json":
        problems.append({"field": "quality.cases_file", "message": "cases_file must point to /etc/haeorum-ai-search/quality-cases.json"})
    thresholds = {
        "min_products": (300, None),
        "max_text_ms": (1, 3000),
        "max_image_ms": (1, 5000),
        "max_mixed_ms": (1, 5000),
    }
    parsed_quality = {}
    for field, (minimum, maximum) in thresholds.items():
        try:
            value = int(quality.get(field, 0))
        except (TypeError, ValueError):
            value = 0
        parsed_quality[field] = value
        if value < minimum:
            problems.append({"field": f"quality.{field}", "message": f"{field} must be at least {minimum}"})
        if maximum is not None and value > maximum:
            problems.append({"field": f"quality.{field}", "message": f"{field} must not exceed the MVP target {maximum}"})

    return {
        "name": "marqo_quality_config",
        "ok": not problems,
        "details": {
            "marqo_url": marqo_url,
            "gemini_embedding_url": gemini_embedding_url,
            "marqo_index": marqo_index,
            "marqo_container": marqo_container,
            "quality_cases_file": cases_file,
            "quality": parsed_quality,
            "problems": problems,
        },
    }


def check_quality_cases_template(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "quality-cases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "quality_cases_template", "ok": False, "details": {"error": str(exc)}}

    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        return {"name": "quality_cases_template", "ok": False, "details": {"error": "cases must be a list"}}

    problems = []
    names = []
    type_counts = {"text": 0, "image": 0, "text_image": 0}
    low_confidence_case_count = 0
    text_variant_case_count = 0
    image_path_prefix = "/data/haeorum-ai-search/quality-images/"
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            problems.append({"case": index, "field": "case", "message": "case must be an object"})
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            problems.append({"case": index, "field": "name", "message": "name is required"})
        elif quality_case_has_placeholder(name):
            problems.append({"case": index, "field": "name", "message": "name must not contain placeholders"})
        else:
            names.append(name)

        query = item.get("query")
        if not isinstance(query, dict):
            problems.append({"case": name or index, "field": "query", "message": "query object is required"})
            query = {}
        q = str(query.get("q") or "").strip()
        if q and quality_case_has_placeholder(q):
            problems.append({"case": name or index, "field": "query.q", "message": "query text must not contain placeholders"})
        has_image = bool(query.get("image") is True or item.get("image_path") or item.get("image_base64") or item.get("image_data_url"))
        if q and has_image:
            query_type = "text_image"
        elif has_image:
            query_type = "image"
        elif q:
            query_type = "text"
        else:
            query_type = ""
            problems.append({"case": name or index, "field": "query", "message": "case must provide q or image input"})
        if query_type:
            type_counts[query_type] += 1
        if query_type == "text" and quality_case_has_text_variant_marker(item):
            text_variant_case_count += 1

        if has_image:
            image_path = str(item.get("image_path") or "").strip()
            if not image_path:
                problems.append({"case": name or index, "field": "image_path", "message": "image cases must use deployed reference image files"})
            elif not image_path.startswith(image_path_prefix) or quality_case_has_placeholder(image_path):
                problems.append(
                    {
                        "case": name or index,
                        "field": "image_path",
                        "message": f"image_path must start with {image_path_prefix} and contain no placeholders",
                    }
                )
            if item.get("image_base64") or item.get("image_data_url"):
                problems.append({"case": name or index, "field": "image", "message": "bundle template must not embed base64/data URL images"})

        expects_low_confidence = item.get("expected_low_confidence") is True
        if expects_low_confidence:
            low_confidence_case_count += 1
        if not expects_low_confidence:
            try:
                min_results = int(item.get("expected_min_results", 0))
            except (TypeError, ValueError):
                min_results = 0
            if min_results < 3:
                problems.append(
                    {
                        "case": name or index,
                        "field": "expected_min_results",
                        "message": "positive search cases must set expected_min_results at least 3",
                    }
                )
        if not str(item.get("expected_category") or item.get("expected_top_product_id") or "").strip() and not expects_low_confidence:
            problems.append(
                {
                    "case": name or index,
                    "field": "expected_category",
                    "message": "case must assert expected_category, expected_top_product_id, or expected_low_confidence",
                }
            )

    duplicate_names = sorted(name for name in set(names) if names.count(name) > 1)
    for duplicate in duplicate_names:
        problems.append({"field": "name", "message": "duplicate case name", "name": duplicate})
    minimum_type_counts = {"text": 2, "image": 1, "text_image": 1}
    for query_type, minimum in minimum_type_counts.items():
        count = type_counts.get(query_type, 0)
        if count < minimum:
            problems.append({"field": "cases", "message": f"at least {minimum} {query_type} case(s) are required"})
    if low_confidence_case_count < 1:
        problems.append(
            {
                "field": "expected_low_confidence",
                "message": "at least one low-confidence or poor-image case with expected_low_confidence=true is required",
                "actual": low_confidence_case_count,
            }
        )
    if text_variant_case_count < 1:
        problems.append(
            {
                "field": "tags",
                "message": "at least one text typo, synonym, or expression-variant case tagged typo_or_synonym is required",
                "actual": text_variant_case_count,
            }
        )

    return {
        "name": "quality_cases_template",
        "ok": len(cases) >= 5 and not problems,
        "details": {
            "case_count": len(cases),
            "minimum_case_count": 5,
            "minimum_type_counts": minimum_type_counts,
            "type_counts": type_counts,
            "low_confidence_case_count": low_confidence_case_count,
            "text_variant_case_count": text_variant_case_count,
            "duplicate_names": duplicate_names,
            "problems": problems,
        },
    }


def check_deployment_reference_files(bundle_dir: Path) -> dict[str, Any]:
    required_files = [
        "deploy/reference/Dockerfile",
        "deploy/reference/compose-haeorum-marqo.yaml",
        "deploy/reference/compose-haeorum-gemini.yaml",
        "deploy/reference/compose-haeorum-existing-8gb.yaml",
        "deploy/reference/compose-haeorum-demo.yaml",
        "deploy/reference/requirements.txt",
        "deploy/reference/requirements-mssql.txt",
        "docs/OPERATIONS.md",
        "docs/INTEGRATION.md",
        "docs/REQUIREMENTS_TRACE.md",
        "sql/v_ai_search_products_template.sql",
    ]
    missing = [relative for relative in required_files if not (bundle_dir / relative).exists()]
    problems = []
    dockerfile = read_text(bundle_dir / "deploy" / "reference" / "Dockerfile")
    marqo_compose = read_text(bundle_dir / "deploy" / "reference" / "compose-haeorum-marqo.yaml")
    gemini_compose = read_text(bundle_dir / "deploy" / "reference" / "compose-haeorum-gemini.yaml")
    existing_profile_compose = read_text(bundle_dir / "deploy" / "reference" / "compose-haeorum-existing-8gb.yaml")
    demo_compose = read_text(bundle_dir / "deploy" / "reference" / "compose-haeorum-demo.yaml")
    requirements = read_text(bundle_dir / "deploy" / "reference" / "requirements.txt")
    requirements_mssql = read_text(bundle_dir / "deploy" / "reference" / "requirements-mssql.txt")
    operations_doc = read_text(bundle_dir / "docs" / "OPERATIONS.md")
    integration_doc = read_text(bundle_dir / "docs" / "INTEGRATION.md")
    trace_doc = read_text(bundle_dir / "docs" / "REQUIREMENTS_TRACE.md")
    view_template = read_text(bundle_dir / "sql" / "v_ai_search_products_template.sql")
    expected_tokens = [
        ("Dockerfile", dockerfile, "COPY app ./app"),
        ("Dockerfile", dockerfile, "requirements-mssql.txt"),
        ("compose-haeorum-marqo.yaml", marqo_compose, "marqo-api"),
        ("compose-haeorum-marqo.yaml", marqo_compose, "vespa"),
        ("compose-haeorum-marqo.yaml", marqo_compose, "reindex-once"),
        ("compose-haeorum-marqo.yaml", marqo_compose, "127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}:8000"),
        ("compose-haeorum-marqo.yaml", marqo_compose, "127.0.0.1:${MARQO_PORT:-8882}:8882"),
        ("compose-haeorum-gemini.yaml", gemini_compose, "GEMINI_AUTH_MODE"),
        ("compose-haeorum-gemini.yaml", gemini_compose, "GEMINI_API_KEY"),
        ("compose-haeorum-gemini.yaml", gemini_compose, "GEMINI_PROXY_API_KEY"),
        ("compose-haeorum-gemini.yaml", gemini_compose, "HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY"),
        ("compose-haeorum-existing-8gb.yaml", existing_profile_compose, "HAEORUM_SEARCH_MAX_CONCURRENCY"),
        ("compose-haeorum-existing-8gb.yaml", existing_profile_compose, "GEMINI_PROXY_RATE_LIMIT_RPM"),
        ("compose-haeorum-demo.yaml", demo_compose, "HAEORUM_SEARCH_ENGINE"),
        ("requirements.txt", requirements, "fastapi"),
        ("requirements-mssql.txt", requirements_mssql, "pyodbc"),
        ("OPERATIONS.md", operations_doc, "운영 readiness"),
        ("INTEGRATION.md", integration_doc, "HaeorumAISearch.init"),
        ("REQUIREMENTS_TRACE.md", trace_doc, "요구사항 추적표"),
        ("v_ai_search_products_template.sql", view_template, "CREATE OR ALTER VIEW dbo.v_ai_search_products"),
        ("v_ai_search_products_template.sql", view_template, "read-only"),
    ]
    for file_name, text, token in expected_tokens:
        if token not in text:
            problems.append({"file": file_name, "missing_token": token})
    checklist = read_text(bundle_dir / "CHECKLIST.md")
    if "deploy/reference/*" not in checklist:
        problems.append({"file": "CHECKLIST.md", "missing_token": "deploy/reference/*"})
    if "standalone bundle build context" not in checklist:
        problems.append({"file": "CHECKLIST.md", "missing_token": "standalone bundle build context"})
    for token in ["docs/*", "sql/v_ai_search_products_template.sql", "read-only MSSQL View"]:
        if token not in checklist:
            problems.append({"file": "CHECKLIST.md", "missing_token": token})
    reference_install_commands = sorted(
        set(re.findall(r"sudo install[^\r\n]*deploy/reference[^\r\n]*", checklist))
    )
    if reference_install_commands:
        problems.append(
            {
                "file": "CHECKLIST.md",
                "message": "deploy/reference files are review-only and must not be installed",
                "commands": reference_install_commands,
            }
        )
    return {
        "name": "deployment_reference_files",
        "ok": not missing and not problems,
        "details": {
            "missing": missing,
            "problems": problems,
        },
    }


def check_intake_runtime_handoff_docs(bundle_dir: Path) -> dict[str, Any]:
    required_files = [
        "server-db-intake.md",
        "tools/server_db_intake_check.py",
        "tools/compose_exposure_check.py",
        "tools/go_live_scenario_check.py",
        "docs/runtime-stack-gemini-marqo.md",
        "docs/production-handoff-checklist.md",
        "docs/operational-risk-register.md",
        "docs/go-live-failure-scenarios.md",
        "docs/production-incident-runbook.md",
        "docs/server82-runbook.md",
    ]
    missing = [relative for relative in required_files if not (bundle_dir / relative).exists()]
    problems = []
    local_path_findings: list[dict[str, Any]] = []
    file_tokens = [
        (
            "server-db-intake.md",
            [
                "ready_for_env_and_server_preflight",
                "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly",
                "Product deletion/hidden/sold-out rules",
                "Gemini quota page checked",
                "Fallback behavior if AI API is down",
                "API/Marqo/Gemini internal bind/listen policy",
                "Nginx forwarded header policy",
                "Docker log rotation values",
            ],
        ),
        (
            "tools/server_db_intake_check.py",
            [
                "ready_for_env_and_server_preflight",
                "no_plaintext_secrets",
                "TrustServerCertificate must not be allowed",
                "AI API/Marqo/Gemini ports must not be public inbound ports",
                "Nginx forwarded header policy must overwrite X-Forwarded-For with $remote_addr",
                "Docker log rotation values must include max-size and max-file",
                "API/Marqo/Gemini internal bind/listen policy must keep API/Marqo/Gemini ports private",
            ],
        ),
        (
            "tools/compose_exposure_check.py",
            [
                "protected_ports_loopback_only",
                "embedding_proxy_loopback_only",
                "0.0.0.0",
                "127.0.0.1",
            ],
        ),
        (
            "tools/go_live_scenario_check.py",
            [
                "SCENARIOS",
                "gemini_quota_429_or_cost_runaway",
                "runtime_health_gemini_marqo",
                "runtime_critical_alerts_absent",
            ],
        ),
        (
            "docs/runtime-stack-gemini-marqo.md",
            [
                "Marqo + Gemini",
                "embedding_backend=gemini",
                "gemini-embedding-2",
                "API, Marqo, and Gemini proxy ports may be published only to",
            ],
        ),
        (
            "docs/production-handoff-checklist.md",
            [
                "server_db_intake_check.py",
                "pre_handoff_audit.py",
                "GEMINI_AUTH_MODE=api_key",
                "Rate limits enabled",
                "rollback",
            ],
        ),
        (
            "docs/operational-risk-register.md",
            [
                "API resource exhaustion",
                "Gemini project quota exceeded",
                "Log disk fill",
                "DB View drift",
                "Rollback is tested",
            ],
        ),
        (
            "docs/go-live-failure-scenarios.md",
            [
                "abusive_or_accidental_traffic_spike",
                "gemini_quota_429_or_cost_runaway",
                "internal_port_exposure",
                "multi_api_scale_state_split",
                "observability_alerting_gap",
                "production-incident-runbook.md",
            ],
        ),
        (
            "docs/production-incident-runbook.md",
            [
                "Production Incident Runbook",
                "First 10 Minutes",
                "Gemini Quota Or Cost Spike",
                "Unsafe External URL Or Image Source",
                "Deployment Or Restart Failure",
                "Required Pre-Signoff Alerts",
                "Recovery Exit Criteria",
            ],
        ),
        (
            "docs/server82-runbook.md",
            [
                "server_db_intake_check.py",
                "Marqo + Gemini",
                "80/443",
                "rollback",
            ],
        ),
    ]
    for relative, tokens in file_tokens:
        text = read_text(bundle_dir / relative)
        local_path_findings.extend(find_prohibited_local_paths(text, relative))
        for token in tokens:
            if token not in text:
                problems.append({"file": relative, "missing_token": token})
        if relative.startswith("docs/") and "qwen" in text.lower():
            problems.append({"file": relative, "message": "operator-facing handoff material must not mention legacy qwen runtime"})

    for relative in ["docs/OPERATIONS.md", "docs/INTEGRATION.md", "docs/REQUIREMENTS_TRACE.md"]:
        path = bundle_dir / relative
        if path.exists():
            local_path_findings.extend(find_prohibited_local_paths(read_text(path), relative))

    checklist = read_text(bundle_dir / "CHECKLIST.md")
    local_path_findings.extend(find_prohibited_local_paths(checklist, "CHECKLIST.md"))
    for token in [
        "server-db-intake.md",
        "tools/server_db_intake_check.py",
        "tools/compose_exposure_check.py",
        "tools/go_live_scenario_check.py",
        "ready_for_env_and_server_preflight",
        "Marqo + Gemini embedding API",
        "API, Marqo, and Gemini proxy ports may be published only to",
    ]:
        if token not in checklist:
            problems.append({"file": "CHECKLIST.md", "missing_token": token})

    return {
        "name": "intake_runtime_handoff_docs",
        "ok": not missing and not problems and not local_path_findings,
        "details": {
            "missing": missing,
            "problems": problems,
            "local_path_findings": local_path_findings,
        },
    }


def check_blocker_checklist_paths(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "requirements-blockers.md"
    if not path.exists():
        return {"name": "blocker_checklist_paths", "ok": True, "details": {"present": False}}

    text = read_text(path)
    local_path_findings = find_prohibited_local_paths(text, "requirements-blockers.md")
    unresolved_placeholders = [token for token in UNRESOLVED_OPERATIONAL_PLACEHOLDERS if token in text]
    required_token_groups = [
        (
            "deployment_project_root",
            [f"Run from: {DEPLOYMENT_PROJECT_ROOT}", f"Run from: `{DEPLOYMENT_PROJECT_ROOT}`"],
        ),
        (
            "deployment_evidence_dir",
            [f"Evidence output dir: {DEPLOYMENT_EVIDENCE_DIR}", f"Evidence output dir: `{DEPLOYMENT_EVIDENCE_DIR}`"],
        ),
    ]
    missing_required_tokens = [
        {"name": name, "accepted_tokens": accepted_tokens}
        for name, accepted_tokens in required_token_groups
        if not any(token in text for token in accepted_tokens)
    ]
    return {
        "name": "blocker_checklist_paths",
        "ok": not local_path_findings and not unresolved_placeholders and not missing_required_tokens,
        "details": {
            "present": True,
            "local_path_findings": local_path_findings,
            "unresolved_placeholders": unresolved_placeholders,
            "missing_required_tokens": missing_required_tokens,
        },
    }


def check_blocker_resolution_commands(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "requirements-blockers.md"
    if not path.exists():
        return {"name": "blocker_resolution_commands", "ok": True, "details": {"present": False}}

    text = read_text(path)
    required_tokens = [
        "scripts/mssql_export_csv.py",
        "scripts/poc_dataset_builder.py",
        "scripts/mall_config_builder.py",
        "--api-server-count 1",
        "--api-server-count 2",
        "load-mixed-traffic-1-api.json",
        "load-mixed-traffic-2-api.json",
    ]
    missing = [token for token in required_tokens if token not in text]
    return {
        "name": "blocker_resolution_commands",
        "ok": not missing,
        "details": {
            "present": True,
            "missing": missing,
        },
    }


def check_missing_evidence_script_paths(bundle_dir: Path) -> dict[str, Any]:
    script_paths = [
        path
        for path in [bundle_dir / "missing-evidence.sh", bundle_dir / "missing-evidence.ps1"]
        if path.exists()
    ]
    if not script_paths:
        return {"name": "missing_evidence_script_paths", "ok": True, "details": {"present": False}}

    local_path_findings = []
    missing_required_tokens = []
    unresolved_placeholders = []
    for script_path in script_paths:
        relative = script_path.name
        text = read_text(script_path)
        local_path_findings.extend(find_prohibited_local_paths(text, relative))
        unresolved_placeholders.extend(
            {"file": relative, "token": token}
            for token in UNRESOLVED_OPERATIONAL_PLACEHOLDERS
            if token in text
        )
        if script_path.suffix == ".sh":
            required_tokens = [
                f"PROJECT_ROOT='{DEPLOYMENT_PROJECT_ROOT}'",
                f"EVIDENCE_DIR='{DEPLOYMENT_EVIDENCE_DIR}'",
                "PLACEHOLDER_OPEN='<'",
                "grep -Ev",
                'mkdir -p "$EVIDENCE_DIR"',
                "[haeorum-evidence]",
                DEPLOYMENT_EVIDENCE_DIR,
            ]
        else:
            required_tokens = [
                f"$ProjectRoot = '{DEPLOYMENT_PROJECT_ROOT}'",
                f"$EvidenceDir = '{DEPLOYMENT_EVIDENCE_DIR}'",
                "$PlaceholderOpen = '<'",
                "New-Item -ItemType Directory -Force -Path $EvidenceDir",
                "[haeorum-evidence]",
                DEPLOYMENT_EVIDENCE_DIR,
            ]
        missing_required_tokens.extend(
            {"file": relative, "token": token}
            for token in required_tokens
            if token not in text
        )
    return {
        "name": "missing_evidence_script_paths",
        "ok": not local_path_findings and not missing_required_tokens and not unresolved_placeholders,
        "details": {
            "present": True,
            "files": [path.name for path in script_paths],
            "local_path_findings": local_path_findings,
            "missing_required_tokens": missing_required_tokens,
            "unresolved_placeholders": unresolved_placeholders,
        },
    }


def check_local_acceptance_report(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "local-acceptance.json"
    if not path.exists():
        return {"name": "local_acceptance_report", "ok": True, "details": {"present": False}}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "local_acceptance_report", "ok": False, "details": {"present": True, "error": str(exc)}}
    if not isinstance(data, dict):
        return {"name": "local_acceptance_report", "ok": False, "details": {"present": True, "error": "report must be an object"}}

    problems = []
    command_output_fields = find_command_output_fields(data)
    if command_output_fields:
        problems.append(
            {
                "field": "command_output",
                "message": "bundled local acceptance report must omit stdout_tail/stderr_tail command output",
                "paths": command_output_fields,
            }
        )
    if data.get("ok") is not True:
        problems.append({"field": "ok", "message": "local acceptance report must be ok=true"})
    if data.get("local_only") is not True:
        problems.append({"field": "local_only", "message": "local acceptance report must be marked local_only=true"})
    if data.get("not_operational_readiness") is not True:
        problems.append(
            {
                "field": "not_operational_readiness",
                "message": "local acceptance report must be marked not_operational_readiness=true",
            }
        )

    summary = data.get("summary")
    if not isinstance(summary, dict):
        problems.append({"field": "summary", "message": "summary object is required"})
        summary = {}
    try:
        total = int(summary.get("total", 0))
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
    except (TypeError, ValueError):
        total = passed = 0
        failed = 1
    if total < len(REQUIRED_LOCAL_ACCEPTANCE_CHECKS) or passed != total or failed != 0:
        problems.append(
            {
                "field": "summary",
                "message": "local acceptance summary must show all required checks passing",
            }
        )

    checks = data.get("checks")
    if not isinstance(checks, list):
        problems.append({"field": "checks", "message": "checks list is required"})
        checks = []
    checks_by_name = {str(item.get("name")): item for item in checks if isinstance(item, dict)}
    missing = [name for name in REQUIRED_LOCAL_ACCEPTANCE_CHECKS if name not in checks_by_name]
    failing = [
        name
        for name in REQUIRED_LOCAL_ACCEPTANCE_CHECKS
        if isinstance(checks_by_name.get(name), dict) and checks_by_name[name].get("ok") is not True
    ]
    if missing:
        problems.append({"field": "checks", "message": "required local acceptance checks are missing", "missing": missing})
    if failing:
        problems.append({"field": "checks", "message": "required local acceptance checks are not passing", "failing": failing})

    acceptance_check = checks_by_name.get("acceptance_check")
    acceptance_summary = acceptance_check.get("stdout_json_summary") if isinstance(acceptance_check, dict) else None
    if not isinstance(acceptance_summary, dict):
        problems.append(
            {
                "field": "acceptance_check.stdout_json_summary",
                "message": "acceptance_check must include parsed stdout_json_summary from scripts/acceptance_check.py",
            }
        )
        acceptance_summary = {}
    try:
        acceptance_check_count = int(acceptance_summary.get("check_count", 0) or 0)
    except (TypeError, ValueError):
        acceptance_check_count = 0
    acceptance_failed = [str(item) for item in acceptance_summary.get("failed_checks") or []]
    acceptance_check_names = [str(item) for item in acceptance_summary.get("check_names") or []]
    missing_acceptance_checks = [
        name for name in REQUIRED_ACCEPTANCE_INNER_CHECKS if name not in acceptance_check_names
    ]
    if acceptance_check_count < len(REQUIRED_ACCEPTANCE_INNER_CHECKS):
        problems.append(
            {
                "field": "acceptance_check.stdout_json_summary.check_count",
                "message": f"acceptance_check must report at least {len(REQUIRED_ACCEPTANCE_INNER_CHECKS)} inner checks",
            }
        )
    if acceptance_failed:
        problems.append(
            {
                "field": "acceptance_check.stdout_json_summary.failed_checks",
                "message": "acceptance_check inner checks failed",
                "failing": acceptance_failed,
            }
        )
    if missing_acceptance_checks:
        problems.append(
            {
                "field": "acceptance_check.stdout_json_summary.check_names",
                "message": "required acceptance inner checks are missing",
                "missing": missing_acceptance_checks,
            }
        )

    source_fingerprint = data.get("source_fingerprint")
    expected_source_fingerprint = build_source_fingerprint()
    source_fingerprint_match = False
    if not isinstance(source_fingerprint, dict):
        problems.append(
            {
                "field": "source_fingerprint",
                "message": "local acceptance report must include the current source_fingerprint",
            }
        )
        source_fingerprint = {}
    else:
        source_fingerprint_match = (
            source_fingerprint.get("algorithm") == expected_source_fingerprint.get("algorithm")
            and source_fingerprint.get("file_count") == expected_source_fingerprint.get("file_count")
            and source_fingerprint.get("digest") == expected_source_fingerprint.get("digest")
        )
        if not source_fingerprint_match:
            problems.append(
                {
                    "field": "source_fingerprint.digest",
                    "message": "local acceptance report was not generated from the current source tree",
                    "actual": source_fingerprint.get("digest"),
                    "expected": expected_source_fingerprint.get("digest"),
                }
            )

    return {
        "name": "local_acceptance_report",
        "ok": not problems,
        "details": {
            "present": True,
            "ok": data.get("ok"),
            "local_only": data.get("local_only"),
            "not_operational_readiness": data.get("not_operational_readiness"),
            "summary": summary,
            "missing_required_checks": missing,
            "failing_required_checks": failing,
            "acceptance_check_count": acceptance_check_count,
            "missing_acceptance_inner_checks": missing_acceptance_checks,
            "source_fingerprint_match": source_fingerprint_match,
            "source_fingerprint_digest": source_fingerprint.get("digest"),
            "expected_source_fingerprint_digest": expected_source_fingerprint.get("digest"),
            "command_output_fields": command_output_fields,
            "problems": problems,
        },
    }


def find_command_output_fields(value: Any, path: str = "$") -> list[str]:
    fields: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in {"stdout_tail", "stderr_tail"}:
                fields.append(child_path)
            fields.extend(find_command_output_fields(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            fields.extend(find_command_output_fields(item, f"{path}[{index}]"))
    return fields


def check_optional_handoff_reports(bundle_dir: Path) -> dict[str, Any]:
    report_pairs = [
        ("server-db-intake-check", "server-db-intake-check.json", "server-db-intake-check.md"),
        ("compose-exposure-check", "compose-exposure-check.json", "compose-exposure-check.md"),
        ("go-live-scenario-check", "go-live-scenario-check.json", "go-live-scenario-check.md"),
        ("requirements-audit", "requirements-audit.json", "requirements-audit.md"),
        ("operational-readiness", "operational-readiness.json", "operational-readiness.md"),
        ("evidence-collection-plan", "evidence-collection-plan.json", "evidence-collection-plan.md"),
    ]
    problems = []
    present = []
    local_path_findings = []
    legacy_provider_findings = []
    unresolved_placeholders = []
    command_output_fields = []
    parsed_json = []
    for report_name, json_name, markdown_name in report_pairs:
        json_path = bundle_dir / json_name
        markdown_path = bundle_dir / markdown_name
        json_present = json_path.exists()
        markdown_present = markdown_path.exists()
        if json_present or markdown_present:
            present.append(report_name)
        if json_present != markdown_present:
            problems.append(
                {
                    "report": report_name,
                    "message": "JSON and Markdown handoff reports must be included together",
                    "json_present": json_present,
                    "markdown_present": markdown_present,
                }
            )
        if json_present:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                parsed_json.append(json_name)
                command_output_fields.extend(
                    {"file": json_name, "path": field}
                    for field in find_command_output_fields(data)
                )
                if report_name == "server-db-intake-check":
                    status = str(data.get("status") or "")
                    if data.get("ok") is not True:
                        problems.append(
                            {
                                "report": report_name,
                                "file": json_name,
                                "message": "server/DB intake report must be ok=true before it is bundled",
                            }
                        )
                    if status not in {"template_shape_ok", "ready_for_env_and_server_preflight"}:
                        problems.append(
                            {
                                "report": report_name,
                                "file": json_name,
                                "message": "server/DB intake report must be template_shape_ok or ready_for_env_and_server_preflight",
                                "status": status,
                            }
                        )
                if report_name == "compose-exposure-check" and data.get("ok") is not True:
                    problems.append(
                        {
                            "report": report_name,
                            "file": json_name,
                            "message": "compose exposure report must be ok=true before it is bundled",
                        }
                    )
                if report_name == "go-live-scenario-check" and data.get("ok") is not True:
                    problems.append(
                        {
                            "report": report_name,
                            "file": json_name,
                            "message": "go-live scenario report must be ok=true before it is bundled",
                        }
                    )
            except (OSError, json.JSONDecodeError) as exc:
                problems.append({"report": report_name, "file": json_name, "message": str(exc)})
        for file_path in [path for path in [json_path, markdown_path] if path.exists()]:
            text = read_text(file_path)
            local_path_findings.extend(find_prohibited_local_paths(text, file_path.name))
            legacy_provider_findings.extend(find_prohibited_legacy_provider_terms(text, file_path.name))
            unresolved_placeholders.extend(
                {"file": file_path.name, "token": token}
                for token in UNRESOLVED_OPERATIONAL_PLACEHOLDERS
                if token in text
            )
    return {
        "name": "optional_handoff_reports",
        "ok": not problems
        and not local_path_findings
        and not legacy_provider_findings
        and not unresolved_placeholders
        and not command_output_fields,
        "details": {
            "present": present,
            "parsed_json": parsed_json,
            "problems": problems,
            "local_path_findings": local_path_findings,
            "legacy_provider_findings": legacy_provider_findings,
            "unresolved_placeholders": unresolved_placeholders,
            "command_output_fields": command_output_fields,
        },
    }


def check_handoff_secret_exposure(bundle_dir: Path) -> dict[str, Any]:
    findings = []
    for relative in HANDOFF_SECRET_SCAN_FILES:
        path = bundle_dir / relative
        if not path.exists():
            continue
        findings.extend(find_secret_exposures(read_text(path), relative))
    return {
        "name": "handoff_secret_exposure",
        "ok": not findings,
        "details": {
            "files_scanned": [relative for relative in HANDOFF_SECRET_SCAN_FILES if (bundle_dir / relative).exists()],
            "findings": findings,
        },
    }


def find_secret_exposures(text: str, filename: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    def add(kind: str, line_number: int, key: str = "") -> None:
        identity = (kind, line_number, key)
        if identity in seen:
            return
        seen.add(identity)
        finding = {
            "file": filename,
            "line": line_number,
            "kind": kind,
            "value": "[redacted]",
        }
        if key:
            finding["key"] = key
        findings.append(finding)

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in QUOTED_SECRET_FIELD_EXPOSURE_PATTERN.finditer(line):
            value = match.group("value")
            if not is_redacted_or_placeholder_secret_value(value):
                add("json_secret_field", line_number, match.group("key"))
        for match in SECRET_ASSIGNMENT_EXPOSURE_PATTERN.finditer(line):
            value = match.group("value")
            if not is_redacted_or_placeholder_secret_value(value):
                add("secret_assignment", line_number, match.group("key"))
        for match in CLI_SECRET_OPTION_EXPOSURE_PATTERN.finditer(line):
            value = match.group("value")
            if not is_redacted_or_placeholder_secret_value(value):
                add("secret_cli_option", line_number, match.group("option"))
        if URL_CREDENTIAL_EXPOSURE_PATTERN.search(line):
            add("url_credentials", line_number)
    return findings


def is_redacted_or_placeholder_secret_value(value: Any) -> bool:
    text = str(value or "").strip().strip("\"'")
    normalized = text.lower()
    if not text:
        return True
    if text in {"***", "****"}:
        return True
    if normalized in {"redacted", "masked", "none", "null", "[redacted]", "[redacted-secret]", "[redacted_api_key]"}:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return any(token in normalized for token in ("replace-with", "change-me", "changeme", "..."))


def find_prohibited_local_paths(text: str, filename: str) -> list[dict[str, Any]]:
    findings = []
    for name, pattern in PROHIBITED_LOCAL_PATH_PATTERNS.items():
        matches = sorted(set(pattern.findall(text)))
        findings.extend({"file": filename, "pattern": name, "match": match} for match in matches)
    return findings


def find_prohibited_legacy_provider_terms(text: str, filename: str) -> list[dict[str, Any]]:
    findings = []
    for name, pattern in PROHIBITED_LEGACY_PROVIDER_PATTERNS.items():
        matches = sorted({match.group(0) for match in pattern.finditer(text)})
        findings.extend({"file": filename, "pattern": name, "match": match} for match in matches)
    return findings


def parse_env_template(text: str) -> dict[str, str]:
    entries = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        entries[key] = value.strip()
    return entries


def classify_env_value(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return "empty"
    if "replace-with" in normalized or "..." in normalized:
        return "placeholder"
    return "set"


def quality_case_has_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return not normalized or "replace-with" in normalized or "..." in normalized or "<" in normalized or ">" in normalized


def quality_case_has_text_variant_marker(item: dict[str, Any]) -> bool:
    name = str(item.get("name") or "").lower()
    if any(token in name for token in ["typo", "synonym", "variant"]):
        return True
    return "typo_or_synonym" in quality_case_tags(item)


def quality_case_tags(item: dict[str, Any]) -> list[str]:
    raw_tags = item.get("tags")
    if isinstance(raw_tags, str):
        values = raw_tags.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        values = []
    return [str(value).strip().lower() for value in values if str(value).strip()]


def is_local_bundle_http_host(hostname: str | None) -> bool:
    return is_non_public_host(hostname)


def is_link_or_unspecified_bundle_http_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if host in {"0.0.0.0", "::"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_unspecified or address.is_link_local


def is_safe_bundle_http_url(
    value: str,
    origin_only: bool = False,
    require_https: bool = False,
    allow_local: bool = True,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = text.lower()
    if "replace-with" in normalized or "..." in normalized or "sample" in normalized or "dummy" in normalized:
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
    if is_link_or_unspecified_bundle_http_host(parsed.hostname):
        return False
    if not allow_local and is_local_bundle_http_host(parsed.hostname):
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    if origin_only and parsed.path not in {"", "/"}:
        return False
    return True


def contains_local_demo_token(value: str) -> bool:
    normalized = value.strip().lower()
    return any(token.lower() in normalized for token in PROHIBITED_OPERATIONAL_TOKENS) or "dev-key" in normalized


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Operational Bundle Check",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Bundle: `{report.get('bundle_dir')}`",
        "",
        "| Check | OK | Details |",
        "| --- | --- | --- |",
    ]
    for check in report.get("checks", []):
        lines.append(
            "| {name} | `{ok}` | {details} |".format(
                name=escape_markdown_cell(check.get("name")),
                ok=check.get("ok"),
                details=escape_markdown_cell(json.dumps(check.get("details") or {}, ensure_ascii=False, sort_keys=True)),
            )
        )
    return "\n".join(lines) + "\n"


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Haeorum AI Search operational bundle template.")
    parser.add_argument("--bundle-dir", default="", help="Existing bundle directory to validate. Defaults to a generated temporary bundle.")
    parser.add_argument("--output", default="", help="Optional JSON report output path.")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown report output path.")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(to_markdown(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
