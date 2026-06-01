from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import is_placeholder_public_api_key

TEMPLATE_FILES = [
    {
        "source": ROOT / "contracts" / "operational_evidence.config.example.json",
        "target": Path("operational-evidence.config.json"),
        "install_path": "/etc/haeorum-ai-search/operational-evidence.config.json",
        "mode": "0640",
        "description": "Collector config. Fill deployed paths, mall id, base_url, origin, Marqo URL, and env var names.",
    },
    {
        "source": ROOT / "contracts" / "operational_evidence.env.example",
        "target": Path("operational-evidence.env"),
        "install_path": "/etc/haeorum-ai-search/operational-evidence.env",
        "mode": "0600",
        "description": "Collector secrets. Fill public API key, admin key, and read-only MSSQL connection string.",
    },
    {
        "source": ROOT / "contracts" / "representative_sites.example.json",
        "target": Path("representative-sites.config.json"),
        "install_path": "/etc/haeorum-ai-search/representative-sites.config.json",
        "mode": "0640",
        "description": "Representative site checks. Fill real URLs, origins, API keys, and product URL rules.",
    },
    {
        "source": ROOT / "contracts" / "quality_cases.example.json",
        "target": Path("quality-cases.json"),
        "install_path": "/etc/haeorum-ai-search/quality-cases.json",
        "mode": "0640",
        "description": "Production PoC quality cases. Fill at least two text, one image-only, and one mixed-search case with expected_min_results >= 3.",
    },
    {
        "source": ROOT / "deploy" / "haeorum-ai-search.env.example",
        "target": Path("haeorum-ai-search.env"),
        "install_path": "/etc/haeorum-ai-search/haeorum-ai-search.env",
        "mode": "0640",
        "description": "Production service env file. Fill production values before installing.",
    },
    {
        "source": ROOT / "sample_malls.json",
        "target": Path("malls.json"),
        "install_path": "/etc/haeorum-ai-search/malls.json",
        "mode": "0640",
        "description": "Mall config structure template. Expand to 1,700 production malls with real non-sample API keys/origins before installing.",
    },
    {
        "source": ROOT / "sample_query_synonyms.json",
        "target": Path("query-synonyms.json"),
        "install_path": "/etc/haeorum-ai-search/query-synonyms.json",
        "mode": "0640",
        "description": "Query synonym seed config referenced by HAEORUM_QUERY_SYNONYM_PATH.",
    },
    {
        "source": ROOT / "deploy" / "nginx" / "haeorum-ai-search.conf",
        "target": Path("deploy") / "nginx" / "haeorum-ai-search.conf",
        "install_path": "/etc/nginx/sites-enabled/haeorum-ai-search.conf",
        "mode": "0644",
        "description": "Nginx HTTPS reverse proxy template.",
    },
    {
        "source": ROOT / "deploy" / "systemd" / "haeorum-ai-search.service",
        "target": Path("deploy") / "systemd" / "haeorum-ai-search.service",
        "install_path": "/etc/systemd/system/haeorum-ai-search.service",
        "mode": "0644",
        "description": "API server systemd unit.",
    },
    {
        "source": ROOT / "deploy" / "systemd" / "haeorum-ai-sync.service",
        "target": Path("deploy") / "systemd" / "haeorum-ai-sync.service",
        "install_path": "/etc/systemd/system/haeorum-ai-sync.service",
        "mode": "0644",
        "description": "Continuous sync worker systemd unit.",
    },
    {
        "source": ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.service",
        "target": Path("deploy") / "systemd" / "haeorum-ai-reindex.service",
        "install_path": "/etc/systemd/system/haeorum-ai-reindex.service",
        "mode": "0644",
        "description": "One-shot reindex systemd unit.",
    },
    {
        "source": ROOT / "deploy" / "systemd" / "haeorum-ai-reindex.timer",
        "target": Path("deploy") / "systemd" / "haeorum-ai-reindex.timer",
        "install_path": "/etc/systemd/system/haeorum-ai-reindex.timer",
        "mode": "0644",
        "description": "Nightly full reindex timer.",
    },
    {
        "source": ROOT / "deploy" / "logrotate" / "haeorum-ai-search",
        "target": Path("deploy") / "logrotate" / "haeorum-ai-search",
        "install_path": "/etc/logrotate.d/haeorum-ai-search",
        "mode": "0644",
        "description": "Logrotate rule for search/error/sync JSONL logs.",
    },
    {
        "source": ROOT / "Dockerfile",
        "target": Path("deploy") / "reference" / "Dockerfile",
        "install_path": "",
        "mode": "0644",
        "description": "Reference API container image build file. Use from the deployed source root, not as a standalone bundle build context.",
    },
    {
        "source": ROOT / "compose-haeorum-marqo.yaml",
        "target": Path("deploy") / "reference" / "compose-haeorum-marqo.yaml",
        "install_path": "",
        "mode": "0644",
        "description": "Reference Marqo/Vespa/API compose topology for PoC or controlled deployment review.",
    },
    {
        "source": ROOT / "compose-haeorum-gemini.yaml",
        "target": Path("deploy") / "reference" / "compose-haeorum-gemini.yaml",
        "install_path": "",
        "mode": "0644",
        "description": "Reference Gemini embedding proxy compose override for API-key production mode.",
    },
    {
        "source": ROOT / "compose-haeorum-existing-8gb.yaml",
        "target": Path("deploy") / "reference" / "compose-haeorum-existing-8gb.yaml",
        "install_path": "",
        "mode": "0644",
        "description": "Reference conservative resource profile for the existing 8GB Linux server class.",
    },
    {
        "source": ROOT / "compose-haeorum-demo.yaml",
        "target": Path("deploy") / "reference" / "compose-haeorum-demo.yaml",
        "install_path": "",
        "mode": "0644",
        "description": "Reference local demo compose topology.",
    },
    {
        "source": ROOT / "requirements.txt",
        "target": Path("deploy") / "reference" / "requirements.txt",
        "install_path": "",
        "mode": "0644",
        "description": "Reference Python runtime dependency list.",
    },
    {
        "source": ROOT / "requirements-mssql.txt",
        "target": Path("deploy") / "reference" / "requirements-mssql.txt",
        "install_path": "",
        "mode": "0644",
        "description": "Reference Python dependency list for MSSQL/ODBC-enabled sync and export hosts.",
    },
    {
        "source": ROOT / "contracts" / "widget_init.example.html",
        "target": Path("widget_init.example.html"),
        "install_path": "",
        "mode": "0644",
        "description": "Reference HTML snippet for inserting the widget on existing mall pages.",
    },
    {
        "source": ROOT / "scripts" / "widget_integration_probe.py",
        "target": Path("tools") / "widget_integration_probe.py",
        "install_path": "",
        "mode": "0644",
        "description": "Offline probe for saved mall HTML that recommends data-hai-auto-init selectors and flags CSP script/connect-src or relative-script risks.",
    },
    {
        "source": ROOT / "OPERATIONS.md",
        "target": Path("docs") / "OPERATIONS.md",
        "install_path": "",
        "mode": "0644",
        "description": "Reference operations guide for deployment, checks, monitoring, and evidence collection.",
    },
    {
        "source": ROOT / "INTEGRATION.md",
        "target": Path("docs") / "INTEGRATION.md",
        "install_path": "",
        "mode": "0644",
        "description": "Reference integration guide for existing mall templates and widget/API adoption.",
    },
    {
        "source": ROOT / "REQUIREMENTS_TRACE.md",
        "target": Path("docs") / "REQUIREMENTS_TRACE.md",
        "install_path": "",
        "mode": "0644",
        "description": "Reference requirements traceability matrix for local and operational evidence.",
    },
    {
        "source": ROOT / "deploy" / "runtime-stack-gemini-marqo.md",
        "target": Path("docs") / "runtime-stack-gemini-marqo.md",
        "install_path": "",
        "mode": "0644",
        "description": "Canonical runtime stack definition for the Marqo + Gemini embedding deployment.",
    },
    {
        "source": ROOT / "deploy" / "production-handoff-checklist.md",
        "target": Path("docs") / "production-handoff-checklist.md",
        "install_path": "",
        "mode": "0644",
        "description": "Production handoff gate checklist to run after receiving server and DB inputs.",
    },
    {
        "source": ROOT / "deploy" / "operational-risk-register.md",
        "target": Path("docs") / "operational-risk-register.md",
        "install_path": "",
        "mode": "0644",
        "description": "Go-live risk register covering abuse, quota, logging, DB drift, rollout, and rollback controls.",
    },
    {
        "source": ROOT / "deploy" / "go-live-failure-scenarios.md",
        "target": Path("docs") / "go-live-failure-scenarios.md",
        "install_path": "",
        "mode": "0644",
        "description": "Failure scenario checklist for abuse, quota, overload, logging, DB drift, widget rollback, and scale controls.",
    },
    {
        "source": ROOT / "deploy" / "production-incident-runbook.md",
        "target": Path("docs") / "production-incident-runbook.md",
        "install_path": "",
        "mode": "0644",
        "description": "Production incident runbook for spike, quota, backend, DB drift, unsafe URL, disk, widget, and rollback incidents.",
    },
    {
        "source": ROOT / "deploy" / "server82-runbook.md",
        "target": Path("docs") / "server82-runbook.md",
        "install_path": "",
        "mode": "0644",
        "description": "Server 82 deployment runbook for the separate Linux host.",
    },
    {
        "source": ROOT / "deploy" / "server-db-intake.md",
        "target": Path("server-db-intake.md"),
        "install_path": "",
        "mode": "0640",
        "description": "Server and DB intake form to fill after the existing developer provides final inputs.",
    },
    {
        "source": ROOT / "scripts" / "server_db_intake_check.py",
        "target": Path("tools") / "server_db_intake_check.py",
        "install_path": "",
        "mode": "0755",
        "description": "Offline validator for the filled server and DB intake form.",
    },
    {
        "source": ROOT / "scripts" / "compose_exposure_check.py",
        "target": Path("tools") / "compose_exposure_check.py",
        "install_path": "",
        "mode": "0755",
        "description": "Offline validator that Docker Compose does not publish AI API, Marqo, or embedding ports publicly.",
    },
    {
        "source": ROOT / "scripts" / "go_live_scenario_check.py",
        "target": Path("tools") / "go_live_scenario_check.py",
        "install_path": "",
        "mode": "0755",
        "description": "Static and optional runtime validator for common go-live failure scenarios.",
    },
    {
        "source": ROOT / "sql" / "v_ai_search_products_template.sql",
        "target": Path("sql") / "v_ai_search_products_template.sql",
        "install_path": "",
        "mode": "0644",
        "description": "Reference MSSQL View template for the read-only AI search product export.",
    },
]
OPTIONAL_MANAGED_TARGETS = {
    Path("requirements-blockers.md"),
    Path("missing-evidence.sh"),
    Path("missing-evidence.ps1"),
    Path("server-db-intake-check.json"),
    Path("server-db-intake-check.md"),
    Path("compose-exposure-check.json"),
    Path("compose-exposure-check.md"),
    Path("go-live-scenario-check.json"),
    Path("go-live-scenario-check.md"),
    Path("local-acceptance.json"),
    Path("local-acceptance.md"),
    Path("requirements-audit.json"),
    Path("requirements-audit.md"),
    Path("operational-readiness.json"),
    Path("operational-readiness.md"),
    Path("evidence-collection-plan.json"),
    Path("evidence-collection-plan.md"),
}


def build_bundle(
    output_dir: str | Path,
    force: bool = False,
    blocker_checklist_source: str | Path | None = None,
    missing_commands_source: str | Path | None = None,
    local_acceptance_source: str | Path | None = None,
    local_acceptance_markdown_source: str | Path | None = None,
    server_db_intake_source: str | Path | None = None,
    server_db_intake_markdown_source: str | Path | None = None,
    compose_exposure_source: str | Path | None = None,
    compose_exposure_markdown_source: str | Path | None = None,
    go_live_scenario_source: str | Path | None = None,
    go_live_scenario_markdown_source: str | Path | None = None,
    requirements_audit_source: str | Path | None = None,
    requirements_audit_markdown_source: str | Path | None = None,
    operational_readiness_source: str | Path | None = None,
    operational_readiness_markdown_source: str | Path | None = None,
    evidence_collection_source: str | Path | None = None,
    evidence_collection_markdown_source: str | Path | None = None,
) -> dict[str, Any]:
    target_root = Path(output_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    expected_targets = expected_bundle_targets(
        blocker_checklist_source=blocker_checklist_source,
        missing_commands_source=missing_commands_source,
        local_acceptance_source=local_acceptance_source,
        local_acceptance_markdown_source=local_acceptance_markdown_source,
        server_db_intake_source=server_db_intake_source,
        server_db_intake_markdown_source=server_db_intake_markdown_source,
        compose_exposure_source=compose_exposure_source,
        compose_exposure_markdown_source=compose_exposure_markdown_source,
        go_live_scenario_source=go_live_scenario_source,
        go_live_scenario_markdown_source=go_live_scenario_markdown_source,
        requirements_audit_source=requirements_audit_source,
        requirements_audit_markdown_source=requirements_audit_markdown_source,
        operational_readiness_source=operational_readiness_source,
        operational_readiness_markdown_source=operational_readiness_markdown_source,
        evidence_collection_source=evidence_collection_source,
        evidence_collection_markdown_source=evidence_collection_markdown_source,
    )
    cleaned = clean_stale_bundle_files(target_root, expected_targets) if force else []
    copied = []
    skipped = []
    for template in TEMPLATE_FILES:
        source = Path(template["source"])
        target = target_root / Path(template["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": template.get("install_path", ""),
            "mode": template.get("mode", ""),
            "description": template.get("description", ""),
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
            continue
        copy_template_file(source, target, template)
        copied.append(entry)

    if blocker_checklist_source:
        source = Path(blocker_checklist_source)
        if not source.exists():
            raise FileNotFoundError(f"Blocker checklist source does not exist: {source}")
        target = target_root / "requirements-blockers.md"
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": "",
            "mode": "0644",
            "description": "Current operational blocker checklist generated from requirements_audit.py.",
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
        else:
            copy_text_without_local_paths(source, target)
            copied.append(entry)

    if missing_commands_source:
        source = Path(missing_commands_source)
        if not source.exists():
            raise FileNotFoundError(f"Missing evidence commands source does not exist: {source}")
        target = target_root / source.name
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": "",
            "mode": "0755" if source.suffix in {".sh", ".ps1"} else "0644",
            "description": "Current missing operational evidence command script generated from operational_readiness.py.",
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
        else:
            copy_text_without_local_paths(source, target)
            copied.append(entry)

    if local_acceptance_source:
        source = Path(local_acceptance_source)
        if not source.exists():
            raise FileNotFoundError(f"Local acceptance source does not exist: {source}")
        target = target_root / "local-acceptance.json"
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": "/var/log/haeorum-ai-search/local-acceptance.json",
            "mode": "0644",
            "description": "Latest local acceptance JSON report used by requirements_audit.py; rerun on deployed code if changed.",
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
        else:
            copy_json_without_command_output(source, target)
            copied.append(entry)

    if local_acceptance_markdown_source:
        source = Path(local_acceptance_markdown_source)
        if not source.exists():
            raise FileNotFoundError(f"Local acceptance Markdown source does not exist: {source}")
        target = target_root / "local-acceptance.md"
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": "/var/log/haeorum-ai-search/local-acceptance.md",
            "mode": "0644",
            "description": "Latest local acceptance Markdown report for operator review.",
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
        else:
            copy_text_without_local_paths(source, target)
            copied.append(entry)

    optional_report_sources = [
        (
            server_db_intake_source,
            "server-db-intake-check.json",
            "/var/log/haeorum-ai-search/server-db-intake-check.json",
            "Latest server/DB intake validation JSON report; must be ready_for_env_and_server_preflight after final inputs are filled.",
        ),
        (
            server_db_intake_markdown_source,
            "server-db-intake-check.md",
            "/var/log/haeorum-ai-search/server-db-intake-check.md",
            "Latest server/DB intake validation Markdown report for operator review.",
        ),
        (
            compose_exposure_source,
            "compose-exposure-check.json",
            "/var/log/haeorum-ai-search/compose-exposure-check.json",
            "Latest Docker Compose exposure validation JSON report.",
        ),
        (
            compose_exposure_markdown_source,
            "compose-exposure-check.md",
            "/var/log/haeorum-ai-search/compose-exposure-check.md",
            "Latest Docker Compose exposure validation Markdown report.",
        ),
        (
            go_live_scenario_source,
            "go-live-scenario-check.json",
            "/var/log/haeorum-ai-search/go-live-scenario-check.json",
            "Latest go-live failure scenario validation JSON report.",
        ),
        (
            go_live_scenario_markdown_source,
            "go-live-scenario-check.md",
            "/var/log/haeorum-ai-search/go-live-scenario-check.md",
            "Latest go-live failure scenario validation Markdown report for operator review.",
        ),
        (
            requirements_audit_source,
            "requirements-audit.json",
            "/var/log/haeorum-ai-search/requirements-audit.json",
            "Latest requirements audit JSON report for operator review and handoff traceability.",
        ),
        (
            requirements_audit_markdown_source,
            "requirements-audit.md",
            "/var/log/haeorum-ai-search/requirements-audit.md",
            "Latest requirements audit Markdown report for operator review.",
        ),
        (
            operational_readiness_source,
            "operational-readiness.json",
            "/var/log/haeorum-ai-search/operational-readiness.json",
            "Latest operational readiness JSON report showing currently missing production evidence.",
        ),
        (
            operational_readiness_markdown_source,
            "operational-readiness.md",
            "/var/log/haeorum-ai-search/operational-readiness.md",
            "Latest operational readiness Markdown report for operator review.",
        ),
        (
            evidence_collection_source,
            "evidence-collection-plan.json",
            "/var/log/haeorum-ai-search/evidence-collection-plan.json",
            "Latest evidence collection dry-run or plan JSON report showing blocking inputs.",
        ),
        (
            evidence_collection_markdown_source,
            "evidence-collection-plan.md",
            "/var/log/haeorum-ai-search/evidence-collection-plan.md",
            "Latest evidence collection dry-run or plan Markdown report for operator review.",
        ),
    ]
    for source_value, target_name, install_path, description in optional_report_sources:
        if not source_value:
            continue
        source = Path(source_value)
        if not source.exists():
            raise FileNotFoundError(f"Operational report source does not exist: {source}")
        target = target_root / target_name
        entry = {
            "source": str(source),
            "target": str(target),
            "install_path": install_path,
            "mode": "0644",
            "description": description,
        }
        if target.exists() and not force:
            skipped.append({**entry, "reason": "target exists"})
        else:
            if target_name.endswith(".json"):
                copy_json_without_command_output(source, target)
            else:
                copy_text_without_local_paths(source, target)
            copied.append(entry)

    checklist_path = target_root / "CHECKLIST.md"
    if checklist_path.exists() and not force:
        skipped.append(
            {
                "source": "",
                "target": str(checklist_path),
                "install_path": "",
                "mode": "",
                "description": "Operational bundle checklist.",
                "reason": "target exists",
            }
        )
    else:
        checklist_path.write_text(to_markdown(copied + skipped, target_root), encoding="utf-8")
        if not any(item["target"] == str(checklist_path) for item in copied):
            copied.append(
                {
                    "source": "",
                    "target": str(checklist_path),
                    "install_path": "",
                    "mode": "0644",
                    "description": "Operational bundle checklist.",
                }
            )

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(target_root),
        "copied": copied,
        "skipped": skipped,
        "cleaned": cleaned,
        "checklist": str(checklist_path),
        "next_commands": next_commands(),
    }


def strip_command_output_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_command_output_fields(item)
            for key, item in value.items()
            if key not in {"stdout_tail", "stderr_tail"}
        }
    if isinstance(value, list):
        return [strip_command_output_fields(item) for item in value]
    return value


def sanitize_bundle_local_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_bundle_text(str(key)): sanitize_bundle_local_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_bundle_local_paths(item) for item in value]
    if isinstance(value, str):
        return sanitize_bundle_text(value)
    return value


def sanitize_bundle_text(text: str) -> str:
    sanitized = text
    markers = {
        str(ROOT),
        str(ROOT).replace("\\", "/"),
        "examples\\HaeorumAISearch",
        "examples/HaeorumAISearch",
    }
    for marker in sorted(markers, key=len, reverse=True):
        sanitized = sanitized.replace(marker, "<local-project-root>")
    sanitized = re.sub(r"[A-Za-z]:[\\/][^\s`\"'<>]*", "<local-path>", sanitized)
    legacy_replacements = [
        ("Qwen/Qwen3-VL-Embedding-2B", "gemini-embedding-2"),
        ("Qwen/image-search", "embedding/image-search"),
        ("qwen/image-search", "embedding/image-search"),
        ("--embedding-backend qwen", "--embedding-backend gemini"),
        ("--qwen-model", "--gemini-model"),
        ("--qwen-embedding-url", "--gemini-embedding-url"),
        ("--qwen-embedding-dimensions", "--gemini-embedding-dimensions"),
        ("<qwen_url>", "<gemini_embedding_url>"),
        ("qwen_query_vector", "gemini_query_embedding"),
        ("backend_qwen", "backend_gemini"),
        ("qwen_model", "gemini_model"),
        ("qwen_embedding_dimensions", "gemini_embedding_dimensions"),
        ("qwen_embedding_config", "gemini_embedding_config"),
        ("Qwen", "Gemini"),
        ("qwen", "gemini"),
    ]
    for source, replacement in legacy_replacements:
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def copy_json_without_command_output(source: Path, target: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    sanitized = sanitize_bundle_local_paths(strip_command_output_fields(data))
    target.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_text_without_local_paths(source: Path, target: Path) -> None:
    target.write_text(sanitize_bundle_text(source.read_text(encoding="utf-8")), encoding="utf-8")


def expected_bundle_targets(
    blocker_checklist_source: str | Path | None = None,
    missing_commands_source: str | Path | None = None,
    local_acceptance_source: str | Path | None = None,
    local_acceptance_markdown_source: str | Path | None = None,
    server_db_intake_source: str | Path | None = None,
    server_db_intake_markdown_source: str | Path | None = None,
    compose_exposure_source: str | Path | None = None,
    compose_exposure_markdown_source: str | Path | None = None,
    go_live_scenario_source: str | Path | None = None,
    go_live_scenario_markdown_source: str | Path | None = None,
    requirements_audit_source: str | Path | None = None,
    requirements_audit_markdown_source: str | Path | None = None,
    operational_readiness_source: str | Path | None = None,
    operational_readiness_markdown_source: str | Path | None = None,
    evidence_collection_source: str | Path | None = None,
    evidence_collection_markdown_source: str | Path | None = None,
) -> set[Path]:
    targets = {Path(item["target"]) for item in TEMPLATE_FILES}
    targets.add(Path("CHECKLIST.md"))
    if blocker_checklist_source:
        targets.add(Path("requirements-blockers.md"))
    if missing_commands_source:
        targets.add(Path(Path(missing_commands_source).name))
    if local_acceptance_source:
        targets.add(Path("local-acceptance.json"))
    if local_acceptance_markdown_source:
        targets.add(Path("local-acceptance.md"))
    optional_sources = [
        (server_db_intake_source, "server-db-intake-check.json"),
        (server_db_intake_markdown_source, "server-db-intake-check.md"),
        (compose_exposure_source, "compose-exposure-check.json"),
        (compose_exposure_markdown_source, "compose-exposure-check.md"),
        (go_live_scenario_source, "go-live-scenario-check.json"),
        (go_live_scenario_markdown_source, "go-live-scenario-check.md"),
        (requirements_audit_source, "requirements-audit.json"),
        (requirements_audit_markdown_source, "requirements-audit.md"),
        (operational_readiness_source, "operational-readiness.json"),
        (operational_readiness_markdown_source, "operational-readiness.md"),
        (evidence_collection_source, "evidence-collection-plan.json"),
        (evidence_collection_markdown_source, "evidence-collection-plan.md"),
    ]
    for source, target_name in optional_sources:
        if source:
            targets.add(Path(target_name))
    return targets


def clean_stale_bundle_files(target_root: Path, expected_targets: set[Path]) -> list[str]:
    managed_targets = {Path(item["target"]) for item in TEMPLATE_FILES}
    managed_targets.update(OPTIONAL_MANAGED_TARGETS)
    managed_targets.add(Path("CHECKLIST.md"))

    cleaned = []
    for relative in sorted(managed_targets - expected_targets, key=lambda path: path.as_posix()):
        target = target_root / relative
        if target.exists() and target.is_file():
            target.unlink()
            cleaned.append(relative.as_posix())
    return cleaned


def to_markdown(files: list[dict[str, Any]], output_dir: Path) -> str:
    lines = [
        "# Haeorum AI Search Operational Bundle",
        "",
        "This bundle collects the editable configs and deployment templates needed before operational evidence collection.",
        "",
        "## Files",
        "",
        "| File | Install Path | Mode | Purpose |",
        "| --- | --- | --- | --- |",
    ]
    for item in files:
        if not item.get("install_path") and Path(str(item.get("target", ""))).name == "CHECKLIST.md":
            continue
        target = Path(str(item.get("target", "")))
        try:
            relative_target = target.relative_to(output_dir)
        except ValueError:
            relative_target = target
        lines.append(
            "| {file} | {install_path} | {mode} | {description} |".format(
                file=escape_markdown_cell(relative_target.as_posix()),
                install_path=escape_markdown_cell(item.get("install_path", "")),
                mode=escape_markdown_cell(item.get("mode", "")),
                description=escape_markdown_cell(item.get("description", "")),
            )
        )
    lines.extend(
        [
            "",
            "## Required Edits",
            "",
            "- Replace every `replace-with...`, `<...>`, `...`, `sample`, `dummy`, and `dev-key` placeholder before running evidence collection.",
            "- Fill `operational-evidence.env` with the public mall API key, admin key, internal Gemini proxy key, and read-only MSSQL connection string.",
            "- Fill `server-db-intake.md` only after receiving the final server, DB, domain, and Gemini credential policy from the existing developer; do not paste secret values into this Markdown file.",
            "- Run `python tools/server_db_intake_check.py --intake-file server-db-intake.md --print-summary` and proceed only when it reports `ready_for_env_and_server_preflight`.",
            "- Run `python tools/go_live_scenario_check.py --print-summary` and keep it passing before and after server deployment; rerun with `--base-url` and `--admin-key` after the API is up.",
            "- Install `operational-evidence.env` with mode `0600`; install `haeorum-ai-search.env` and other secret-bearing config files with mode `0640` or stricter.",
            "- Keep `api_key_env`, `admin_key_env`, `mssql_connection_string_env`, and Gemini proxy key values aligned with variable names in `operational-evidence.env` and `haeorum-ai-search.env`.",
            "- Use absolute HTTPS non-local API URLs without credentials, whitespace, query strings, fragments, localhost, or invalid ports: `base_url` is the API base URL and `origin` is only the representative mall browser origin.",
            "- Keep service env endpoints and host evidence endpoints separate: Docker containers use `MARQO_URL=http://marqo-api:8882` and `HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098`, while host-run evidence uses `marqo.url=http://127.0.0.1:8882` and `marqo.gemini_embedding_url=http://127.0.0.1:8098` through loopback-only published ports.",
            "- Fill `haeorum-ai-search.env` with production values, including matching `GEMINI_PROXY_API_KEY` and `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`, and keep paths aligned with `operational-evidence.config.json`.",
            "- Expand `malls.json` to 1,700 enabled production malls with real non-sample API keys, allowed origins, and product URL templates.",
            "- Replace `quality-cases.json` with real PoC text, image, and mixed-search cases; include at least two text cases, one image-only case, one mixed case, `expected_min_results >= 3`, and real reference image files for image and mixed cases.",
            "- Set `load.image_file` plus two `load.image_files` entries to deployed real, distinct reference image files; image, mixed, 850-user, and API scale load evidence must not use a generated placeholder or one cached image.",
            "- Review `query-synonyms.json` and add recurring low-confidence or zero-result query terms from search logs.",
            "- Fill `representative-sites.config.json` with real representative mall URLs, origins, API keys, and product URL rules.",
            "- Keep `representative_sites.require_saved_widget_probe_sources=true` while existing-site developer access is unavailable; include PC and mobile saved HTML paths for every representative site so collector preflight can block unsafe selectors or CSP before live evidence runs.",
            "- Set `security.sync_alerting_configured=true` only after `HAEORUM_SYNC_ALERT_WEBHOOK_URL` or external sync failure alerting is actually configured.",
            "- Keep `input_preparation.enabled=true` unless `/data/haeorum-ai-search/products-full.csv` and `/data/haeorum-ai-search/poc-products.csv` are generated externally.",
            "- With `input_preparation.enabled=true`, the collector runs `mssql_export` and `poc_dataset` first and writes `mssql-export.json` and `poc-dataset.json` evidence.",
            "- Keep `marqo.url`, `marqo.gemini_embedding_url`, `marqo.index`, and `marqo.container` aligned with the deployed Marqo/Gemini services, and keep `quality.min_products>=300` with text/image/mixed thresholds at or below the MVP targets.",
            "- Produce API scale reports for 1 API server and 2+ API servers before `load_compare.py` runs, using the same three reference image files in both source reports.",
            "- Run `server_preflight_check.py` on the target Linux host and upgrade or replace the server if `supported_linux_release` fails.",
            "- If `local-acceptance.json` is present, install it to `/var/log/haeorum-ai-search/local-acceptance.json` or rerun `scripts/local_acceptance.py` on the deployed code.",
            "- If `requirements-blockers.md` is present, work through that checklist before the final requirements audit.",
            "- If `missing-evidence.sh` is present, replace placeholders and run it from the deployed Linux project root to produce missing evidence reports. Use `.ps1` only for a local Windows-only operator run.",
            "- Treat `deploy/reference/*` as review copies of container/compose/dependency files. Build and run them from the deployed source root so Dockerfile `COPY` paths resolve.",
            "- Use `tools/widget_integration_probe.py --sites representative-sites.config.json --snippets-output-dir widget-snippets` on saved PC/mobile mall HTML when the existing-site developer is unavailable; proceed only after `data_auto_init_ready=true` and `blocking_risks=[]` for the target pages. The snippet bundle is review-required helper material, not operational readiness evidence.",
            "- Treat `docs/*` as review copies of the operations, integration, requirements traceability, runtime stack, production handoff, go-live scenario, incident runbook, risk register, and server 82 runbook guides.",
            "- Keep the runtime stack as Marqo + Gemini embedding API. The production stack should run only API, Gemini embedding proxy, Marqo API, MIOC, and Vespa; API, Marqo, and Gemini proxy ports may be published only to `127.0.0.1`, never publicly.",
            "- Give `sql/v_ai_search_products_template.sql` to the DBA or existing-site developer as the starting point for the read-only MSSQL View.",
            "",
            "## Install Commands",
            "",
            "```bash",
            *install_commands(files, output_dir),
            "```",
            "",
            "## Evidence Commands",
            "",
            "Run these from the deployed project root after the required edits and installs.",
            "",
            "```bash",
            *next_commands(),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def copy_template_file(source: Path, target: Path, template: dict[str, Any]) -> None:
    if Path(template["target"]).as_posix() == "malls.json":
        target.write_text(json.dumps(production_mall_template(source), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    if Path(template["target"]).as_posix().startswith("docs/") and source.suffix.lower() in {".md", ".html"}:
        copy_text_without_local_paths(source, target)
        return
    shutil.copyfile(source, target)


def production_mall_template(source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    malls = data.get("malls") if isinstance(data, dict) else data
    if not isinstance(malls, list):
        raise ValueError(f"Mall template source must contain a malls list: {source}")
    rewritten = []
    for entry in malls:
        if not isinstance(entry, dict):
            rewritten.append(entry)
            continue
        mall = dict(entry)
        mall_id = str(mall.get("mall_id") or "mall").strip() or "mall"
        api_key = str(mall.get("api_key") or "")
        if is_placeholder_public_api_key(api_key):
            mall["api_key"] = f"replace-with-{mall_id}-public-key"
        origins = mall.get("allowed_origins")
        if isinstance(origins, list):
            production_origins = [
                str(origin).strip()
                for origin in origins
                if str(origin).strip().lower().startswith("https://")
                and "localhost" not in str(origin).strip().lower()
                and "127.0.0.1" not in str(origin).strip().lower()
            ]
            mall["allowed_origins"] = production_origins or [f"https://{mall_id}.haeorumgift.com"]
        rewritten.append(mall)
    return {**data, "malls": rewritten} if isinstance(data, dict) else {"malls": rewritten}


def next_commands() -> list[str]:
    return [
        "python tools/server_db_intake_check.py --intake-file server-db-intake.md --print-summary",
        "python tools/compose_exposure_check.py --print-summary",
        "python tools/go_live_scenario_check.py --print-summary",
        (
            "python scripts/collect_operational_evidence.py "
            "--config /etc/haeorum-ai-search/operational-evidence.config.json "
            "--env-file /etc/haeorum-ai-search/operational-evidence.env "
            "--evidence-dir /var/log/haeorum-ai-search --dry-run "
            "--output /var/log/haeorum-ai-search/evidence-collection-plan.json "
            "--markdown-output /var/log/haeorum-ai-search/evidence-collection-plan.md "
            "--local-acceptance-report /var/log/haeorum-ai-search/local-acceptance.json "
            "--requirements-audit-output /var/log/haeorum-ai-search/requirements-audit.json "
            "--requirements-audit-markdown-output /var/log/haeorum-ai-search/requirements-audit.md "
            "--requirements-blocker-checklist-output /var/log/haeorum-ai-search/requirements-blockers.md"
        ),
        (
            "python scripts/collect_operational_evidence.py "
            "--config /etc/haeorum-ai-search/operational-evidence.config.json "
            "--env-file /etc/haeorum-ai-search/operational-evidence.env "
            "--evidence-dir /var/log/haeorum-ai-search "
            "--output /var/log/haeorum-ai-search/evidence-collection.json "
            "--markdown-output /var/log/haeorum-ai-search/evidence-collection.md "
            "--local-acceptance-report /var/log/haeorum-ai-search/local-acceptance.json "
            "--requirements-audit-output /var/log/haeorum-ai-search/requirements-audit.json "
            "--requirements-audit-markdown-output /var/log/haeorum-ai-search/requirements-audit.md "
            "--requirements-blocker-checklist-output /var/log/haeorum-ai-search/requirements-blockers.md"
        ),
    ]


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def install_commands(files: list[dict[str, Any]], output_dir: Path) -> list[str]:
    commands = ["sudo install -d -m 0750 /etc/haeorum-ai-search /var/log/haeorum-ai-search /data/haeorum-ai-search"]
    seen: set[str] = set()
    for item in files:
        install_path = str(item.get("install_path") or "").strip()
        if not install_path or install_path in seen:
            continue
        target = Path(str(item.get("target", "")))
        try:
            relative_target = target.relative_to(output_dir)
        except ValueError:
            relative_target = target
        mode = str(item.get("mode") or "0644")
        commands.append(f"sudo install -m {mode} {relative_target.as_posix()} {install_path}")
        seen.add(install_path)
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Haeorum AI Search operational config and evidence templates.")
    parser.add_argument("--output-dir", required=True, help="Directory where the operational bundle will be written.")
    parser.add_argument("--force", action="store_true", help="Overwrite files that already exist in the output directory.")
    parser.add_argument("--json-output", default="", help="Optional path for a JSON report.")
    parser.add_argument("--markdown-output", default="", help="Optional path for a Markdown copy of the checklist.")
    parser.add_argument(
        "--blocker-checklist-source",
        default="",
        help="Optional existing requirements-blockers.md file to copy into the bundle.",
    )
    parser.add_argument(
        "--missing-commands-source",
        default="",
        help="Optional missing-evidence.sh file to copy into the Linux deployment bundle; .ps1 is accepted only for local Windows handoff compatibility.",
    )
    parser.add_argument(
        "--local-acceptance-source",
        default="",
        help="Optional local-acceptance.json file to copy into the bundle and install under /var/log.",
    )
    parser.add_argument(
        "--local-acceptance-markdown-source",
        default="",
        help="Optional local-acceptance.md file to copy into the bundle and install under /var/log.",
    )
    parser.add_argument(
        "--server-db-intake-source",
        default="",
        help="Optional server-db-intake-check.json file to copy into the bundle.",
    )
    parser.add_argument(
        "--server-db-intake-markdown-source",
        default="",
        help="Optional server-db-intake-check.md file to copy into the bundle.",
    )
    parser.add_argument(
        "--compose-exposure-source",
        default="",
        help="Optional compose-exposure-check.json file to copy into the bundle.",
    )
    parser.add_argument(
        "--compose-exposure-markdown-source",
        default="",
        help="Optional compose-exposure-check.md file to copy into the bundle.",
    )
    parser.add_argument(
        "--go-live-scenario-source",
        default="",
        help="Optional go-live-scenario-check.json file to copy into the bundle.",
    )
    parser.add_argument(
        "--go-live-scenario-markdown-source",
        default="",
        help="Optional go-live-scenario-check.md file to copy into the bundle.",
    )
    parser.add_argument("--requirements-audit-source", default="", help="Optional requirements-audit.json file to copy into the bundle.")
    parser.add_argument(
        "--requirements-audit-markdown-source",
        default="",
        help="Optional requirements-audit.md file to copy into the bundle.",
    )
    parser.add_argument(
        "--operational-readiness-source",
        default="",
        help="Optional operational-readiness.json file to copy into the bundle.",
    )
    parser.add_argument(
        "--operational-readiness-markdown-source",
        default="",
        help="Optional operational-readiness.md file to copy into the bundle.",
    )
    parser.add_argument(
        "--evidence-collection-source",
        default="",
        help="Optional evidence-collection-plan.json file to copy into the bundle.",
    )
    parser.add_argument(
        "--evidence-collection-markdown-source",
        default="",
        help="Optional evidence-collection-plan.md file to copy into the bundle.",
    )
    args = parser.parse_args()

    report = build_bundle(
        args.output_dir,
        force=args.force,
        blocker_checklist_source=args.blocker_checklist_source or None,
        missing_commands_source=args.missing_commands_source or None,
        local_acceptance_source=args.local_acceptance_source or None,
        local_acceptance_markdown_source=args.local_acceptance_markdown_source or None,
        server_db_intake_source=args.server_db_intake_source or None,
        server_db_intake_markdown_source=args.server_db_intake_markdown_source or None,
        compose_exposure_source=args.compose_exposure_source or None,
        compose_exposure_markdown_source=args.compose_exposure_markdown_source or None,
        go_live_scenario_source=args.go_live_scenario_source or None,
        go_live_scenario_markdown_source=args.go_live_scenario_markdown_source or None,
        requirements_audit_source=args.requirements_audit_source or None,
        requirements_audit_markdown_source=args.requirements_audit_markdown_source or None,
        operational_readiness_source=args.operational_readiness_source or None,
        operational_readiness_markdown_source=args.operational_readiness_markdown_source or None,
        evidence_collection_source=args.evidence_collection_source or None,
        evidence_collection_markdown_source=args.evidence_collection_markdown_source or None,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(Path(report["checklist"]).read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
