from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILES = (
    "compose-haeorum-marqo.yaml",
    "compose-haeorum-gemini.yaml",
    "compose-haeorum-existing-8gb.yaml",
)
PROTECTED_SERVICE_PORTS = {
    "ai-search": {8000},
    "marqo-api": {8882},
    "embedding-service": {8098},
}
SAFE_HOST_IPS = {"127.0.0.1", "::1", "localhost"}
UNSAFE_HOST_IPS = {"", "0.0.0.0", "::"}


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
            f"compose exposure check: ok={report['ok']} "
            f"published={report['summary']['published_protected_ports']} "
            f"unsafe={report['summary']['unsafe_public_bindings']}"
        )
    return 0 if report["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify production Docker Compose does not publish AI ports publicly.")
    parser.add_argument(
        "-f",
        "--compose-file",
        action="append",
        default=[],
        help="Compose file to include. Defaults to the production Marqo+Gemini+8GB stack.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    compose_files = resolve_compose_files(args.compose_file or DEFAULT_COMPOSE_FILES)
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.extend(["config", "--format", "json"])

    result = run_command(command)
    checks: list[dict[str, Any]] = [
        {
            "name": "compose_config_command",
            "ok": result["exit_code"] == 0,
            "details": {
                "command": " ".join(command),
                "exit_code": result["exit_code"],
                "stderr_tail": result["stderr_tail"],
            },
        }
    ]
    config: dict[str, Any] | None = None
    if result["exit_code"] == 0:
        try:
            config = json.loads(result["stdout"].lstrip("\ufeff"))
            checks.extend(check_config(config))
        except json.JSONDecodeError as exc:
            checks.append({"name": "compose_config_json", "ok": False, "details": {"error": str(exc)}})

    failed = [check["name"] for check in checks if check.get("ok") is not True]
    protected_ports = collect_protected_ports(config or {})
    unsafe_bindings = [item for item in protected_ports if item.get("unsafe")]
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "compose_files": [str(path) for path in compose_files],
        "failed_checks": failed,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "published_protected_ports": len(protected_ports),
            "unsafe_public_bindings": len(unsafe_bindings),
        },
        "protected_ports": protected_ports,
        "checks": checks,
    }


def resolve_compose_files(values: list[str] | tuple[str, ...]) -> list[Path]:
    resolved: list[Path] = []
    for value in values:
        path = Path(value)
        if path.exists() or path.is_absolute():
            resolved.append(path)
            continue
        reference_path = Path("deploy") / "reference" / path.name
        resolved.append(reference_path if reference_path.exists() else path)
    return resolved


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr_tail": completed.stderr.strip()[-2000:],
    }


def check_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    protected_ports = collect_protected_ports(config)
    unsafe = [item for item in protected_ports if item.get("unsafe")]
    missing_expected = []
    by_service = {item["service"]: [] for item in protected_ports}
    for item in protected_ports:
        by_service.setdefault(item["service"], []).append(item)
    for service, targets in PROTECTED_SERVICE_PORTS.items():
        service_targets = {int(item["target"]) for item in by_service.get(service, [])}
        for target in targets:
            if target not in service_targets:
                missing_expected.append({"service": service, "target": target})
    embedding_public = by_service.get("embedding-service", [])
    embedding_unsafe = [item for item in embedding_public if item.get("unsafe")]
    return [
        {
            "name": "protected_ports_loopback_only",
            "ok": not unsafe,
            "details": {"unsafe": unsafe, "protected_ports": protected_ports},
        },
        {
            "name": "expected_loopback_ports_present",
            "ok": not missing_expected,
            "details": {"missing": missing_expected},
        },
        {
            "name": "embedding_proxy_loopback_only",
            "ok": bool(embedding_public) and not embedding_unsafe,
            "details": {"published": embedding_public, "unsafe": embedding_unsafe},
        },
    ]


def collect_protected_ports(config: dict[str, Any]) -> list[dict[str, Any]]:
    services = config.get("services") if isinstance(config, dict) else {}
    if not isinstance(services, dict):
        return []
    protected: list[dict[str, Any]] = []
    for service, targets in PROTECTED_SERVICE_PORTS.items():
        service_config = services.get(service)
        if not isinstance(service_config, dict):
            continue
        for port in service_config.get("ports") or []:
            if not isinstance(port, dict):
                continue
            target = int(port.get("target") or 0)
            if target not in targets:
                continue
            host_ip = str(port.get("host_ip") or "").strip()
            unsafe = host_ip in UNSAFE_HOST_IPS or host_ip not in SAFE_HOST_IPS
            protected.append(
                {
                    "service": service,
                    "target": target,
                    "published": str(port.get("published") or ""),
                    "host_ip": host_ip,
                    "unsafe": unsafe,
                }
            )
    return protected


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Compose Exposure Check",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Compose files: `{', '.join(report.get('compose_files') or [])}`",
        f"- Published protected ports: `{(report.get('summary') or {}).get('published_protected_ports')}`",
        f"- Unsafe public bindings: `{(report.get('summary') or {}).get('unsafe_public_bindings')}`",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks", []):
        lines.append(f"- {'PASS' if check.get('ok') else 'FAIL'}: `{check.get('name')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
