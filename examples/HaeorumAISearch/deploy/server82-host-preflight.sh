#!/usr/bin/env bash
set -euo pipefail

DISK_PATH="${1:-/home/docker}"
OUTPUT="${2:-logs/server82-preflight.json}"
ALLOW_UNSUPPORTED_OS="${HAEORUM_ALLOW_UNSUPPORTED_OS:-true}"

mkdir -p "$(dirname "$OUTPUT")"

declare -a failed=()
declare -a warn=()

add_failed() {
  failed+=("$1")
}

json_array() {
  local first=1
  printf '['
  for item in "$@"; do
    if [ "$first" -eq 0 ]; then
      printf ','
    fi
    first=0
    printf '"%s"' "$(printf '%s' "$item" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  done
  printf ']'
}

os_release="$(cat /etc/centos-release 2>/dev/null || cat /etc/os-release 2>/dev/null || true)"
cpu_count="$(nproc 2>/dev/null || echo 0)"
mem_kb="$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
mem_gb="$((mem_kb / 1024 / 1024))"
disk_avail_kb="$(df -Pk "$DISK_PATH" 2>/dev/null | awk 'NR==2 {print $4}')"
disk_avail_gb="$(( ${disk_avail_kb:-0} / 1024 / 1024 ))"
open_files="$(ulimit -n)"
docker_version="$(docker --version 2>/dev/null || true)"
compose_version="$(docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || true)"
docker_root="$(docker info 2>/dev/null | awk -F': ' '/Docker Root Dir/ {print $2}')"
docker_logging="$(docker info 2>/dev/null | awk -F': ' '/Logging Driver/ {print $2}')"

if ! echo "$os_release" | grep -Eq 'CentOS Linux release 7\.6\.1810'; then
  warn+=("unexpected_os_release")
elif [ "$ALLOW_UNSUPPORTED_OS" != "true" ] && [ "$ALLOW_UNSUPPORTED_OS" != "1" ]; then
  add_failed "unsupported_os_release"
else
  warn+=("centos_7_6_unsupported_baseline_allowed")
fi

[ "${cpu_count:-0}" -ge 8 ] || add_failed "cpu_count"
[ "${mem_gb:-0}" -ge 7 ] || add_failed "memory_total_gb"
[ "${disk_avail_gb:-0}" -ge 50 ] || add_failed "disk_free_gb"
[ "${open_files:-0}" -ge 65535 ] || add_failed "open_file_limit"
echo "$docker_version" | grep -Eq 'Docker version (2[4-9]|[3-9][0-9])\.' || add_failed "docker_version"
echo "$compose_version" | grep -Eq 'Docker Compose version v?2\.' || add_failed "docker_compose"
[ "$docker_root" = "/home/docker" ] || add_failed "docker_data_root"
[ "$docker_logging" = "json-file" ] || add_failed "docker_logging_driver"

ok=false
if [ "${#failed[@]}" -eq 0 ]; then
  ok=true
fi

{
  printf '{\n'
  printf '  "ok": %s,\n' "$ok"
  printf '  "failed_checks": '
  if [ "${#failed[@]}" -eq 0 ]; then
    printf '[]'
  else
    json_array "${failed[@]}"
  fi
  printf ',\n'
  printf '  "warnings": '
  if [ "${#warn[@]}" -eq 0 ]; then
    printf '[]'
  else
    json_array "${warn[@]}"
  fi
  printf ',\n'
  printf '  "system": {\n'
  printf '    "os_release": "%s",\n' "$(printf '%s' "$os_release" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '    "cpu_count": %s,\n' "${cpu_count:-0}"
  printf '    "memory_total_gb_floor": %s,\n' "${mem_gb:-0}"
  printf '    "disk_path": "%s",\n' "$DISK_PATH"
  printf '    "disk_free_gb_floor": %s,\n' "${disk_avail_gb:-0}"
  printf '    "open_file_limit": %s,\n' "${open_files:-0}"
  printf '    "docker_version": "%s",\n' "$(printf '%s' "$docker_version" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '    "compose_version": "%s",\n' "$(printf '%s' "$compose_version" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '    "docker_root": "%s",\n' "$docker_root"
  printf '    "docker_logging_driver": "%s"\n' "$docker_logging"
  printf '  }\n'
  printf '}\n'
} >"$OUTPUT"

cat "$OUTPUT"

if [ "$ok" != "true" ]; then
  exit 1
fi
