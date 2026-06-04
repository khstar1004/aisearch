from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROLE_REQUIREMENTS = {
    "poc": {"min_cpu": 4, "min_memory_gb": 8, "min_disk_free_gb": 50, "min_open_files": 16384},
    "api": {"min_cpu": 4, "min_memory_gb": 8, "min_disk_free_gb": 20, "min_open_files": 65535},
    "sync": {"min_cpu": 4, "min_memory_gb": 8, "min_disk_free_gb": 50, "min_open_files": 16384},
    "marqo": {"min_cpu": 8, "min_memory_gb": 32, "min_disk_free_gb": 200, "min_open_files": 65535},
    "combined": {"min_cpu": 8, "min_memory_gb": 32, "min_disk_free_gb": 200, "min_open_files": 65535},
}
REQUIRED_API_MODULES = ["fastapi", "uvicorn", "pydantic", "multipart", "PIL", "redis", "psutil"]
MIN_PYTHON_VERSION = (3, 11)
MIN_DOCKER_VERSION = (24, 0, 0)
SUPPORTED_LINUX_BASELINES = {
    "ubuntu": ((20, 4), "20.04", "Ubuntu 20.04+"),
    "debian": ((11,), "11", "Debian 11+"),
    "rhel": ((8,), "8", "RHEL-compatible 8+"),
    "centos": ((8,), "8", "CentOS/RHEL-compatible 8+"),
    "rocky": ((8,), "8", "Rocky Linux 8+"),
    "almalinux": ((8,), "8", "AlmaLinux 8+"),
    "ol": ((8,), "8", "Oracle Linux 8+"),
}


CommandRunner = Callable[[list[str], int], dict[str, Any]]
ModuleChecker = Callable[[str], bool]
OdbcDriverProvider = Callable[[], list[str]]


def collect_open_file_limit() -> dict[str, int | str | None]:
    try:
        import resource
    except ImportError:
        return {"soft": None, "hard": None}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        return {"soft": None, "hard": None}

    def normalize(value: int) -> int | str:
        if value == getattr(resource, "RLIM_INFINITY", -1):
            return "unlimited"
        return int(value)

    return {"soft": normalize(soft), "hard": normalize(hard)}


def limit_to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().lower() in {"unlimited", "infinity", "inf"}:
        return math.inf  # type: ignore[return-value]
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.groups(default="0"))


def version_at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    if not version:
        return False
    width = max(len(version), len(minimum))
    return tuple(version + (0,) * (width - len(version))) >= tuple(minimum + (0,) * (width - len(minimum)))


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def available_odbc_drivers() -> list[str]:
    try:
        import pyodbc
    except ImportError:
        return []
    try:
        return sorted(str(driver) for driver in pyodbc.drivers())
    except Exception:
        return []


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"ok": False, "command": command, "error": f"{command[0]} is not installed"}
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "command": command, "error": str(exc)}
    output = (completed.stdout or completed.stderr or "").strip()
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "exit_code": completed.returncode,
        "output": output,
    }


def collect_system_info(disk_path: str | Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "cpu_count": None,
        "memory_total_bytes": None,
        "disk_free_bytes": None,
        "disk_total_bytes": None,
        "os_release": read_os_release(),
    }
    open_file_limit = collect_open_file_limit()
    info["open_file_limit_soft"] = open_file_limit["soft"]
    info["open_file_limit_hard"] = open_file_limit["hard"]
    try:
        import psutil

        info["cpu_count"] = psutil.cpu_count(logical=True)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(disk_path))
        info["memory_total_bytes"] = int(memory.total)
        info["disk_free_bytes"] = int(disk.free)
        info["disk_total_bytes"] = int(disk.total)
    except Exception as exc:
        info["psutil_error"] = str(exc)
    return info


def read_os_release(path: str | Path = "/etc/os-release") -> dict[str, str]:
    target = Path(path)
    if not target.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def bytes_to_gb(value: Any) -> float:
    try:
        return round(float(value) / (1024**3), 2)
    except (TypeError, ValueError):
        return 0.0


def check_python(system_info: dict[str, Any]) -> dict[str, Any]:
    version = parse_version(str(system_info.get("python_version") or ""))
    return {
        "name": "python_version",
        "ok": version_at_least(version, MIN_PYTHON_VERSION),
        "version": system_info.get("python_version"),
        "minimum": ".".join(str(part) for part in MIN_PYTHON_VERSION),
    }


def check_modules(module_checker: ModuleChecker, require_pyodbc: bool) -> dict[str, Any]:
    required = list(REQUIRED_API_MODULES)
    if require_pyodbc:
        required.append("pyodbc")
    missing = sorted(module for module in required if not module_checker(module))
    return {"name": "python_modules", "ok": not missing, "required": required, "missing": missing}


def check_odbc_driver(
    driver_provider: OdbcDriverProvider,
    expected_driver: str,
    required: bool,
) -> dict[str, Any]:
    drivers = driver_provider() if required else []
    expected = str(expected_driver or "").strip()
    ok = True
    if required:
        ok = bool(expected) and expected in drivers
    return {
        "name": "odbc_driver",
        "ok": ok,
        "required": required,
        "expected_driver": expected,
        "drivers": drivers,
    }


def check_linux(system_info: dict[str, Any], require_linux: bool) -> dict[str, Any]:
    platform_name = str(system_info.get("platform") or "")
    return {
        "name": "linux_host",
        "ok": (platform_name.lower() == "linux") if require_linux else True,
        "required": require_linux,
        "platform": platform_name,
        "release": system_info.get("platform_release"),
        "os_release": system_info.get("os_release") or {},
    }


def linux_release_ids(os_release: dict[str, str]) -> list[str]:
    values = [str(os_release.get("ID") or "").strip().lower()]
    values.extend(str(os_release.get("ID_LIKE") or "").lower().split())
    seen: set[str] = set()
    ids = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def check_supported_linux_release(system_info: dict[str, Any], required: bool) -> dict[str, Any]:
    platform_name = str(system_info.get("platform") or "")
    os_release = system_info.get("os_release") or {}
    ids = linux_release_ids(os_release) if isinstance(os_release, dict) else []
    version_text = str(os_release.get("VERSION_ID") or "") if isinstance(os_release, dict) else ""
    version = parse_version(version_text)
    baseline_id = next((candidate for candidate in ids if candidate in SUPPORTED_LINUX_BASELINES), "")
    minimum, minimum_text, label = SUPPORTED_LINUX_BASELINES.get(baseline_id, ((), "", ""))
    release_ok = bool(baseline_id and version and version_at_least(version, minimum))
    ok = release_ok if required and platform_name.lower() == "linux" else True
    return {
        "name": "supported_linux_release",
        "ok": ok,
        "required": required,
        "platform": platform_name,
        "id": ids[0] if ids else None,
        "id_like": ids[1:],
        "version_id": version_text or None,
        "matched_baseline": baseline_id or None,
        "minimum_version": minimum_text or None,
        "baseline": label or None,
        "supported_ids": sorted(SUPPORTED_LINUX_BASELINES),
    }


def check_resources(system_info: dict[str, Any], requirements: dict[str, int]) -> dict[str, Any]:
    cpu_count = int(system_info.get("cpu_count") or 0)
    memory_gb = bytes_to_gb(system_info.get("memory_total_bytes"))
    disk_free_gb = bytes_to_gb(system_info.get("disk_free_bytes"))
    platform_name = str(system_info.get("platform") or "")
    open_file_limit_soft = limit_to_int(system_info.get("open_file_limit_soft"))
    open_file_limit_hard = limit_to_int(system_info.get("open_file_limit_hard"))
    min_open_files = int(requirements.get("min_open_files") or 0)
    problems = []
    if cpu_count < int(requirements["min_cpu"]):
        problems.append("cpu_count")
    if memory_gb < float(requirements["min_memory_gb"]):
        problems.append("memory_total_gb")
    if disk_free_gb < float(requirements["min_disk_free_gb"]):
        problems.append("disk_free_gb")
    if min_open_files > 0 and platform_name.lower() == "linux":
        if open_file_limit_soft is None or open_file_limit_soft < min_open_files:
            problems.append("open_file_limit_soft")
        if open_file_limit_hard is not None and open_file_limit_hard < min_open_files:
            problems.append("open_file_limit_hard")
    return {
        "name": "host_resources",
        "ok": not problems,
        "cpu_count": cpu_count,
        "memory_total_gb": memory_gb,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": bytes_to_gb(system_info.get("disk_total_bytes")),
        "open_file_limit_soft": system_info.get("open_file_limit_soft"),
        "open_file_limit_hard": system_info.get("open_file_limit_hard"),
        "requirements": requirements,
        "problems": problems,
    }


def check_docker(command_runner: CommandRunner, timeout: int, required: bool) -> dict[str, Any]:
    result = command_runner(["docker", "--version"], timeout)
    version = parse_version(str(result.get("output") or ""))
    ok = result.get("ok") is True and version_at_least(version, MIN_DOCKER_VERSION)
    return {
        "name": "docker",
        "ok": ok if required else True,
        "required": required,
        "raw_ok": result.get("ok"),
        "version": ".".join(str(part) for part in version) if version else None,
        "minimum": ".".join(str(part) for part in MIN_DOCKER_VERSION),
        "output": result.get("output"),
        "error": result.get("error"),
    }


def check_compose(command_runner: CommandRunner, timeout: int, required: bool) -> dict[str, Any]:
    result = command_runner(["docker", "compose", "version"], timeout)
    fallback = None
    if result.get("ok") is not True:
        fallback = command_runner(["docker-compose", "--version"], timeout)
    ok = result.get("ok") is True or (fallback or {}).get("ok") is True
    active = result if result.get("ok") is True else fallback or result
    return {
        "name": "docker_compose",
        "ok": ok if required else True,
        "required": required,
        "raw_ok": ok,
        "output": active.get("output"),
        "error": active.get("error"),
    }


def build_report(
    args: argparse.Namespace,
    command_runner: CommandRunner = run_command,
    module_checker: ModuleChecker = module_available,
    odbc_driver_provider: OdbcDriverProvider = available_odbc_drivers,
    system_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = args.role
    requirements = {
        "min_cpu": args.min_cpu or ROLE_REQUIREMENTS[role]["min_cpu"],
        "min_memory_gb": args.min_memory_gb or ROLE_REQUIREMENTS[role]["min_memory_gb"],
        "min_disk_free_gb": args.min_disk_free_gb or ROLE_REQUIREMENTS[role]["min_disk_free_gb"],
        "min_open_files": getattr(args, "min_open_files", 0) or ROLE_REQUIREMENTS[role]["min_open_files"],
    }
    info = system_info or collect_system_info(args.disk_path)
    require_supported_os = not bool(getattr(args, "allow_unsupported_os", False)) and not bool(
        getattr(args, "allow_non_linux", False)
    )
    checks = [
        check_linux(info, require_linux=not args.allow_non_linux),
        check_supported_linux_release(info, required=require_supported_os),
        check_python(info),
        check_modules(module_checker, require_pyodbc=args.require_pyodbc),
        check_odbc_driver(odbc_driver_provider, args.expected_odbc_driver, required=args.require_pyodbc),
        check_resources(info, requirements),
        check_docker(command_runner, args.timeout, required=args.require_docker),
        check_compose(command_runner, args.timeout, required=args.require_compose),
    ]
    failed = [check["name"] for check in checks if check.get("ok") is not True]
    return {
        "ok": not failed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "requirements": requirements,
        "failed_checks": failed,
        "system": info,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build server preflight evidence for Haeorum AI Search deployment.")
    parser.add_argument("--role", choices=sorted(ROLE_REQUIREMENTS), default="api")
    parser.add_argument("--disk-path", default="/")
    parser.add_argument("--min-cpu", type=int, default=0)
    parser.add_argument("--min-memory-gb", type=int, default=0)
    parser.add_argument("--min-disk-free-gb", type=int, default=0)
    parser.add_argument("--min-open-files", type=int, default=0)
    parser.add_argument("--require-docker", action="store_true")
    parser.add_argument("--require-compose", action="store_true")
    parser.add_argument("--require-pyodbc", action="store_true")
    parser.add_argument("--expected-odbc-driver", default="ODBC Driver 18 for SQL Server")
    parser.add_argument(
        "--allow-non-linux",
        action="store_true",
        help="Only for local dry-runs. Production preflight should run on Linux without this flag.",
    )
    parser.add_argument(
        "--allow-unsupported-os",
        action="store_true",
        help="Only for migration dry-runs. Production readiness requires a supported Linux release baseline.",
    )
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
