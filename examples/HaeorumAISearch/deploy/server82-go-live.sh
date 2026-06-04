#!/usr/bin/env bash
set -euo pipefail

ROOT="${HAEORUM_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${HAEORUM_ENV_FILE:-/etc/haeorum-ai-search/haeorum-ai-search.env}"
API_SERVER_COUNT="${HAEORUM_API_SERVER_COUNT:-1}"
DOCKER_DISK_PATH="${HAEORUM_DOCKER_DISK_PATH:-/home/docker}"
ALLOW_UNSUPPORTED_OS="${HAEORUM_ALLOW_UNSUPPORTED_OS:-true}"

cd "$ROOT"
mkdir -p logs

env_get() {
  local key="$1"
  local default="${2:-}"
  if [ ! -f "$ENV_FILE" ]; then
    printf '%s\n' "$default"
    return
  fi
  local line
  line="$(awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      sub(/^[^=]*=/, "")
      print
      found=1
      exit
    }
    END { if (!found) exit 1 }
  ' "$ENV_FILE" 2>/dev/null || true)"
  if [ -z "$line" ]; then
    printf '%s\n' "$default"
    return
  fi
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf '%s\n' "$line"
}

first_origin() {
  awk 'NF && $0 !~ /^[[:space:]]*#/ { print; exit }' "$1"
}

require_value() {
  local name="$1"
  local value="$2"
  case "$value" in
    ""|replace-with*|change-me|changeme|dummy|sample|...)
      echo "Missing required value: $name" >&2
      exit 1
      ;;
  esac
}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

MALL_CONFIG="$(env_get HAEORUM_MALL_CONFIG_PATH /etc/haeorum-ai-search/malls.json)"
CORS_FILE="$(env_get HAEORUM_CORS_ORIGINS_FILE /etc/haeorum-ai-search/cors-origins.txt)"
QUERY_SYNONYMS="$(env_get HAEORUM_QUERY_SYNONYM_PATH /etc/haeorum-ai-search/query-synonyms.json)"
API_PORT="$(env_get HAEORUM_AI_SEARCH_PORT 8120)"
ADMIN_KEY="$(env_get HAEORUM_ADMIN_API_KEY "")"
GEMINI_AUTH_MODE="$(env_get GEMINI_AUTH_MODE api_key)"
GEMINI_API_KEY="$(env_get GEMINI_API_KEY "")"
GEMINI_PROXY_API_KEY="$(env_get GEMINI_PROXY_API_KEY "")"
GEMINI_QUOTA_PROJECT="$(env_get GEMINI_QUOTA_PROJECT "")"

require_value HAEORUM_ADMIN_API_KEY "$ADMIN_KEY"
require_value GEMINI_PROXY_API_KEY "$GEMINI_PROXY_API_KEY"
if [ "$GEMINI_AUTH_MODE" = "api_key" ]; then
  require_value GEMINI_API_KEY "$GEMINI_API_KEY"
elif [ "$GEMINI_AUTH_MODE" = "adc" ]; then
  require_value GEMINI_QUOTA_PROJECT "$GEMINI_QUOTA_PROJECT"
else
  echo "GEMINI_AUTH_MODE must be api_key or adc, got: $GEMINI_AUTH_MODE" >&2
  exit 1
fi

for path in "$MALL_CONFIG" "$CORS_FILE" "$QUERY_SYNONYMS"; do
  if [ ! -f "$path" ]; then
    echo "Missing required config file: $path" >&2
    exit 1
  fi
done

SMOKE_MALL_ID="${HAEORUM_SMOKE_MALL_ID:-$(env_get HAEORUM_SMOKE_MALL_ID haeorumgift)}"
SMOKE_PUBLIC_API_KEY="${HAEORUM_SMOKE_PUBLIC_API_KEY:-$(env_get HAEORUM_SMOKE_PUBLIC_API_KEY "")}"
SMOKE_ORIGIN="${HAEORUM_SMOKE_ORIGIN:-$(first_origin "$CORS_FILE")}"
require_value HAEORUM_SMOKE_MALL_ID "$SMOKE_MALL_ID"
require_value HAEORUM_SMOKE_PUBLIC_API_KEY "$SMOKE_PUBLIC_API_KEY"
require_value HAEORUM_SMOKE_ORIGIN "$SMOKE_ORIGIN"

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f compose-haeorum-marqo.yaml
  -f compose-haeorum-gemini.yaml
  -f compose-haeorum-existing-8gb.yaml
  -f compose-haeorum-server82.yaml
)

echo "== server preflight =="
bash deploy/server82-host-preflight.sh "$DOCKER_DISK_PATH" logs/server82-preflight.json

echo "== compose config =="
"${COMPOSE[@]}" config >/tmp/haeorum-ai-search.compose.yaml

echo "== build ai-search utility image =="
"${COMPOSE[@]}" build ai-search
PY_IN_APP=("${COMPOSE[@]}" run --rm --no-deps ai-search python)

echo "== intake/env/static checks =="
"${PY_IN_APP[@]}" scripts/server_db_intake_check.py \
  --intake-file deploy/server-db-intake.md \
  --output logs/server-db-intake-check.json \
  --markdown-output logs/server-db-intake-check.md \
  --print-summary

"${PY_IN_APP[@]}" scripts/env_check.py \
  --env-file "$ENV_FILE" \
  --role combined \
  --api-server-count "$API_SERVER_COUNT" \
  --output logs/server82-env-check.json \
  --markdown-output logs/server82-env-check.md

"${PY_IN_APP[@]}" scripts/compose_exposure_check.py --print-summary
"${PY_IN_APP[@]}" scripts/go_live_scenario_check.py \
  --output logs/go-live-scenario-check.json \
  --markdown-output logs/go-live-scenario-check.md \
  --print-summary

echo "== start stack =="
"${COMPOSE[@]}" up -d --build

echo "== wait health =="
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/tmp/haeorum-ai-search.health.json; then
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${API_PORT}/health" | tee logs/server82-health.json >/dev/null
curl -fsS -H "X-Admin-Key: ${ADMIN_KEY}" \
  "http://127.0.0.1:${API_PORT}/admin/metrics" \
  | tee logs/server82-admin-metrics.json >/dev/null
curl -fsS "http://127.0.0.1:8098/health" | tee logs/server82-gemini-health.json >/dev/null

echo "== MSSQL view check =="
"${COMPOSE[@]}" run --rm ai-search \
  python scripts/mssql_view_check.py \
  --allow-missing-domain-filter-fields \
  --output /app/logs/mssql-view-check.json

echo "== full reindex =="
"${COMPOSE[@]}" --profile reindex run --rm reindex-once

echo "== start sync worker =="
"${COMPOSE[@]}" --profile sync up -d --build --no-deps sync-worker

echo "== API smoke =="
"${COMPOSE[@]}" exec -T ai-search python scripts/api_smoke_test.py \
  --base-url "http://127.0.0.1:8000" \
  --mall-id "$SMOKE_MALL_ID" \
  --api-key "$SMOKE_PUBLIC_API_KEY" \
  --origin "$SMOKE_ORIGIN" \
  --mall-config "$MALL_CONFIG" \
  --admin-key "$ADMIN_KEY" \
  --allow-local-target \
  --output logs/server82-api-smoke.json

"${COMPOSE[@]}" exec -T ai-search python scripts/pre_handoff_audit.py \
  --require-runtime \
  --base-url "http://127.0.0.1:8000" \
  --mall-id "$SMOKE_MALL_ID" \
  --admin-key "$ADMIN_KEY" \
  --api-key "$SMOKE_PUBLIC_API_KEY" \
  --origin "$SMOKE_ORIGIN" \
  --output logs/server82-pre-handoff-audit.json \
  --markdown-output logs/server82-pre-handoff-audit.md \
  --print-summary

echo "Done. Keep logs/*.json and logs/*.md as go-live evidence."
