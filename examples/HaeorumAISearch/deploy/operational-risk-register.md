# Operational Risk Register

This register covers the production risks that should be checked before contract handoff. The current target architecture is Marqo + Gemini embedding API on a separate Linux server.

References checked:

- OWASP API4:2023 Unrestricted Resource Consumption: https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/
- Gemini API rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Google Cloud quota management: https://cloud.google.com/docs/quotas/view-manage
- Docker json-file log rotation: https://docs.docker.com/engine/logging/drivers/json-file/
- Nginx `proxy_set_header`: https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header

## Go-Live Risk Matrix

| Risk | Production symptom | Control in this project | Evidence before go-live |
| --- | --- | --- | --- |
| API resource exhaustion / intentional abuse | 429/503 spikes, high API CPU, queue wait, Gemini bill/quota burn | Per-IP and per-mall rate limits, search/image concurrency gates, queue timeout, backend active request slots, circuit breaker, internal Gemini proxy shared secret | `load_test.py` report has rate-limit/cache/queue/circuit deltas; `/admin/metrics` shows `rate_limited_events`, queue stats, backend transport, `engine.gemini.proxy_auth_configured=true` |
| Gemini project quota exceeded | 429 RESOURCE_EXHAUSTED, search falls to 503, Google usage grows unexpectedly | Gemini proxy shared secret, proxy rate limiter, retry-after handling, admin Gemini card, Google quota/budget check | Google AI Studio/Cloud quota page, `/admin-ui` Gemini usage card, `engine.gemini.proxy_auth_configured=true`, `engine.gemini.usage.failed_total=0` |
| Product image or large upload abuse | Large request bodies, image queue saturation, slow image search | Nginx `client_max_body_size`, app max image MB/dimension checks, image search rate limit and queue | `api_smoke_test.py` oversized image rejection, `/admin/metrics` image queue and image validation counters |
| Log disk fill | Docker host disk usage rises, containers fail/restart | Docker json-file log rotation in compose, app log paths, logrotate template | `docker inspect` logging options, `deploy/logrotate/haeorum-ai-search`, `/admin/metrics` disk/log stats |
| Spoofed client IP through proxy headers | One attacker bypasses IP rate limits by sending fake `X-Forwarded-For` | API trusts only configured proxy IPs; Nginx overwrites forwarded headers with `$remote_addr` | `deploy/nginx/haeorum-ai-search.conf`, `HAEORUM_TRUSTED_PROXY_IPS`, security smoke |
| DB View drift or unsafe DB permissions | Reindex fails, wrong/hidden products exposed, deleted products remain | Read-only MSSQL connection validation, required column checks, deletion/status rules in intake, sync lock and stale lock recovery | `mssql_view_check.py`, `mssql_export.py`, `env_check.py`, sync logs |
| Bad CORS or leaked API key | Site cannot call API, or public key is reused from another origin | Exact HTTPS origins, per-mall API key, query/body key alias rejection | `env_check.py`, `api_smoke_test.py`, representative site evidence |
| Malformed Korean query encoding | A real Korean query reaches API as `?? ??` or replacement chars and returns unrelated products | `SearchRequest` rejects malformed query text before Marqo/Gemini, OpenAPI documents UTF-8, go-live runtime check posts valid and malformed Korean text | `models.py` unit test, `go_live_scenario_check.py --mall-id --origin --public-api-key` |
| Wrong search backend after deployment | Admin UI shows a non-Gemini/native provider, or index vector dimensions mismatch | Compose defaults to Gemini; local GPU embedding containers are forbidden in handoff audit; Marqo resource contract checks dimensions/model | `pre_handoff_audit.py`, `/health`, `marqo_resource_check.py` |
| Cold cache or new query latency | First query is slower than repeated query; p95 rises after deployment | Runtime query vector cache, singleflight, backend keep-alive, separate text/image cache quotas | text/image/mixed load reports with before/after server metrics |
| Reindex interruption | Partial index, stale old products, confusing search quality | New index name or preserved previous index, reindex report, failed product/batch counts | `csv_index.py` report, `/admin/sync-status`, preserve previous index until acceptance |
| Multi-API scaling without Redis | Per-process rate limits/cache allow inconsistent behavior | Redis required by env preflight for multiple API containers | `env_check.py --api-server-count 2`, API scale report |
| Frontend rollout failure | Widget blocks classic search or breaks page layout | Banner/opt-in rollout first, classic search fallback, representative site widget check | representative site evidence, rollback test confirmation |

## Required Acceptance Evidence

1. `scripts/pre_handoff_audit.py --require-runtime --mall-id <real mall>` passes.
2. `scripts/env_check.py --env-file <protected env> --role api --api-server-count <count>` passes.
3. `/health` returns `ok=true`, `embedding_backend=gemini`, and indexed document count matches the accepted dataset.
4. `/admin/metrics` shows Gemini model/dimensions, `engine.gemini.proxy_auth_configured=true`, zero Gemini provider failures during smoke, zero circuit-open events.
5. Text, image, and mixed smoke tests return `meta.engine=marqo` and `meta.embedding_backend=gemini`.
6. Runtime malformed-query check rejects `?? ??` while accepting normal UTF-8 Korean text with the same public mall key/origin.
7. Real-server load reports include text 100 concurrent, image 30 concurrent, mixed 30 concurrent, and 850 active-user mixed traffic before broad rollout.
8. Google quota page and budget alert are captured for the production project.
9. Rollback is tested by disabling the AI widget while classic search still works.

## Rollout Policy

- Start with internal or low-traffic banner exposure.
- Watch `/admin-ui`, Google API usage, Docker memory/disk, and Nginx access/error logs for at least several hours.
- Do not replace the main site search until p95/p99, error rate, Gemini failures, rate limits, disk, and memory remain within the rollout gate.
- Keep the previous index and previous env file until the new deployment is accepted.
