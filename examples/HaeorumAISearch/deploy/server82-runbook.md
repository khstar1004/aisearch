# Server 82 Deployment Runbook

This runbook assumes the AI search service runs on a separate Linux server and the existing Haeorum Gift site calls it over HTTPS API.

Use `deploy/production-handoff-checklist.md` as the input gate. Until the server, DB View, domain/TLS, and Gemini credential items there are filled, this runbook is not a final go-live approval.

Before deployment, send `deploy/server-db-request.ko.md`, fill `deploy/server-db-intake.md`, and run `scripts/go_live_scenario_check.py` plus `scripts/pre_handoff_audit.py` locally. The production stack is Marqo + Gemini; local GPU embedding containers must not be started for this runbook.

When the filled intake file is received, run:

```bash
python scripts/server_db_intake_check.py --intake-file deploy/server-db-intake.md --print-summary
python scripts/go_live_scenario_check.py --print-summary
```

Do not proceed to env creation, DB connection, or reindex until the status is `ready_for_env_and_server_preflight`.

Review `deploy/go-live-failure-scenarios.md` and `deploy/operational-risk-register.md` before opening traffic. They list the failure scenarios that must have evidence, including API abuse, Gemini quota exhaustion, backend overload, log disk growth, proxy header spoofing, DB View drift, scale state split, and rollback.

## 1. Required Server Setup

- Docker Engine and Docker Compose plugin installed
- Outbound HTTPS allowed for Gemini API
- Inbound public traffic allowed only to Apache/Nginx reverse proxy `80/443`
- API, Marqo, and Gemini embedding proxy published ports bind to `127.0.0.1` only; they are not bound to `0.0.0.0`
- Enough memory for the conservative profile: recommended `8GB+ RAM`

## 2. Environment

Create a protected env file from `deploy/haeorum-ai-search.env.example`.
For server 82, start from `deploy/server82.env.example` because it sets the
8GB guardrails, loopback ports, and host-mounted config/log paths.

Required production overrides:

```bash
HAEORUM_ENV=production
HAEORUM_SEARCH_ENGINE=marqo
MARQO_URL=http://marqo-api:8882
HAEORUM_MARQO_MODEL=Marqo/marqo-ecommerce-embeddings-L
HAEORUM_ADMIN_API_KEY=<strong-admin-key>
HAEORUM_CORS_ORIGINS=https://www.haeorumgift.com,https://haeorumgift.com
HAEORUM_INDEX_NAME=haeorum-products

HAEORUM_EMBEDDING_BACKEND=gemini
HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098
HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY=<internal-gemini-proxy-key>
HAEORUM_GEMINI_MODEL=gemini-embedding-2
HAEORUM_GEMINI_EMBEDDING_DIMENSIONS=1536
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_EMBEDDING_DIMENSIONS=1536
GEMINI_EMBEDDING_TIMEOUT_SECONDS=90
GEMINI_MAX_RESPONSE_BYTES=33554432
GEMINI_PROXY_API_KEY=<internal-gemini-proxy-key>
GEMINI_PROXY_MAX_INPUTS_PER_REQUEST=64
GEMINI_PROXY_MAX_CONCURRENT_CALLS=12
GEMINI_PROXY_QUEUE_TIMEOUT_SECONDS=15
GEMINI_PROXY_RATE_LIMIT_RPM=1200
GEMINI_PROXY_RATE_LIMIT_BURST=240
GEMINI_EMBEDDING_PORT=8098

# Use API key in production if that is the chosen operating method.
GEMINI_AUTH_MODE=api_key
GEMINI_API_KEY=<protected-gemini-api-key>

# If using Google ADC instead:
# GEMINI_AUTH_MODE=adc
# GEMINI_QUOTA_PROJECT=<google-cloud-project-id>

# If the reverse proxy targets a Docker-published API port, include the Docker bridge CIDR.
# Do not expose the API port directly to the internet when trusting forwarded headers.
HAEORUM_TRUSTED_PROXY_IPS=127.0.0.1,::1,172.16.0.0/12
HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD=5
HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS=5
HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS=1
MARQO_API_KEEPALIVE_TIMEOUT=75
MARQO_API_GZIP_MINIMUM_SIZE=1024
```

Initial guardrails:

```bash
HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=900
HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=300
HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE=2000
HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE=600
HAEORUM_SEARCH_MAX_CONCURRENCY=16
HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY=3
HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS=6
HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS=10
HAEORUM_DOCKER_LOG_MAX_SIZE=20m
HAEORUM_DOCKER_LOG_MAX_FILE=5
```

The longer queue timeouts are intentional on the 8GB profile. They let short bursts around 100 simultaneous text searches queue instead of failing immediately while still keeping image searches bounded. The per-IP search/image rate limits are intentionally high enough for the required 850-user mixed-traffic evidence; use Apache/Nginx/WAF abuse controls and mall-level limits for public protection during rollout.

## 3. Start

```bash
docker compose \
  -f compose-haeorum-marqo.yaml \
  -f compose-haeorum-gemini.yaml \
  -f compose-haeorum-existing-8gb.yaml \
  -f compose-haeorum-server82.yaml \
  up -d --build

python scripts/compose_exposure_check.py --print-summary
python scripts/go_live_scenario_check.py --print-summary
```

The compose exposure check must show API, Marqo, and Gemini embedding proxy host ports on `127.0.0.1`, with no AI API/Marqo/Gemini line on `0.0.0.0`.

## 4. Index Products

```bash
docker compose \
  -f compose-haeorum-marqo.yaml \
  -f compose-haeorum-gemini.yaml \
  -f compose-haeorum-existing-8gb.yaml \
  -f compose-haeorum-server82.yaml \
  --profile reindex run --rm reindex-once
```

## 5. Apache

server82 currently runs Apache httpd on `80/443`; Nginx is not installed. Use `deploy/apache/haeorum-ai-search.conf` and replace:

- `ServerName ai-search.haeorumgift.com`
- certificate paths
- `ProxyPass` upstream port if different from `127.0.0.1:8120`

The config must overwrite forwarded headers:

```apache
RequestHeader unset Forwarded early
RequestHeader unset X-Forwarded-For early
RequestHeader set X-Forwarded-For "%{CLIENT_REMOTE_ADDR}e" early
RequestHeader set X-Real-IP "%{CLIENT_REMOTE_ADDR}e" early
```

## 6. Smoke Checks

```bash
curl -fsS http://127.0.0.1:8120/health
curl -fsS -H "X-Admin-Key: $HAEORUM_ADMIN_API_KEY" http://127.0.0.1:8120/admin/metrics
curl -fsS http://127.0.0.1:8098/health
```

For host-run operational evidence, set `/etc/haeorum-ai-search/operational-evidence.config.json` endpoints to the loopback ports:

```json
"marqo": {
  "url": "http://127.0.0.1:8882",
  "gemini_embedding_url": "http://127.0.0.1:8098"
}
```

Do not put those loopback URLs into the Docker service env; containers still use `marqo-api` and `gemini-embedding` service DNS.

Open the local admin dashboard through the HTTPS domain after Apache/Nginx is connected:

```text
https://ai-search.haeorumgift.com/admin-ui
```

The Gemini card must show the selected model, auth mode, quota project/API key state, provider calls, failures, and active calls. Google billing credits may update later than API usage, so use this admin page plus Google AI Studio/Cloud usage together.
It must also show `Proxy auth=true`; if false, the embedding proxy is reachable without the internal shared secret and the stack is not ready for production traffic.

Run the local handoff audit against the deployed API:

```bash
python scripts/pre_handoff_audit.py \
  --require-runtime \
  --base-url http://127.0.0.1:8120 \
  --mall-id <mall-id> \
  --admin-key "$HAEORUM_ADMIN_API_KEY" \
  --api-key <mall-public-api-key> \
  --origin https://www.haeorumgift.com
```

Run a public API check from the deployment host:

```bash
python scripts/load_test.py \
  --base-url http://127.0.0.1:8120 \
  --mall-id shop001 \
  --api-key <mall-public-api-key> \
  --origin https://www.haeorumgift.com \
  --admin-key "$HAEORUM_ADMIN_API_KEY" \
  --allow-local-target \
  --mode text \
  --unique-query-suffix server82-backend-proof \
  --requests 50 \
  --concurrency 10
```

Use `--unique-query-suffix` on admin-metrics evidence runs when you need to prove the Marqo/Gemini backend path. Without it, repeated local test queries can be served from search/query-vector cache and the latency result is still useful, but backend-attempt deltas may stay at zero.

## 7. Rollout Gate

Initial production rollout should be banner/opt-in exposure, not full search replacement.

Promote to broader exposure only after:

- `/health` remains `ok=true`
- p95 search latency stays below target during real traffic
- 429 rate is expected and not affecting normal users
- Gemini quota errors are `0`
- Marqo/Gemini circuit breakers remain closed
- memory does not grow continuously over several hours

For multiple API containers, add Redis and set `HAEORUM_REDIS_URL` so rate limit/cache are shared.
