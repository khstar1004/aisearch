from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
CommandRunner = Callable[[list[str], int], dict[str, Any]]
REPO_RELATIVE_PREFIX = ("examples", "HaeorumAISearch")
PROJECT_ROOT_DISPLAY = "examples/HaeorumAISearch"
DEFAULT_MIN_PRODUCTS = 300
DEFAULT_MAX_TEXT_MS = 3000
DEFAULT_MAX_IMAGE_MS = 5000
DEFAULT_MAX_MIXED_MS = 5000


def path_arg(path: str | Path) -> str:
    return str(normalize_user_path(path))


def project_path(path: str | Path) -> Path:
    value = normalize_user_path(path)
    return value if value.is_absolute() else ROOT / value


def normalize_user_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    parts = value.parts
    if len(parts) >= len(REPO_RELATIVE_PREFIX) and parts[: len(REPO_RELATIVE_PREFIX)] == REPO_RELATIVE_PREFIX:
        stripped = parts[len(REPO_RELATIVE_PREFIX) :]
        return Path(*stripped) if stripped else Path(".")
    return value


def evidence_path(args: argparse.Namespace, filename: str) -> str:
    return path_arg(normalize_user_path(args.evidence_dir) / filename)


def local_quality_thresholds(args: argparse.Namespace) -> dict[str, int]:
    return {
        "min_products": int(getattr(args, "min_products", DEFAULT_MIN_PRODUCTS)),
        "max_text_ms": int(getattr(args, "max_text_ms", DEFAULT_MAX_TEXT_MS)),
        "max_image_ms": int(getattr(args, "max_image_ms", DEFAULT_MAX_IMAGE_MS)),
        "max_mixed_ms": int(getattr(args, "max_mixed_ms", DEFAULT_MAX_MIXED_MS)),
    }


def build_command_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    python = sys.executable
    thresholds = local_quality_thresholds(args)
    local_acceptance_command = [
        python,
        "scripts/local_acceptance.py",
        "--min-products",
        str(thresholds["min_products"]),
        "--max-text-ms",
        str(thresholds["max_text_ms"]),
        "--max-image-ms",
        str(thresholds["max_image_ms"]),
        "--max-mixed-ms",
        str(thresholds["max_mixed_ms"]),
        "--output",
        evidence_path(args, "local-acceptance.json"),
        "--markdown-output",
        evidence_path(args, "local-acceptance.md"),
    ]
    if args.skip_unit_tests:
        local_acceptance_command.append("--skip-unit-tests")
    if args.skip_node:
        local_acceptance_command.append("--skip-node")
    go_live_scenario_command = [
        python,
        "scripts/go_live_scenario_check.py",
        "--output",
        evidence_path(args, "go-live-scenario-check.json"),
        "--markdown-output",
        evidence_path(args, "go-live-scenario-check.md"),
        "--print-summary",
    ]
    runtime_base_url = getattr(args, "runtime_base_url", "")
    runtime_admin_key_env = getattr(args, "runtime_admin_key_env", "")
    runtime_timeout = getattr(args, "runtime_timeout", 8.0)
    if runtime_base_url:
        go_live_scenario_command.extend(["--base-url", runtime_base_url])
    if runtime_admin_key_env:
        go_live_scenario_command.extend(["--admin-key-env", runtime_admin_key_env])
    if runtime_timeout:
        go_live_scenario_command.extend(["--runtime-timeout", str(runtime_timeout)])

    return [
        {
            "name": "local_acceptance",
            "command": local_acceptance_command,
            "timeout": args.local_timeout,
            "allowed_exit_codes": [0],
            "description": "Regenerate local-only acceptance evidence from the current source tree.",
        },
        {
            "name": "local_quality_report",
            "command": [
                python,
                "scripts/quality_report.py",
                "--csv",
                "sample_products.csv",
                "--strict",
                "--min-products",
                str(thresholds["min_products"]),
                "--max-text-ms",
                str(thresholds["max_text_ms"]),
                "--max-image-ms",
                str(thresholds["max_image_ms"]),
                "--max-mixed-ms",
                str(thresholds["max_mixed_ms"]),
                "--json-output",
                evidence_path(args, "quality-report.json"),
                "--markdown-output",
                evidence_path(args, "quality-report.md"),
            ],
            "timeout": args.local_timeout,
            "allowed_exit_codes": [0],
            "description": "Refresh local-only quality evidence included in the handoff so blocker details match the current source tree.",
        },
        *(
            []
            if args.skip_node
            else [
                {
                    "name": "local_widget_dom_report",
                    "command": [
                        "node",
                        "scripts/widget_dom_check.js",
                        "--output",
                        evidence_path(args, "widget-dom.json"),
                    ],
                    "timeout": args.local_timeout,
                    "allowed_exit_codes": [0],
                    "description": "Refresh local-only widget DOM evidence included in local acceptance and handoff from the current widget source.",
                }
            ]
        ),
        {
            "name": "local_csv_index_report",
            "command": [
                python,
                "scripts/csv_index.py",
                "--csv",
                "sample_products.csv",
                "--engine",
                "local",
                "--dry-run",
                "--output",
                evidence_path(args, "csv-index.json"),
                "--markdown-output",
                evidence_path(args, "csv-index.md"),
            ],
            "timeout": args.local_timeout,
            "allowed_exit_codes": [0],
            "description": "Refresh local-only CSV index dry-run evidence included in the handoff from the current sample CSV.",
        },
        {
            "name": "server_db_intake_template",
            "command": [
                python,
                "scripts/server_db_intake_check.py",
                "--template-ok",
                "--output",
                evidence_path(args, "server-db-intake-check.json"),
                "--markdown-output",
                evidence_path(args, "server-db-intake-check.md"),
                "--print-summary",
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0],
            "description": "Validate the server/DB intake template shape before the existing developer fills final production inputs.",
        },
        {
            "name": "compose_exposure_check",
            "command": [
                python,
                "scripts/compose_exposure_check.py",
                "--output",
                evidence_path(args, "compose-exposure-check.json"),
                "--markdown-output",
                evidence_path(args, "compose-exposure-check.md"),
                "--print-summary",
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0],
            "description": "Render production Docker Compose and fail if AI API, Marqo, or embedding ports are publicly bound.",
        },
        {
            "name": "go_live_scenario_check",
            "command": go_live_scenario_command,
            "timeout": args.timeout,
            "allowed_exit_codes": [0],
            "description": "Check that common go-live failure scenario controls are still present in code and deployment files.",
        },
        {
            "name": "operational_simulation",
            "command": [
                python,
                "-X",
                "faulthandler",
                "scripts/operational_simulation.py",
                "--output-dir",
                evidence_path(args, "simulation"),
            ],
            "timeout": args.local_timeout,
            "allowed_exit_codes": [0],
            "description": "Regenerate simulated operational rehearsal evidence, including MSSQL shape, risk probes, search insights, and sync lifecycle checks.",
        },
        {
            "name": "evidence_collection_dry_run",
            "command": [
                python,
                "scripts/collect_operational_evidence.py",
                "--config",
                "contracts/operational_evidence.config.example.json",
                "--evidence-dir",
                path_arg(args.evidence_dir),
                "--env-file",
                "contracts/operational_evidence.env.example",
                "--dry-run",
                "--output",
                evidence_path(args, "evidence-collection-plan.json"),
                "--markdown-output",
                evidence_path(args, "evidence-collection-plan.md"),
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0, 1],
            "description": "Generate the operational evidence dry-run plan; exit 1 is expected while production inputs are missing.",
        },
        {
            "name": "operational_readiness",
            "command": [
                python,
                "scripts/operational_readiness.py",
                "--evidence-dir",
                path_arg(args.evidence_dir),
                "--expected-malls",
                str(args.expected_malls),
                "--required-sites",
                str(args.required_sites),
                "--output",
                evidence_path(args, "operational-readiness.json"),
                "--markdown-output",
                evidence_path(args, "operational-readiness.md"),
                "--missing-commands-output",
                evidence_path(args, "missing-evidence.sh"),
                "--missing-commands-shell",
                "bash",
                "--missing-commands-project-root",
                args.deployment_project_root,
                "--missing-commands-evidence-dir",
                args.deployment_evidence_dir,
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0, 1],
            "description": "Rebuild readiness and the Linux missing-evidence command script.",
        },
        {
            "name": "requirements_audit",
            "command": [
                python,
                "scripts/requirements_audit.py",
                "--local-acceptance-report",
                evidence_path(args, "local-acceptance.json"),
                "--operational-readiness-report",
                evidence_path(args, "operational-readiness.json"),
                "--evidence-collection-report",
                evidence_path(args, "evidence-collection-plan.json"),
                "--output",
                evidence_path(args, "requirements-audit.json"),
                "--markdown-output",
                evidence_path(args, "requirements-audit.md"),
                "--blocker-checklist-output",
                evidence_path(args, "requirements-blockers.md"),
                "--blocker-checklist-project-root",
                args.deployment_project_root,
                "--blocker-checklist-evidence-dir",
                args.deployment_evidence_dir,
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0, 1],
            "description": "Map local and operational evidence to the original requirements.",
        },
        {
            "name": "prepare_operational_bundle",
            "command": [
                python,
                "scripts/prepare_operational_bundle.py",
                "--output-dir",
                path_arg(args.bundle_dir),
                "--force",
                "--local-acceptance-source",
                evidence_path(args, "local-acceptance.json"),
                "--local-acceptance-markdown-source",
                evidence_path(args, "local-acceptance.md"),
                "--server-db-intake-source",
                evidence_path(args, "server-db-intake-check.json"),
                "--server-db-intake-markdown-source",
                evidence_path(args, "server-db-intake-check.md"),
                "--compose-exposure-source",
                evidence_path(args, "compose-exposure-check.json"),
                "--compose-exposure-markdown-source",
                evidence_path(args, "compose-exposure-check.md"),
                "--go-live-scenario-source",
                evidence_path(args, "go-live-scenario-check.json"),
                "--go-live-scenario-markdown-source",
                evidence_path(args, "go-live-scenario-check.md"),
                "--requirements-audit-source",
                evidence_path(args, "requirements-audit.json"),
                "--requirements-audit-markdown-source",
                evidence_path(args, "requirements-audit.md"),
                "--operational-readiness-source",
                evidence_path(args, "operational-readiness.json"),
                "--operational-readiness-markdown-source",
                evidence_path(args, "operational-readiness.md"),
                "--evidence-collection-source",
                evidence_path(args, "evidence-collection-plan.json"),
                "--evidence-collection-markdown-source",
                evidence_path(args, "evidence-collection-plan.md"),
                "--blocker-checklist-source",
                evidence_path(args, "requirements-blockers.md"),
                "--missing-commands-source",
                evidence_path(args, "missing-evidence.sh"),
                "--json-output",
                evidence_path(args, "operational-bundle-prepare.json"),
                "--markdown-output",
                evidence_path(args, "operational-bundle-prepare.md"),
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0],
            "description": "Create the operator handoff bundle with current local and operational blocker reports.",
        },
        {
            "name": "operational_bundle_check",
            "command": [
                python,
                "scripts/operational_bundle_check.py",
                "--bundle-dir",
                path_arg(args.bundle_dir),
                "--output",
                evidence_path(args, "operational-bundle-check.json"),
                "--markdown-output",
                evidence_path(args, "operational-bundle-check.md"),
            ],
            "timeout": args.timeout,
            "allowed_exit_codes": [0],
            "description": "Validate the generated bundle before handoff.",
        },
    ]


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "exit_code": None,
            "elapsed_ms": elapsed_ms(started),
            "error": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "elapsed_ms": elapsed_ms(started),
            "error": f"command timed out after {timeout}s",
            "stdout_tail": tail_text(exc.stdout),
            "stderr_tail": tail_text(exc.stderr),
        }
    return {
        "exit_code": completed.returncode,
        "elapsed_ms": elapsed_ms(started),
        "stdout_tail": tail_text(completed.stdout),
        "stderr_tail": tail_text(completed.stderr),
    }


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def tail_text(value: Any, max_chars: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def run_plan(plan: list[dict[str, Any]], command_runner: CommandRunner = run_command) -> list[dict[str, Any]]:
    results = []
    for item in plan:
        command_result = command_runner(item["command"], int(item["timeout"]))
        exit_code = command_result.get("exit_code")
        expected = exit_code in item["allowed_exit_codes"]
        result = compact_command_result(command_result, include_output=bool(item.get("include_output")) or not expected)
        results.append(
            {
                "name": item["name"],
                "ok": expected,
                "expected_exit": expected,
                "allowed_exit_codes": item["allowed_exit_codes"],
                "command": display_command(item["command"]),
                "description": item["description"],
                **result,
            }
        )
    return results


def compact_command_result(command_result: dict[str, Any], include_output: bool = False) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in command_result.items()
        if key not in {"stdout_tail", "stderr_tail"}
    }
    if include_output:
        stdout_tail = sanitize_report_text(command_result.get("stdout_tail"))
        stderr_tail = sanitize_report_text(command_result.get("stderr_tail"))
        if stdout_tail:
            compact["stdout_tail"] = tail_text(stdout_tail, max_chars=1200)
        if stderr_tail:
            compact["stderr_tail"] = tail_text(stderr_tail, max_chars=1200)
    return compact


def sanitize_report_text(value: Any) -> str:
    text = str(value or "")
    replacements = {
        str(ROOT): PROJECT_ROOT_DISPLAY,
        str(ROOT).replace("\\", "/"): PROJECT_ROOT_DISPLAY,
        str(sys.executable): "python",
        str(sys.executable).replace("\\", "/"): "python",
    }
    for needle, replacement in replacements.items():
        if needle:
            text = text.replace(needle, replacement)
    return text


def display_command(command: list[str]) -> str:
    parts = []
    for index, part in enumerate(command):
        if index == 0 and Path(part) == Path(sys.executable):
            parts.append("python")
        else:
            parts.append(part)
    return " ".join(parts)


def read_json(path: str | Path) -> dict[str, Any]:
    report_path = ROOT / Path(path) if not Path(path).is_absolute() else Path(path)
    if not report_path.exists():
        return {"present": False, "ok": None}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"present": True, "ok": False, "parse_error": str(exc)}
    if not isinstance(data, dict):
        return {"present": True, "ok": False, "parse_error": "JSON root must be an object"}
    data["present"] = True
    return data


def status_counts(report: dict[str, Any]) -> dict[str, Any]:
    counts = report.get("status_counts")
    return counts if isinstance(counts, dict) else {}


def sanitize_public_provider_terms(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_public_provider_terms(str(key)): sanitize_public_provider_terms(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_public_provider_terms(item) for item in value]
    if not isinstance(value, str):
        return value
    replacements = [
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
    sanitized = value
    for source, replacement in replacements:
        sanitized = sanitized.replace(source, replacement)
    return sanitized


def build_report(args: argparse.Namespace, command_runner: CommandRunner = run_command) -> dict[str, Any]:
    project_path(args.evidence_dir).mkdir(parents=True, exist_ok=True)
    plan = build_command_plan(args)
    for step in plan:
        step["include_output"] = bool(getattr(args, "include_command_output", False))
    steps = run_plan(plan, command_runner)

    local_acceptance = read_json(evidence_path(args, "local-acceptance.json"))
    operational_simulation = read_json(evidence_path(args, "simulation/operational-simulation.json"))
    sync_lifecycle = read_json(evidence_path(args, "simulation/sync-lifecycle.json"))
    server_db_intake = read_json(evidence_path(args, "server-db-intake-check.json"))
    compose_exposure = read_json(evidence_path(args, "compose-exposure-check.json"))
    go_live_scenarios = read_json(evidence_path(args, "go-live-scenario-check.json"))
    evidence_plan = read_json(evidence_path(args, "evidence-collection-plan.json"))
    readiness = read_json(evidence_path(args, "operational-readiness.json"))
    audit = read_json(evidence_path(args, "requirements-audit.json"))
    bundle_check = read_json(evidence_path(args, "operational-bundle-check.json"))

    command_status_ok = all(step["ok"] is True for step in steps)
    handoff_ok = (
        command_status_ok
        and local_acceptance.get("ok") is True
        and operational_simulation.get("ok") is True
        and sync_lifecycle.get("ok") is True
        and go_live_scenarios.get("ok") is True
        and bundle_check.get("ok") is True
    )
    operational_signoff_ok = (
        evidence_plan.get("evidence_complete") is True
        and readiness.get("ok") is True
        and audit.get("ok") is True
    )

    report = {
        "ok": handoff_ok,
        "handoff_ok": handoff_ok,
        "operational_signoff_ok": operational_signoff_ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": PROJECT_ROOT_DISPLAY,
        "evidence_dir": str(normalize_user_path(args.evidence_dir)),
        "bundle_dir": str(normalize_user_path(args.bundle_dir)),
        "steps": steps,
        "summary": {
            "commands_expected": command_status_ok,
            "local_acceptance_ok": local_acceptance.get("ok"),
            "local_acceptance_source_fingerprint": local_acceptance.get("source_fingerprint"),
            "operational_simulation_ok": operational_simulation.get("ok"),
            "operational_simulation_checks": operational_simulation.get("checks"),
            "sync_lifecycle_ok": sync_lifecycle.get("ok"),
            "sync_lifecycle_checks": sync_lifecycle.get("checks"),
            "server_db_intake_template_ok": server_db_intake.get("ok"),
            "server_db_intake_status": server_db_intake.get("status"),
            "compose_exposure_ok": compose_exposure.get("ok"),
            "compose_exposure_summary": compose_exposure.get("summary"),
            "go_live_scenario_ok": go_live_scenarios.get("ok"),
            "go_live_scenario_summary": go_live_scenarios.get("summary"),
            "evidence_collection_ok": evidence_plan.get("ok"),
            "evidence_collection_ready_to_execute": evidence_plan.get("ready_to_execute"),
            "evidence_collection_complete": evidence_plan.get("evidence_complete"),
            "evidence_collection_status_counts": status_counts(evidence_plan),
            "operational_readiness_ok": readiness.get("ok"),
            "operational_readiness_status_counts": status_counts(readiness),
            "requirements_audit_ok": audit.get("ok"),
            "requirements_audit_status_counts": status_counts(audit),
            "requirements_audit_summary": audit.get("summary"),
            "local_acceptance_gate": audit.get("local_acceptance_gate"),
            "bundle_check_ok": bundle_check.get("ok"),
            "bundle_check_summary": bundle_check.get("summary"),
        },
        "next_action": next_action(evidence_plan, readiness, audit),
    }
    return sanitize_public_provider_terms(report)


def next_action(evidence_plan: dict[str, Any], readiness: dict[str, Any], audit: dict[str, Any]) -> str:
    if audit.get("ok") is True:
        return "Operational requirements audit is green; review the bundle and proceed with signoff."
    blocking_inputs = evidence_plan.get("blocking_inputs")
    if isinstance(blocking_inputs, dict) and blocking_inputs:
        return "Resolve blocking_inputs in evidence-collection-plan.json, then rerun this script."
    if readiness.get("ok") is not True:
        return "Generate missing operational evidence reports listed in missing-evidence.sh, then rerun this script."
    return "Rerun requirements audit after refreshing operational readiness evidence."


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Handoff Report",
        "",
        f"- Handoff OK: `{report.get('handoff_ok')}`",
        f"- Operational signoff OK: `{report.get('operational_signoff_ok')}`",
        f"- Evidence dir: `{report.get('evidence_dir')}`",
        f"- Bundle dir: `{report.get('bundle_dir')}`",
        f"- Next action: {report.get('next_action')}",
        "",
        "## Command Status",
        "",
        "| Step | OK | Exit | Allowed |",
        "| --- | --- | --- | --- |",
    ]
    for step in report.get("steps", []):
        lines.append(
            f"| `{step.get('name')}` | `{step.get('ok')}` | `{step.get('exit_code')}` | `{step.get('allowed_exit_codes')}` |"
        )
    summary = report.get("summary") or {}
    lines.extend(
        [
            "",
            "## Evidence Summary",
            "",
            f"- Local acceptance OK: `{summary.get('local_acceptance_ok')}`",
            f"- Operational simulation OK: `{summary.get('operational_simulation_ok')}`",
            f"- Sync lifecycle OK: `{summary.get('sync_lifecycle_ok')}`",
            f"- Server/DB intake template OK: `{summary.get('server_db_intake_template_ok')}`",
            f"- Server/DB intake status: `{summary.get('server_db_intake_status')}`",
            f"- Compose exposure OK: `{summary.get('compose_exposure_ok')}`",
            f"- Go-live scenario OK: `{summary.get('go_live_scenario_ok')}`",
            f"- Evidence collection complete: `{summary.get('evidence_collection_complete')}`",
            f"- Evidence collection status counts: `{summary.get('evidence_collection_status_counts')}`",
            f"- Operational readiness OK: `{summary.get('operational_readiness_ok')}`",
            f"- Operational readiness status counts: `{summary.get('operational_readiness_status_counts')}`",
            f"- Requirements audit OK: `{summary.get('requirements_audit_ok')}`",
            f"- Requirements audit status counts: `{summary.get('requirements_audit_status_counts')}`",
            f"- Requirements completion ready: `{(summary.get('requirements_audit_summary') or {}).get('completion_ready')}`",
            f"- Bundle check OK: `{summary.get('bundle_check_ok')}`",
        ]
    )
    local_gate = summary.get("local_acceptance_gate")
    if isinstance(local_gate, dict):
        lines.extend(
            [
                "",
                "## Local Acceptance Gate",
                "",
                f"- OK: `{local_gate.get('ok')}`",
                f"- Source fingerprint match: `{local_gate.get('source_fingerprint_match')}`",
                f"- Digest: `{local_gate.get('source_fingerprint_digest')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate local acceptance, operational blocker reports, and the handoff bundle."
    )
    parser.add_argument("--evidence-dir", default="logs")
    parser.add_argument("--bundle-dir", default="logs/operational-bundle")
    parser.add_argument("--deployment-project-root", default="/opt/haeorum-ai-search")
    parser.add_argument("--deployment-evidence-dir", default="/var/log/haeorum-ai-search")
    parser.add_argument("--expected-malls", type=int, default=1700)
    parser.add_argument("--required-sites", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--local-timeout", type=int, default=180)
    parser.add_argument("--min-products", type=int, default=DEFAULT_MIN_PRODUCTS)
    parser.add_argument("--max-text-ms", type=int, default=DEFAULT_MAX_TEXT_MS)
    parser.add_argument("--max-image-ms", type=int, default=DEFAULT_MAX_IMAGE_MS)
    parser.add_argument("--max-mixed-ms", type=int, default=DEFAULT_MAX_MIXED_MS)
    parser.add_argument("--runtime-base-url", default="", help="Optional running API base URL for go-live runtime checks.")
    parser.add_argument(
        "--runtime-admin-key-env",
        default="",
        help="Environment variable name containing the admin key for go-live runtime checks.",
    )
    parser.add_argument("--runtime-timeout", type=float, default=8.0)
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument("--skip-node", action="store_true")
    parser.add_argument("--include-command-output", action="store_true")
    parser.add_argument("--output", default="logs/handoff-report.json")
    parser.add_argument("--markdown-output", default="logs/handoff-report.md")
    args = parser.parse_args()

    report = build_report(args)
    if args.output:
        output = project_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown_output:
        markdown_output = project_path(args.markdown_output)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["handoff_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
