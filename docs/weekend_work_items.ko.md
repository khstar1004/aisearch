# 주말 집중 작업 목록

기준일: 2026-06-06

이 문서는 `packages/commerce_ai_search` 제품 패키지의 현재 코드 상태를 기준으로, 토요일/일요일 동안 끝내기 적합한 고효율 작업을 정리한다.

## 완료

1. 패키지 설치 안정화
   - `commerce_ai_search` editable 설치가 `legacy`, `resources` 최상위 디렉터리 자동 감지 때문에 실패하던 문제를 수정했다.
   - `pyproject.toml`에 `build-system`과 `tool.setuptools.packages.find`를 명시했다.

2. 검색 정확도 회귀 테스트와 복합어 개선
   - `검정우산`, `스텐텀블러`, `송월타올`처럼 붙여 쓰는 한국어 상품 검색어를 동의어/상품 용어 기준으로 분해하도록 개선했다.
   - 분해된 복합어 조각에도 tenant별 사용자 정의 동의어가 적용되도록 보강했다.
   - 로컬 검색 엔진의 색상, 카테고리, 오타 허용, 몰 필터, 가격/수량/납기 필터 테스트를 추가했다.

3. 운영 로그 계측 버그 수정
   - `SearchLogger`의 idle close 카운터가 `return` 뒤에 있어 증가하지 않던 버그를 수정했다.
   - 검색 로그 tail과 민감정보 마스킹 테스트를 추가했다.

4. 부하 방어선 계측 보강
   - rate limit bucket overflow pruning이 실제로 일어나도 `pruned_buckets` 지표에 반영되지 않던 문제를 수정했다.
   - 검색 큐 포화, 이미지 검색 큐 포화, 큐 대기 후 정상 진입, rate limit window 복구, 프로덕션 threadpool 산식 테스트를 추가했다.
   - 동일 텍스트 검색 동시 요청이 하나의 엔진 호출로 합쳐지는 search singleflight 테스트를 추가했다.

5. 위젯 UI/UX 방어선 보강
   - 이미지 업로드 dropzone에 button role과 aria label을 추가했다.
   - loading/notice 영역에 screen reader용 status semantics를 추가했다.
   - API 응답의 `top`, `items`, `suggested_categories`가 비배열일 때 렌더링이 깨지지 않게 방어했다.
   - 상품 이미지 URL이 없거나 로드 실패할 때 빈 이미지 요청 대신 `이미지 없음` 상태를 렌더링하도록 수정했다.

6. 이미지 업로드 엣지케이스 테스트
   - base64 오류, MIME spoofing, 작은 이미지, 용량 초과, 리사이즈, 극단 비율 경고, Content-Length 제한 테스트를 추가했다.
   - multipart 업로드 스트림 제한과 동일 이미지 동시 검증 singleflight 동작을 테스트로 고정했다.

7. API 라우트와 수명주기 회귀 테스트
   - FastAPI `TestClient` 기반으로 `/health`, `/api/ai-search`, `/widget.js`, admin 인증, API key 전달 오류, rate limit, oversized JSON 응답을 고정했다.
   - deprecated `@app.on_event` startup/shutdown 훅을 lifespan으로 전환해 테스트 경고 없이 앱 수명주기를 유지했다.

8. 결과 페이지 정적 UX/보안 보강
   - `ai-search.html` 업로드 dropzone과 file input에 접근성 라벨을 추가했다.
   - 결과 요약에 적용 중인 카테고리 필터가 표시되도록 수정했다.
   - 추천 카테고리 버튼에 `aria-pressed` 상태를 추가했다.
   - 상품/이미지 URL 렌더링에서 credentials, localhost/link-local, 잘못된 포트, 상대 경로, 제어문자/공백/역슬래시를 거르도록 위젯 수준의 URL 방어선을 맞췄다.
   - 검색 결과 상태에서 hero 영역을 압축하고 자동 scroll을 제거해 모바일 첫 화면에 검색 입력, 업로드, 결과 요약, 첫 상품 카드가 함께 보이도록 조정했다.
   - 위젯과 결과 페이지 정적 자산의 핵심 방어선이 빠지지 않도록 unit test를 추가했다.

9. 운영 오류/Redis fallback 회귀 테스트
   - 내부 예외가 발생해도 `/api/ai-search`가 `internal server error`만 반환하고 error log에는 sanitized detail과 error type을 남기는지 고정했다.
   - Redis rate limiter 장애 시 local fallback으로 요청을 처리하고, backoff 중 Redis 호출을 건너뛴 횟수가 지표에 반영되는지 테스트했다.

10. 검색 대표 fixture 확장
   - 우산, 텀블러, 타올, 포스트잇, 볼펜, 에코백, 달력, 상패, 손선풍기, 보조배터리, 물티슈, 네임택, 키링, 마우스패드 대표 질의의 top 결과를 고정했다.
   - 머그컵, 물병, USB, 종이컵, 앞치마, 마스크, 구급함, 목걸이줄, 스티커, 배너, 파우치, 종이백, 클립, 노트, 시계, 충전기, 케이블, 거치대, 핸드워시, 마그넷까지 대표 fixture를 30개 이상으로 확장했다.
   - 색상/재질/동의어/오타/붙여쓰기 질의와 브랜드/가격/수량/납기 조합 필터를 로컬 검색 엔진 회귀 테스트에 추가했다.

11. 운영 자원 프리셋 문서화
   - `docs/commerce_ai_search_resource_presets.ko.md`에 CPU/RAM 규모별 concurrency, threadpool, rate limit, Redis 권장값을 정리했다.
   - 프로덕션 검증 산식인 `search_max_concurrency + image_search_max_concurrency + 8` 기준을 문서화했다.

12. `/admin/metrics` 샘플 응답 고정
   - metrics JSON에서 engine, rate limit, cache, singleflight, search queue, image queue, API threadpool 상태가 노출되는지 테스트했다.
   - Prometheus 출력에 threadpool required tokens, rate limit, search/image queue concurrency series가 포함되는지 고정했다.

13. public API 오류 계약 추가 고정
   - multipart 이미지 검색에서 body 초과는 413, 지원하지 않는 이미지 bytes는 400으로 반환되는지 테스트했다.
   - click-log의 `product_url`이 mall 상품 URL 템플릿 외부 도메인이면 400으로 거부되는지 고정했다.

14. 이미지 파일 품질/정규화 엣지케이스 추가
   - 손상된 PNG/JPG/WEBP payload가 `image is damaged or cannot be decoded`로 거부되는지 테스트했다.
   - EXIF orientation이 있는 JPEG가 transpose되어 width/height와 normalized flag가 반영되는지 고정했다.
   - animated WEBP, CMYK JPEG, 큰 EXIF metadata를 가진 JPEG가 안전하게 decode/분석되는지 테스트했다.

15. 동의어 설정 감사와 term 폭증 방어
   - `query-synonyms.json` 로더가 그룹을 정규화하고 양방향 링크로 확장하는지 테스트했다.
   - 사용자 동의어 설정에서 정규화 후 중복 key, 중복 value, 자기참조, 기본 동의어와의 overlap을 감지하는 `audit_query_synonyms` 헬퍼를 추가했다.
   - 복합어 분해와 사용자 동의어 확장이 중복 term을 만들지 않고 제한된 개수로 유지되는지 테스트했다.

16. 로컬 검색 micro benchmark 추가
   - `packages/commerce_ai_search/tests/benchmarks/local_search_micro_benchmark.py`를 추가했다.
   - 1천/1만 상품 fixture에서 질의별 p50/p95, min/max, hit 수, 첫 상품 ID를 JSON으로 출력한다.
   - smoke로 `--sizes 1000 --iterations 3 --limit 10` 실행을 확인했다.

17. restricted mall click-log 접근 제어 고정
   - mall config가 활성화된 상태에서 click-log도 API key 누락은 401, origin 누락/불일치와 mall 불일치는 403으로 거부되는지 테스트했다.
   - 유효한 API key, origin, mall, product URL 조합은 `{"ok": true}`로 기록되는지 고정했다.

18. reverse proxy client IP rate limit 분리
   - trusted proxy 뒤에서 `X-Forwarded-For`의 첫 client IP를 rate limit key로 쓰는지 테스트했다.
   - 같은 forwarded IP의 두 번째 검색은 429로 제한되고, 다른 forwarded IP는 별도 bucket으로 통과하는지 고정했다.

19. 로컬 API/위젯 smoke 자동화
   - `packages/commerce_ai_search/tests/smoke/local_api_widget_smoke.py`를 추가했다.
   - in-process 모드에서 `/health`, `/widget.js`, `/api/ai-search`, `/api/click-log`, `/admin/metrics`, `/admin/metrics.prom`, `demo.html`, `ai-search.html` 계약을 JSON으로 확인한다.
   - `--base-url http://127.0.0.1:8120`를 주면 실행 중인 로컬 API 서버에도 같은 smoke를 적용할 수 있고, `--api-key`, `--origin`, `--mall-id`로 restricted mall public API까지 검증한다.
   - `--expected-first-product-id=`는 특정 ID 고정 대신 top 결과 존재 여부만 검증하고, `--skip-click-log`는 배포 전 live URL 계약 확인처럼 읽기 전용 smoke에 사용한다.
   - smoke 실행 결과 모든 check가 `ok: true`임을 확인했다.

20. Redis fallback 분산 배포 문서화
   - Redis 정상/장애 시 rate limit 적용 범위 차이를 문서화했다.
   - Redis 장애 중 replica 수만큼 local fallback 허용량이 커질 수 있음을 명시했다.
   - fallback 장기화 시 확인할 `/admin/metrics` 지표와 임시 limit 조정 기준을 정리했다.

21. 위젯 DOM 회귀 검사 재연결
   - 레거시 `widget_dom_check.js`가 현재 `resources/widget/widget.js`를 검사하도록 경로 해결을 보강했다.
   - fake DOM 기반으로 modal, trigger, image upload, drag/drop, keyboard trap, loading state, duplicate submit, unsafe URL neutralization, category refetch, click logging을 확인했다.
   - 실행 결과 `ok: true`와 4개 site scenario 통과를 확인했다.

22. Redis fallback 다중 replica 시나리오 테스트
   - Redis 장애가 발생한 두 API replica가 같은 client key를 각각 local fallback bucket으로 제한하는 동작을 테스트했다.
   - 클러스터 전체 rate limit는 Redis 정상 상태에서만 공유되고, 장애 중에는 replica별 limit로 완화될 수 있음을 회귀 테스트로 고정했다.

23. 실제 Chrome 렌더링 기반 UI 확인
   - 인앱 브라우저 `iab`는 현재 세션에 노출되지 않았지만, 로컬 Chrome headless로 `ai-search.html?q=스텐텀블러` desktop/mobile 캡처를 생성했다.
   - desktop 첫 화면에서 검색 입력, 업로드, 결과 요약, 추천 카테고리, 상위 상품 카드가 함께 보이는지 확인했다.
   - mobile 390px 폭에서도 로고, 검색 입력, 업로드, 결과 요약, 추천 카테고리, 첫 상품 카드가 한 화면에 들어오는지 확인했다.

24. 로컬 통합 환경 상태 확인
   - `docker ps` 기준 Marqo API, Vespa, embedding service, ai-search API 컨테이너가 실행 중임을 확인했다.
   - `http://127.0.0.1:8120/health` 기준 engine/backend는 `marqo`, index는 `haeorum-gemini-marqo-jclgift-live-full-20260602`, 문서 수는 103,632개, vector 수는 192,069개, Marqo/Gemini ready 상태임을 확인했다.
   - 컨테이너 env/config에서 키를 읽되 비밀값을 출력하지 않는 방식으로 `/admin/metrics`를 확인했고, threadpool은 configured/requested/runtime 512 tokens, required 360 tokens, search concurrency 256, image concurrency 96 상태임을 확인했다.

25. wheel 배포 리소스 누락 수정
   - 기존 wheel에는 `resources/widget/widget.js`, `ai-search.html`, sample CSV, contracts가 포함되지 않아 설치본에서 정적 파일/API contract가 빠지는 문제가 있었다.
   - `pyproject.toml`의 `data-files`에 runtime resources를 명시하고, source checkout은 기존 `resources/`를, wheel 설치본은 `sys.prefix/share/commerce_ai_search/resources`를 보도록 `default_resource_root()`를 추가했다.
   - wheel zip 내부에 resource 21개가 포함되는지 확인했고, 임시 venv에 wheel을 설치해 `ROOT`, `widget.js`, `ai-search.html`, `sample_products.csv` 존재를 검증했다.
   - 새 resource 파일을 추가했는데 wheel data-files에 넣지 않으면 실패하는 unit test를 추가했다.

26. 운영 검증 스크립트 기본 점검
   - 실제 운영 증적 수집과 부하 테스트는 별도 실행 창에서 수행해야 하므로 이번 변경 검증에서는 제외했지만, `collect_operational_evidence.py`, `api_smoke_test.py`, `load_test.py`, `load_compare.py`, `operational_readiness.py`, `operational_bundle_check.py`, `prepare_operational_bundle.py`, `marqo_gemini_exact_benchmark.py`의 Python 문법 검사를 통과시켰다.

27. live public search와 click-log URL 계약 점검
   - Docker 컨테이너 내부 env/config를 사용해 비밀값을 출력하지 않고 live public search를 실행했다.
   - `스텐텀블러` live search는 200으로 성공했고 top 3개, 첫 결과 `447951`, score 76.2%, engine `marqo`, latest smoke 기준 elapsed 37.6ms를 확인했다.
   - 동일 결과의 click-log는 `product_url is not allowed for mall`로 400이 발생했다. live index의 raw URL origin은 `https://www.jclgift.com`, mall config template origin은 `https://shop001.haeorumgift.com`이라 응답 URL과 click-log 검증 계약이 불일치했다.
   - `resolve_product_url()`이 mall별 `product_url_template`을 raw indexed URL보다 우선하도록 수정했고, raw URL이 외부 도메인인 restricted mall 검색에서도 응답 URL이 mall template으로 생성되는 unit test를 고정했다.
   - 현재 실행 중인 8120 컨테이너는 이전 코드이므로 live read-only smoke는 `--skip-click-log`로 통과시켰다.
   - current code를 8121 임시 컨테이너로 띄워 같은 Marqo/Gemini live index에 붙인 뒤 smoke를 재실행했고, 검색 응답 URL origin이 mall template의 `https://shop001.haeorumgift.com`으로 보정되며 `/api/click-log`가 200으로 통과하는 것을 확인했다.

28. 로컬 검색 엔진 동의어 확장 캐시 상한과 관측성 보강
   - 커스텀 동의어가 많은 tenant/query 조합에서 레코드 term 확장 캐시가 무한히 커질 수 있던 구조를 bounded LRU로 변경했다.
   - 동일한 동의어 내용은 dict/list 순서가 달라도 digest 기반으로 같은 캐시 entry를 재사용하도록 바꿨다.
   - 동일한 term set은 서로 다른 `frozenset` 객체여도 content 기반 cache key를 공유하도록 바꿔, 같은 카테고리/tag를 가진 상품이 많은 fixture에서 캐시 상한을 덜 소모하게 했다.
   - local engine `health()`에 `expanded_terms_cache.entry_count/max_entries`를 노출해 운영 지표에서 캐시 크기를 확인할 수 있게 했다.
   - 캐시 재사용, LRU eviction, health 노출, query synonym policy 변경 시 검색 응답 cache key가 달라지는 계약을 단위 테스트로 고정했고, 1천 상품 micro benchmark에서 대표 질의 p50 7.2-20.7ms 범위를 확인했다.

29. 분산 cache miss lock 부하 방어 테스트 추가
   - 두 API worker가 같은 Redis 계열 cache를 공유할 때 첫 worker만 miss owner가 되고, 두 번째 worker는 backend 검색을 중복 실행하지 않고 cache fill을 기다리는 계약을 단위 테스트로 고정했다.
   - lock contention, owner release, `lock_wait_events`, `lock_wait_timeouts=0`까지 확인해 cache stampede 방어선이 실제 호출 수를 줄이는지 검증했다.

30. mixed search weight overflow 방어
   - `text_weight=1e308`, `image_weight=1e308`처럼 개별 값은 finite지만 합계가 overflow되는 요청을 400으로 거부하도록 `SearchRequest` validator를 보강했다.
   - 검증을 우회한 내부 호출에서도 `_weights()`가 finite 합계를 다시 확인하도록 방어했다.
   - `HAEORUM_MIXED_TEXT_WEIGHT`, `HAEORUM_MIXED_IMAGE_WEIGHT` 환경 설정도 합계가 finite가 아니면 `validate_settings()`에서 거부하도록 보강했다.
   - 모델 단위 테스트, 설정 검증 테스트, `/api/ai-search` 400 응답 테스트를 추가했다.

31. 실제 Docker 배포 경로 동기화와 current-code live smoke
   - `examples/HaeorumAISearch` Docker 이미지가 `packages/commerce_ai_search`가 아니라 `examples/HaeorumAISearch/app`, `examples/HaeorumAISearch/widget`을 복사해 `app.main:app`으로 실행하는 구조임을 확인했다.
   - 패키지에서 수정한 engine, main, models, rate_limit, search_service, widget HTML/JS를 예제 배포 경로에 동기화했다.
   - 예제 전용 `config.py`는 root/resource 경로 차이를 유지한 채 mixed weight overflow 검증만 같은 계약으로 보강했다.
   - 기존 8120 컨테이너는 유지하고, 같은 이미지/네트워크/env와 현재 예제 app/widget bind mount로 8121 임시 컨테이너를 띄워 live Marqo/Gemini index smoke를 수행했다.
   - 8121 smoke에서 `/health`, `/widget.js`, `/api/ai-search`, `/api/click-log`, `/admin/metrics`, `/admin/metrics.prom`, demo/result page 정적 계약이 모두 통과했다.
   - 패키지 파일과 예제 Docker 배포 파일이 다시 벌어지지 않도록 engine/main/models/rate_limit/search_service/widget HTML/JS byte-for-byte 동기화 단위 테스트를 추가했다.

32. current-code live 소규모 부하 증적
   - 8121 임시 컨테이너에서 live Marqo/Gemini index를 대상으로 `load_test.py` text mode를 실행했다.
   - cold 상태 20건/동시성 4는 응답 20/20, 오류 0, queue full 0이었지만 p95 5387.8ms로 3000ms 기준을 넘었고, singleflight 평균 대기가 689.9ms로 runtime guardrail을 넘었다.
   - 같은 질의 warm 상태 20건/동시성 4는 p50 6.9ms, p95 10.2ms, p99 28.5ms까지 떨어졌지만 backend 요청 delta가 0이라 cache-only 수치로 분리했다.
   - `--unique-query-suffix`로 backend 경로를 강제한 14건/동시성 2는 p50 1779.6ms, p95 2761.0ms, p99 3407.6ms, 오류 0, RPS 1.02, search event delta 14, Marqo request delta 30, Gemini request delta 15로 통과했다.
   - 해당 run에서 rate limit 0, fallback 0, cache error 0, queue full 0, singleflight timeout 0, Marqo/Gemini error 0, circuit open/short 0, process RSS growth 1.0MiB, API threadpool OK를 확인했다.

33. 대표 질의 query embedding prewarm 경로 보강
   - cold text load에서 query embedding 대기가 p95에 큰 영향을 주는 것을 확인해, 운영 배포 직후 대표 질의를 cache에 미리 넣는 절차를 보강했다.
   - `build_query_embedding_cache.py`에 `--query`와 `--query-file` 옵션을 추가해 상품 CSV 파생 후보보다 운영 대표 질의를 먼저 cache 후보에 포함할 수 있게 했다.
   - query file은 UTF-8 한 줄 한 질의 형식이고, 빈 줄과 `#` 주석은 무시한다.
   - 예제 운영 스크립트와 패키지 legacy 스크립트를 동기화했고, CLI 값/query file 병합, 대표 질의 우선순위, 예제-패키지 스크립트 동기화를 단위 테스트로 고정했다.

34. runtime query embedding cache prewarm admin endpoint 추가
   - `POST /admin/prewarm-query-cache`를 추가해 API 컨테이너 재시작 직후 대표 질의를 runtime query embedding cache에 직접 채울 수 있게 했다.
   - 요청은 `queries` 최대 200개, `batch_size` 최대 128로 제한하고, 빈 질의/중복 질의/깨진 인코딩 질의를 정규화 또는 거부한다.
   - local/native backend에서는 안전한 no-op으로 `supported=false`를 반환하고, Gemini/Qwen backend에서는 기존 runtime cache, precomputed cache, singleflight key와 같은 cache key를 사용한다.
   - endpoint 응답에는 `computed`, `cached`, `deduplicated`, `skipped`, runtime text cache entry 수를 포함해 prewarm 효과를 운영자가 확인할 수 있게 했다.
   - 8121 current-code live smoke에서 `admin_query_cache_prewarm`이 통과했고, 이미 검색으로 올라간 `스텐텀블러`는 cached 1, 추가 대표 질의 `검정우산`은 computed 1로 확인했다.

35. image/mixed 빠른 smoke 계약 추가
   - `local_api_widget_smoke.py`가 in-process smoke에서는 기본으로 image search와 text+image mixed search까지 확인하도록 보강했다.
   - 외부 `--base-url` live smoke에서는 이미지 embedding 비용을 피하기 위해 기본 off로 두고, `--include-image-search`를 명시하면 image/mixed까지 검증한다.
   - image/mixed smoke는 query_type, top 결과 존재, 결과 URL origin, admin metrics, click-log, prewarm endpoint를 함께 확인한다.
   - 8121 current-code live smoke에서 text/image/mixed 모두 200으로 통과했고, text 첫 결과 `447951`, image 첫 결과 `492734`, mixed 첫 결과 `276248`, 세 결과 모두 `https://shop001.haeorumgift.com` origin을 사용했다.
   - 같은 run에서 image elapsed 1434.7ms, mixed elapsed 202.4ms, click-log 200, admin prewarm `computed=1/cached=1`을 확인했다.

36. 8120 Docker 배포 컨테이너 최신 코드 재생성과 최종 smoke/load
   - `examples/HaeorumAISearch` compose 경로에서 `ai-search` 이미지를 다시 빌드하고, 기존 8120 서비스만 `--no-deps`로 재생성했다.
   - 재생성 후 `http://127.0.0.1:8120/health`에서 engine `marqo`, embedding backend `gemini`, index `haeorum-gemini-marqo-jclgift-live-full-20260602`, 문서 103,632개, vector 192,069개 ready 상태를 확인했다.
   - 8120 live smoke를 `--include-image-search`로 다시 실행해 `/health`, `/widget.js`, text/image/mixed search, `/api/click-log`, `/admin/prewarm-query-cache`, `/admin/metrics`, `/admin/metrics.prom`, demo/result page 계약을 모두 통과시켰다.
   - 8120 smoke 결과는 최신 widget CSS 재빌드 후에도 통과했으며, text 첫 결과 `447951` 634.4ms, image 첫 결과 `492734` 891.0ms, mixed 첫 결과 `276248` 23.2ms였고, 세 결과 모두 `https://shop001.haeorumgift.com` origin을 사용했다.
   - 8120 backend-forced text load 14건/동시성 2는 p50 588.2ms, p95 752.6ms, p99 965.9ms, 오류 0, RPS 3.09로 통과했다. 같은 run의 search-log coverage는 14건 모두 확인했고, Marqo request delta 30, Gemini request delta 15, queue full 0, rate limit 0, circuit open/short 0, RSS growth 2.5MiB였다.
   - 8120 image/mixed 포함 소규모 load 9건/동시성 2는 text 1건, image 1건, mixed 7건으로 실행했고 p50 1082.7ms, p95 2186.5ms, p99 2186.5ms, 오류 0, RPS 1.47로 통과했다. Marqo request delta 19, Gemini request delta 10, search/image queue full 0, rate limit 0, circuit open 0, RSS growth 0.0MiB를 확인했다.

37. 실제 Chrome 기반 위젯 UI 감사와 모바일 mode strip 보정
   - Chrome headless에서 현재 `widget.js`를 직접 로드하고 fetch를 mock해 텍스트 검색, 낮은 유사도 안내, 이미지 없는 상품 카드, 추천 카테고리, 더보기 흐름을 실제 브라우저 DOM으로 렌더링했다.
   - 감사 결과 card 4개, `이미지 없음` placeholder 4개, 낮은 유사도 notice, 추천 카테고리 3개, fetch 2회, 더보기 종료 상태, 수평 overflow 없음이 확인됐다.
   - 모바일 캡처 검토 중 mode strip 라벨이 headless 스케일링 조건에서 잘릴 수 있음을 발견해, strip을 3열 grid로 바꾸고 라벨에 `overflow-wrap:anywhere`와 안정적인 line-height/padding을 적용했다.
   - 같은 CSS 수정은 패키지 widget과 `examples/HaeorumAISearch/widget` 배포 경로에 모두 반영했다.

38. 이미지 검색 반복 요청 속도와 손상 이미지 안정성 보강
   - 같은 이미지가 `image/jpg`와 `image/jpeg`처럼 MIME alias가 다른 data URL로 들어와도 같은 validation cache entry를 재사용하도록 canonical image validation key를 적용했다.
   - MIME spoofing 방어는 유지하기 위해 `image/png`와 `image/jpeg`처럼 실제로 다른 선언 MIME은 별도 validation key로 분리한다.
   - 손상되었거나 거부된 동일 이미지 payload는 validation error를 짧은 TTL 동안 negative cache에 저장해, 같은 공격성/실수성 요청이 반복될 때 PIL decode와 검증 작업을 매번 반복하지 않게 했다.
   - `/admin/metrics`와 Prometheus에 image validation error cache entries/hits/evictions를 노출해 손상 이미지 반복 유입 여부와 방어 효과를 운영에서 확인할 수 있게 했다.
   - 단위 테스트로 MIME alias cache hit, MIME spoofing key 분리, invalid image error cache hit, 패키지-예제 runtime sync를 고정했다.

39. 이미지 검색 첫 요청 비용과 부하 관측성 개선
   - `HAEORUM_QUERY_IMAGE_ANALYSIS`가 패키지 런타임에서 실제로 적용되지 않던 문제를 고쳐, 기본 off일 때 검색 업로드 이미지의 perceptual hash/품질 분석 pass를 건너뛰게 했다.
   - 검색용 이미지 정규화 기본값을 640px로 맞춰 이미지 검증/정규화와 Gemini image embedding payload 비용을 낮췄다.
   - 이미지 validation cache key에 query image analysis 설정을 포함해 분석 on/off 결과가 섞이지 않게 했다.
   - Gemini/Qwen runtime query vector cache에 text/image별 hit, miss, eviction 카운터를 추가하고 Prometheus에 노출했다.
   - 로컬 Docker `.env`의 이미지 검색 concurrency 96/queue wait 120초 설정을 8/2초로 낮춰 이미지 폭주가 오래 누적되지 않고 fail-fast로 보호되게 했다.
   - compose 기본값도 query image 640px, analysis off로 바꿔 별도 env 없이도 낮은 지연 중심으로 시작한다.
   - 단위 테스트로 feature analysis skip, 검색 서비스 설정 전달, runtime image vector cache hit/miss/eviction, Prometheus series를 고정했다.

## 완료 감사

- 리팩토링: lifespan 전환, wheel resource 경로, 예제 Docker 배포 경로 동기화, bounded LRU term cache, query prewarm service/API 분리까지 완료했다.
- 다양한 엣지케이스 테스트: 이미지 bytes/MIME/EXIF/animated WEBP/CMYK/손상 파일, API 오류 계약, click-log 접근 제어, reverse proxy IP rate limit, Redis fallback, mixed weight overflow, 동의어 감사, static widget 방어선을 단위 테스트로 고정했다.
- 검색 정확도 향상: 한국어 복합어 분해, 사용자 동의어 확장, 대표 fixture 30개 이상, live `스텐텀블러` 결과 계약을 확인했다.
- 검색 속도 향상: singleflight/cache, query embedding prewarm 파일/endpoint, query image analysis off, 640px 이미지 정규화, local micro benchmark, 8120 backend-forced text load와 image/mixed load 증적을 확보했다.
- 자원 최적화: threadpool 산식, search/image queue, rate limit/Redis fallback, cache miss lock, process RSS/queue full/circuit 지표를 코드와 문서에 반영했다.
- UI/UX 디테일: widget 접근성, 모바일 결과 밀도, URL guard, missing image placeholder, 낮은 유사도 안내, 더보기 흐름, 실제 Chrome 렌더링 감사를 완료했다.
- 부하 대응: search/image queue, rate limit, Redis fallback, distributed cache miss lock, image validation negative cache, runtime image vector cache hit/miss/eviction metrics, backend circuit/transport metrics, live smoke/load를 검증했다.

## 운영 후속 관찰

1. 검색 속도
   - 운영 트래픽 영향이 적은 시간대에 image/mixed 장시간 live API benchmark를 저장해 p50/p95 기준값을 계속 보정한다.
   - 대표 검색어가 바뀌면 query embedding cache 파일과 `/admin/prewarm-query-cache` 입력 목록을 같이 갱신한다.

2. 자원/설정 최적화
   - `/admin/metrics` 샘플을 정기적으로 저장하고 문서의 프리셋, queue full, Redis fallback 지표와 비교한다.

3. 운영 리허설
   - 배포 후 운영 브라우저와 실제 쇼핑몰 템플릿에서 widget 위치와 클릭 로그 유입을 한 번 더 관찰한다.
   - Marqo/Gemini live API에 대한 장시간 load test는 운영 트래픽 영향이 적은 시간대에 별도 실행 창에서 수행한다.
