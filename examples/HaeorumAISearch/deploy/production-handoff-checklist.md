# Production Handoff Checklist

This checklist is the practical handoff gate before putting Haeorum AI Search on the separate Linux server.

Use `deploy/server-db-request.ko.md` to ask the existing developer for inputs, then fill `deploy/server-db-intake.md` with the received server, DB, domain, and Gemini credential details. The codebase should pass `scripts/pre_handoff_audit.py` before those inputs arrive.

After the intake file is filled, run `scripts/server_db_intake_check.py`. Do not create the production env file or connect to MSSQL until this report is `ready_for_env_and_server_preflight`.

Use `deploy/go-live-failure-scenarios.md` and `deploy/operational-risk-register.md` as the go-live risk checklist. Every rollout approval should map back to the evidence listed there.

## 1. Server Inputs

- SSH host, port, user, sudo policy
- Linux release and CPU/RAM/disk free space
- Docker Engine and Docker Compose plugin versions
- Inbound firewall: only reverse proxy `80/443` public
- API, Marqo, and Gemini embedding proxy ports kept private to localhost or internal network
- Outbound HTTPS allowed for Gemini API and product image URLs
- Subdomain, DNS target, TLS certificate method
- Docker log rotation values: `HAEORUM_DOCKER_LOG_MAX_SIZE`, `HAEORUM_DOCKER_LOG_MAX_FILE`
- Docker env uses service DNS inside containers: `MARQO_URL=http://marqo-api:8882`, `HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098`
- Host-run evidence config uses loopback endpoints: `marqo.url=http://127.0.0.1:8882`, `marqo.gemini_embedding_url=http://127.0.0.1:8098`
- Docker published ports must bind to `127.0.0.1`, not `0.0.0.0`; external traffic reaches the API only through Apache/Nginx `80/443`.

## 2. Database Inputs

- MSSQL read-only connection string
- AI search View or read-only query
- Required columns: `product_id`, `product_name`, `price`, `category_name`, `main_image_url`, `product_url`, `status`, `updated_at`, `is_deleted` or `display_yn`, optional `mall_id`
- Confirm `updated_at` semantics for incremental sync
- Confirm deletion, hidden, sold-out, and non-display status rules
- Confirm product detail URL template and mall mapping

## 3. Gemini Inputs

- Production mode: `GEMINI_AUTH_MODE=api_key` or `GEMINI_AUTH_MODE=adc`
- If API key: store only in protected env file, never in git
- If ADC: quota project configured and credential file mounted read-only
- Required app settings: `HAEORUM_EMBEDDING_BACKEND=gemini`, `HAEORUM_GEMINI_EMBEDDING_URL`, `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`, `HAEORUM_GEMINI_MODEL`, `HAEORUM_GEMINI_EMBEDDING_DIMENSIONS`
- Required internal proxy secret: `GEMINI_PROXY_API_KEY` must match `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`
- Required Gemini proxy guardrails: `GEMINI_MAX_RESPONSE_BYTES`, `GEMINI_PROXY_RATE_LIMIT_RPM`, `GEMINI_PROXY_RATE_LIMIT_BURST`, `GEMINI_PROXY_MAX_CONCURRENT_CALLS`, `GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS`
- Google quota checked for `gemini-embedding-2`: request per minute, token per minute, daily request quota
- Google budget alert and usage dashboard bookmarked
- `/admin-ui` Gemini card checked after smoke tests, including `Proxy auth=true`

## 4. Security Controls

- Strong `HAEORUM_ADMIN_API_KEY`
- Strong per-mall public API keys
- Exact HTTPS CORS origins, no wildcard
- `HAEORUM_TRUSTED_PROXY_IPS` includes only real reverse proxy peers
- Reverse proxy overwrites `X-Forwarded-For`; API port not exposed directly
- Rate limits enabled for search, click, and image search
- Redis configured before running multiple API containers
- No local GPU embedding container running in the Haeorum production stack

## 5. Required Commands

```bash
python scripts/server_db_intake_check.py --intake-file deploy/server-db-intake.md --print-summary
python scripts/compose_exposure_check.py --print-summary
python scripts/go_live_scenario_check.py --print-summary
python scripts/pre_handoff_audit.py --require-runtime --base-url http://127.0.0.1:8120 --mall-id "<mall-id>" --admin-key "$HAEORUM_ADMIN_API_KEY" --api-key "<mall-public-api-key>" --origin https://www.haeorumgift.com
python scripts/env_check.py --env-file /etc/haeorum-ai-search/haeorum-ai-search.env --role api --api-server-count 1
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml -f compose-haeorum-existing-8gb.yaml -f compose-haeorum-server82.yaml up -d --build
curl -fsS http://127.0.0.1:8120/health
curl -fsS -H "X-Admin-Key: $HAEORUM_ADMIN_API_KEY" http://127.0.0.1:8120/admin/metrics
curl -fsS http://127.0.0.1:8098/health
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml -f compose-haeorum-existing-8gb.yaml -f compose-haeorum-server82.yaml --profile reindex run --rm reindex-once
```

## 6. Rollout Gate

- Text, image, and mixed search smoke tests pass
- Search response `meta.engine=marqo`, `meta.embedding_backend=gemini`
- `/admin-ui` shows Gemini requests/calls/errors after test searches
- p95 text search stays below the target on the real server
- Image/mixed p95 stays below the target with real images
- 429 only happens in abuse/load tests, not normal usage
- Gemini error count and circuit open count stay at `0`
- Marqo/Vespa memory and disk stay below alert thresholds
- Admin-metrics backend proof load uses `scripts/load_test.py --unique-query-suffix <run-id>` so cache-only repeats do not hide Marqo/Gemini backend-attempt deltas.

## 7. Backout

- Confirm rollback test evidence before broad rollout.
- Disable AI widget exposure on the existing site
- Keep classic search as fallback
- Stop sync worker before destructive reindex troubleshooting
- Preserve previous index name until the new index is accepted
- Keep previous env file backup with secrets protected
