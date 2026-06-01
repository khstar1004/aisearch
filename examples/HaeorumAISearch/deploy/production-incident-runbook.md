# Production Incident Runbook

이 문서는 82번 서버 반입 후 운영자가 장애를 빠르게 판단하고 기존 쇼핑몰 피해 없이 되돌릴 수 있도록 만든 런북입니다. 서버/DB 정보를 받기 전에도 아래 항목은 코드와 배포 템플릿에 고정해 둡니다.

## External Operating Lessons

- Google SRE Monitoring Distributed Systems: 운영자는 query count, error count, latency, service lifetime 같은 내부 지표를 볼 수 있어야 하며, 사람을 깨우는 알림은 사용자 영향이 있는 증상 중심이어야 합니다.
- Google SRE Handling Overload: backend 과부하에서는 빠른 거절, 제한된 retry, load shedding, client/backoff 정책이 필요합니다.
- Google Cloud Billing budgets: 예산 알림은 비용 임계값과 forecast를 기준으로 email 또는 Pub/Sub로 보낼 수 있고, 비용 데이터에는 지연이 있으므로 여유 있게 낮은 임계값을 둬야 합니다.
- Docker json-file logging driver: json-file 로그는 기본적으로 무제한일 수 있으므로 `max-size`와 `max-file` 회전 설정이 필요하고 기존 컨테이너는 재생성해야 적용됩니다.
- OWASP API8/API10: API stack 보안 설정, 외부 API/URL 응답 검증, redirect 제한, timeout과 `GEMINI_MAX_RESPONSE_BYTES` 같은 응답 크기 제한은 운영 방어의 일부입니다.

## Severity

| 등급 | 증상 | 즉시 조치 |
| --- | --- | --- |
| P0 | 기존 쇼핑몰 주문/상품 페이지 영향, AI 검색 API가 전체 요청을 지연시킴, Nginx 5xx 급증 | 위젯 비활성화 또는 기존 검색 fallback, API upstream 제외, 장애 기록 시작 |
| P1 | AI 검색만 실패, p95/p99 급증, Gemini 429/5xx 반복, Marqo/Gemini circuit open | rate limit 강화, cache TTL 확인, backend circuit/queue 지표 확인, 필요 시 API 1대씩 재시작 |
| P2 | 일부 mall만 결과 없음, 일부 이미지 실패, sync 지연, 품질 저하 | mall config, MSSQL View, 이미지 URL, sync log, search insights 확인 |

## First 10 Minutes

1. `/health`에서 `ready=true`, `engine=marqo`, `embedding_backend=gemini`, `gemini_ready=true`, 문서 수가 예상 범위인지 확인합니다.
2. `/admin/metrics`와 `/admin/metrics.prom`에서 `alerts`, p95/p99, `rate_limited_events`, `search_queue_full_events`, `image_queue_full_events`, `backend_*_circuit_open`, `backend_*_retry_after_responses`, `disk_used_percent`, `system_memory_used_percent`를 확인합니다.
3. Nginx error/access log에서 4xx/5xx, upstream timeout, client body too large, CORS preflight 실패를 확인합니다.
4. `logs/sync.jsonl`에서 `sync_failed`, `sync_alert_failed`, `sync_lock_busy`, `search_cache_clear_failed`, 삭제/숨김 상품 이벤트를 확인합니다.
5. Gemini 사용량/쿼터 페이지와 Cloud Billing budget alert 수신 상태를 확인합니다.
6. 문제가 기존 쇼핑몰 UX에 영향을 주면 먼저 AI 위젯을 비활성화하고 기존 검색 fallback을 유지합니다.

## Scenario Runbooks

### Traffic Spike Or Abuse

- 신호: `rate_limited_events`, `search_queue_full_events`, `image_queue_full_events`, 동일 IP/mall의 짧은 burst 증가.
- 확인: `/admin/search-log`, `/admin/metrics`, Nginx access log.
- 조치: `HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE`, `HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE`, mall별 제한을 낮추고 Nginx/WAF에서 악성 IP를 차단합니다. 이미지 요청이 원인이면 이미지 queue와 업로드 크기 제한을 먼저 유지합니다.

### Gemini Quota Or Cost Spike

- 신호: Gemini `rate_limited_total`, `provider_call_total`, `provider_elapsed_ms_max`, `Retry-After`, Cloud Console 429/usage 증가.
- 확인: `/admin/metrics`, Gemini API usage/quota, Billing budget alerts.
- 조치: 반복 검색 cache TTL과 runtime cache를 확인하고, 이미지 검색 rate limit을 임시로 낮춥니다. budget alert 미수신이면 운영 signoff를 중단합니다.

### Marqo Or Gemini Backend Overload

- 신호: `backend_marqo_*` 또는 `backend_gemini_*` error/close/retry-after/circuit 지표 증가, p95/p99 급증.
- 확인: `/admin/metrics`, Docker stats, `marqo-resource.json`, Vespa/Marqo logs.
- 조치: circuit breaker가 열려 fail-fast 되는지 확인합니다. backend retry 횟수를 늘리지 말고, queue/concurrency와 API 서버 수를 먼저 조정합니다.

### DB View Drift Or Stale Index

- 신호: 특정 상품 미노출/삭제 상품 노출, `mssql_view`/`mssql_export` 실패, index 문서 수와 CSV active product 수 불일치.
- 확인: `mssql_view_check.py`, `mssql_export_csv.py`, `csv_index.py`, `logs/sync.jsonl`.
- 조치: View 컬럼/타입/삭제 신호를 복구한 뒤 `reindex-once` 또는 `haeorum-ai-reindex.service`를 실행합니다. 전체 재색인 전에는 기존 검색 fallback을 유지합니다.

### Unsafe External URL Or Image Source

- 신호: 이미지 다운로드 timeout 급증, SSRF 의심 URL, private/link-local IP, redirect chain 이상, 대표 사이트 probe 실패.
- 확인: `image_url_check.py`, `representative_site_check.py`, `widget_integration_probe.py`, `url_safety` rejection 로그.
- 조치: 비공개 IP/localhost/link-local/credential 포함 URL은 색인에서 제외하고, 상품 원본 데이터를 고칩니다. 외부 이미지 timeout을 늘려 해결하지 않습니다.

### Disk, Memory, Or Log Exhaustion

- 신호: `disk_usage_high`, `system_memory_used_percent`, Docker log 급증, JSONL write error.
- 확인: `/admin/metrics`, `docker system df`, `/var/log/haeorum-ai-search`, container log size.
- 조치: logrotate와 Docker json-file rotation 적용 여부를 확인하고, 필요 시 로그를 보존 후 압축/이관합니다. 용량 확보 전 재색인은 금지합니다.

### Widget Integration Failure

- 신호: 기존 검색창 클릭 불가, 모바일 레이아웃 깨짐, CSP로 widget/API 차단, 특정 mall만 검색 불가.
- 확인: saved PC/mobile HTML 기반 `widget_integration_probe.py`, browser console, CSP headers.
- 조치: 해당 mall 위젯 자동 mount를 끄거나 fallback floating mode로 전환합니다. 기존 검색 기능은 유지해야 합니다.

### Deployment Or Restart Failure

- 신호: 배포 직후 `/health` not ready, Nginx upstream 502/504, `gemini_ready=false`, Marqo index 404/0 docs.
- 확인: `docker compose ps`, healthcheck, `go_live_scenario_check.py --base-url`, `api_smoke_test.py`.
- 조치: 새 API를 upstream에서 제외하거나 이전 env/compose로 되돌립니다. reindex와 sync worker는 API health가 정상화된 뒤 실행합니다.

## Required Pre-Signoff Alerts

- API: 5xx ratio, p95/p99 latency, rate limit surge, search/image queue full, process RSS, disk usage.
- Backend: Marqo/Gemini circuit open, Retry-After, error responses, stale reconnects, provider latency.
- Sync: `sync_failed`, `sync_product_failures`, `sync_batch_failures`, `sync_lock_contention`, `sync_alert_failed`.
- Data: active product count/index document count mismatch, stale evidence, missing image URL ratio.
- Cost: Gemini quota usage, 429 count, Cloud Billing budget actual and forecast thresholds.

## Recovery Exit Criteria

- `/health.ready=true`, `embedding_backend=gemini`, document count matches accepted dataset.
- `go_live_scenario_check.py --base-url ...` passes.
- `api_smoke_test.py` passes for text, image, mixed, CORS, click, admin, and Prometheus checks.
- `operational_readiness.py` has no failed/missing production evidence.
- Existing search fallback and widget rollback steps were tested after the incident.
