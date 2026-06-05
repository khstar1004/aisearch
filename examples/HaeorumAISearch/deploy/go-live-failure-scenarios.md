# Go-Live Failure Scenarios

이 문서는 서버/DB 정보를 받기 전에 고정해 둔 운영 장애 시나리오 표입니다. 실제 반입 전에는 개발 트리의 `scripts/go_live_scenario_check.py`, 운영 번들의 `tools/go_live_scenario_check.py`를 실행해 아래 방어막이 현재 코드와 배포 파일에 남아 있는지 확인합니다.

외부 자료 기준:

- Google Gemini API rate limits: Gemini 제한은 RPM, TPM, RPD처럼 여러 축으로 적용되고 project 단위로 평가됩니다.
- OWASP API4:2023 Unrestricted Resource Consumption: 운영 API는 timeout, 업로드 크기, 반환 record 수, 파일 descriptor, 외부 서비스 비용/한도까지 제한해야 합니다.
- Google SRE Handling Overload: overload에서는 빠른 거절, bounded retry, degraded response, per-customer/request limit이 필요합니다.
- Google SRE Monitoring Distributed Systems: 사람을 깨우는 알림은 사용자 영향 증상 중심이어야 하고, 대시보드는 query/error/latency/saturation 지표를 보여야 합니다.
- Google Cloud Billing budgets: 실제/예상 비용 기준 alert를 걸 수 있지만 billing 데이터 지연이 있으므로 여유 있는 임계값이 필요합니다.
- Docker json-file logging driver: Docker json-file 로그는 파일 기반이므로 `max-size`/`max-file` 회전 설정이 필요하고, 기존 컨테이너는 재생성해야 설정이 적용됩니다.
- OWASP API7/API10: 외부 URL/API 응답을 소비할 때 scheme, host, redirect, private network target, timeout, 응답 크기, schema를 방어적으로 제한해야 합니다.

| ID | 실제로 자주 나는 문제 | 현재 방어막 | 증거 |
| --- | --- | --- | --- |
| `abusive_or_accidental_traffic_spike` | 검색/이미지 요청 폭주, 악의적 호출, API 비용/CPU 고갈 | IP/mall rate limit, 요청 body/image size 제한, 검색/이미지 queue gate, load test | `app/main.py`, `app/config.py`, `scripts/load_test.py` |
| `gemini_quota_429_or_cost_runaway` | Gemini quota/RPM/TPM 초과, 429, 외부 API 비용 증가, provider 응답 과대 | Gemini proxy rate limit/concurrency/response-size cap, query vector cache, circuit breaker, 관리자 usage 지표, quota/budget intake | `app/gemini_embedding_proxy.py`, `app/gemini_embeddings.py`, `app/engine.py`, `admin_dashboard.html`, `deploy/server-db-intake.md` |
| `backend_overload_retry_explosion` | Marqo/Gemini 장애 시 재시도 폭증, threadpool 고갈, tail latency 급증 | backend active request slot, Retry-After cap, bounded retry, circuit breaker, fail-fast 503 | `app/engine.py`, `app/metrics.py`, `OPERATIONS.md` |
| `disk_or_log_exhaustion` | Docker/app 로그 누적으로 디스크 100%, 서비스 중단 | Docker json-file rotation, app JSONL logrotate, disk usage metrics, intake validation | `compose-haeorum-marqo.yaml`, `deploy/logrotate/haeorum-ai-search`, `app/metrics.py` |
| `internal_port_exposure` | AI API/Marqo/embedding 내부 포트가 인터넷에 직접 노출 | Compose loopback binding, embedding proxy no host publish, Nginx-only ingress, exposure checker | `compose-haeorum-marqo.yaml`, `scripts/compose_exposure_check.py`, `deploy/nginx/haeorum-ai-search.conf` |
| `db_view_drift_or_stale_index` | MSSQL View 컬럼 변경, 삭제/비노출 상품 미반영, stale Marqo 문서 | MSSQL View checker, batched export, deletion signal validation, sync lock, stale cleanup evidence | `scripts/mssql_view_check.py`, `scripts/mssql_export_csv.py`, `app/sync.py` |
| `credential_cors_or_mall_config_misuse` | API key 유출, query/body key 허용, CORS wildcard, 가맹점 설정 오염 | Header-only key, body/query key rejection, env/security checks, mall origin/API key validation | `app/main.py`, `scripts/env_check.py`, `scripts/security_check.py` |
| `malformed_query_encoding_or_charset_drift` | 브라우저/프록시/운영자 테스트 환경의 문자셋 문제로 한글 검색어가 `?? ??` 또는 replacement 문자로 깨져 엉뚱한 상품이 노출됨 | `SearchRequest` 단계에서 깨진 검색어를 400/422로 거절, OpenAPI UTF-8 계약, go-live runtime public search check | `app/models.py`, `contracts/openapi.json`, `scripts/go_live_scenario_check.py` |
| `widget_integration_or_rollback_failure` | 기존 쇼핑몰 검색창/모바일/CSP와 충돌하거나 장애 시 기존 검색으로 못 돌아감 | Widget probe, fallback floating mode, CSP risk detection, rollback contact/intake | `scripts/widget_integration_probe.py`, `widget/widget.js`, `deploy/server-db-intake.md` |
| `multi_api_scale_state_split` | API 서버 2대 이상에서 캐시/rate limit이 분리되어 품질·방어 기준이 흔들림 | Redis-required-for-scale check, admin metrics source coverage, API scale comparison | `scripts/env_check.py`, `scripts/load_test.py`, `scripts/load_compare.py` |
| `observability_alerting_gap` | 장애가 나도 latency/error/queue/cost/sync 알림을 운영자가 바로 못 봄 | `/admin/metrics`, Prometheus metrics, 관리자 대시보드, sync alert webhook, incident first-10-minute checklist | `app/metrics.py`, `admin_dashboard.html`, `scripts/security_check.py`, `deploy/production-incident-runbook.md` |
| `unsafe_external_url_or_image_source` | 상품 이미지/대표 사이트 URL로 SSRF, private IP 접근, redirect loop, 외부 응답 hang 발생 | safe URL parser, public-network resolution, safe redirect handler, URL/image checker, representative site checker | `app/url_safety.py`, `app/gemini_embeddings.py`, `scripts/image_url_check.py`, `scripts/representative_site_check.py` |
| `deployment_restart_or_rollback_gap` | 배포/재시작 실패 후 502/504가 지속되거나 이전 경로로 못 되돌림 | Compose healthcheck/restart policy, systemd restart/hardening, Nginx upstream fail policy, incident rollback runbook | `compose-haeorum-marqo.yaml`, `deploy/systemd/haeorum-ai-search.service`, `deploy/nginx/haeorum-ai-search.conf`, `deploy/production-incident-runbook.md` |
| `index_rebuild_or_sync_recovery_gap` | DB View 변경/색인 오염 후 재색인, 삭제 반영, 캐시 무효화 복구 경로가 불명확함 | reindex profile/service/timer, sync lock, delete-from-index event, cache clear event, stale-index runbook | `compose-haeorum-marqo.yaml`, `deploy/systemd/haeorum-ai-reindex.*`, `app/sync.py`, `deploy/production-incident-runbook.md` |
| `cost_budget_notification_gap` | Gemini 사용량이 늘어도 quota/budget 알림이 없어 비용 또는 크레딧 소모를 늦게 발견함 | Gemini quota/budget intake gate, admin Gemini usage, billing alert item in incident runbook | `deploy/server-db-intake.md`, `admin_dashboard.html`, `deploy/production-incident-runbook.md` |
| `operator_surface_gemini_only` | 운영자 문서/데모에 레거시 로컬 GPU provider 또는 임시 벡터 데모 표현이 다시 노출되어 실제 반입 경로가 헷갈림 | Operator-facing surface scan, Gemini+Marqo only docs/demo gate | `scripts/go_live_scenario_check.py`, `README.md`, `OPERATIONS.md`, `deploy/*` |

운영 반입 직전 실행:

```bash
python scripts/go_live_scenario_check.py \
  --base-url https://ai-search.haeorumgift.com \
  --admin-key "$HAEORUM_ADMIN_API_KEY" \
  --mall-id "<운영몰ID>" \
  --origin "https://www.jclgift.com" \
  --public-api-key "$HAEORUM_PUBLIC_API_KEY" \
  --output /var/log/haeorum-ai-search/go-live-scenario-check.json \
  --markdown-output /var/log/haeorum-ai-search/go-live-scenario-check.md \
  --print-summary
```

로컬 데모 확인:

```powershell
python scripts\go_live_scenario_check.py `
  --base-url http://127.0.0.1:8120 `
  --admin-key dev-admin-key `
  --mall-id shop001 `
  --origin http://127.0.0.1:3000 `
  --public-api-key public-shop001-dev-key `
  --print-summary
```
