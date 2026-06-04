from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
CommandRunner = Callable[[list[str], int], dict[str, Any]]
SOURCE_FINGERPRINT_PATTERNS = (
    "app/**/*.py",
    "scripts/**/*.py",
    "tests/**/*.py",
    "widget/**/*.js",
    "contracts/**/*.json",
    "contracts/**/*.html",
    "contracts/**/*.env.example",
    "deploy/**/*",
    "sql/**/*.sql",
    ".env.example",
    "Dockerfile",
    "requirements*.txt",
    "compose-haeorum*.yaml",
    "sample_products.csv",
    "sample_*.json",
    "README.md",
    "OPERATIONS.md",
    "INTEGRATION.md",
    "REQUIREMENTS_TRACE.md",
    ".gitignore",
)


def build_command_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    python = sys.executable
    checks = [
        {
            "name": "contract_check",
            "command": [python, "scripts/contract_check.py"],
            "required": True,
        },
        {
            "name": "unit_tests",
            "command": [python, "-m", "unittest", "discover", "tests"],
            "required": not args.skip_unit_tests,
        },
        {
            "name": "acceptance_check",
            "command": [python, "scripts/acceptance_check.py"],
            "required": True,
        },
        {
            "name": "quality_report",
            "command": [
                python,
                "scripts/quality_report.py",
                "--csv",
                "sample_products.csv",
                "--strict",
                "--min-products",
                str(args.min_products),
                "--max-text-ms",
                str(args.max_text_ms),
                "--max-image-ms",
                str(args.max_image_ms),
                "--max-mixed-ms",
                str(args.max_mixed_ms),
            ],
            "required": True,
        },
        {
            "name": "csv_index_dry_run",
            "command": [python, "scripts/csv_index.py", "--csv", "sample_products.csv", "--engine", "local", "--dry-run"],
            "required": True,
        },
        {
            "name": "operational_bundle_check",
            "command": [python, "scripts/operational_bundle_check.py"],
            "required": True,
        },
        {
            "name": "widget_dom_check",
            "command": ["node", "scripts/widget_dom_check.js"],
            "required": not args.skip_node,
        },
        {
            "name": "widget_js_syntax",
            "command": ["node", "--check", "widget/widget.js"],
            "required": not args.skip_node,
        },
        {
            "name": "widget_dom_check_syntax",
            "command": ["node", "--check", "scripts/widget_dom_check.js"],
            "required": not args.skip_node,
        },
    ]
    return [check for check in checks if check["required"]]


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
            "ok": False,
            "exit_code": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": f"command timed out after {timeout}s",
            "stdout_tail": tail_text(exc.stdout),
            "stderr_tail": tail_text(exc.stderr),
        }
    result = {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "stdout_tail": tail_text(completed.stdout),
        "stderr_tail": tail_text(completed.stderr),
    }
    result.update(extract_json_summary(completed.stdout))
    return result


def tail_text(value: Any, max_chars: int = 6000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def extract_json_summary(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in [
        "ok",
        "quality_ok",
        "response_time_ok",
        "dataset_ready",
        "dry_run",
        "engine",
        "persistent_index",
        "indexed",
        "deleted",
        "failed",
        "would_index",
        "would_delete",
    ]:
        if key in data:
            summary[key] = data[key]
    checks = data.get("checks")
    if isinstance(checks, list):
        summary["check_count"] = len(checks)
        summary["check_names"] = [
            str(check.get("name") or index)
            for index, check in enumerate(checks)
            if isinstance(check, dict)
        ]
        summary["failed_checks"] = [
            str(check.get("name") or index)
            for index, check in enumerate(checks)
            if isinstance(check, dict) and check.get("ok") is not True
        ]
    dataset = data.get("dataset") or data.get("summary")
    if isinstance(dataset, dict):
        summary["dataset"] = {
            key: dataset.get(key)
            for key in [
                "total_products",
                "active_products",
                "inactive_products",
                "category_count",
                "missing_active_image_count",
                "missing_image_url_count",
            ]
            if key in dataset
        }
    response_time = data.get("response_time")
    if isinstance(response_time, dict):
        summary["response_time_ok"] = response_time.get("ok")
        summary["response_time_by_query_type"] = response_time.get("by_query_type")
    checked_sites = data.get("checked_sites")
    if isinstance(checked_sites, list):
        summary["checked_site_count"] = len(checked_sites)
    return {"stdout_json_summary": summary} if summary else {}


def build_report(args: argparse.Namespace, command_runner: CommandRunner = run_command) -> dict[str, Any]:
    checks = []
    for check in build_command_plan(args):
        result = command_runner(check["command"], args.timeout)
        display_result = sanitize_display_value(result)
        checks.append(
            {
                "name": check["name"],
                "ok": result.get("ok") is True,
                "command": display_command_to_string(check["command"]),
                **display_result,
            }
        )
    failed = [check["name"] for check in checks if check.get("ok") is not True]
    return {
        "ok": not failed,
        "local_only": True,
        "not_operational_readiness": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": display_project_root(),
        "source_fingerprint": build_source_fingerprint(),
        "failed_checks": failed,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "skipped_unit_tests": bool(args.skip_unit_tests),
            "skipped_node": bool(args.skip_node),
        },
        "checks": checks,
    }


def build_source_fingerprint(root: Path = ROOT) -> dict[str, Any]:
    files = source_fingerprint_files(root)
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
        digest.update(b"\0")
    return {
        "algorithm": "sha256",
        "file_count": len(files),
        "digest": digest.hexdigest(),
    }


def source_fingerprint_files(root: Path = ROOT) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern in SOURCE_FINGERPRINT_PATTERNS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            files[path.relative_to(root).as_posix()] = path
    return [files[key] for key in sorted(files)]


def command_to_string(command: list[str]) -> str:
    return " ".join(quote_command_part(part) for part in command)


def display_command_to_string(command: list[str]) -> str:
    display_command = list(command)
    if display_command and str(display_command[0]) == sys.executable:
        display_command[0] = "python"
    return command_to_string(display_command)


def display_project_root() -> str:
    if len(ROOT.parts) >= 2:
        return "/".join(ROOT.parts[-2:])
    return ROOT.name


def sanitize_display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_display_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_display_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_display_text(value)
    return value


def sanitize_display_text(value: str) -> str:
    display_root = display_project_root()
    text = normalize_python_invocation(value)
    replacements = {
        str(ROOT): display_root,
        ROOT.as_posix(): display_root,
        str(ROOT).replace("\\", "\\\\"): display_root,
        sys.executable: "python",
        sys.executable.replace("\\", "\\\\"): "python",
    }
    for old, new in replacements.items():
        if old:
            text = text.replace(old, new)
    text = text.replace(display_root + "\\\\", display_root + "/")
    text = text.replace(display_root + "\\", display_root + "/")
    return text


def normalize_python_invocation(text: str) -> str:
    return re.sub(r"[A-Za-z]:\\[^\r\n\"']*?python(?:\.exe)?(?=\s+(?:-m|scripts[\\/]))", "python", text)


def quote_command_part(value: Any) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Haeorum AI Search Local Acceptance Report",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Local only: `{report.get('local_only')}`",
        f"- Not operational readiness: `{report.get('not_operational_readiness')}`",
        f"- Project root: `{report.get('project_root')}`",
        "",
        "| Check | OK | Exit | ms | Command |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for check in report.get("checks", []):
        lines.append(
            "| {name} | `{ok}` | `{exit_code}` | `{elapsed_ms}` | `{command}` |".format(
                name=escape_markdown_cell(check.get("name")),
                ok=check.get("ok"),
                exit_code="" if check.get("exit_code") is None else check.get("exit_code"),
                elapsed_ms=check.get("elapsed_ms"),
                command=escape_markdown_cell(check.get("command")),
            )
        )
    failed = report.get("failed_checks") or []
    if failed:
        lines.extend(["", "## Failed Checks", ""])
        for name in failed:
            lines.append(f"- `{name}`")
    return "\n".join(lines) + "\n"


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Haeorum AI Search acceptance checks and write evidence.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--min-products", type=int, default=300)
    parser.add_argument("--max-text-ms", type=int, default=3000)
    parser.add_argument("--max-image-ms", type=int, default=5000)
    parser.add_argument("--max-mixed-ms", type=int, default=5000)
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument("--skip-node", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
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
