# Commerce AI Search 자원/설정 프리셋

기준일: 2026-06-06

이 문서는 `packages/commerce_ai_search` 운영 시 CPU/RAM 규모별로 먼저 적용해볼 수 있는 보수적인 설정값을 정리한다. 실제 값은 `/admin/metrics`, API 지연시간, 이미지 검색 비율, Redis 사용 여부를 보고 조정한다.

## 공통 산식

`HAEORUM_API_THREADPOOL_TOKENS`는 최소한 아래 값을 만족해야 한다.

```text
HAEORUM_SEARCH_MAX_CONCURRENCY
+ HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY
+ 8
```

프로덕션 설정 검증도 이 산식을 사용한다. 이미지 검색은 업로드 검증과 검색 슬롯을 함께 쓰므로, 이미지 검색 비율이 높으면 `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`를 올리기 전에 CPU와 응답 지연을 먼저 확인한다.

## 권장 프리셋

| 용도 | 서버 기준 | 검색 동시성 | 이미지 동시성 | threadpool tokens | Redis | 비고 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| local/dev | 2 CPU, 4GB RAM | 4 | 1 | 16 | 선택 | 기능 확인과 단위 smoke용 |
| small | 2-4 CPU, 4-8GB RAM | 8 | 2 | 18 | 선택 | 낮은 트래픽 쇼핑몰 1-2개 |
| standard | 4-8 CPU, 8-16GB RAM | 32 | 4 | 44 | 권장 | 일반 운영 시작점 |
| default/high | 8-16 CPU, 16GB+ RAM | 64 | 8 | 96 | 권장 | 현재 코드 기본 동시성에 맞춘 운영값 |
| image-heavy | 8-16 CPU, 16GB+ RAM | 48 | 12 | 80 | 권장 | 이미지 업로드 비중이 높은 경우 |

## 환경 변수 예시

### small

```env
HAEORUM_SEARCH_MAX_CONCURRENCY=8
HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY=2
HAEORUM_API_THREADPOOL_TOKENS=18
HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=120
HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE=600
HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=10
HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE=60
HAEORUM_RATE_LIMIT_MAX_BUCKETS=5000
```

### standard

```env
HAEORUM_SEARCH_MAX_CONCURRENCY=32
HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY=4
HAEORUM_API_THREADPOOL_TOKENS=44
HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=300
HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE=1500
HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=20
HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE=120
HAEORUM_RATE_LIMIT_MAX_BUCKETS=10000
HAEORUM_REDIS_URL=redis://redis.internal:6379/0
```

### default/high

```env
HAEORUM_SEARCH_MAX_CONCURRENCY=64
HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY=8
HAEORUM_API_THREADPOOL_TOKENS=96
HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=600
HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE=3000
HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=40
HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE=240
HAEORUM_RATE_LIMIT_MAX_BUCKETS=20000
HAEORUM_REDIS_URL=redis://redis.internal:6379/0
```

## 운영 확인 포인트

1. `/admin/metrics`에서 `api_threadpool.ok`, `search_execution_gate.queue_full_events`, `image_search_gate.queue_full_events`를 확인한다.
2. Redis를 켠 경우 `rate_limit.fallback_events`, `rate_limit.redis_backoff_skipped_operations`, `rate_limit.fallback_bucket_count`가 계속 증가하는지 본다.
3. `search_singleflight.in_flight`와 cache hit 지표를 함께 본다. 동일 질의가 많은데 cache hit가 낮으면 TTL과 cache key 구성을 먼저 점검한다. Redis cache를 쓰는 경우 `cache.lock_contention_events`, `cache.lock_wait_events`, `cache.lock_wait_timeouts`도 같이 확인해 miss lock이 backend 중복 호출을 줄이는지 본다.
4. 이미지 검색이 느리면 `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`를 무작정 올리기보다 `HAEORUM_MAX_IMAGE_MB`, `HAEORUM_QUERY_IMAGE_MAX_DIMENSION`, `HAEORUM_QUERY_IMAGE_ANALYSIS`, 이미지 검증 cache hit를 함께 확인한다. 기본 운영값은 640px 리사이즈와 query image analysis off로 첫 이미지 요청 비용을 줄이는 쪽이다.
5. queue full이 0에 가깝고 CPU 여유가 충분할 때만 동시성을 한 단계 올린다. threadpool tokens도 같은 산식으로 같이 올린다.
6. local engine을 쓰는 개발/검증 환경에서는 `/health` 또는 `/admin/metrics`의 `engine.expanded_terms_cache.entry_count/max_entries`를 확인해 커스텀 동의어 조합이 캐시 상한을 지속적으로 밀어내는지 본다. 같은 카테고리/tag term set은 content 기반 cache key를 공유하므로, entry count가 계속 상한에 붙어 있으면 tenant별 동의어 조합 수나 fixture 다양도부터 확인한다.
7. mixed search weight를 외부에서 조정할 때는 `text_weight + image_weight`가 finite 범위에 머물러야 한다. 비정상적으로 큰 요청값은 400으로 거부되고, `HAEORUM_MIXED_TEXT_WEIGHT + HAEORUM_MIXED_IMAGE_WEIGHT`가 overflow되는 환경 설정은 시작 시 검증에서 거부된다. 운영 기본값은 0-1 범위의 비율값으로 유지한다.
8. 손상 이미지나 잘못된 MIME 이미지가 반복 유입되면 `/admin/metrics`와 Prometheus의 `image_validation.error_cache_*` 또는 `haeorum_image_validation_error_cache_*` 지표를 확인한다. error cache hit가 올라가면 같은 invalid payload의 반복 decode를 방어하고 있다는 뜻이다.
9. Gemini 이미지 검색이 몰릴 때는 `engine.gemini_query_embedding_cache.runtime_image_hits/misses/evictions` 또는 `haeorum_gemini_query_vector_runtime_image_cache_*`를 본다. hit가 낮으면 고유 이미지 비율이 높거나 cache TTL/공유 캐시가 부족한 것이고, eviction이 높으면 runtime image vector cache quota를 늘릴지 검토한다.

## Redis rate limit fallback

`HAEORUM_REDIS_URL`이 설정되면 rate limit는 Redis 기준으로 공유된다. Redis 호출이 실패하거나 backoff가 열리면 각 API 프로세스는 local fallback bucket을 사용한다.

- Redis 정상: 모든 API replica가 같은 Redis counter를 공유하므로 `HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE`, `HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE`가 클러스터 단위 제한에 가깝게 동작한다.
- Redis 장애: 각 replica가 자체 memory bucket을 사용한다. replica가 `N`개이면 장애 중 실제 허용량은 최악의 경우 설정값의 약 `N`배까지 커질 수 있다.
- 장애가 길어지면 `/admin/metrics`의 `rate_limit.fallback_events`, `rate_limit.fallback_active`, `rate_limit.redis_backoff_skipped_operations`, `rate_limit.fallback_bucket_count`를 확인한다.
- fallback이 5분 이상 지속되면 Redis 복구를 우선하고, 필요하면 임시로 per-minute limit를 replica 수만큼 낮춘다.
- fallback bucket은 프로세스 메모리에만 있으므로 프로세스 재시작 시 초기화된다. 장애 중 재시작이 잦으면 rate limit가 더 느슨해질 수 있다.
- 단위 테스트는 두 replica가 Redis 장애 중 같은 client key를 각각 local fallback으로 제한하는 상황을 고정한다. 실제 클러스터 부하는 replica 수와 L7 분산 방식에 따라 운영 환경에서 별도 확인한다.

## 로컬 검색 속도 측정

설정 변경 전후에는 같은 명령으로 로컬 검색 기준 p50/p95를 비교한다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search"
python packages\commerce_ai_search\tests\benchmarks\local_search_micro_benchmark.py --sizes 1000 10000 --iterations 20 --limit 20
```

출력은 JSON이며 `product_count`, `build_ms`, 질의별 `p50_ms`, `p95_ms`, `hit_count`, `first_product_id`를 포함한다. 운영 서버의 기준값을 잡을 때는 같은 CPU/RAM, 같은 Python 버전, 같은 `HAEORUM_*` 설정에서 3회 이상 반복 측정한다.

## 로컬 API/위젯 smoke

브라우저 수동 확인 전에는 API와 위젯 정적 자산 계약을 먼저 확인한다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search"
python packages\commerce_ai_search\tests\smoke\local_api_widget_smoke.py
```

실제 로컬 서버가 `8120` 포트에서 실행 중이면 같은 smoke를 외부 HTTP로 적용할 수 있다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search"
python packages\commerce_ai_search\tests\smoke\local_api_widget_smoke.py --base-url http://127.0.0.1:8120 --admin-key dev-admin-key
```

restricted mall 설정이 켜진 서버는 public API key와 허용 origin도 함께 넘긴다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search"
python packages\commerce_ai_search\tests\smoke\local_api_widget_smoke.py `
  --base-url http://127.0.0.1:8120 `
  --admin-key <admin-key> `
  --mall-id <mall-id> `
  --api-key <public-api-key> `
  --origin <allowed-origin> `
  --query "스텐텀블러" `
  --expected-first-product-id=
```

`--expected-first-product-id=`처럼 빈 값으로 넘기면 특정 상품 ID 대신 top 결과 존재 여부만 검증한다. 배포 전 live 서버처럼 검색 응답 URL과 mall template URL 계약이 아직 맞지 않는 환경에서는 `--skip-click-log`를 추가해 읽기 전용 smoke만 수행하고, 새 코드 배포 후에는 옵션을 제거해 `/api/click-log`까지 200 응답을 확인한다.

in-process smoke는 image search와 text+image mixed search를 기본으로 포함한다. `--base-url`을 넘기는 live smoke는 이미지 embedding 비용을 피하기 위해 기본적으로 text search만 확인하고, 아래처럼 `--include-image-search`를 추가하면 image/mixed까지 확인한다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search"
python packages\commerce_ai_search\tests\smoke\local_api_widget_smoke.py `
  --base-url http://127.0.0.1:8120 `
  --admin-key <admin-key> `
  --mall-id <mall-id> `
  --api-key <public-api-key> `
  --origin <allowed-origin> `
  --query "스텐텀블러" `
  --expected-first-product-id= `
  --include-image-search
```

이 smoke는 `/health`, `/widget.js`, `/api/ai-search`, `/api/click-log`, `/admin/prewarm-query-cache`, `/admin/metrics`, `/admin/metrics.prom`, `demo.html`, `ai-search.html`의 핵심 계약을 JSON으로 보고한다. 8120 latest-code live smoke에서는 text/image/mixed 모두 200으로 통과했고, 결과 URL origin이 모두 mall template origin으로 생성됐다.

UI 변경 후에는 실제 Chrome headless로 widget을 한 번 더 렌더링해 검색, 낮은 유사도 notice, 이미지 없는 상품 placeholder, 추천 카테고리, 더보기 흐름을 확인한다. 이번 감사에서는 card 4개, `이미지 없음` placeholder 4개, fetch 2회, 더보기 종료 상태, 수평 overflow 없음이 확인됐고, 모바일 mode strip은 라벨이 잘리지 않도록 3열 grid와 `overflow-wrap:anywhere`로 보정했다.

## Live text load 해석 기준

반복 질의 부하 테스트는 query embedding runtime cache와 search response cache의 영향을 크게 받는다. 운영 기준을 잡을 때는 아래 세 값을 분리해서 본다.

- cold run: cache가 비어 있거나 대표 query embedding이 없는 상태다. Gemini embedding 대기와 singleflight wait가 p95에 직접 반영된다.
- warm run: 같은 질의를 반복해 cache가 올라간 상태다. 사용자 체감 반복 검색과 cache 효율을 보기에 좋지만, backend Marqo/Gemini delta가 0이면 backend 성능 증적으로 보지 않는다.
- backend-forced run: `--unique-query-suffix`를 붙여 요청별 query를 다르게 만든다. backend path와 admin metrics delta를 증명할 때 사용한다.

이번 latest-code live 검증에서는 재생성된 8120 컨테이너로 14건/동시성 2 backend-forced text load를 실행했고, p50 588.2ms, p95 752.6ms, p99 965.9ms, 오류 0, RPS 3.09, Marqo request delta 30, Gemini request delta 15, queue full 0, circuit open/short 0으로 통과했다. 같은 질의 warm run처럼 backend delta가 0인 결과는 cache-only 결과로 분류한다.

image/mixed 경로는 8120에서 text 1건, image 1건, mixed 7건의 소규모 live load로 확인했다. 결과는 p50 1082.7ms, p95 2186.5ms, p99 2186.5ms, 오류 0, RPS 1.47, Marqo request delta 19, Gemini request delta 10, search/image queue full 0, rate limit 0, circuit open 0이었다. 이 값은 smoke 수준의 빠른 기준이며, 운영 p50/p95 임계값은 트래픽 영향이 적은 시간대의 장시간 run으로 별도 확정한다.

검색 첫 요청 지연을 줄이려면 운영 배포 직후 대표 검색어 query embedding cache를 prewarm한다. cold text p95가 기준을 넘는 경우에는 동시성부터 올리기보다 대표 query 목록, query embedding cache, Gemini 응답 시간, singleflight wait를 먼저 확인한다.

대표 질의를 직접 포함한 cache 파일은 아래처럼 만든다. `--query`와 `--query-file`로 들어온 값은 상품 CSV에서 파생한 후보보다 먼저 들어가므로, `--max-queries`가 작아도 핵심 질의가 잘리지 않는다.

```powershell
$env:PYTHONPATH="D:\aisearch\examples\HaeorumAISearch"
python examples\HaeorumAISearch\scripts\build_query_embedding_cache.py `
  --product-csv D:\aisearch\examples\HaeorumAISearch\logs\jclgift-products-1800-supported.csv `
  --output D:\aisearch\examples\HaeorumAISearch\logs\query-embedding-cache.json.gz `
  --query "스텐텀블러" `
  --query "검정우산" `
  --query "송월타올" `
  --query-file D:\aisearch\examples\HaeorumAISearch\representative-queries.txt `
  --max-queries 20000 `
  --compact
```

생성한 파일은 `HAEORUM_QWEN_QUERY_EMBEDDING_CACHE_PATH` 또는 호환 alias인 `HAEORUM_GEMINI_QUERY_EMBEDDING_CACHE_PATH`로 API 컨테이너에 주입한다.

cache 파일을 미리 만들지 못했거나 배포 직후 runtime cache를 바로 데우려면 admin endpoint를 사용한다.

```powershell
$headers = @{ "X-Admin-Key" = "<admin-key>" }
$body = @{
  queries = @("스텐텀블러", "검정우산", "송월타올", "에코백", "보조배터리")
  batch_size = 5
} | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "https://<api-host>/admin/prewarm-query-cache" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

응답의 `supported=true`, `computed`, `cached`, `runtime_text_entries`를 확인한다. `supported=false`는 local/native backend처럼 query embedding runtime cache가 쓰이지 않는 환경이라는 뜻이다. 이번 8120 latest-code live smoke에서는 검색으로 이미 올라간 `스텐텀블러`가 cached 1, 추가 대표 질의 `검정우산`이 computed 1로 확인됐다.

## Docker 예제 배포 경로 확인

`examples/HaeorumAISearch` compose 이미지는 패키지 설치본이 아니라 `examples/HaeorumAISearch/app`과 `examples/HaeorumAISearch/widget`을 이미지에 복사한 뒤 `app.main:app`으로 실행한다. 패키지 코드에서 API, 검색, rate limit, widget 계약을 바꿨다면 예제 배포 경로도 같이 동기화한 뒤 이미지를 재빌드하거나 컨테이너를 재생성해야 한다.

기존 8120 컨테이너를 건드리지 않고 current code를 live Marqo/Gemini index에 붙여 확인하려면 같은 이미지/네트워크/env를 사용하되 현재 `app`/`widget` 디렉터리를 bind mount한 임시 컨테이너를 별도 포트에 띄운다. 이번 검증에서는 8121 임시 컨테이너에서 restricted mall public smoke를 `--skip-click-log` 없이 실행했고, 검색 응답의 상품 URL이 mall template origin으로 생성되며 `/api/click-log`까지 200으로 통과했다.

8120 운영 컨테이너를 실제로 최신 코드로 바꿀 때는 compose 경로에서 이미지를 재빌드한 뒤 `ai-search` 서비스만 재생성한다.

```powershell
Set-Location D:\aisearch\examples\HaeorumAISearch
docker compose `
  -f compose-haeorum-marqo.yaml `
  -f compose-haeorum-marqo-gemini-localtest.yaml `
  build ai-search
docker compose `
  -f compose-haeorum-marqo.yaml `
  -f compose-haeorum-marqo-gemini-localtest.yaml `
  up -d --no-deps ai-search
```

이번 검증에서는 위 절차로 8120 서비스를 재생성한 뒤 health에서 `marqo`/`gemini` ready, index `haeorum-gemini-marqo-jclgift-live-full-20260602`, 문서 103,632개, vector 192,069개를 확인했다. 이후 `--include-image-search` live smoke, backend-forced text load, image/mixed 소규모 load가 모두 통과해 8120 배포 경로도 최신 코드 기준으로 확정했다.
