from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTAKE = ROOT / "deploy" / "server-db-intake.md"

PLACEHOLDER_RE = re.compile(r"^\s*(?:$|[-_ ]*|yes/no|api key or adc|n/?a|tbd|todo|unknown|replace-with.*|<.*>|\.\.\.)\s*$", re.I)
SECRET_RE = re.compile(
    r"(?:password\s*=|pwd\s*=|gemini[_-]?api[_-]?key\s*=|api[_-]?key\s*=|authorization:|bearer\s+[a-z0-9._-]{20,})",
    re.I,
)
PUBLIC_ORIGIN_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::443)?$")


REQUIRED_FIELDS = {
    "server": [
        "SSH host",
        "SSH port",
        "SSH user",
        "sudo allowed",
        "Docker Engine version",
        "Docker Compose plugin version",
        "Linux release",
        "CPU cores",
        "RAM",
        "Free SSD/NVMe disk path and size",
        "Public inbound ports allowed",
        "Outbound HTTPS allowed",
        "API/Marqo/Gemini internal bind/listen policy",
        "Nginx forwarded header policy",
        "Docker log rotation values",
        "Reverse proxy owner",
        "Production API subdomain",
        "TLS certificate method",
    ],
    "mssql": [
        "SQL Server host and port",
        "Database",
        "Read-only username",
        "Password delivery method",
        "ODBC driver version allowed",
        "Encryption required",
        "`TrustServerCertificate` allowed",
        "Read-only View name",
        "Incremental sync timestamp column",
        "Product deletion/hidden/sold-out rules",
        "Mall identifier column",
        "Product detail URL template",
    ],
    "gemini": [
        "Production auth method",
        "Internal Gemini proxy key delivery method",
        "Gemini quota page checked for `gemini-embedding-2`",
        "Budget alert configured",
        "Usage dashboard owner",
    ],
    "site": [
        "First rollout page(s)",
        "Exact CORS origins",
        "Public API key per mall/site",
        "Widget insertion location",
        "Fallback behavior if AI API is down",
        "Admin contact for rollback",
    ],
}


SECTION_TITLES = {
    "## 1. Server 82": "server",
    "## 2. MSSQL": "mssql",
    "## 3. Gemini": "gemini",
    "## 4. Haeorum Site Integration": "site",
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(Path(args.intake_file), require_filled=not args.template_ok)
    write_output(Path(args.output), report)
    if args.markdown_output:
        write_output(Path(args.markdown_output), render_markdown(report))
    if args.print_summary:
        print(
            f"server-db intake check: ok={report['ok']} status={report['status']} "
            f"passed={report['summary']['passed']}/{report['summary']['total']} failed={report['summary']['failed']}"
        )
    return 0 if report["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate filled server/DB handoff inputs before production deployment.")
    parser.add_argument("--intake-file", default=str(DEFAULT_INTAKE))
    parser.add_argument("--template-ok", action="store_true", help="Allow blank fields; use only to lint the template shape.")
    parser.add_argument("--output", default="logs/server-db-intake-check.json")
    parser.add_argument("--markdown-output", default="logs/server-db-intake-check.md")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args(argv)


def build_report(path: Path, *, require_filled: bool = True) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fields = parse_intake_fields(text)
    checks = [
        check_required_fields(fields, require_filled=require_filled),
        check_no_plaintext_secrets(text),
        check_server_policy(fields, require_filled=require_filled),
        check_mssql_policy(fields, require_filled=require_filled),
        check_gemini_policy(fields, require_filled=require_filled),
        check_site_policy(fields, require_filled=require_filled),
    ]
    failed = [check["name"] for check in checks if check.get("ok") is not True]
    status = (
        "template_shape_ok"
        if not require_filled and not failed
        else "ready_for_env_and_server_preflight"
        if not failed
        else "needs_input_fix"
    )
    return {
        "ok": not failed,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intake_file": str(path),
        "require_filled": require_filled,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
        "failed_checks": failed,
        "checks": checks,
    }


def parse_intake_fields(text: str) -> dict[str, dict[str, str]]:
    current_section = ""
    fields: dict[str, dict[str, str]] = {section: {} for section in REQUIRED_FIELDS}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line in SECTION_TITLES:
            current_section = SECTION_TITLES[line]
            continue
        if not current_section or not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        fields.setdefault(current_section, {})[key.strip()] = value.strip()
    return fields


def check_required_fields(fields: dict[str, dict[str, str]], *, require_filled: bool) -> dict[str, Any]:
    missing: list[str] = []
    blank: list[str] = []
    for section, names in REQUIRED_FIELDS.items():
        section_fields = fields.get(section, {})
        for name in names:
            field_id = f"{section}.{name}"
            if name not in section_fields:
                missing.append(field_id)
            elif require_filled and is_placeholder(section_fields.get(name, "")):
                blank.append(field_id)
    return {
        "name": "required_intake_fields",
        "ok": not missing and not blank,
        "details": {"missing": missing, "blank_or_placeholder": blank},
    }


def check_no_plaintext_secrets(text: str) -> dict[str, Any]:
    findings = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if SECRET_RE.search(line):
            findings.append({"line": line_no, "reason": "secret-like token should not be stored in intake markdown"})
    return {"name": "no_plaintext_secrets", "ok": not findings, "details": {"findings": findings}}


def check_server_policy(fields: dict[str, dict[str, str]], *, require_filled: bool) -> dict[str, Any]:
    server = fields.get("server", {})
    problems: list[str] = []
    if require_filled:
        if parse_int(server.get("SSH port")) not in range(1, 65536):
            problems.append("SSH port must be 1-65535")
        if normalize_yes_no(server.get("sudo allowed")) is not True:
            problems.append("sudo allowed must be yes")
        if parse_int(server.get("CPU cores")) < 4:
            problems.append("CPU cores must be at least 4")
        if parse_size_gb(server.get("RAM")) < 8:
            problems.append("RAM must be at least 8GB")
        if parse_size_gb(server.get("Free SSD/NVMe disk path and size")) < 20:
            problems.append("free SSD/NVMe disk must be at least 20GB")
        inbound = (server.get("Public inbound ports allowed") or "").lower()
        if "80" not in inbound or "443" not in inbound:
            problems.append("public inbound ports must include 80 and 443")
        if any(port in inbound for port in ("8000", "8098", "8120", "8122", "8882")):
            problems.append("AI API/Marqo/Gemini ports must not be public inbound ports")
        if normalize_yes_no(server.get("Outbound HTTPS allowed")) is not True:
            problems.append("outbound HTTPS must be yes")
        api_bind_policy = (
            server.get("API/Marqo/Gemini internal bind/listen policy")
            or server.get("API internal bind/listen policy")
            or ""
        ).lower()
        if is_placeholder(api_bind_policy) or not any(token in api_bind_policy for token in ("localhost", "127.0.0.1", "docker", "internal", "private")):
            problems.append("API/Marqo/Gemini internal bind/listen policy must keep API/Marqo/Gemini ports private")
        forwarded_header_policy = (server.get("Nginx forwarded header policy") or "").lower()
        if (
            is_placeholder(forwarded_header_policy)
            or "x-forwarded-for" not in forwarded_header_policy
            or "$remote_addr" not in forwarded_header_policy
            or not any(token in forwarded_header_policy for token in ("overwrite", "덮어", "replace", "재설정"))
        ):
            problems.append("Nginx forwarded header policy must overwrite X-Forwarded-For with $remote_addr")
        log_rotation = (server.get("Docker log rotation values") or "").lower()
        if (
            is_placeholder(log_rotation)
            or "max-size" not in log_rotation
            or "max-file" not in log_rotation
            or not re.search(r"max-size\s*[:=]\s*\d+\s*[kmg]", log_rotation)
            or not re.search(r"max-file\s*[:=]\s*\d+", log_rotation)
        ):
            problems.append("Docker log rotation values must include max-size and max-file")
        api_domain = server.get("Production API subdomain") or ""
        if not is_public_hostname(api_domain):
            problems.append("production API subdomain must be a public hostname without scheme/path")
        if is_placeholder(server.get("TLS certificate method")):
            problems.append("TLS certificate method must be specified")
    return {"name": "server_policy", "ok": not problems, "details": {"problems": problems}}


def check_mssql_policy(fields: dict[str, dict[str, str]], *, require_filled: bool) -> dict[str, Any]:
    mssql = fields.get("mssql", {})
    problems: list[str] = []
    if require_filled:
        if normalize_yes_no(mssql.get("Encryption required")) is not True:
            problems.append("MSSQL encryption must be required")
        if normalize_yes_no(mssql.get("`TrustServerCertificate` allowed")) is not False:
            problems.append("TrustServerCertificate must not be allowed")
        if "18" not in (mssql.get("ODBC driver version allowed") or ""):
            problems.append("ODBC Driver 18 must be allowed")
        if not has_host_and_port(mssql.get("SQL Server host and port") or ""):
            problems.append("SQL Server host and port must include host:port")
        if is_placeholder(mssql.get("Read-only View name")):
            problems.append("Read-only View name must be specified")
        status_rules = mssql.get("Product deletion/hidden/sold-out rules") or ""
        if is_placeholder(status_rules) or len(status_rules) < 10:
            problems.append("product deletion/hidden/sold-out rules must be explicit")
        url_template = mssql.get("Product detail URL template") or ""
        if not ("{product_id}" in url_template or "p_idx=" in url_template or "product" in url_template.lower()):
            problems.append("product detail URL template must identify how product_id maps to detail pages")
    return {"name": "mssql_policy", "ok": not problems, "details": {"problems": problems}}


def check_gemini_policy(fields: dict[str, dict[str, str]], *, require_filled: bool) -> dict[str, Any]:
    gemini = fields.get("gemini", {})
    problems: list[str] = []
    if require_filled:
        auth = (gemini.get("Production auth method") or "").strip().lower()
        if auth not in {"api key", "api_key", "apikey", "adc"}:
            problems.append("Production auth method must be API key or ADC")
        proxy_key_policy = (gemini.get("Internal Gemini proxy key delivery method") or "").lower()
        if (
            is_placeholder(proxy_key_policy)
            or "gemini_proxy_api_key" not in proxy_key_policy
            or "haeorum_gemini_embedding_proxy_api_key" not in proxy_key_policy
        ):
            problems.append(
                "Internal Gemini proxy key delivery method must set matching "
                "GEMINI_PROXY_API_KEY and HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY"
            )
        if normalize_yes_no(gemini.get("Gemini quota page checked for `gemini-embedding-2`")) is not True:
            problems.append("Gemini quota page checked must be yes")
        if normalize_yes_no(gemini.get("Budget alert configured")) is not True:
            problems.append("Budget alert configured must be yes")
        if is_placeholder(gemini.get("Usage dashboard owner")):
            problems.append("Usage dashboard owner must be specified")
    return {"name": "gemini_policy", "ok": not problems, "details": {"problems": problems}}


def check_site_policy(fields: dict[str, dict[str, str]], *, require_filled: bool) -> dict[str, Any]:
    site = fields.get("site", {})
    problems: list[str] = []
    if require_filled:
        origins = split_values(site.get("Exact CORS origins") or "")
        if not origins:
            problems.append("Exact CORS origins must include at least one HTTPS origin")
        for origin in origins:
            if not PUBLIC_ORIGIN_RE.match(origin):
                problems.append(f"CORS origin is not a clean HTTPS origin: {origin}")
        fallback = site.get("Fallback behavior if AI API is down") or ""
        if is_placeholder(fallback) or "classic" not in fallback.lower() and "기존" not in fallback:
            problems.append("fallback behavior must explicitly keep or restore the existing search")
        if is_placeholder(site.get("Admin contact for rollback")):
            problems.append("rollback admin contact must be specified")
    return {"name": "site_policy", "ok": not problems, "details": {"problems": problems}}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Server/DB Intake Check",
        "",
        f"- Status: `{report.get('status')}`",
        f"- OK: `{report.get('ok')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Intake file: `{report.get('intake_file')}`",
        f"- Passed: `{report['summary']['passed']} / {report['summary']['total']}`",
        f"- Failed: `{report['summary']['failed']}`",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks", []):
        status = "PASS" if check.get("ok") else "FAIL"
        lines.append(f"- {status}: `{check.get('name')}`")
        if not check.get("ok"):
            details = check.get("details") or {}
            lines.append(f"  - `{json.dumps(details, ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


def write_output(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def is_placeholder(value: str | None) -> bool:
    return PLACEHOLDER_RE.match(str(value or "").strip()) is not None


def normalize_yes_no(value: str | None) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"yes", "y", "true", "1", "ok", "가능", "예", "네"}:
        return True
    if normalized in {"no", "n", "false", "0", "불가능", "아니오", "아니요"}:
        return False
    return None


def parse_int(value: str | None) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def parse_size_gb(value: str | None) -> float:
    text = str(value or "").lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|tib|gb|gib|g|t)", text)
    if not match:
        return 0.0
    amount = float(match.group(1))
    unit = match.group(2)
    return amount * 1024.0 if unit.startswith("t") else amount


def is_public_hostname(value: str) -> bool:
    host = value.strip().lower()
    if not host or "://" in host or "/" in host or host in {"localhost", "127.0.0.1"}:
        return False
    if "." not in host:
        return False
    if any(part in {"local", "internal", "test"} for part in host.split(".")):
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+", host))


def has_host_and_port(value: str) -> bool:
    text = value.strip()
    return bool(re.search(r"^[^:,\s]+(?:\\[^:,\s]+)?[:,]\s*\d{2,5}$", text))


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
