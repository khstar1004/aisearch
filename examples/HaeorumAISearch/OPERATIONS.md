# 해오름기프트 AI 검색 운영 체크리스트

이 문서는 상위 저장소의 `../../docs/plan.md`, `../../docs/development_plan.md` 기준으로 MVP를 운영 환경에 붙이기 전 확인해야 할 항목입니다.

현재 반입 기준 기본 구성은 **Marqo + Gemini embedding API**입니다. 운영 env는 `HAEORUM_EMBEDDING_BACKEND=gemini`와 `HAEORUM_GEMINI_*`를 사용하세요. 필요한 컨테이너는 `ai-search`, `gemini-embedding`, `marqo-api`, `mioc`, `vespa` 다섯 개입니다. 로컬 GPU 임베딩 컨테이너는 Gemini 운영 구성에 띄우지 않습니다. 실행 상태 기준은 `deploy/runtime-stack-gemini-marqo.md`를 우선합니다.

서버/DB 정보 수령 전에는 `deploy/server-db-request.ko.md`를 기존 개발자에게 보내고 `deploy/server-db-intake.md`로 남은 입력값을 채웁니다. `deploy/go-live-failure-scenarios.md`, `deploy/production-incident-runbook.md`, `deploy/operational-risk-register.md`로 운영 리스크별 증거와 장애 대응 절차를 맞춘 뒤, 반입 직전에는 `scripts/go_live_scenario_check.py --mall-id <운영몰ID> --origin <운영Origin> --public-api-key <공개검색키> --print-summary`와 `scripts/pre_handoff_audit.py --require-runtime --mall-id <운영몰ID>`로 Gemini compose 기본값, 로컬 GPU 임베딩 컨테이너 미실행, Nginx/secret 기본 보안값, `/health`, `/admin/metrics`, 정상 UTF-8 한글 검색, 깨진 한글 검색어 거절, 대표 검색, 데모 페이지를 확인합니다.

## 1. 필수 외부 연결

### Marqo

- `MARQO_URL`에서 `/health` 또는 `/` 응답이 정상이어야 하며, Gemini 백엔드 운영에서는 API `GET /health`가 Gemini `/health`의 ready/model/dimensions/proxy auth 계약까지 `ready=true`와 `proxy_auth_configured=true`로 증명해야 합니다.
- `HAEORUM_INDEX_NAME` 값이 운영 인덱스명과 일치해야 합니다.
- 최초 운영 반영 전 `POST /admin/reindex`로 색인을 생성합니다. Marqo index settings 조회가 404를 반환할 때만 자동 생성하며, 5xx/timeout/연결 오류는 backend 장애로 보고 색인을 중단합니다.
- 모델 변경 또는 검색 품질 튜닝 후에는 전체 재색인이 필요합니다.
- Marqo 모델명은 `HAEORUM_MARQO_MODEL`로 관리합니다. 모델을 바꾸면 기존 벡터와 호환되지 않을 수 있으므로 전체 재색인을 수행합니다.
- Marqo OSS 리스크가 현실화되면 `app/engine_factory.py`의 `typesense` 또는 `qdrant` 예비 어댑터 자리에 실제 구현을 붙입니다. 현재 예비 어댑터는 `health()`에서 `reserved_adapter=true`를 반환하고 검색/색인 호출은 실패하도록 막혀 있습니다. `HAEORUM_ENV=production` 런타임에서는 `app/engine_factory.py`가 `marqo` 외 엔진 생성을 거절하므로 예비 어댑터가 배포 승인 증거로 쓰일 수 없습니다.

### MSSQL

- 운영 계정은 read-only 권한이어야 합니다.
- AI 검색용 View는 아래 필드를 제공해야 합니다.

```sql
product_id, -- p_idx/id/product_no/goods_no/상품번호 별칭 허용
product_name, -- name/title/goods_name/상품명 별칭 허용
price, -- sell_price/sale_price/판매가/가격 별칭 허용
price_min, -- 선택, 가격 범위 하한
price_max, -- 선택, 가격 범위 상한
category_name, -- category/category_nm/카테고리명 별칭 허용
print_methods, -- 선택, print_method/printing_methods/printing 별칭 허용
materials, -- 선택, material/소재/재질 별칭 허용
colors, -- 선택, color/색상/컬러 별칭 허용
min_order_qty, -- 선택, minimum_order_qty/moq/최소주문수량 별칭 허용
delivery_days, -- 선택, lead_time_days/delivery_lead_days/납기일수 별칭 허용
product_group_id, -- 선택, group_id 별칭 허용
main_image_url, -- image_url/대표이미지URL 별칭 허용
product_url, -- url/detail_url/상품상세URL 별칭 허용
status, -- state/상품상태 별칭 허용
updated_at, -- update_dt/수정일시 별칭 허용
is_deleted, -- 또는 display_yn/show_yn/삭제여부/노출여부 중 최소 하나
mall_id -- site_id/shop_code/가맹점ID 별칭 허용
```

- 삭제/비노출/품절 상태가 어떤 값으로 들어오는지 운영 전에 확정해야 합니다.
- `updated_at`은 `POST /admin/sync`와 정기 worker가 변경 상품만 가져오는 기준입니다.
- 색상/용량/공급사만 다른 중복 상품은 같은 `product_group_id`를 내려주면 검색 API가 대표 1개만 우선 노출합니다. 값이 비어 있으면 상품번호 단위로 개별 노출됩니다.
- `product_group_id` collapse 전에 검색 API는 관련 상품 `offset/limit`보다 넓은 후보를 Marqo에 요청합니다. 텍스트 검색은 Marqo rerank 품질을 위해 이미 가져온 최소 후보 창을 collapse 단계까지 넘겨, 옵션 중복 때문에 같은 `/search`를 불필요하게 다시 호출하지 않도록 합니다. collapse 뒤에도 후보가 부족하고 검색엔진이 요청 한도만큼 결과를 돌려준 경우에는 Marqo 후보 상한 안에서 후보 수를 단계적으로 늘려 재조회합니다. 운영 부하 리포트에서 Marqo `/search` 후보 수가 응답 상품 수보다 큰 것은 옵션 중복이 Top/related 페이지를 잠식하지 않게 하는 정상 동작입니다.
- 검색 로그와 `/admin/metrics`의 `engine_search_attempts`, `engine_adaptive_refetches`, `engine_candidate_limits`, `engine_underfilled_after_max_candidates`로 후보 overfetch/adaptive refetch를 관측합니다. `engine_adaptive_refetches`가 특정 부하에서 크게 늘면 상품군 옵션 중복이 backend 요청 수를 키우는 병목 후보이고, `engine_underfilled_after_max_candidates`가 발생하면 Marqo 후보 상한까지 넓혀도 해당 검색어의 상품 다양성이 부족한 상태입니다. 운영 readiness는 load/API scale 리포트에 검색 시도/재조회/후보 한도 지표가 없거나 `engine_underfilled_after_max_candidates_events` delta가 0보다 크면 검색 후보 보강 증거를 실패 처리합니다.
- 운영 View가 상품번호를 `p_idx`로만 노출하면 `HAEORUM_MSSQL_PRODUCT_ID_COLUMN=p_idx`를 설정합니다. 변경 시각 컬럼명도 `HAEORUM_MSSQL_UPDATED_AT_COLUMN`으로 바꿀 수 있으며 두 값은 단순 컬럼 식별자만 허용됩니다.
- 운영 MSSQL 연결 문자열에는 `Server`, `Database`, `Encrypt=yes` 또는 `Encrypt=mandatory/strict`, `TrustServerCertificate=no`, `ApplicationIntent=ReadOnly`를 명시합니다. Production 설정 로딩, env preflight, collector dry-run, MSSQL evidence CLI는 이 값을 먼저 검사해 암호화가 꺼졌거나 서버 인증서를 무조건 신뢰하거나 read-only intent가 빠진 연결 문자열을 배포 전에 차단합니다.
- View 초안은 `sql/v_ai_search_products_template.sql`을 참고합니다.
- 운영 연결 전 `python scripts/mssql_view_check.py`로 필수 컬럼, read-only 쿼리, 샘플 row 파싱, `updated_at` 누락/형식 오류/미래값, active 상품 이미지/상품 URL/mall 식별값 누락, unsafe 이미지/상품 URL, 가격/수량/납기/속성 필터 필드 coverage, DB role/permission 기준 read-only 계정 여부를 확인합니다. 컬럼명은 공백/대소문자 차이를 정규화하고 `goods_no`, `상품번호`, `판매가`, `대표이미지URL`, `가맹점ID` 같은 운영 export 별칭도 같은 상품 파서로 처리합니다. `column_report.noncanonical_required_aliases`와 `column_report.suggested_select_list`에는 운영 View를 canonical `AS product_id`/`AS mall_id` 형태로 고칠 projection 힌트가 남습니다. `HAEORUM_MSSQL_QUERY`는 설정 로딩과 env preflight에서도 단일 `SELECT`/`WITH` read-only 문장만 허용하며 쓰기/실행 키워드와 SQL 주석을 차단합니다. 연결/드라이버/쿼리 오류가 나도 `--output` JSON은 `ok=false`로 남고 connection string password와 URL credentials는 마스킹됩니다.
- PoC/초기 색인 CSV가 필요하면 `python scripts/mssql_export_csv.py`로 read-only View를 정규화된 CSV로 내보냅니다. export는 ODBC `fetchmany` batch로 row를 가져오며 `fetch_size`, `fetch_batches`, `max_fetch_batch_rows`, `batched_fetch`를 리포트에 남깁니다. 운영 readiness는 batch fetch 증거가 없으면 대량 MSSQL export가 메모리 피크에 취약한 구형 리포트로 보고 실패합니다. export는 전체 row 기준 중복 상품번호, `updated_at` 누락/형식 오류/미래값, active 상품 이미지/상품 URL/mall 식별값 누락, unsafe URL 건수와 `domain_filter_coverage`를 남기며 하나라도 있으면 실패합니다. View가 active 상품만 반환하면 삭제/비노출 상품을 색인에서 정리할 수 없으므로 `inactive_products=0`, `source_deletion_signal_ok=false`로 실패합니다. `domain_filter_coverage`는 active 상품의 가격 필터 가능 여부, 가격 범위, 최소 주문 수량, 납기, 인쇄 방식, 소재, 색상 값이 운영 데이터에 실제로 들어 있는지 확인합니다. `--mall-config /etc/haeorum-ai-search/malls.json`을 함께 넘기면 active 상품의 `mall_id`가 enabled mall에 있는지, `product_url`이 해당 mall의 `product_url_template` prefix와 맞는지도 `mall_config_alignment`로 검증합니다. export 실패 시에도 `--report-output` JSON이 생성되어 실패 원인을 남기며 connection string 원문은 기록하지 않습니다.
- API 서버는 `HAEORUM_MSSQL_QUERY`가 `SELECT` 또는 `WITH`로 시작하는 단일 read-only 문장인지 검사합니다. `UPDATE`, `DELETE`, `INSERT`, `MERGE`, `EXEC`, `SELECT INTO`, `USE`, `DECLARE`, `SET`, 트랜잭션 제어문, SQL 주석 등 쓰기/실행/우회 가능 구문이 들어가면 기동 또는 동기화 전에 실패시킵니다.
- View 샘플 점검, 증분 동기화, 단일 상품 재색인, CSV export는 이 쿼리를 `ai_products` 파생 테이블로 감싼 뒤 필요한 `TOP`/설정된 변경시각 컬럼/설정된 상품번호 컬럼 조건과 정렬을 붙입니다. `WITH` CTE query는 최종 `SELECT`만 파생 테이블로 감싸 SQL Server에서 `FROM (WITH ...)` 문법 오류가 나지 않도록 처리합니다.
- 실제 MSSQL 접근 전에는 `python scripts/operational_simulation.py`의 `sync-lifecycle.json`으로 변경분 동기화, `updated_at` cutoff와 같은 시각의 행 포함, 숨김/삭제 상품 제거, 원본에서 사라진 상품 단건 재색인 삭제, 전체/단건 재색인의 중복 상품번호 fail-closed 처리, 검색 캐시 무효화 로그, sync lock 충돌 로그, stale sync lock 자동 회수가 로컬 엔진에서 유지되는지 먼저 확인할 수 있습니다. 같은 시뮬레이션의 `mssql-export.json`은 가상 MSSQL export와 mall config alignment를, `mssql-alias-compatibility.json`은 `goods_no`/`shop_code` 같은 레거시 영문 컬럼과 `상품번호`/`가맹점ID` 같은 한글 export 컬럼이 같은 상품 파서와 View 샘플 검증을 통과하는지 확인합니다. `mixed-weight-sweep.json`은 혼합 검색 text/image 가중치 후보별 품질과 충돌 민감도를, `response-materialization-probe.json`은 응답으로 반환되는 Top/related 상품만 materialize하면서 `product_group_id` 중복 collapse 뒤에도 페이지가 비지 않도록 후보 overfetch와 adaptive refetch가 동작하는지 확인합니다. `widget-integration-probe.json`은 저장 HTML 기준 검색 input selector, `data-hai-auto-init` 스니펫, CSP/외부 `widget.js` 위험, HTTPS 비로컬 API/widget URL 오류를 미리 확인합니다. `operational-risk-probes.json`은 위젯 프로브가 unsafe API/widget/page URL, CSP 외부 widget 차단, 검색창 미검출을 실제로 리스크로 잡는지, MSSQL export의 미래 `updated_at`, active-only export의 삭제/비노출 신호 누락, PoC/색인 CSV의 unsafe 또는 HTTP 대표 이미지 URL, `--validate-images` 누락, stale Marqo 문서 수, placeholder/sample 대표 이미지 경고 및 mall config의 중복 enabled origin/상품 URL prefix를 차단하는지, 대표 사이트 중복 mall/url/origin/API key 설정, 대표 사이트 저장 PC/mobile HTML 중복 캡처, 부하/API scale 클라이언트 keep-alive 재사용 누락, backend active request slot 포화 시 추가 백엔드 연결 없이 503으로 빠르게 실패하고 circuit breaker를 오염시키지 않는지, Redis cache miss lock 오류와 follower wait timeout, singleflight wait timeout, mall config와 맞지 않는 대표 사이트 API key, 관련 상품 wrong-mall URL, strict mall 검색이 전체 로컬 상품을 훑는 병목을 차단하는지도 negative control로 확인합니다. `local-search-load-probe.json`은 query type별 지연 통계, 느린 샘플, 시뮬레이션 클릭 이벤트 수를 남겨 운영 부하 테스트 전에 어떤 검색 경로가 병목인지와 검색 로그 기반 CTR/클릭 attribution 분석이 동작하는지 좁히는 데 씁니다. 이 산출물은 운영 증거가 아니며, 배포 후에는 `api_smoke_test.py`, `csv_index.py`, `representative_site_check.py`, `load_test.py`, `security_check.py` 운영 증거로 같은 동작을 다시 확인해야 합니다.

### 이미지 서버

- AI 검색 서버에서 `main_image_url`에 직접 접근 가능해야 합니다.
- JPG, PNG, WEBP 대표 이미지를 제공해야 합니다.
- 대량 색인 중 이미지 서버 부하가 높으면 동기화 worker 주기와 배치 크기를 조정합니다.
- `HAEORUM_VALIDATE_PRODUCT_IMAGES=true`이면 동기화 중 대표 이미지를 다운로드해 포맷/손상/최소 크기를 검증하고 실패 상품을 색인에서 제외합니다.
- 검증에 성공해도 투명/컷아웃 배경, 낮은 대비/단색 배경, URL 기반 워터마크/샘플 이미지 힌트는 `image_quality_warning` 로그로 남깁니다. 이 경고는 색인 제외 조건이 아니라 운영 품질 점검용입니다.
- Marqo 색인 payload에는 안전한 절대 HTTP(S) 대표 이미지 URL만 넣고, 상품 URL은 안전한 절대 HTTP(S) URL 또는 루트 상대경로만 저장합니다. `javascript:`, credential 포함 URL, protocol-relative URL, localhost/사설망/예약 IP URL은 View/export 단계와 색인 payload 단계에서 모두 제거 또는 실패 처리됩니다. 검색 시 가격/수량/납기 필터는 Marqo range filter로 pushdown되므로 이미지/혼합 검색의 후보 수를 불필요하게 키우지 않습니다. 인쇄 방식/소재/색상처럼 운영 데이터 표기가 흔들릴 수 있는 fuzzy 텍스트 속성 필터만 앱 후처리를 위해 추가 후보를 가져옵니다.

## 2. 환경 변수

`.env.example`을 기준으로 운영 값을 채웁니다.

필수:

- `HAEORUM_ENV=production`
- `HAEORUM_SEARCH_ENGINE=marqo`
- `MARQO_URL`
- `HAEORUM_MARQO_MODEL`
- `HAEORUM_EMBEDDING_BACKEND=gemini`
- `HAEORUM_GEMINI_EMBEDDING_URL`
- `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`
- `HAEORUM_GEMINI_EMBEDDING_DIMENSIONS`
- `HAEORUM_GEMINI_MODEL`
- `GEMINI_PROXY_API_KEY`
- `HAEORUM_INDEX_NAME`
- `HAEORUM_ADMIN_API_KEY` 개발/placeholder 값이 아닌 16자 이상의 운영 key
- `HAEORUM_MALL_CONFIG_PATH`
- `HAEORUM_CORS_ORIGINS`
- `HAEORUM_MSSQL_READONLY_CONNECTION_STRING`, 호환용 `HAEORUM_MSSQL_CONNECTION_STRING`, 또는 `HAEORUM_PRODUCT_CSV`

`MARQO_URL`은 API 서버에서 접근하는 내부 Marqo endpoint입니다. `http://localhost:8882` 같은 개발·단일 호스트 값은 허용하지만 절대 HTTP(S) URL이어야 하며 credentials, query string, fragment, 공백, 역슬래시, 잘못된 port, link-local/unspecified host는 설정 로딩과 env preflight에서 거절됩니다. production에서는 `...`, `<...>`, `replace-with...`, `change-me` 같은 예시/placeholder 값을 그대로 둔 필수 env도 거절됩니다.
`HAEORUM_REDIS_URL`은 `redis://redis:6379/0` 또는 TLS용 `rediss://redis:6380/0` 같은 절대 Redis URL이어야 합니다. placeholder, 잘못된 port, link-local/unspecified host, URL fragment, 잘못된 DB path는 설정 로딩과 env preflight에서 거절됩니다.
`HAEORUM_CORS_ORIGINS`와 mall별 `allowed_origins`는 `https://shop.example.com` 형식의 origin만 입력합니다. `/path`, `?query`, 계정 정보가 포함된 URL은 기동 전에 거절되며, 운영에서는 wildcard `*`를 사용할 수 없습니다. mall별 `allowed_origins`에 있는 모든 origin은 전역 `HAEORUM_CORS_ORIGINS`에도 포함되어야 합니다.
주요 숫자 환경 변수도 기동 전에 검증됩니다. 숫자 env에 `...`, `<...>`, `replace-with...` 같은 placeholder나 숫자가 아닌 값을 넣으면 해당 env 이름이 포함된 오류로 실패합니다. `HAEORUM_MAX_IMAGE_MB`, `HAEORUM_MAX_IMAGE_DIMENSION`, `HAEORUM_MIN_IMAGE_DIMENSION`, 이미지 검증 cache entry 상한, 동기화 주기, Marqo/Gemini 검색 timeout, backend circuit cooldown, 이미지 URL probe timeout, Marqo 이미지 다운로드 worker 수, 알림 timeout은 양수여야 합니다. 운영 동기화 주기는 1시간 변경 동기화 요구를 지키기 위해 3600초 이하여야 합니다. `HAEORUM_MAX_IMAGE_DIMENSION`은 `HAEORUM_MIN_IMAGE_DIMENSION` 이상이어야 합니다. rate limit, cache TTL, 이미지 검증 cache TTL, 검색 retry 횟수, circuit failure threshold, 이미지 검색 동시성은 `0` 이상이어야 하며 운영 Marqo API에서는 circuit failure threshold가 0보다 커야 합니다. rate limit bucket 상한과 half-open probe 상한은 1 이상이어야 합니다. 혼합 검색 text/image 가중치는 음수가 될 수 없고 둘 다 `0`일 수 없습니다.
Boolean 환경 변수는 `true/false`, `yes/no`, `on/off`, `1/0`만 허용합니다. `HAEORUM_FILTER_BY_MALL_ID`, `HAEORUM_VALIDATE_PRODUCT_IMAGES`에 오타나 placeholder가 들어가면 서비스가 조용히 기본값으로 떨어지지 않고 기동 전에 실패합니다.

운영 env 파일은 서비스 기동 전에 `scripts/env_check.py`로 검증합니다. 이 검사는 placeholder secret, production 필수값, Marqo/Redis URL 형식, Gemini embedding backend/URL/model/dimension 명시 설정, CORS wildcard/HTTP origin, 전역 상품 URL 템플릿 HTTPS 여부, mall/synonym 파일 경로, mall별 API key/origin/상품 URL 템플릿 보안, MSSQL/CSV source와 MSSQL read-only TLS 연결 문자열, boolean/숫자 설정, 1시간 이하 동기화 주기, trusted proxy IP/CIDR, 동기화 알림 webhook URL 형식, 실제 설정 로딩, API 서버 2대 이상 구성의 Redis 공유 설정을 JSON/Markdown으로 남깁니다. `typesense`와 `qdrant`는 예비 어댑터로만 등록되어 있으므로 env preflight에서 배포 가능한 검색엔진으로 인정하지 않습니다.
서비스가 env preflight 없이 직접 기동되더라도 `HAEORUM_ENV=production`의 `load_settings()`는 `HAEORUM_SEARCH_ENGINE`, `MARQO_URL`, `HAEORUM_MARQO_MODEL`, `HAEORUM_INDEX_NAME`, `HAEORUM_MALL_CONFIG_PATH`, MSSQL/CSV 데이터 소스와 Gemini backend/URL/internal proxy key/model/dimension이 env에 명시되지 않았거나 dimension이 1 미만이면 실패합니다. `GEMINI_PROXY_API_KEY`는 Gemini embedding proxy의 `/embed` shared secret이고 API/reindex/sync 컨테이너에는 같은 값을 `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`로 전달합니다. `HAEORUM_MALL_CONFIG_PATH`가 번들 `sample_malls.json`을 가리키거나 필수/선택 config file env가 예시 placeholder 값이면 설정 파일을 읽기 전에 차단합니다. CSV 데이터 소스로 운영하는 경우에도 `HAEORUM_PRODUCT_CSV`가 존재하지 않거나 번들 `sample_products.csv`를 가리키면 기동 전에 차단합니다.

```bash
python scripts/env_check.py \
  --env-file /etc/haeorum-ai-search/haeorum-ai-search.env \
  --role api \
  --api-server-count 2 \
  --output /var/log/haeorum-ai-search/env-check.json \
  --markdown-output /var/log/haeorum-ai-search/env-check.md
```

권장:

- `HAEORUM_MAX_IMAGE_MB=5`
- `HAEORUM_MAX_IMAGE_DIMENSION=1600`
- `HAEORUM_MIN_IMAGE_DIMENSION=16` 업로드 이미지와 대표 이미지 검증의 최소 가로/세로 픽셀
- `HAEORUM_MIXED_TEXT_WEIGHT=0.4`
- `HAEORUM_MIXED_IMAGE_WEIGHT=0.6`
- `HAEORUM_LOW_SCORE_THRESHOLD=0.4`
- `HAEORUM_QUERY_SYNONYM_PATH=/etc/haeorum-ai-search/query-synonyms.json` 무결과/저신뢰 검색어 보정용 동의어 설정
- `HAEORUM_CATEGORY_SUGGESTION_LIMIT=15` 비슷한 카테고리 추천 최대 개수. 기획서 기준에 맞춰 1~15 범위만 허용
- `HAEORUM_MAX_OFFSET=200` 관련 상품 더보기 offset 기본값. Marqo candidate fan-out을 제한하기 위해 설정/런타임 hard cap은 500이고 Marqo `/search` 후보 요청 수는 2000개로 capped됩니다.
- `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS=15` 공개 검색 요청의 Marqo `/search` timeout. 느린 Marqo 호출이 API worker를 장시간 점유하지 않도록 부하 테스트 p95와 같이 조정
- `HAEORUM_MARQO_SEARCH_RETRY_COUNT=1`
- `HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS=0.1` 408/429/5xx, 연결 오류, timeout 같은 일시 오류의 짧은 재시도 대기. 공개 검색뿐 아니라 Marqo 색인/삭제와 Gemini 상품 embedding 요청에도 같은 재시도 정책을 적용합니다.
- `HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS=2` 429/503 응답의 `Retry-After`를 이 상한 안에서 반영해 과부하 backend를 너무 빨리 재타격하지 않습니다. `0`이면 `Retry-After` 반영을 끄고 고정 retry delay만 사용합니다.
- `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS=55` thread-local Marqo/Gemini keep-alive 연결을 이 idle 시간 이후 선제적으로 새로 열어, 운영 proxy/backend idle timeout 뒤 첫 요청이 stale 연결 재시도로 느려지는 일을 줄입니다. `0`이면 선제 교체를 끕니다.
- `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS=96`
- `HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS=1` API process 하나에서 Marqo/Gemini으로 동시에 나가는 backend 요청 슬롯 수와 슬롯 대기 시간을 제한합니다. 슬롯이 모두 사용 중이면 짧게 대기한 뒤 503으로 fail-fast 처리해 threadpool이 backend 대기열에 무한히 묶이지 않게 합니다. `0`이면 active request 슬롯 제한을 끕니다.
- `HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD=5`
- `HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS=5`
- `HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS=1` Marqo/Gemini에서 timeout, 연결 오류, 408/429/5xx가 반복될 때 backend circuit breaker를 열어 같은 API process의 후속 backend 호출을 짧게 fail-fast 처리합니다. cooldown 뒤에는 소수의 half-open probe만 허용해 복구 여부를 확인합니다.
- `HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE=128` Marqo `/documents` 색인 요청당 최대 상품 수
- `HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES=8388608` Marqo `/documents` JSON body soft cap. 큰 description/vector payload는 상품 수 한도보다 먼저 byte cap으로 분할합니다. `0`이면 byte cap을 끄고 상품 수 한도만 적용합니다.
- `HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE=512` Marqo `delete-batch` 요청당 최대 document ID 수
- `HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS=15` Gemini backend의 검색 쿼리 embedding timeout. 색인용 embedding timeout과 분리됨
- `HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM=8` Gemini split-vector 혼합 검색에서 텍스트 query vector와 이미지 query vector를 동시에 계산하는 shared worker 수. `0`이면 순차 계산으로 되돌림
- `HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES=2048` 반복 텍스트 query embedding 런타임 LRU quota
- `HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES=512` 반복 이미지 query embedding 런타임 LRU quota
- `HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE=900`
- `HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE=2000`
- `HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE=600`
- `HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE=3000`
- `HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE=300`
- `HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE=600`
- `HAEORUM_RATE_LIMIT_MAX_BUCKETS=10000` Redis 미사용 또는 Redis rate limit fallback 시 프로세스당 로컬 IP/mall bucket 상한
- `HAEORUM_SEARCH_MAX_CONCURRENCY=64` API 프로세스별 전체 검색엔진 호출 동시성 제한. `0`이면 비활성화
- `HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS=2` 전체 검색 동시성 슬롯 대기 시간. 초과 시 429
- `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY=8`
- `HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS=2`
- `HAEORUM_API_THREADPOOL_TOKENS=96` FastAPI/anyio blocking worker token 수. 검색/이미지 queue보다 작으면 API threadpool이 먼저 병목
- `HAEORUM_API_GZIP_MINIMUM_SIZE=1024` 공개 API 검색 JSON 응답 gzip 압축 최소 크기. `0`이면 앱 레벨 압축을 끕니다.
- `HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS=30`, `HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES=32` 같은 프로세스의 동일 이미지 업로드 검증 결과를 짧게 재사용해 검색 응답 캐시 hit 전 이미지 디코딩/리사이즈 비용을 줄입니다.
- `HAEORUM_CACHE_TTL_SECONDS=30` API 서버 2대 이상 운영에서는 공유 캐시가 꺼지지 않도록 0보다 크게 유지
- `HAEORUM_CACHE_MAX_ENTRIES=10000` Redis 없이 메모리 캐시를 쓸 때 프로세스당 최대 검색 응답 수
- `HAEORUM_CACHE_MISS_LOCK_SECONDS=35` Redis 캐시에서 동일 검색 miss를 API 서버 간에 합치는 lock TTL. 운영에서는 Marqo 검색 timeout과 retry delay를 모두 합친 최악 예산보다 길게 둡니다.
- `HAEORUM_CACHE_MISS_WAIT_SECONDS=5` 다른 API 서버가 miss lock을 잡은 경우 캐시 fill을 기다리는 최대 시간
- `HAEORUM_CACHE_MISS_POLL_SECONDS=0.05` cache fill 대기 중 Redis를 재확인하는 주기
- `HAEORUM_REDIS_URL=` API 서버를 2대 이상 운영하거나 공유 rate limit/cache와 sync/reindex 후 캐시 무효화가 필요하면 Redis URL 지정
- `HAEORUM_REDIS_KEY_PREFIX=haeorum-ai-search`
- `HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS=0.5`, `HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS=0.5`, `HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS=2` Redis 장애/지연 시 공개 요청 worker가 오래 묶이거나 매 요청마다 즉시 Redis 재시도하는 상황을 줄이는 timeout/backoff
- `HAEORUM_TRUSTED_PROXY_IPS=127.0.0.1,::1` `X-Forwarded-For`/표준 `Forwarded`/`X-Real-IP`를 신뢰할 Nginx/LB IP 또는 CIDR 목록
- `HAEORUM_SYNC_INTERVAL_SECONDS=3600`
- `HAEORUM_MSSQL_SYNC_FETCH_SIZE=1000` sync worker의 MSSQL ODBC fetchmany row 수. 큰 운영 View를 `fetchall()`로 한 번에 당기지 않습니다.
- `HAEORUM_SEARCH_LOG_PATH=/app/logs/search.jsonl`
- `HAEORUM_ERROR_LOG_PATH=/app/logs/error.jsonl`
- `HAEORUM_SYNC_LOG_PATH=/app/logs/sync.jsonl`
- `HAEORUM_SYNC_LOCK_STALE_SECONDS=21600` 오래된 lock 자동 회수. 긴 재색인 환경에서는 충분히 크게 잡고, `0`이면 비활성화
- `HAEORUM_VALIDATE_PRODUCT_IMAGES=false`에서 시작하고, 운영 이미지 서버 부하를 확인한 뒤 `true`로 전환
- `HAEORUM_PRODUCT_IMAGE_PROBE_TIMEOUT_SECONDS=10`
- `HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT=1`
- `HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS=0.25`
- `HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT=3`
- `HAEORUM_SYNC_ALERT_WEBHOOK_URL=` 운영 동기화 실패 알림 수신 URL 지정. 값이 있으면 `https://` 절대 URL이어야 하며 URL 사용자 정보, fragment, 공백은 허용하지 않습니다. 이 값을 쓰지 않는 경우에는 외부 모니터링에서 sync failure 알림을 구성하고 security evidence에 `--sync-alerting-configured`를 명시합니다.
- `HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS=5`
- `HAEORUM_FILTER_BY_MALL_ID=false`에서 시작합니다. 운영 View가 가맹점별 row를 별도로 제공하고 `mall_id`가 실제 노출 정책을 의미할 때만 `true`로 바꿉니다.
- 가맹점별 브라우저 호출 origin은 `malls.json`의 `allowed_origins`로 제한합니다. 값이 있으면 공개 검색/클릭 API는 `Origin` 헤더가 없거나 목록에 없는 요청을 403으로 거절합니다.
- 가맹점별 단순 노출 제외는 `malls.json`의 `excluded_product_ids`, `excluded_categories`로 적용합니다. 이 필터는 검색엔진 결과를 받은 뒤 API 응답 단계에서 적용되므로, 공통 상품 row 구조에서도 사용할 수 있습니다.
- 가맹점별 가격 표시 정책은 `malls.json`의 `hide_prices`, `price_multiplier`, `price_adjustment`, `price_round_to`로 적용합니다. 운영 DB View가 몰별 가격 row를 제공하면 그 값을 우선 사용하고, 공통 row 구조에서만 보정 정책을 둡니다.

검색어는 `app/query_normalizer.py`의 공통 보정으로 오타와 띄어쓰기 차이를 먼저 정규화합니다. `app/category_intent.py`는 `수건 -> 타올`, `포스트잇 -> 점착메모지`처럼 검색어의 카테고리 의도를 추론해 hard filter가 아닌 점수 보강 신호로 사용합니다. 검색 로그에는 원문 `q`, `normalized_query`, `inferred_categories`, 응답 `result_mall_ids`, `result_mall_id_mismatch_count`와 이미지 검색의 `image_hash`/`image_perceptual_hash`, `image_width`, `image_height`, `image_size_bytes`, `image_normalized`, `image_quality_warnings`가 함께 남으므로 `/admin/search-insights`와 `scripts/search_insights.py`에서 반복되는 무결과/low-confidence query, 유사 이미지 반복 검색, 업로드 이미지 품질 문제, 가맹점 격리 이상 신호를 확인할 때 함께 봅니다. 검색어 동의어 파일은 `{ "synonyms": { "파우치": ["가방", "백"], "에코백": "친환경 가방, 장바구니" } }` 형식으로 관리합니다. 반복 패턴을 이 파일에 추가하면 텍스트 검색과 혼합 검색의 텍스트 query가 양방향으로 확장됩니다.
Marqo 색인 중 대표 이미지 URL 다운로드는 `HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT`로 제한합니다. 운영 이미지 서버 부하가 낮고 재색인 시간이 길면 4~5까지 올리고, 타임아웃이나 5xx가 늘면 1~2로 낮춥니다. 직접 Marqo embedding 모드도 전체 CSV를 먼저 큰 list로 만들지 않고 batch generator로 문서를 변환합니다. Gemini embedding backend에서는 상품 텍스트/이미지 embedding 요청을 Marqo document batch 크기 단위로 나누고, embedding 후 Marqo add-documents payload를 `HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES` 기준으로 다시 분할해 큰 MSSQL export 색인 시 단일 backend 요청이 과도하게 커지지 않게 합니다. Marqo/Gemini backend 요청 body는 compact JSON으로 직렬화해 Gemini 벡터 payload와 대량 색인 요청의 wire byte를 줄이고, 같은 soft cap에서 불필요한 batch split이 늘지 않게 합니다. 숨김/삭제/이미지 검증 실패 상품 정리도 `HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE` 단위로 나누어 단일 delete-batch 요청이 지나치게 커지지 않게 합니다. `csv_index.py` 리포트의 `indexing.batch_count`, `indexing.max_batch_size`, `indexing.max_request_body_bytes`가 운영 실제 색인 배치 크기 증거입니다.
공개 검색 경로의 Marqo `/search`와 Gemini query embedding 호출은 색인 작업보다 짧은 timeout을 사용합니다. `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS`와 `HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS`를 부하 테스트 p95 목표보다 지나치게 크게 두면 worker thread가 느린 backend 호출에 묶이고, 너무 작게 두면 정상적인 이미지/혼합 검색이 500 오류로 실패합니다. 공개 검색 worker thread는 Marqo/Gemini JSON HTTP 연결을 thread-local keep-alive로 재사용하고, 요청 body는 compact JSON으로 보내며, `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS`보다 오래 쉰 연결은 재사용 전 선제적으로 새로 엽니다. `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS`로 process별 backend active request 슬롯을 제한하고, 슬롯이 부족하면 `HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS`만큼만 기다린 뒤 503으로 fail-fast 처리합니다. 연결 오류나 HTTP protocol 오류가 나면 해당 thread의 연결을 닫은 뒤 재시도 정책에 따라 새 연결을 잡습니다. backend 호출은 gzip 응답을 요청하고, reference Marqo API는 `MARQO_API_GZIP_MINIMUM_SIZE=1024` 이상 JSON 응답을 gzip으로 압축합니다. Gemini provider 응답은 `GEMINI_MAX_RESPONSE_BYTES` 기본 32MB를 넘으면 502로 차단해 외부 API 응답이 메모리를 잠식하지 않게 합니다. 공개 API 응답도 `HAEORUM_API_GZIP_MINIMUM_SIZE=1024` 이상이면 gzip 압축되어 위젯 검색 JSON의 전송량을 줄입니다. 429/503 응답에 `Retry-After`가 있으면 `HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS` 안에서 retry delay보다 긴 값을 반영하므로, Marqo/Gemini이 과부하 신호를 냈을 때 즉시 같은 요청을 반복해 혼잡을 키우지 않습니다. timeout/연결 오류/408/429/5xx가 `HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD`만큼 연속되면 circuit breaker가 열리고, `HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS` 동안 같은 process의 후속 backend 호출은 즉시 실패합니다. 검색 API는 이 상태를 503으로 반환해 worker thread가 장애 backend에 계속 묶이지 않게 합니다. cooldown 뒤에는 `HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS`개 probe만 통과시키고 성공하면 닫습니다. `/admin/metrics`의 `engine.transport`와 Prometheus `haeorum_backend_http_*{service="marqo|gemini"}`는 backend request attempts, active request slots, slot acquire wait/timeouts, connections opened, connection reuses, idle/stale reconnects, gzip responses, Retry-After responses/max/last seconds, error responses, connection-close responses, response elapsed ms, request body bytes, wire/decoded response body bytes, circuit state/open/short-circuit/recovery를 노출하므로, 부하 중 p95 상승이 backend 연결 슬롯 부족, 연결 churn, payload 증가, 검색엔진 자체 지연, backend 과부하 backoff, 장애 fail-fast 중 어디에서 오는지 분리해 봅니다. Gemini 혼합 검색은 텍스트 query vector와 이미지 query vector를 둘 다 만들어 정규화된 `HAEORUM_MIXED_TEXT_WEIGHT`/`HAEORUM_MIXED_IMAGE_WEIGHT`를 weighted `context.tensor`로 보내므로 Gemini split-vector 경로에서도 텍스트 조건이 이미지 조건에 묻히지 않습니다. Gemini 텍스트 query embedding은 프로세스 내부 LRU와 in-flight dedupe로 캐시에 없는 텍스트/이미지 질의를 재사용해 limit/offset/filter 차이로 검색 응답 캐시를 못 타는 반복 질의의 Gemini 호출을 줄입니다. 텍스트와 이미지 런타임 캐시는 별도 quota를 쓰므로 고유 이미지 업로드가 몰려도 자주 반복되는 텍스트 query vector가 같이 evict되지 않습니다. 운영 preflight의 query runtime cache capacity 검사는 Gemini split-vector 검색에서 텍스트 cache quota가 100 concurrent, 이미지 cache quota가 30 concurrent 기준보다 작으면 실패합니다. `/admin/metrics`의 `engine.gemini_query_embedding_cache`와 Prometheus 지표 `haeorum_gemini_query_vector_*`는 runtime text/image cache entry와 max entry, 현재 in-flight 계산, wait event, wait timeout, wait ms를 노출합니다. 408/429/5xx, 연결 오류, timeout은 `HAEORUM_MARQO_SEARCH_RETRY_COUNT`만큼 짧게 재시도합니다. 이 재시도 정책은 Marqo 색인/삭제와 Gemini 상품 embedding 요청에도 적용되어 일시 장애로 재색인 전체가 실패하는 비율을 줄이지만, backend 장애 때 부하를 늘릴 수 있으므로 운영에서는 0~1회부터 시작합니다.
운영 부하와 API scale 리포트는 성공 응답이 있는데 `server_metrics.delta.backend_marqo_request_attempts`가 0인 경우를 실패로 처리합니다. `load_test.py`는 `request_profile`에 고유 검색 요청 signature와 최소 backend attempt 하한을 남기며, Marqo request attempt delta가 `request_profile.min_backend_marqo_request_attempts`보다 작아도 실패합니다. Gemini embedding backend의 이미지/혼합 성공 응답도 `server_metrics.delta.backend_gemini_request_attempts`가 0이거나 `request_profile.min_backend_gemini_request_attempts`보다 작으면 실패하므로, 캐시 hit만으로 만든 리포트를 실제 검색엔진 부하 증거로 제출할 수 없습니다. 이미지/혼합 부하는 `--additional-image-file`을 반복 지정하거나 수집 설정의 `load.image_files`를 채워 여러 실제 기준 이미지를 순환시킬 수 있으며, 이때 리포트는 `image_input.source=files`, `file_count`, `unique_sha256_count`, `request_profile.unique_image_inputs`를 남겨 단일 이미지 캐시 hit가 Gemini/이미지 경로 병목을 숨기지 않게 합니다. 검색 후보 보강 관측성도 필수라서 `server_metrics.delta.engine_search_attempts`, `engine_adaptive_refetches`, `engine_adaptive_refetch_searches`, `engine_underfilled_after_max_candidates_events`와 after snapshot의 `engine_average_search_attempts`, `engine_max_search_attempts`, `engine_average_final_candidate_limit`, `engine_max_final_candidate_limit`가 빠진 리포트는 실패합니다. API 1대/2대 비교는 `request_profile`의 고유 요청 signature, 반복 요청 수, query type별 고유 요청 수, mall별 고유 요청 수도 같아야 하므로 한쪽만 캐시가 과하게 잘 타는 부하로 확장성 통과 판정을 만들 수 없습니다. `load_test.py --p99-ms`를 생략하면 p95 기준의 1.6배를 p99 상한으로, `--request-timeout-seconds`를 생략하면 `max(10초, p99의 2배)`를 검색 요청 timeout으로, `--max-server-wait-avg-ms`를 생략하면 `max(250ms, p95의 20%)`를 내부 평균 wait 상한으로, `--min-rps`를 생략하면 `max(1 RPS, concurrency / p95초 * 0.25)`를 처리량 하한으로, `--max-process-rss-growth-mb`를 생략하면 512MiB를 API 프로세스 RSS 증가 상한으로 자동 적용합니다. 운영 수집 설정에는 텍스트 4800ms/10초/600ms/8.3RPS/512MiB, 이미지/혼합 8000ms/16초/1000ms/1.5RPS/512MiB, 850-user mixed traffic 8000ms/16초/1000ms/5RPS/512MiB처럼 명시해 tail latency, 요청 timeout, queue/cache 대기 포화, 처리량 저하, 프로세스 메모리 증가를 별도 기준으로 남깁니다. readiness와 API scale 비교는 전체 p99, query type별 p99, `max(10초, p99의 3배)`를 넘는 요청 timeout, 처리량 하한 미달, cache lock/singleflight/Gemini/search queue/image queue 평균 wait가 이 상한을 넘는 리포트를 실패 처리합니다. 또한 부하 종료 후 `singleflight_in_flight`, `search_queue_in_flight`, 이미지 부하의 `image_queue_in_flight`, Gemini compatibility key의 `gemini_query_vector_in_flight`가 0으로 돌아오지 않거나 API 서버 `system_memory_used_percent`/`disk_used_percent`가 85% 이상이거나 before/after `process_memory_rss_bytes` 증가량이 `max_process_rss_growth_mb`를 넘으면 지연시간이 통과해도 운영 안정성 실패로 봅니다.
해오름 reference compose는 API 2대 기준 기본 검색 동시성까지 Marqo 내부 HTTP pool이 먼저 병목이 되지 않도록 `VESPA_POOL_SIZE`와 `MARQO_INFERENCE_POOL_SIZE` 기본값을 128로 둡니다. 이 값이 Marqo 기본 10으로 남으면 API queue가 허용한 요청이 Marqo 내부 Vespa HTTP pool에서 다시 대기해 p95가 튈 수 있습니다. Marqo API worker 기본값도 1이므로 reference compose는 `MARQO_API_WORKERS=2`, 검색 throttling 호환값 `MARQO_MAX_CONCURRENT_SEARCH=100`을 기본으로 둡니다. API 컨테이너는 anyio threadpool 기본 40개가 `HAEORUM_SEARCH_MAX_CONCURRENCY=64`보다 먼저 병목이 되지 않도록 `HAEORUM_API_THREADPOOL_TOKENS=96`을 기본으로 두고, backend active request 슬롯도 `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS=96`으로 둡니다. 또한 Marqo의 Vespa 검색 timeout 기본 1000ms는 운영 텍스트 p95 목표 3000ms보다 먼저 504를 만들 수 있으므로 `VESPA_SEARCH_TIMEOUT_MS` 기본값을 5000ms로 둡니다. Marqo API keep-alive는 `MARQO_API_KEEPALIVE_TIMEOUT=75`, Marqo gzip 최소 크기는 `MARQO_API_GZIP_MINIMUM_SIZE=1024`, Marqo API 기본 응답 직렬화는 `ORJSONResponse`, 공개 API gzip 최소 크기는 `HAEORUM_API_GZIP_MINIMUM_SIZE=1024`, 앱 backend idle rotation은 `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS=55`로 두어 앱 thread-local backend 연결이 Uvicorn keep-alive timeout보다 먼저 교체되고 큰 검색 응답은 압축되도록 합니다. reference compose는 주요 컨테이너에 `ulimits.nofile=65535`를 두고, 직접 systemd 배포에서는 API/sync/reindex 서비스 모두 `LimitNOFILE=65535` 이상이어야 합니다. 서버 preflight는 API/Marqo 역할에서 현재 open-file limit도 65535 이상인지 확인합니다. 운영에서 API 서버 수나 `HAEORUM_SEARCH_MAX_CONCURRENCY`를 올릴 때는 Marqo 컨테이너 CPU/RAM, Vespa CPU/RAM과 함께 이 pool/worker/throttling/threadpool/backend-slot/keep-alive/gzip/open-file-limit 값도 같이 조정하고, `scripts/env_check.py --api-server-count N`으로 pool 값이 명시되어 있으며 `HAEORUM_SEARCH_MAX_CONCURRENCY * N` 이상인지, worker/throttling 값이 required load concurrency를 받을 수 있는지, API threadpool token이 검색/이미지 queue보다 충분한지, backend active request 슬롯이 한 API 서버의 검색 동시성보다 작지 않은지, `VESPA_SEARCH_TIMEOUT_MS`가 p95 목표와 `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS` 예산 안에 있는지, `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS`가 `MARQO_API_KEEPALIVE_TIMEOUT`보다 낮은지, gzip 최소 크기가 켜져 있고 너무 크지 않은지 확인합니다. 실제 여유는 `marqo-resource.json`과 부하 리포트의 backend transport 지표로 확인합니다.
모든 검색 실행은 API 프로세스별 `HAEORUM_SEARCH_MAX_CONCURRENCY`만큼만 동시에 검색엔진으로 들어가고, 초과 요청은 `HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS` 동안 빈 슬롯을 기다립니다. 대기 시간이 지나면 429로 실패시켜 텍스트 100 concurrent 같은 부하에서 Marqo가 순간 호출 폭주에 끌려가지 않도록 합니다. 사용자 이미지/혼합 검색은 그 안에서 추가로 `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`만큼 동시에 처리하고, 초과 요청은 `HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS` 동안 빈 슬롯을 기다립니다. multipart 요청은 Content-Length와 API key/IP/mall rate limit을 먼저 통과한 뒤 업로드 bytes 읽기 구간에 image gate를 잡고, 서비스 내부에서는 이미지 디코딩/리사이즈/해시 계산과 실제 backend 호출 구간에만 image gate를 다시 잡습니다. 같은 이미지 cache miss가 동시에 몰리면 follower 요청은 singleflight 대기 중 image gate를 점유하지 않으므로 실제 이미지 처리 슬롯은 owner 요청과 짧은 검증 구간에 집중됩니다. API 서버를 여러 대 운영하면 전체 검색 동시 처리량은 대략 `프로세스 수 * HAEORUM_SEARCH_MAX_CONCURRENCY`, 이미지/혼합 검색 동시 처리량은 `프로세스 수 * HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`입니다. `/admin/metrics`와 Prometheus의 `haeorum_search_queue_wait_*`, `haeorum_image_search_queue_wait_*`는 슬롯 대기 횟수/총 대기/ms 평균/ms 최대를 노출하므로, queue full 429가 나오기 전에도 이미지 전처리, backend 포화나 API 서버 수 부족을 확인할 수 있습니다.
공개 검색/클릭 라우트의 rate limit 저장소 조회, 동기 검색 엔진 호출, 캐시 조회, 클릭 로그 기록은 FastAPI threadpool에서 실행되므로 운영 부하 테스트에서는 event-loop stall 대신 worker thread/검색엔진 지연, 이미지 검색 gate 대기, Redis fallback 여부를 함께 확인합니다. `/admin/metrics`와 Prometheus의 `api_threadpool`/`haeorum_api_threadpool_*` 지표는 startup에서 적용된 runtime token 수와 필요한 최소 token 수를 노출합니다.
JSON 검색/클릭 요청은 body 파싱 전에 header API key/Origin이 어떤 enabled mall 후보와도 맞지 않는 요청과 IP rate limit 초과 요청을 먼저 차단하고, `Content-Length`가 없더라도 앱이 스트리밍으로 최대 JSON 본문 크기를 강제합니다. header 후보 검증은 서비스 설정별 API key/origin 인덱스를 재사용하므로 1,700개 mall 운영에서도 공개 요청마다 전체 mall 목록을 훑지 않습니다. multipart 이미지 검색은 `Content-Length`가 없는 요청을 거절하고, 전체 multipart `Content-Length` 상한을 확인합니다. 또한 form 파싱 전에 같은 header 후보 검증과 IP 검색 rate limit을 먼저 적용합니다. 공개 API key/Origin, 검색 rate limit, 이미지 rate limit을 통과한 뒤 업로드 파일 bytes를 제한 크기까지 chunk로 읽으므로 인증 실패 또는 rate limit 초과 요청이 이미지 파일을 base64로 변환하는 경로에 들어가지 않습니다. 이미지 검증 cache miss에서도 품질 경고와 perceptual hash는 같은 PIL feature 분석 pass에서 계산해 이미지-only/혼합 검색의 첫 요청 디코딩 비용을 줄입니다.
단일 상품 재색인/삭제 관리자 경로는 `/`를 포함한 상품번호도 path로 받을 수 있습니다. 상품번호가 `P/001`이면 `POST /admin/reindex/P/001?mall_id=shop001`, `DELETE /admin/product/P/001?mall_id=shop001`처럼 호출하고, 공백/`?`/`#` 같은 URL 예약 문자는 클라이언트에서 URL encode합니다. 운영 프록시나 호출 도구가 예약 문자를 path에서 다르게 처리하면 JSON body 기반 `POST /admin/reindex-product`, `POST /admin/delete-product`에 `{"mall_id":"shop001","product_id":"..."}`를 보내 원문 상품번호를 전달합니다. 다중 몰 운영 또는 `HAEORUM_FILTER_BY_MALL_ID=true` 환경에서는 `mall_id` 없는 단건 재색인/삭제가 실패하도록 막아, 같은 상품번호를 쓰는 다른 가맹점 문서와 혼동하지 않게 합니다.
관련 상품 더보기는 `offset` 기반으로 동작합니다. 운영에서 너무 깊은 페이지 요청이 Marqo 부하로 이어지지 않도록 `HAEORUM_MAX_OFFSET`을 두며, 설정값이 500을 넘으면 기동 전 preflight와 앱 설정 로딩에서 실패하고 런타임도 500 초과 offset을 거절합니다. 속성 후처리를 위해 추가 후보를 가져오는 경로도 Marqo 후보 요청 수를 최대 2000개로 제한해 deep pagination과 fuzzy 속성 필터가 같이 들어와도 단일 검색이 과도한 후보 수를 요청하지 않게 합니다.
대표 이미지 URL 검증에서 timeout/5xx 등 일시 실패가 잦으면 `HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT`를 2~3으로 올립니다. 이 값은 최초 시도 이후의 재시도 횟수이며, `0`이면 최초 1회만 시도합니다. HTTP 4xx는 잘못된 URL로 보고 재시도하지 않습니다.
동기화 실패 알림 웹훅을 설정하면 `type=sync_failed`, 실패 수, 마지막 오류, 색인명, 엔진 정보를 포함한 JSON payload를 전송합니다. 알림 전송 자체가 실패하면 `logs/sync.jsonl`에 `type: "sync_alert_failed"`로 남깁니다. 운영 readiness의 `security.json`은 이 웹훅이 credentials, fragment, whitespace/backslash, localhost/loopback/link-local/unspecified host가 없는 유효한 HTTPS URL로 설정되어 있거나, Prometheus/Grafana 등 외부 알림이 `sync_last_error`, `sync_product_failures`, `sync_batch_failures`, `sync_lock_contention`을 감시하도록 구성됐다는 `--sync-alerting-configured` 확인이 있어야 통과합니다.
`HAEORUM_ENV=production`에서는 개발용 관리자 key, `HAEORUM_CORS_ORIGINS=*`, Marqo engine/URL/model/index 누락, MSSQL/CSV 데이터 소스 누락, CSV 데이터 소스의 샘플 파일 사용, Gemini backend/URL/model/dimension 누락 또는 잘못된 dimension, HTTP 또는 로컬 CORS origin, HTTP 또는 비공개/로컬 상품 URL 템플릿, 비어 있는 mall 설정, 가맹점별 `api_key`/`allowed_origins`/URL 템플릿 누락, sample/placeholder 공개 API key, mall별 wildcard/HTTP/localhost/private/link-local/reserved `allowed_origins`, HTTP 또는 localhost/private/link-local/reserved `product_url_template`, 전역 CORS에 포함되지 않은 mall별 origin, 3600초 초과 동기화 주기, 잘못된 동기화 알림 webhook URL을 기동 전에 차단합니다.
IP별 rate limit과 오류 로그의 client 값은 신뢰된 프록시에서 온 `X-Forwarded-For`의 첫 번째 IP, 표준 `Forwarded`의 `for` 값, `X-Real-IP` 순서로 사용합니다. 기본값은 같은 서버의 Nginx가 uvicorn으로 넘기는 `127.0.0.1,::1`만 신뢰하므로, 별도 로드밸런서나 프록시를 앞단에 두면 `HAEORUM_TRUSTED_PROXY_IPS`에 그 장비의 사설 IP/CIDR을 추가합니다. 이 목록에 없는 peer가 보낸 forwarded header는 무시합니다. Nginx 템플릿은 외부 클라이언트가 임의로 보낸 `X-Forwarded-For`를 이어 붙이지 않고 `$remote_addr`로 덮어쓰고, `X-Real-IP`도 `$remote_addr`로 덮어쓰며, 표준 `Forwarded` 헤더는 제거해 앱이 검증한 peer 기준 IP만 사용하게 합니다.
`HAEORUM_REDIS_URL`이 비어 있으면 캐시와 전체 검색/클릭 로그/이미지 검색 rate limit은 API 프로세스 메모리에 저장됩니다. 운영 API 서버를 2대 이상 두는 경우에는 같은 Redis를 지정해 캐시와 제한 카운터를 공유합니다. 이미지/혼합 검색 요청은 API key와 이미지 rate limit을 먼저 통과한 뒤 업로드 bytes 읽기, 이미지 검증, 검색엔진 호출 구간만 image queue로 제한하고, cache hit는 backend 호출을 건너뛰므로 동일 이미지 반복 검색이 많은 대표 사이트는 Redis 공유 캐시를 쓰는 편이 검색엔진 부하를 더 줄입니다. 동일 API 프로세스 안의 같은 cache miss는 singleflight로 합쳐 한 요청만 backend를 호출하고, follower 요청은 singleflight 대기 중 이미지 queue 슬롯을 붙잡지 않으며, backend가 실패해도 현재 대기 중인 동일 요청들은 같은 실패를 공유해 장애 중 재시도 폭주를 만들지 않습니다. 로컬 singleflight 대기나 Redis miss-lock follower 대기는 `HAEORUM_CACHE_MISS_WAIT_SECONDS`로 제한되며, timeout 후에는 같은 miss를 직접 재실행하지 않고 429로 fail-fast 처리합니다. 이 동작은 backend 호출이 비정상적으로 오래 걸릴 때 동일 검색어 후속 요청이 무기한 묶이거나 Marqo/Gemini 중복 호출 storm으로 번지는 것을 막습니다. 이 대기 횟수와 timeout은 `/admin/metrics`의 `singleflight` 객체와 Prometheus `haeorum_search_singleflight_*` 지표로 확인합니다. Redis 캐시는 동일 검색어가 여러 API 서버에서 동시에 miss될 때 `search-cache-lock` key로 짧은 lock을 잡아 한 서버만 Marqo를 호출하게 하고, 다른 서버는 `HAEORUM_CACHE_MISS_WAIT_SECONDS` 동안 cache fill을 기다립니다. `HAEORUM_CACHE_MISS_LOCK_SECONDS`는 `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS`, retry 횟수, exponential retry delay를 합친 backend 예산보다 길어야 느린 Marqo 응답 중 lock이 먼저 만료되어 다른 API 서버가 같은 검색을 재실행하지 않습니다. `env_check.py`의 `cache_miss_coordination` 검사는 API 서버 2대 이상 또는 Redis 캐시가 설정된 Marqo API 구성에서 이 조건을 사전에 실패 처리합니다. lock 획득/경합/오류와 follower wait/timeout은 `/admin/metrics`의 `cache.lock_claims`, `cache.lock_contention_events`, `cache.lock_errors`, `cache.lock_release_errors`, `cache.lock_wait_events`, `cache.lock_wait_timeouts`와 Prometheus `haeorum_search_cache_lock_*` 지표로 확인합니다. 검색 캐시는 Redis 읽기/쓰기/삭제/clear/lock 실패나 손상된 캐시 값을 miss 또는 sync log 경고로 처리해 검색 요청 자체가 실패하지 않게 하며, Redis get/set/decode/delete/clear/lock 오류 횟수와 마지막 오류는 `/admin/metrics`의 `cache` 객체와 `haeorum_search_cache_*` Prometheus 지표로 노출합니다. Redis client는 `HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS`/`HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`로 빠르게 실패하고, 실패 뒤에는 `HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS` 동안 같은 프로세스의 캐시/rate limit Redis 호출을 건너뛰어 Redis 장애가 공개 요청 worker를 계속 점유하지 않게 합니다. 이 backoff 상태는 `/admin/metrics`의 `cache.redis_backoff_*`, `rate_limit.redis_backoff_*`와 Prometheus `haeorum_search_cache_redis_backoff_*`, `haeorum_rate_limit_redis_backoff_*`로 확인합니다. Redis 없이 메모리 캐시를 쓰는 프로세스는 `HAEORUM_CACHE_MAX_ENTRIES`를 넘으면 LRU 순서로 오래된 항목을 제거하고 `haeorum_search_cache_evictions`를 증가시켜, 고유 검색어가 많은 다중접속 상황에서도 캐시가 무한히 커지지 않습니다. 동기화/재색인/삭제가 상품 인덱스를 변경하면 검색 캐시를 즉시 비우고 `logs/sync.jsonl`에 `search_cache_cleared` 또는 `search_cache_clear_failed` 이벤트를 남깁니다. 별도 sync worker와 여러 API 서버를 쓰는 운영 구성에서는 같은 `HAEORUM_REDIS_URL`/`HAEORUM_REDIS_KEY_PREFIX`를 공유해야 worker가 API 서버 캐시까지 무효화할 수 있습니다. Redis rate limit 카운터는 `INCR`와 TTL 갱신을 Lua eval 또는 pipeline 경로로 묶어 만료 없는 제한 key가 남을 위험을 줄입니다. Redis rate limit 저장소가 일시적으로 실패하면 해당 API 프로세스의 메모리 bucket으로 fallback해 요청 제한을 계속 적용하고, 오래된 bucket과 `HAEORUM_RATE_LIMIT_MAX_BUCKETS`를 넘는 고유 IP/mall bucket은 정리해 fallback 자체가 메모리를 계속 키우지 않게 합니다. 검색 캐시 키에는 충돌 위험이 낮은 정규화 이미지 SHA256과 백엔드, 인덱스명, Marqo 모델명, mall별 URL/가격/노출 정책 지문, query synonym 설정, 카테고리 추천 수 설정, 정규화 검색어와 추론 카테고리가 들어갑니다. 이 정책 지문은 실제 mall 정책이 있는 ID만 mall별 digest로 재사용하고 알 수 없는 mall_id는 base digest를 써서, 동의어 seed가 크거나 오입력 mall_id가 반복되어도 매 요청마다 전체 동의어 사전을 직렬화하거나 정책 토큰 캐시를 계속 키우지 않습니다. 유사/중복 이미지 분석용 perceptual hash는 캐시 키가 아니라 검색 로그의 `image_perceptual_hash`로 남기므로, 같은 Redis를 써도 인덱스/모델/정책/동의어/응답 구성값이 다른 응답이나 정책 변경 직후 응답이 섞이지 않습니다.
Nginx 템플릿은 `upstream haeorum_ai_search_api`와 `least_conn`을 사용합니다. 같은 서버에서 포트만 늘리거나 별도 API 서버를 추가할 때는 `deploy/nginx/haeorum-ai-search.conf`의 upstream에 `server <host>:8000 max_fails=3 fail_timeout=10s;` 항목을 더하고, API 서버가 2대 이상이면 `HAEORUM_REDIS_URL`을 반드시 공유 값으로 설정합니다.
검색/클릭/API 오류 로그는 이메일, 전화번호, 주민등록번호 형태의 흔한 개인정보 패턴과 비밀번호/API key/token 형태의 민감값을 저장 전에 마스킹합니다. 과도하게 긴 문자열, 큰 배열/객체, 깊은 중첩도 잘라 저장해 깨진 payload나 대형 validation error가 로그 파일을 급격히 키우지 않게 합니다. 검색/오류 JSONL 로그는 동시 쓰기 구간에서 append 핸들을 공유하고 `HAEORUM_LOG_KEEP_OPEN_SECONDS` 동안 짧게 재사용해 요청별 파일 open/close churn을 줄입니다. 앱 기본값은 0초이고 reference compose/env는 1초로 둡니다. 로그는 매 write마다 flush되고 배포 logrotate가 `copytruncate`를 쓰므로 운영 회전과 충돌하지 않습니다. `/admin/metrics`의 `logs.*.output_opens`, `output_reuses`, `idle_closes`, `buffer_open`은 burst 부하에서 핸들 재사용이 실제로 일어나는지 보여줍니다. 로그 쓰기 실패는 검색·클릭 요청 실패로 전파하지 않고 `/admin/metrics`의 `logs.*.write_errors`, 운영 알림, Prometheus `haeorum_log_write_error_events`에 남깁니다. `/admin/metrics`와 Prometheus `haeorum_log_write_ms_*`는 로그 쓰기 지연시간을 함께 노출하므로 부하 중 로컬 디스크나 로그 경로가 p95를 끌어올리는지 분리해 확인할 수 있습니다. 운영 로그 보관 정책에서도 원문 웹서버 access log와 reverse proxy log에 민감정보가 남지 않도록 별도로 점검합니다.

## 3. 배포 순서

로컬 통합 데모에서는 Marqo/Vespa/API 서버를 한 번에 띄울 수 있습니다.

```powershell
cd examples\HaeorumAISearch
docker compose -f compose-haeorum-marqo.yaml up --build -d
docker compose -f compose-haeorum-marqo.yaml --profile reindex up --build reindex-once
python scripts\api_smoke_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key public-shop001-dev-key `
  --origin https://shop001.haeorumgift.com `
  --admin-key dev-admin-key `
  --allow-local-target
```

위 key 값은 로컬 통합 데모 전용입니다. 운영 env, 대표 사이트 설정, evidence collector에는 실제 public/admin key를 넣어야 하며 `dev-key`/`dev-admin-key` 계열 값은 배포 전 검증에서 실패합니다.

API 스모크는 검색/클릭 로그 CORS preflight 허용/거절, 텍스트 검색, `site_id` 별칭 검색과 충돌 400 거절, JSON base64 이미지 검색, 용량 초과 JSON 이미지 본문 413 거절, 너무 작은 JSON 이미지 400 거절, `multipart/form-data` 이미지 업로드 검색, 알 수 없는 multipart 필드 거절, 비이미지/손상/최소 크기 미달/용량 초과/깨진 multipart 거절, 혼합 검색, 배포된 OpenAPI의 클릭 로그 429 계약과 도메인 필터 필드, 공개 API key/Origin/payload/도메인 필터/깨진 JSON 거절, URL/JSON/multipart의 `api_key`/`apiKey`/`apikey`/`api-key`/`x-api-key` 및 관리자 key 별칭 거절, 클릭 로그, 관리자 상태/검색 로그/동기화 로그/오류 로그/검색 인사이트/메트릭, 관리자 로그 응답의 key/data URL/base64 이미지 payload redaction, 잘못된 관리자 key 401 거절, 관리자 상태 엔드포인트의 URL key alias 400 거절, 잘못된 관리자 key로 동기화/전체 재색인/단일 상품 재색인/상품 삭제 API가 모두 401로 보호되는지 확인합니다. 공개 key는 `X-API-Key`, 관리자 key는 `X-Admin-Key` 또는 `Authorization: Bearer ...`로만 전달합니다. 성공 검색 응답은 `meta.query_type`/`elapsed_ms`/`engine`/`limit`/`offset`, `top` 1~3개, 1개 이상의 `items`, 1개 이상의 `suggested_categories`, 결과 상품의 상품번호/상품명/카테고리/가격/이미지/상세 URL/유사도 필드를 모두 검사합니다. 클릭 로그 `product_url`은 절대 안전 URL뿐 아니라 해당 mall의 상품 URL 템플릿과 같은 scheme/host/port 및 `{product_id}` 앞 path/query prefix여야 하며, readiness 게이트도 외부 도메인 주입을 `foreign_click_product_url_rejected`, 같은 도메인의 잘못된 상세 URL path/query prefix를 `click_product_url_template_prefix_mismatch_rejected`로 확인합니다. readiness 게이트도 `cors_preflight`, `invalid_cors_preflight_rejected`, `click_log_cors_preflight`, `invalid_click_log_cors_preflight_rejected`, `site_id_search`, `conflicting_site_id_rejected`, `oversized_json_image_rejected`, `small_json_image_rejected`, `multipart_image_search`, `site_id_multipart_image_search`, `conflicting_multipart_site_id_rejected`, `unsupported_multipart_field_rejected`, `invalid_multipart_image_rejected`, `damaged_multipart_image_rejected`, `small_multipart_image_rejected`, `oversized_multipart_image_rejected`, `malformed_multipart_rejected`, `openapi_click_rate_limit_documented`, `invalid_api_key_rejected`, `query_api_key_rejected`, `query_api_key_alias_rejected`, `query_admin_key_alias_rejected`, `empty_query_api_key_rejected`, `body_api_key_rejected`, `body_api_key_alias_rejected`, `body_admin_key_alias_rejected`, `multipart_body_api_key_rejected`, `multipart_body_api_key_alias_rejected`, `multipart_body_admin_key_alias_rejected`, `invalid_origin_rejected`, `invalid_search_payload_rejected`, `invalid_domain_filter_rejected`, `malformed_search_json_rejected`, `site_id_click_log`, `conflicting_click_site_id_rejected`, `invalid_click_api_key_rejected`, `query_click_api_key_rejected`, `query_click_api_key_alias_rejected`, `query_click_admin_key_alias_rejected`, `empty_query_click_api_key_rejected`, `body_click_api_key_rejected`, `body_click_api_key_alias_rejected`, `body_click_admin_key_alias_rejected`, `invalid_click_origin_rejected`, `invalid_click_payload_rejected`, `unsafe_click_product_url_rejected`, `foreign_click_product_url_rejected`, `click_product_url_template_prefix_mismatch_rejected`, `malformed_click_json_rejected`, `click_log_rate_limited`, `invalid_admin_key_rejected`, `admin_query_key_alias_rejected`, `admin_mutation_endpoints_protected`, `search_log`, `sync_log`, `error_log`, `sensitive_log_redaction`, `search_insights`, `metrics`, `prometheus_metrics` 증거가 없으면 API 스모크를 실패로 판정합니다. 운영 readiness는 각 성공 검색 응답의 `meta.engine=marqo`와 `metrics.engine_ok=true`, `metrics.engine_backend=marqo`도 요구합니다. 운영 readiness 증거를 만들 때는 HTTPS 비로컬 `--base-url`, HTTPS 가맹점 `--origin`, `--admin-key`, `--expect-click-rate-limit`, `--click-rate-limit-probe-count <configured_click_rate_limit_plus_1>`를 지정합니다. 이 클릭 로그 429 증거는 격리된 mall/key 또는 staging에서 수집합니다. 운영 이미지 제한을 10MB보다 크게 바꾼 경우에는 `--oversized-upload-mb`를 제한보다 큰 값으로 지정합니다.
`api_smoke_test.py`와 `load_test.py`는 요청을 보내기 전에 `--base-url`/`--origin`의 credentials, query/fragment, 공백/역슬래시, 잘못된 port, non-public host를 거절하고 `target_validation.ok`를 리포트에 남깁니다. `localhost`나 사설망 대상 로컬 리허설에는 `--allow-local-target`을 명시하고, 운영 evidence에는 이 플래그를 넣지 않습니다. readiness와 API scale 비교는 이 검증 결과가 리포트의 `base_url`/`origin`과 일치하지 않으면 통과시키지 않습니다.
JSON 검색/클릭 로그도 OpenAPI의 `additionalProperties=false` 계약과 맞아야 하므로 readiness는 `unsupported_json_field_rejected`와 `unsupported_click_field_rejected` 증거가 없으면 API 스모크를 통과시키지 않습니다.

정기 동기화 worker까지 같이 올릴 때는 `--profile sync`를 사용합니다.

```powershell
docker compose -f compose-haeorum-marqo.yaml --profile sync up --build -d
```

리눅스 서버에 직접 설치하는 경우 `deploy/` 템플릿을 기준으로 API, sync worker, Nginx를 분리합니다.

```bash
sudo useradd --system --home /opt/haeorum-ai-search --shell /usr/sbin/nologin haeorum
sudo mkdir -p /opt/haeorum-ai-search /etc/haeorum-ai-search /var/log/haeorum-ai-search
sudo chown -R haeorum:haeorum /opt/haeorum-ai-search /var/log/haeorum-ai-search

# repo의 examples/HaeorumAISearch 내용을 /opt/haeorum-ai-search에 배치한 뒤
cd /opt/haeorum-ai-search
sudo -u haeorum python3 -m venv .venv
sudo -u haeorum .venv/bin/pip install -r requirements.txt

# MSSQL source를 운영에서 직접 조회하는 API/worker 서버는 pyodbc와 SQL Server ODBC Driver 18이 필요합니다.
# Debian 계열 서버 예시입니다. 다른 배포판은 Microsoft 공식 ODBC Driver 18 설치 절차를 따릅니다.
curl -fsSLO "https://packages.microsoft.com/config/debian/$(. /etc/os-release && echo ${VERSION_ID%%.*})/packages-microsoft-prod.deb"
sudo dpkg -i packages-microsoft-prod.deb
rm packages-microsoft-prod.deb
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc
sudo -u haeorum .venv/bin/pip install -r requirements-mssql.txt
sudo -u haeorum .venv/bin/python scripts/server_preflight_check.py \
  --role api \
  --require-docker \
  --require-compose \
  --require-pyodbc \
  --expected-odbc-driver "ODBC Driver 18 for SQL Server" \
  --output /var/log/haeorum-ai-search/server-preflight.json

sudo install -m 0640 deploy/haeorum-ai-search.env.example /etc/haeorum-ai-search/haeorum-ai-search.env
sudo cp deploy/systemd/haeorum-ai-search.service /etc/systemd/system/
sudo cp deploy/systemd/haeorum-ai-sync.service /etc/systemd/system/
sudo cp deploy/systemd/haeorum-ai-reindex.service /etc/systemd/system/
sudo cp deploy/systemd/haeorum-ai-reindex.timer /etc/systemd/system/
sudo cp deploy/logrotate/haeorum-ai-search /etc/logrotate.d/haeorum-ai-search
sudo cp deploy/nginx/haeorum-ai-search.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/haeorum-ai-search.conf /etc/nginx/sites-enabled/haeorum-ai-search.conf

# /etc/haeorum-ai-search/haeorum-ai-search.env를 운영 값으로 수정하고 0640 이하 권한을 유지한 뒤,
# malls.json/query-synonyms.json 등 참조 파일을 배치한 뒤 실행합니다.
sudo -u haeorum .venv/bin/python scripts/env_check.py \
  --env-file /etc/haeorum-ai-search/haeorum-ai-search.env \
  --role api \
  --api-server-count 2 \
  --output /var/log/haeorum-ai-search/env-check.json \
  --markdown-output /var/log/haeorum-ai-search/env-check.md

sudo systemctl daemon-reload
sudo systemctl enable --now haeorum-ai-search
sudo systemctl enable --now haeorum-ai-sync
sudo systemctl enable --now haeorum-ai-reindex.timer
sudo systemctl start haeorum-ai-reindex
sudo nginx -t && sudo systemctl reload nginx
```

`haeorum-ai-sync.service`는 `HAEORUM_SYNC_INTERVAL_SECONDS` 기준으로 변경 상품을 반복 반영합니다. 이 값은 1시간 변경 동기화 요구를 지키기 위해 3600초 이하로 유지해야 하며, production runtime, `env_check.py`, `security.json`, readiness가 `sync_interval_hourly`로 검증합니다. `haeorum-ai-reindex.timer`는 매일 03:00에 전체 재색인을 실행해 삭제/비노출 누락을 검증합니다.
sync worker는 `--since`를 지정하지 않으면 공유 `HAEORUM_SYNC_LOG_PATH`에서 마지막 성공한 sync/reindex의 `last_started_at`을 읽어 다음 `updated_at >= ?` 기준으로 사용합니다. 이 값은 API 서버와 worker가 같은 로그 볼륨을 공유해야 복원됩니다.
API 관리자 작업과 worker/timer가 같은 인덱스를 동시에 수정하지 않도록 `HAEORUM_SYNC_LOG_PATH` 옆에 `.lock` 파일을 만듭니다. lock이 이미 있으면 새 동기화/재색인/삭제 작업은 `acquire_sync_lock` 실패로 기록되고 인덱스를 변경하지 않습니다. 프로세스 강제 종료 뒤 lock이 남아도 `HAEORUM_SYNC_LOCK_STALE_SECONDS`보다 오래됐고 같은 host의 소유 프로세스가 살아 있지 않으면 다음 작업이 자동 회수합니다. 재색인이 이 값보다 길 수 있는 운영에서는 값을 늘리거나 `0`으로 비활성화한 뒤 수동 절차를 사용합니다.
MSSQL source의 증분 기준값은 ISO-8601로 검증한 뒤 UTC 기준 datetime 파라미터로 바인딩합니다. 운영 View의 `updated_at`은 SQL Server 문자열 암시 변환에 의존하지 않도록 UTC 기준 `datetime`/`datetime2` 컬럼으로 제공합니다.
CSV source로 증분 동기화를 실행할 때도 `updated_at`은 ISO-8601 datetime으로 파싱해 UTC 기준으로 비교합니다. 비어 있거나 잘못된 `updated_at` row가 있으면 변경 동기화가 실패하므로, MSSQL export CSV와 수동 CSV 모두 `updated_at` 품질을 먼저 확인합니다. CSV 경로가 잘못되면 빈 동기화 성공으로 처리하지 않고 `fetch_products` 실패로 기록해 기존 인덱스를 건드리지 않습니다. 색인 문서 ID는 `mall_id + product_id` 복합키로 생성하므로 서로 다른 몰의 같은 상품번호는 충돌하지 않습니다. 같은 몰 안에서 같은 `product_id`가 2개 이상 있으면 해당 상품들은 색인/삭제 대상에서 제외하고 `sync_product_failed`/`validate_source`/`duplicate_product_id`로 기록합니다. 단건 재색인/삭제에서 상품번호가 여러 몰에 존재하면 요청 body 또는 query에 `mall_id`를 함께 넘겨 대상을 지정합니다.

운영 서버에 올리기 전 로컬 샘플 기준 회귀 증거는 `python scripts/local_acceptance.py --output logs/local-acceptance.json --markdown-output logs/local-acceptance.md`로 한 번에 생성할 수 있습니다. 이 리포트는 `local_only=true`, `not_operational_readiness=true`로 표시되며 아래 운영 증거 묶음을 대체하지 않습니다. 이 래퍼는 운영 번들 템플릿이 필수 파일, 설치 명령, placeholder 교체 안내, env/config 변수명 정합성, 로컬 데모 key 제거 기준을 만족하는지도 함께 확인합니다. 운영 번들에 이 JSON을 포함하면 `operational_bundle_check.py`가 필수 로컬 회귀 checks 통과 여부와 local-only 표시, config의 key env 필드와 env 파일 변수명 일치, Marqo URL/index/container, PoC 품질 임계값을 다시 검증해 불완전한 로컬 증거나 느슨한 운영 설정이 handoff에 섞이지 않게 합니다.
요구사항별 증거 상태는 `python scripts/requirements_audit.py --evidence-collection-report logs/evidence-collection-plan.json --output logs/requirements-audit.json --markdown-output logs/requirements-audit.md --blocker-checklist-output logs/requirements-blockers.md`로 확인합니다. 운영 readiness 리포트가 없거나 실패하면 항목별 상태가 `local_only`, `implemented_unverified`, `missing`, `failed`로 남고 전체 `ok=false`, top-level `completion_ready=false`가 됩니다. 수집 dry-run 리포트를 함께 넘기면 누락 secret, CSV/config, 대표 사이트 설정, 보안 설정 파일 같은 사전 blocker가 감사 Markdown 상단 `Operational Blockers` 요약과 요구사항별 `Collection Blockers` 열에 표시됩니다. `completion_ready=true`는 모든 요구사항 통과, local acceptance 최신성, `operational-readiness.json ok=true`, evidence collection 완료가 모두 충족될 때만 나오는 최종 납품 gate입니다. `requirements-blockers.md`는 blocker별 누락 입력, 해소 방법, redacted 수집 명령, readiness 명령 템플릿을 운영 체크리스트로 분리합니다.
운영 handoff 묶음은 `python scripts/prepare_handoff.py --output logs/handoff-report.json --markdown-output logs/handoff-report.md`로 반복 생성할 수 있습니다. 이 래퍼는 local acceptance, 로컬 `quality-report.json`, `widget-dom.json`, `csv-index.json`, `server-db-intake.md` 템플릿 검사, production compose 포트 노출 검사, 운영 장애 시나리오 점검, `operational_simulation.py` 리허설, evidence dry-run, readiness, requirements audit, operational bundle 생성, bundle check를 같은 순서로 실행합니다. 번들에는 서버/DB 인수 양식, `server_db_intake_check.py`, `compose_exposure_check.py`, `go_live_scenario_check.py`, Gemini+Marqo 런타임 기준, 운영 장애 시나리오표, 운영 위험표, 서버 82 runbook이 같이 들어갑니다. 로컬 품질/위젯/색인/시뮬레이션 리포트는 최신 소스 기준 blocker 설명과 사전 리스크 검출 상태를 남기기 위한 local-only 증거이며 운영 품질·대표 사이트·영구 색인 증거를 대체하지 않습니다. handoff 리포트는 `operational_simulation_ok`, `sync_lifecycle_ok`, `server_db_intake_status`, `compose_exposure_ok`를 별도로 표시합니다. 운영 입력이 아직 없을 때 `evidence_collection_dry_run`, `operational_readiness`, `requirements_audit`가 exit 1을 내는 것은 예상 가능한 blocker 상태로 기록하며, `handoff_ok=true`와 `operational_signoff_ok=false`를 분리해 “전달 묶음 준비 완료”와 “운영 인수 완료”를 혼동하지 않게 합니다. 기본 리포트는 운영 전달용 요약만 남기며, 개별 스크립트 stdout/stderr tail이 필요하면 `--include-command-output`을 추가합니다.

1. `python scripts/server_preflight_check.py --role api --require-docker --require-compose --require-pyodbc --expected-odbc-driver "ODBC Driver 18 for SQL Server"`로 운영 API/동기화 서버의 Linux, 지원 배포판, Python 패키지, MSSQL ODBC, Docker/Compose, CPU/RAM/디스크/open-file limit 기준을 먼저 확인합니다.
2. `python scripts/env_check.py --env-file /etc/haeorum-ai-search/haeorum-ai-search.env --role api --api-server-count <api_server_count>`로 운영 env 파일, 참조 설정 파일, Redis 공유 설정, 실제 설정 로딩을 확인합니다.
3. Marqo 서버를 기동합니다.
4. AI 검색 API 서버를 기동합니다.
5. `GET /health`로 API와 검색엔진 연결을 확인합니다.
6. `python scripts/mssql_view_check.py`로 MSSQL View 필수 컬럼과 `p_idx`/`site_id` 같은 별칭, 샘플 row의 상품번호/상품명/`updated_at` 누락/형식 오류/미래값, 중복 상품번호, active 상품 대표 이미지 URL, 가격/수량/납기/속성 필터 coverage, DB role/permission 기준 read-only 계정 여부를 확인합니다.
7. 필요 시 `python scripts/mssql_export_csv.py --output-csv /data/haeorum-ai-search/products-full.csv --fetch-size 1000 --report-output /var/log/haeorum-ai-search/mssql-export.json`로 전체 상품 CSV를 추출합니다. export 리포트도 `source_columns`와 `column_report`를 남기므로 필수 컬럼/별칭 누락을 View 점검과 같은 기준으로 확인할 수 있습니다. readiness는 `fetchmany` batch 증거(`batched_fetch`, `fetch_batches`, `max_fetch_batch_rows`)가 없는 export 리포트를 구형 증거로 차단합니다.
8. `POST /admin/reindex`로 최초 색인을 실행합니다.
9. `python scripts/image_url_check.py --csv /data/haeorum-ai-search/products-full.csv --limit 100 --concurrency 5 --min-dimension 16 --require-https`로 실제 MSSQL export 기반 대표 이미지 URL 접근성, HTTPS 여부, 이미지 포맷, 최소 크기, 품질 경고를 표본 점검합니다. 대표 이미지 URL은 absolute HTTPS URL이어야 하며 credentials, 상대 URL, data URL, localhost/loopback/private/link-local/reserved/multicast/unspecified 주소와 HTTP 대표 이미지는 색인 전 probe와 운영 readiness에서 차단됩니다. 이미지 probe는 다운로드 전에 호스트 DNS가 사설망/예약/메타데이터 IP로 풀리는지도 확인하고, redirect 대상도 safe HTTP(S) URL인지 재검사하므로 외부 CDN URL이 내부망/메타데이터 주소로 우회되는 경우 실패합니다. 리포트의 `failure_category_counts`, `warning_type_counts`, `blocking_warning_type_counts`, `attempts`, `non_https_active_image_url_count`로 HTTP 4xx/5xx, MIME mismatch, decode 실패, 최소 크기 미달, unsafe redirect, non-public DNS 해석, HTTP 이미지, watermark/placeholder 경고, retry 후 성공/실패 비율을 먼저 분류합니다. readiness는 최소 100개 이미지, 실패 0건, HTTP 대표 이미지 0건, placeholder/sample 대표 이미지 경고 0건, 동시성 1~5개와 timeout/retry/max MB/min dimension 실행 파라미터, `require_https=true`, `csv`, `csv_fingerprint`, `source.csv_is_builtin_sample=false`, `source.dataset_is_builtin_sample_derived=false`가 증거 JSON에 남아 있어야 통과합니다.
10. `python scripts/api_smoke_test.py`로 실제 API 표면을 확인합니다.
11. `python scripts/mall_config_check.py --config <malls.json> --min-count <expected-malls>`로 가맹점 설정을 검증합니다.
12. 실제 MSSQL export에서 만든 PoC CSV를 Marqo에 색인한 뒤 `python scripts/quality_report.py --engine marqo --index-name <index_name> --marqo-url <marqo_url> --cases /etc/haeorum-ai-search/quality-cases.json --strict --min-products 300 --max-text-ms 3000 --max-image-ms 5000 --max-mixed-ms 5000`을 실행해 품질/응답속도 리포트를 생성합니다. local engine 리포트와 번들 `sample_products.csv`를 복사하거나 재저장한 CSV, 또는 기본 내장 품질 케이스만 사용한 리포트는 운영 readiness에서 거절됩니다.
13. 같은 PoC CSV로 `python scripts/csv_index.py --mode reindex --engine marqo`를 실행해 영구 검색 인덱스에 실제 반영하고 `csv-index.json`을 남깁니다. 이 색인 증거도 샘플 파생 CSV이면 운영 통과 증거가 되지 않습니다. `csv_index.py`는 reindex 직후 Marqo stats를 다시 읽어 `post_index_document_count`가 active 상품 수와 정확히 같은지도 리포트에 남기며, 불일치하면 stale 문서나 다른 인덱스를 의심해 실패합니다. readiness는 전체 상품 CSV 지문이 `mssql-export.json`/`image-url-check.json`/`poc-dataset.json` 사이에서 일치하고, PoC CSV 지문이 `poc-dataset.json`/`quality-report.json`/`csv-index.json` 사이에서 일치해야 운영 데이터 계보를 인정합니다.
14. `python scripts/marqo_resource_check.py`로 Marqo health/index stats, `/indexes/{index}/settings` 계약, Gemini health/텍스트+이미지 임베딩 프로브, 컨테이너 CPU/RAM 스냅샷, Vespa 저장소 디스크 스냅샷과 임계치 판정을 남깁니다. `--index`는 품질 리포트와 CSV 색인 리포트에 사용한 같은 Marqo index 이름이어야 하며, Gemini 백엔드에서는 `no_model`/custom vector tensor fields/차원 값과 Gemini `/health`/텍스트+이미지 `/embed` 모델·차원 값, native 백엔드에서는 Marqo model과 이미지 URL 처리 설정이 운영 설정과 맞아야 합니다. readiness는 CPU/RAM 또는 저장소 사용률이 지정 임계치를 넘으면 운영 증거를 실패로 봅니다.
15. `python scripts/load_test.py`로 텍스트/이미지/혼합 검색 부하 스모크를 확인합니다.
16. 같은 850 active user 혼합 트래픽을 API 서버 1대와 2대 이상 구성에서 각각 측정하고 `python scripts/load_compare.py`로 `api-scale.json`을 남깁니다.
17. `python -m app.sync_worker --mode sync`를 별도 프로세스로 등록합니다.
18. 대표 가맹점 1~3개 사이트에 위젯을 삽입합니다.
19. PC/모바일에서 텍스트, 이미지, 혼합 검색과 상품 상세 이동을 확인합니다.
20. 아래 증거 파일을 모아 `python scripts/operational_readiness.py`를 실행하고 결과를 배포 기록에 첨부합니다.

## 4. 운영 전 인수 게이트

- 텍스트 검색이 결과를 반환합니다.
- 이미지 검색이 결과를 반환합니다.
- 텍스트+이미지 혼합 검색이 `meta.query_type=text_image`로 동작합니다.
- `top`은 1~3개만 반환합니다.
- `items`에 관련 상품 리스트가 반환됩니다.
- `suggested_categories`가 반환됩니다.
- 상품 URL이 해당 `mall_id` 도메인 또는 설정된 템플릿으로 생성됩니다.
- `inactive`, `display_yn=N`, 삭제 상품은 검색 결과에서 제외됩니다.
- `POST /admin/sync` 또는 sync worker가 변경 상품을 반영합니다.
- 대표 가맹점 사이트에서 위젯 검색과 상세 페이지 이동이 정상입니다.

## 5. PoC 품질 리포트

운영 PoC CSV는 최소 300개, 권장 500개 상품을 사용합니다. 텍스트/이미지/혼합 검색 케이스, Top 3, 카테고리 추천, 낮은 유사도 안내, 모드별 평균/최대 응답시간과 임계값 통과 여부는 리포트 파일로 남깁니다. 운영 리포트는 `contracts/quality_cases.example.json`을 복사해 만든 실제 `quality-cases.json`을 `--cases`로 넘겨야 하며, 이미지-only와 혼합 검색 케이스는 `image_path`로 실제 기준 이미지 파일을 사용해야 합니다. 운영 bundle check와 readiness는 이 템플릿에 텍스트 2개 이상, 이미지-only 1개 이상, 혼합 검색 1개 이상이 있고, 텍스트 케이스 중 최소 1개가 `typo_or_synonym` 태그로 오타/동의어/표현 변형을 검증하며, positive case가 `expected_min_results >= 3`와 기대 카테고리 또는 기대 상위 상품을 검증하고, 최소 1개 저품질/엉뚱한 이미지 case가 `expected_low_confidence=true`를 검증하는지도 확인합니다. readiness는 `quality-report.json`의 `csv_fingerprint`, `image_cases_with_file_source >= 1`, `mixed_cases_with_file_source >= 1`을 요구하므로 base64/data URL만 넣은 이미지 케이스는 운영 품질 증거로 인정하지 않습니다. `quality_report.py`는 이 case contract가 실패하면 전체 `ok=false`로 기록합니다.

```powershell
python scripts\quality_report.py `
  --csv /data/haeorum-ai-search/poc_products.csv `
  --engine marqo `
  --index-name haeorum-products `
  --marqo-url http://127.0.0.1:8882 `
  --cases /etc/haeorum-ai-search/quality-cases.json `
  --strict `
  --min-products 300 `
  --recommended-products 500 `
  --max-text-ms 3000 `
  --max-image-ms 5000 `
  --max-mixed-ms 5000 `
  --json-output /var/log/haeorum-ai-search/quality-report.json `
  --markdown-output /var/log/haeorum-ai-search/quality-report.md
```

배포 후에는 실제 검색/클릭 로그에서 무결과, 낮은 유사도, 반복 검색되지만 클릭 없는 쿼리와 많이 클릭된 상품을 주기적으로 집계합니다. 검색 로그에는 추론 카테고리, 혼합 검색 가중치, 상위 결과의 text/image/category_intent source score, 이미지 정규화/품질 경고, 응답 `elapsed_ms`, cache hit 여부가 함께 남으므로, 리포트의 `query_type_latency_ms`/`cache_latency_ms`/`slow_queries`/`slow_search_samples`로 text/image/text_image 중 느린 경로와 cache miss 병목을 먼저 분리하고, `mixed_weight_performance`로 가중치 조합별 평균 top score, low-confidence, 무결과 비율을 비교하며, `image_quality_warning_counts`로 반복되는 업로드/대표 이미지 품질 문제를 확인합니다. 리포트의 `recommendations`는 동의어/오타 보정, 카테고리명 보강, 상품명/키워드/대표 이미지 개선 후보와 느린 query type/검색어 재현 후보를 우선순위별로 묶습니다. `synonym_seed_candidates`는 `query-synonyms.json`에 수동 검토 후 넣을 후보, `quality_case_candidates`는 `quality-cases.json`에 추가할 회귀 테스트 초안, `mixed_weight_recommendation`은 대표 품질 케이스로 재검증해야 할 혼합 검색 가중치 A/B 후보입니다. 운영자는 `GET /admin/search-insights?min_searches=3&limit=50&slow_text_ms=3000&slow_image_ms=5000&slow_mixed_ms=5000`로 같은 집계를 조회하거나, 아래 명령으로 JSON/Markdown 증적을 파일로 남길 수 있습니다. 이 리포트는 readiness 필수 증거는 아니지만 운영 품질/성능 개선 작업의 입력으로 보관합니다.

```powershell
python scripts\search_insights.py `
  --search-log /var/log/haeorum-ai-search/search.jsonl `
  --min-searches 3 `
  --limit 50 `
  --slow-text-ms 3000 `
  --slow-image-ms 5000 `
  --slow-mixed-ms 5000 `
  --json-output /var/log/haeorum-ai-search/search-insights.json `
  --markdown-output /var/log/haeorum-ai-search/search-insights.md
```

전체 상품 추출본에서 PoC CSV를 만들 때는 카테고리 균형, 이미지 URL 누락, unsafe/HTTP 대표 이미지 URL, 중복 상품번호를 먼저 점검합니다. `poc-dataset.json`의 `selected_categories`는 권장 카테고리별 최소 수량을 충족해야 하며, 특정 카테고리로 편향된 PoC CSV나 HTTP/credential/내부망 대표 이미지 URL이 섞인 PoC CSV는 운영 readiness에서 차단됩니다.

```powershell
python scripts\poc_dataset_builder.py `
  --csv /data/haeorum-ai-search/products-full.csv `
  --target-size 300 `
  --min-products 300 `
  --min-per-category 10 `
  --output-csv /data/haeorum-ai-search/poc_products.csv `
  --report-output /var/log/haeorum-ai-search/poc-dataset.json
```

PoC CSV를 색인하기 전에는 CSV 색인 스크립트의 드라이런으로 active/inactive 수, 이미지 누락, unsafe/non-HTTPS 이미지 URL, 카테고리 분포, 중복 상품번호를 증거로 남깁니다.

```powershell
python scripts\csv_index.py `
  --csv /data/haeorum-ai-search/poc_products.csv `
  --engine marqo `
  --index-name haeorum-products-poc `
  --marqo-url http://127.0.0.1:8882 `
  --dry-run `
  --output /var/log/haeorum-ai-search/csv-index-dry-run.json `
  --markdown-output /var/log/haeorum-ai-search/csv-index-dry-run.md
```

드라이런과 품질 리포트가 통과하면 같은 CSV를 `--mode reindex`로 PoC 인덱스에 반영합니다. 운영 색인 증거에는 `--validate-images`를 붙여 색인 전에 대표 이미지 다운로드/검증도 수행합니다. 실행 리포트의 `validate_images`가 false이거나 `active_unsafe_image_url_count`/`active_non_https_image_url_count`가 0이 아니면 readiness가 실패합니다. 실행 리포트의 `post_index_document_count_ok`가 false면 색인 후 Marqo 문서 수가 CSV active 상품 수와 맞지 않는 상태이므로 같은 인덱스에 stale 문서가 남았거나 잘못된 인덱스를 확인한 것입니다.

```powershell
python scripts\csv_index.py `
  --csv /data/haeorum-ai-search/poc_products.csv `
  --engine marqo `
  --index-name haeorum-products-poc `
  --marqo-url http://127.0.0.1:8882 `
  --mode reindex `
  --validate-images `
  --output /var/log/haeorum-ai-search/csv-index.json
```

## 6. 가맹점 설정 검증

1,700개 가맹점 목록이 CSV/엑셀 export로 관리된다면 먼저 표준 `malls.json`을 생성합니다. 입력은 `.csv`, `.txt`, `.xlsx`, `.xlsm`을 받을 수 있으며 첫 번째 worksheet의 헤더를 사용합니다. 헤더는 `mall_id`/`site_id`/`가맹점ID`, `domain`/`origin`/`도메인`, `api_key`/`공개API키`를 인식하고, 선택 컬럼으로 `product_url_template`/`상품링크템플릿`, `excluded_product_ids`, `excluded_categories`, `hide_prices`/`가격비공개`, `price_multiplier`, `price_adjustment`, `price_round_to`를 받을 수 있습니다. `사용여부` 값은 `Y/N`뿐 아니라 `사용`/`미사용`, `예`/`아니오`도 처리합니다. `mall_id`/`site_id`는 영문/숫자/하이픈만 쓰고 양끝은 영문 또는 숫자로 둡니다. 운영 mall `allowed_origins`는 HTTPS 공개 origin이어야 하며 localhost/loopback/private/link-local/reserved/multicast/unspecified host는 생성/검증 단계에서 차단됩니다. 운영 배포 전에는 `replace-with...`, `<...>`, `...`, `sample`, `dummy`, `*-dev`, `*dev-key*` 형태의 공개 API key를 실제 값으로 교체합니다. `--generate-missing-api-keys`를 쓰면 비어 있는 enabled mall API key를 난수로 생성하므로, 생성된 key를 각 가맹점 템플릿의 위젯 설정에도 같이 배포합니다.

```powershell
python scripts\mall_config_builder.py `
  --input /etc/haeorum-ai-search/haeorum-malls.xlsx `
  --output /etc/haeorum-ai-search/malls.json `
  --report-output /var/log/haeorum-ai-search/mall-config-build.json `
  --min-count 1700 `
  --sort-by-mall-id
```

생성된 1,700개 가맹점 설정 파일은 중복 `mall_id`, 중복 `api_key`, 중복 enabled `allowed_origins`, 중복 enabled 상품 URL prefix, sample/placeholder 공개 API key, 짧거나 문자 다양성이 낮은 공개 API key, 누락된 URL 템플릿, 제외 정책 형식, 가격 정책 형식을 적용 전에 점검합니다. collector dry-run도 `mall_config_source` export와 `mall_config` 파일을 발견하면 같은 검증을 먼저 수행해 잘못된 파일을 `invalid_input_files` blocker로 표시합니다. 최종 readiness는 `mall-config-build.json`의 `ok=true`, `generated_api_key_count=0`, validation enabled/API key hash 1,700개 이상과 `mall-config-check.json`의 `ok=true`, `mall_count`/`enabled_count` 1,700 이상, `enabled_mall_ids`, `enabled_mall_origins`, `enabled_mall_product_url_prefixes`, `enabled_mall_api_key_hashes` 1,700개 이상, `api_key_strength.required=true`, `weak_api_key_mall_ids=[]`, `problems=[]`를 모두 요구하므로 개수가 맞아도 builder 계보, API key, 중복 origin/prefix, `allowed_origins`, `product_url_template` 문제가 하나라도 남으면 실패합니다.

```powershell
python scripts\mall_config_check.py `
  --config /etc/haeorum-ai-search/malls.json `
  --min-count 1700 `
  --output /var/log/haeorum-ai-search/mall-config-check.json
```

`product_url_template`에는 반드시 `{product_id}`가 들어가야 합니다. 템플릿에서 `{mall_id}`를 쓰는 경우 `mall_id`가 URL 구조를 바꾸지 못하도록 영문/숫자/하이픈 식별자로 먼저 검증되고, 스크립트가 실제 포맷 가능 여부도 함께 확인합니다. 치환 결과는 credentials, 공백, 역슬래시, localhost/loopback/private/link-local/reserved/multicast/unspecified host가 없는 공개 HTTP(S) 절대 URL이어야 합니다. 운영 production에서는 전역 `HAEORUM_PRODUCT_URL_TEMPLATE`과 enabled mall별 `product_url_template`이 모두 HTTPS여야 합니다. 검색 API는 `{product_id}` 치환값을 URL 인코딩하므로 운영 DB의 상품번호에 `&`, `?`, `/`, 공백이 섞여도 상세 URL의 query/path 구조를 깨지 않습니다. MSSQL View의 `product_url`이 상대 경로이면 검색 API가 해당 mall의 `product_url_template` 도메인 기준으로 절대 URL을 생성합니다. `javascript:` scheme이나 protocol-relative 외부 URL처럼 안전하지 않은 `product_url`은 응답에 그대로 쓰지 않고 mall 템플릿으로 대체합니다. JS 위젯은 API 응답 URL도 한 번 더 검사해 안전하지 않은 상세 링크와 이미지 URL을 렌더링하지 않고 클릭 로그에도 원문 위험 URL을 남기지 않습니다. enabled 가맹점은 `api_key`, `product_url_template`, `allowed_origins`가 모두 필요합니다. `allowed_origins`는 `https://shop001.haeorumgift.com`처럼 scheme/host/port만 포함하는 HTTPS 공개 origin이어야 하며 path, query, fragment, localhost, loopback, private, link-local, reserved, multicast, unspecified host는 허용하지 않습니다. `excluded_product_ids`, `excluded_categories`는 배열 또는 쉼표/세미콜론/파이프 구분 문자열로 지정할 수 있습니다. `hide_prices`는 boolean, `price_multiplier`와 `price_adjustment`는 숫자, `price_round_to`는 1 이상의 정수여야 합니다.

## 7. 부하 테스트 기준

문서 기준 목표:

- 텍스트 검색: 1~3초 이내
- 이미지 검색: 3초 내외 목표
- 100 concurrent search 요청 기준 검증

예제 부하 스모크:

`response_contract.ok=true`만으로는 운영 부하 증거가 인정되지 않습니다. readiness는 유효 성공 응답 수, `invalid_successful_responses=0`, 최소 Top/관련상품/카테고리 수, Marqo-only engine 분포, 계획된 `mode_counts` 대비 실제 query type coverage를 다시 계산합니다.

```powershell
python scripts\load_test.py `
  --base-url https://ai-search.haeorumgift.com `
  --mall-id shop001 `
  --api-key public-shop001-key `
  --admin-key 운영관리자키 `
  --origin https://shop001.haeorumgift.com `
  --mode text `
  --unique-query-suffix prod-backend-proof `
  --requests 100 `
  --concurrency 100 `
  --p95-ms 3000

python scripts\load_test.py `
  --base-url https://ai-search.haeorumgift.com `
  --mall-id shop001 `
  --api-key public-shop001-key `
  --admin-key 운영관리자키 `
  --origin https://shop001.haeorumgift.com `
  --mode image `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 90 `
  --concurrency 30 `
  --p95-ms 5000 `
  --max-error-rate 1

python scripts\load_test.py `
  --base-url https://ai-search.haeorumgift.com `
  --mall-id shop001 `
  --api-key public-shop001-key `
  --admin-key 운영관리자키 `
  --origin https://shop001.haeorumgift.com `
  --mode mixed `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 90 `
  --concurrency 30 `
  --p95-ms 5000 `
  --max-error-rate 1
```

850 active user 기준의 전체 혼합 트래픽은 텍스트/이미지/혼합 비율과 실제 기준 이미지 파일을 지정해 별도 리포트로 남깁니다. 운영 readiness와 API scale 비교는 생성 placeholder 이미지가 아니라 `--image-file`로 남긴 `image_input.source=file` 또는 `--additional-image-file`/`load.image_files`로 남긴 `image_input.source=files` 증거만 인정합니다.

관리자 메트릭 기반 backend proof 리포트는 반복 쿼리 cache hit만 측정하지 않도록 `--unique-query-suffix <run-id>`를 붙입니다. 이 옵션을 빼면 체감 지연시간 측정에는 유용하지만, 이미 캐시가 찬 환경에서는 Marqo/Gemini backend-attempt delta가 0으로 남아 운영 증거가 실패할 수 있습니다.

`load_test.py`는 HTTP 2xx 여부와 p95/error rate뿐 아니라 검색 응답 본문이 `meta.query_type`/`elapsed_ms`/`engine`/`limit`/`offset`, `top`, `items`, `suggested_categories`, 상품번호/상품명/카테고리/가격/이미지/상세 URL/유사도 필드 계약을 지키는지도 확인합니다. 부하 중 malformed JSON, 잘못된 `query_type`, `top` 3개 초과, 빈 관련 상품/카테고리 추천, 결과 필드 누락 같은 응답은 오류 요청으로 집계합니다. 리포트의 `response_contract`에는 query type 분포, engine 분포, non-Marqo 응답 수, 유효 응답 수, 최소 Top/관련상품/카테고리 수가 남고 readiness는 `response_contract.ok=true`, `non_marqo_engine_responses=0`, 그리고 `mode_counts`에서 계획한 text/image/mixed 요청 수와 `response_contract.query_type_counts`/`expected_query_type_counts`가 맞지 않으면 해당 부하 증거를 실패로 봅니다. `/admin/metrics`의 `engine.backend`은 `server_metrics.after.snapshot.engine_backend`으로 기록되며, readiness는 이 값이 `marqo`인 부하 증거만 운영 부하 증거로 인정합니다.

```powershell
python scripts\load_test.py `
  --base-url https://ai-search.haeorumgift.com `
  --mall-id shop001 `
  --api-key public-shop001-key `
  --admin-key 운영관리자키 `
  --origin https://shop001.haeorumgift.com `
  --scenario mixed-traffic `
  --active-users 850 `
  --traffic-mix text=70,image=10,mixed=20 `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 850 `
  --concurrency 100 `
  --p95-ms 5000 `
  --max-error-rate 1 `
  --output /var/log/haeorum-ai-search/load-mixed-traffic.json `
  --markdown-output /var/log/haeorum-ai-search/load-mixed-traffic.md
```

같은 조건으로 API 서버 1대와 2대 이상 구성을 각각 측정한 뒤 비교 리포트를 생성합니다.

```powershell
python scripts\load_compare.py `
  --single-report /var/log/haeorum-ai-search/load-mixed-traffic-1-api.json `
  --multi-report /var/log/haeorum-ai-search/load-mixed-traffic-2-api.json `
  --output /var/log/haeorum-ai-search/api-scale.json `
  --markdown-output /var/log/haeorum-ai-search/api-scale.md
```

운영 부하 테스트에서는 `--admin-key`를 지정해 `/admin/metrics` 전후 스냅샷을 함께 남깁니다. readiness 게이트는 부하 리포트의 요청 수, 동시성, HTTPS 비로컬 `base_url`, HTTPS 가맹점 `origin`, `target_validation.ok=true`, 850 active user 혼합 트래픽의 text/image/mixed 포함 여부, `mode_counts`와 실제 응답 `meta.query_type` 기반 `response_contract.query_type_counts`/`expected_query_type_counts`의 일치 여부, query type별 latency, 오류율, p95/p99 지연시간뿐 아니라 `response_contract.ok=true`, 모든 성공 응답의 `meta.engine=marqo`, `engine_backend=marqo`, `engine_index`, `marqo_model`, `embedding_backend`, 실제 파일 기반 `image_input.source=file`/`files`, 성공 응답 수 이상으로 증가한 `search_events`, 이미지/혼합 성공 응답 수 이상으로 증가한 `image_search_events`, Marqo/Gemini backend HTTP request attempts·connections opened·connection reuses·stale reconnects·error responses·connection-close responses·elapsed ms·request body bytes, Gemini query vector runtime cache quota/entry/wait/timeout, rate limit backend/Redis fallback 지표, 검색 캐시 backend/Redis/TTL/오류, cache clear 오류, Redis cache miss lock claim/contention/error/release-error/wait-timeout, singleflight wait/timeout, 검색/이미지 queue full 및 wait event/total wait ms delta도 확인합니다. Gemini 모드에서는 `gemini_query_vector_runtime_text_max_entries`는 100 이상, `gemini_query_vector_runtime_image_max_entries`는 30 이상이어야 하며, 작으면 운영 부하/API scale 증거가 실패합니다. load report의 `expected_query_type_latency_ms`와 `response_query_type_latency_ms`는 전체 p95 외에 text/image/text_image별 count, p95, p99, max를 남기며, readiness/API scale은 query type별 p95 또는 p99가 부하 임계값을 넘거나 해당 latency breakdown이 누락되면 전체 p95가 통과해도 실패 처리합니다. 부하 클라이언트는 검색 POST 요청에 thread-local keep-alive 연결을 사용하고 stale 연결은 1회 재연결하며, `client_transport.search_requests.connections_opened`, `connection_reuses`, `request_attempts`, `requests_sent`, `stale_reconnects`, `connection_close_responses`, `gzip_responses`, wire bytes, decoded bytes를 남겨 클라이언트 재연결 비용, stale 연결 복구, 공개 API gzip 미적용이 p95/p99 측정값에 섞였는지 확인할 수 있습니다. 요청 수가 동시성보다 큰 부하에서 `connection_reuses=0`이거나 `stale_reconnects`/`connection_close_responses`가 증가하거나 `gzip_responses`/응답 byte 계측이 누락 또는 0이거나 wire bytes가 decoded bytes보다 작지 않으면 readiness/API scale 게이트가 실패합니다. 실행 delta에서 `backend_marqo_error_responses`, `backend_marqo_connection_close_responses`, Gemini 모드에서는 `backend_gemini_error_responses`, `backend_gemini_connection_close_responses`, `gemini_query_vector_wait_timeouts`, `cache_lock_errors`, `cache_lock_release_errors`, `cache_lock_wait_timeouts`, `singleflight_wait_timeouts`가 증가해도 backend HTTP 안정성, Gemini query vector coalescing, Redis miss lock, 또는 같은 프로세스 duplicate-miss coalescing이 부하 중 깨진 증거로 보고 실패합니다. `/admin/metrics`의 검색 이벤트 수는 최근 tail 요약이라 운영 로그가 포화되면 delta가 과소 측정될 수 있으므로, load report의 `server_metrics.run_log_coverage`가 같은 실행의 검색 이벤트를 성공 응답 수 이상으로 증명하면 검색 이벤트 delta undercount를 보완 증거로 인정합니다. load report의 server metrics snapshot에는 `search_log_write_errors`와 `error_log_write_errors`도 남아 로그 I/O 문제가 부하 중 요청 실패로 번지지 않았는지 확인할 수 있습니다. `mixed-traffic`에서는 `--requests`가 `--active-users`보다 작으면 active user 수만큼 요청을 자동 실행하므로 850명 시나리오가 100건 기본값으로 축소되지 않습니다. 이 시나리오는 `--mall-sample-size 50`으로 여러 enabled mall의 API key, Origin, product URL prefix를 순환해야 하며, readiness/API scale은 `mall_identity`와 `response_contract.expected_mall_id_counts`가 샘플 mall별 응답을 실제로 포함하지 않으면 단일 몰 부하로 보고 실패합니다. 운영 env preflight는 이 필수 부하 증거가 rate limit에 의해 자체 차단되지 않도록 전체 검색 rate limit을 0 또는 850 이상, 이미지/IP 및 이미지/mall rate limit을 0 또는 255 이상으로 요구합니다. API 서버 1대/2대 비교를 위해 같은 HTTPS API 대상, 같은 `mall_id`/`origin`, 같은 조건의 `mixed-traffic`, 같은 이미지 파일 조건, 같은 인덱스/모델/임베딩 백엔드 리포트를 각각 `--api-server-count 1`, `--api-server-count 2`로 남기고 `load_compare.py`로 `api-scale.json`을 생성합니다. collector dry-run은 이 두 입력 리포트가 운영 API 대상, workload, 파일 기반 이미지, Marqo response engine, `mode_counts` 대비 query type coverage와 query type latency, `/admin/metrics` server metrics와 runtime identity 조건을 만족하는지도 실행 전에 검사합니다. Marqo CPU/RAM은 `marqo-resource.json`으로 별도 확인하며, 상시 모니터링은 admin 인증이 포함된 scrape 설정으로 `/admin/metrics.prom`을 수집합니다.
API 응답에는 `X-Haeorum-API-Instance`가 노출되며, load report는 이를 `api_instance_coverage`로 집계합니다. `--api-server-count 2` 이상인 multi API 부하/API scale 증거는 누락 header가 없어야 하고, 성공 응답이 기대 API 인스턴스 수 이상으로 분산되며, 각 인스턴스가 최소 5% 이상 응답해야 합니다. 같은 host에서 여러 API 인스턴스를 띄우면 `HAEORUM_API_INSTANCE_ID`를 인스턴스마다 다르게 지정해 로드밸런서 분산 증거가 실제 서버/container를 가리키게 합니다. multi API 부하에서는 각 API 인스턴스의 직접 관리자 주소를 `--admin-metrics-base-url`로 반복 지정해 `/admin/metrics`와 `/admin/search-log`를 인스턴스별로 수집하고, 내부 전용 관리자 주소를 쓰는 경우 `--allow-private-admin-metrics-targets`를 함께 지정합니다. `server_metrics.admin_metrics_source_coverage`는 기대 API 서버 수 이상의 metrics source를 증명해야 합니다.

운영 부하 통과 조건에서는 `rate_limited_events`, `rate_limit_fallback_events`, `rate_limit_redis_backoff_*`, `cache_error_count`, `cache_clear_errors`, `cache_redis_backoff_*`, `search_queue_full_events`, `image_queue_full_events`의 실행 delta가 모두 0이어야 합니다. 또한 `cache_lock_run_avg_wait_ms`, `singleflight_run_avg_wait_ms`, `gemini_query_vector_run_avg_wait_ms`, `search_queue_run_avg_wait_ms`, `image_queue_run_avg_wait_ms`가 `max_server_wait_avg_ms`를 넘으면 timeout 전 단계의 내부 대기 포화로 봅니다. 이 중 하나라도 증가하거나 상한을 넘으면 요청 성공률이 임계값 안에 있더라도 부하 중 rate limit 오설정, Redis 장애 fallback/backoff, 캐시 장애, queue 포화가 발생한 증거로 보고 readiness/API scale 게이트가 실패합니다.

API scale 입력 리포트도 같은 응답 형태 검증을 포함해야 합니다. `load_compare.py`가 생성하는 `response_shape`에는 유효/무효 성공 응답 수와 최소 Top/관련상품/카테고리 수가 남고, readiness는 제출된 `api-scale.json`에서 이 값을 다시 계산합니다.

## 8. 운영 readiness 리포트

운영 완료 판단은 개별 명령 실행 여부가 아니라 증거 파일 묶음으로 남깁니다. 아래 명령은 API 스모크, MSSQL View, 이미지 URL, PoC 품질/응답시간, PoC CSV 실제 색인, 1,700개 가맹점 설정, Marqo health/Gemini 텍스트+이미지 probe/CPU/RAM/저장소, 서버 preflight, 텍스트/이미지/혼합/850 active user 부하 테스트, API 서버 1대/2대 비교, 대표 가맹점 사이트, 보안 설정 증거를 모두 확인합니다.

보안 증거는 HTTPS 비로컬 공개 API base URL, 운영 환경 변수, Nginx 업로드 제한, logrotate 설정, 방화벽/DB ACL 확인 결과를 함께 반영합니다.

```powershell
python scripts\marqo_resource_check.py `
  --marqo-url http://127.0.0.1:8882 `
  --index haeorum-products `
  --container marqo-api `
  --storage-container vespa `
  --storage-path /opt/vespa/var `
  --expected-model Marqo/marqo-ecommerce-embeddings-L `
  --embedding-backend gemini `
  --gemini-model gemini-embedding-2 `
  --gemini-embedding-url http://127.0.0.1:8098 `
  --gemini-embedding-dimensions 1536 `
  --max-cpu-percent 90 `
  --max-memory-percent 85 `
  --max-storage-percent 85 `
  --output /var/log/haeorum-ai-search/marqo-resource.json
```

```powershell
python scripts\server_preflight_check.py `
  --role api `
  --require-docker `
  --require-compose `
  --require-pyodbc `
  --expected-odbc-driver "ODBC Driver 18 for SQL Server" `
  --output /var/log/haeorum-ai-search/server-preflight.json
```

```powershell
python scripts\security_check.py `
  --base-url https://ai-search.haeorumgift.com `
  --env-file /etc/haeorum-ai-search/haeorum-ai-search.env `
  --mssql-ip-restricted `
  --nginx-config /etc/nginx/sites-enabled/haeorum-ai-search.conf `
  --systemd-service /etc/systemd/system/haeorum-ai-search.service `
  --sync-systemd-service /etc/systemd/system/haeorum-ai-sync.service `
  --reindex-systemd-service /etc/systemd/system/haeorum-ai-reindex.service `
  --reindex-systemd-timer /etc/systemd/system/haeorum-ai-reindex.timer `
  --logrotate-config /etc/logrotate.d/haeorum-ai-search `
  --sync-alerting-configured `
  --output /var/log/haeorum-ai-search/security.json `
  --markdown-output /var/log/haeorum-ai-search/security.md
```

대표 가맹점 사이트 증거는 `contracts/representative_sites.example.json`을 실제 대표 사이트 URL/API key로 복사해 수정한 뒤 생성합니다. collector dry-run은 이 파일이 존재하기만 해도 통과시키지 않고, 대표 사이트 수, HTTPS 비로컬 `url`/`origin`/API base URL, placeholder API key, 상품 URL prefix 형식을 먼저 검증하며, `mall_config`가 있으면 site별 API key fingerprint가 해당 mall 설정과 맞는지도 대조해 `invalid_input_files` blocker로 표시합니다. 실제 검사는 먼저 `site_config`에서 site별 `url`, `origin`, API key, API base URL, 상품 URL 규칙 입력이 비어 있거나 placeholder로 남아 있지 않은지 확인하고, 설정 오류가 있으면 네트워크 검사 전에 실패 리포트를 남깁니다. API base URL은 query string 또는 fragment가 없는 HTTPS endpoint여야 하며 credentials, 공백, 역슬래시, non-public host, 잘못된 port를 포함하면 실패합니다. 설정이 유효할 때 PC/모바일 페이지의 위젯 삽입 흔적, `widget_init`의 실제 `mallId`/`siteId` 설정값, inline `HaeorumAISearch.init(...)` 또는 `data-hai-auto-init="true"` script 자동 초기화 설정, 위젯 초기화 옵션의 `apiBaseUrl`이 운영 API base URL과 일치하거나 위젯 `script src`가 같은 API base URL의 절대 HTTPS `widget.js`인지, 페이지 CSP가 inline init, 위젯 script, API base URL `connect-src`를 차단하지 않는지, `target`/`attachToSearchInput`/`attachAfterSelector` selector가 실제 HTML에 존재하거나 `autoAttach` 기본 동작이 대표 검색 input을 감지해 mount 가능한지, 텍스트/이미지/혼합 검색, 검색 중 중복 submit 차단, 각 검색의 `meta.engine=marqo`, 상위 결과 존재와 상품번호/상품명/카테고리/가격/이미지/상세 URL/raw `score`/표시용 `score_percent` 필드, 텍스트 검색 추천 카테고리로 재요청하는 `text_category_refetch`, 각 모드 첫 결과 상세 URL과 `top`/`items` 전체 상품 URL이 가맹점 URL 규칙과 맞는지, 상세 URL 접근, 모드별 클릭 로그 호출을 확인합니다. `apiBaseUrl` 옵션이 없어도 위젯 script가 API 서버의 절대 HTTPS URL이면 위젯은 그 origin을 fallback으로 사용합니다. 상대 `/widget.js`나 다른 도메인의 script만 있으면 API base URL을 알 수 없어 검증이 실패합니다. 대표 사이트 설정의 `widget_target`, `attach_to_search_input`, `attach_after_selector`는 실제 쇼핑몰 템플릿 selector를 고정하는 용도이며, 명시 selector가 페이지에 없으면 `widget_init.missing_selectors`로 실패합니다. 기존 사이트 개발자 없이 저장 HTML로 사전 검증하는 경우에는 대표 사이트마다 독립적으로 캡처한 PC/모바일 HTML을 모두 제공해야 하며, 같은 파일이나 같은 내용을 PC/모바일 또는 다른 대표 사이트에 재사용하면 collector dry-run이 `invalid_input_files`로 차단합니다. 이미지/혼합 검색은 생성 이미지가 아니라 `--image-file`로 지정한 실제 기준 JPG/PNG/WEBP 파일을 사용해야 하며, 리포트에는 `image_input.source=file`, 파일 경로, 크기, 해시, width/height가 남아야 readiness를 통과합니다. `text_category_refetch`는 추천 카테고리 필터 요청이 빈 결과를 만들지 않고 다른 카테고리 상품을 섞지 않는지도 함께 기록합니다. 기본적으로 `origin`을 상세 URL prefix로 사용하며, 예외 사이트는 `expected_product_url_prefix`, `expected_product_url_contains`, `expected_product_url_pattern`으로 규칙을 명시합니다. readiness는 서로 다른 `mall_id`, 사이트 URL, origin을 가진 대표 사이트 3개 이상을 요구하고 각 `url`/`origin`/`api_base_url`을 HTTPS 비로컬 URL로, 각 대표 사이트 검색 응답을 Marqo 엔진 응답으로, 각 모드의 `*_all_product_url_rules`를 통과 상태로 다시 검증합니다. 또한 대표 사이트의 `api_key_hash`가 `mall-config-check.json`의 해당 mall `enabled_mall_api_key_hashes`와 다르면 `representative_api_key_not_matching_mall_config`, 모드별 실제 `product_url`이 해당 mall `product_url_template` prefix와 다르면 `representative_product_url_not_matching_mall_template`로 실패합니다.

```powershell
`representative_site_check.py`의 `widget_script_asset` 체크는 페이지에서 계산한 실제 `script src`를 fetch해 2xx/3xx JavaScript 응답, JS content-type, `HaeorumAISearch`/`init` 마커를 확인하고 404/HTML 응답을 실패로 남깁니다. `result_image_csp` 체크는 텍스트/이미지/혼합 검색 결과의 실제 `image_url` origin이 페이지 CSP `img-src` 또는 `default-src`에 막히지 않는지도 확인합니다. readiness는 대표 사이트마다 이 체크들이 누락되거나 실패한 오래된 리포트도 통과시키지 않습니다.

python scripts\representative_site_check.py `
  --sites /etc/haeorum-ai-search/representative-sites.config.json `
  --api-base-url https://ai-search.haeorumgift.com `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --output /var/log/haeorum-ai-search/representative-sites.json `
  --markdown-output /var/log/haeorum-ai-search/representative-sites.md
```

기존 사이트 개발자와 연락이 안 되어 템플릿을 바로 수정할 수 없으면, 먼저 PC/모바일 HTML을 저장하거나 URL을 직접 넘겨 `widget_integration_probe.py`를 실행합니다. 이 도구는 운영 증거를 대체하지 않지만 `data-hai-auto-init` 삽입 스니펫, 검색 input/버튼 selector, 숨김/비활성/읽기전용 검색창 후보 제외, 중복 ID/class로 selector가 모호한 문제, meta/HTTP `Content-Security-Policy`의 `script-src-elem`/`script-src`/`default-src` 기준 inline init 및 외부 API 서버 `widget.js` 차단 위험, `connect-src`/`default-src` 기준 API base URL 호출 차단 위험, 상대 `widget.js` URL 위험, HTTP/localhost/credential/query/fragment가 섞인 API base/widget/page URL 위험을 미리 보여 줍니다. `--snippets-output-dir`를 함께 주면 페이지별 HTML 스니펫, 저장 HTML에 스니펫을 삽입한 `previews/*.preview.html`, preview 삽입 marker와 `data-hai-auto-init` 중복 여부 및 추천 `attachToSearchInput`/`attachAfterSelector`가 preview DOM에서 각각 하나만 매칭되는지 확인한 `preview-validation.json`/`.md`, `manual-install-plan.json`/`.md`가 생성되어 삽입 모드, selector 신뢰도, 수동 검토 사유, CSP allowlist 힌트를 바로 전달할 수 있습니다. collector dry-run도 로컬 저장 HTML에 추천 스니펫을 삽입한 preview 기준으로 marker, `data-hai-auto-init`, selector 재매칭을 검사해 중복 설치나 잘못된 selector 위험을 `preview validation failed` 입력 blocker로 막습니다. 검색창이 없거나 selector를 안정적으로 잡을 수 없는 임시 투입 상황에서는 `--allow-fallback-floating`으로 selector를 쓰지 않는 우측 하단 플로팅 버튼 스니펫을 생성할 수 있습니다. 이 방식은 기존 검색창 prefill이나 검색 버튼 옆 배치는 포기하지만, Tag Manager나 공통 include 한 줄만 넣을 수 있을 때 최소 검색 UI를 살리는 안전망입니다.

```powershell
python scripts\widget_integration_probe.py `
  --input saved-pc.html `
  --input saved-mobile.html `
  --mall-id shop001 `
  --api-key <public_api_key> `
  --api-base-url https://ai-search.haeorumgift.com `
  --output /var/log/haeorum-ai-search/widget-integration-probe.json `
  --markdown-output /var/log/haeorum-ai-search/widget-integration-probe.md `
  --snippets-output-dir /var/log/haeorum-ai-search/widget-snippets
```

운영 서버에 넘길 설정/배포 템플릿 묶음은 아래 명령으로 생성합니다. 이 명령은 지정한 디렉터리에 evidence config/env, 대표 사이트 config, 가맹점 설정 템플릿, 검색어 동의어 seed, 서비스 env, Nginx/systemd/logrotate 템플릿, Docker/Compose/requirements 참조 파일, 설치 및 증거 수집 체크리스트를 모읍니다. `local-acceptance.json`, `requirements-audit.json`, `operational-readiness.json`, `evidence-collection-plan.json`, `requirements-blockers.md`, `missing-evidence.sh`가 이미 생성되어 있으면 함께 넣어 로컬 회귀 증거, 현재 판정 근거, 남은 운영 증거 목록, 실행 명령을 전달합니다. 번들에 들어가는 `local-acceptance.json`은 전달용으로 `stdout_tail`/`stderr_tail` command output을 제거한 요약 JSON입니다. 번들에 들어가는 `malls.json`은 로컬 `sample_malls.json`의 구조를 사용하되 `public-...-dev-key` 값을 `replace-with-...-public-key` placeholder로 바꿔 생성합니다. 실제 `/etc`, `/data`, `/var/log`에는 직접 쓰지 않으므로 운영자가 값을 채운 뒤 체크리스트의 `install` 명령으로 반영합니다. `deploy/reference/*` 파일은 독립 build context가 아니라 운영 source root와 비교·검토할 참조 사본입니다. 번들 검증은 포함된 local acceptance 요약에 command output tail이 없고 handoff 리포트 JSON/Markdown 쌍이 함께 있으며 운영 경로로 렌더링됐는지, `operational-evidence.config.json`의 `api_key_env`, `admin_key_env`, `mssql_connection_string_env`가 `operational-evidence.env`에 존재하는지도 확인합니다.

```powershell
python scripts\prepare_operational_bundle.py `
  --output-dir /tmp/haeorum-ai-operational-bundle `
  --local-acceptance-source logs/local-acceptance.json `
  --local-acceptance-markdown-source logs/local-acceptance.md `
  --requirements-audit-source logs/requirements-audit.json `
  --requirements-audit-markdown-source logs/requirements-audit.md `
  --operational-readiness-source logs/operational-readiness.json `
  --operational-readiness-markdown-source logs/operational-readiness.md `
  --evidence-collection-source logs/evidence-collection-plan.json `
  --evidence-collection-markdown-source logs/evidence-collection-plan.md `
  --blocker-checklist-source logs/requirements-blockers.md `
  --missing-commands-source logs/missing-evidence.sh `
  --json-output /tmp/haeorum-ai-operational-bundle.json `
  --markdown-output /tmp/haeorum-ai-operational-bundle.md
```

생성한 번들은 아래 명령으로 별도 검증할 수 있습니다.

```powershell
python scripts\operational_bundle_check.py `
  --bundle-dir /tmp/haeorum-ai-operational-bundle `
  --output /tmp/haeorum-ai-operational-bundle-check.json `
  --markdown-output /tmp/haeorum-ai-operational-bundle-check.md
```

반복 배포에서는 `contracts/operational_evidence.config.example.json`을 `/etc/haeorum-ai-search/operational-evidence.config.json`으로 복사해 실제 경로와 key 환경 변수명을 채운 뒤 수집기를 먼저 실행합니다. `mall_config_source`에는 1,700개 가맹점 export CSV/XLSX를 지정해 `mall-config-build.json`과 `malls.json` 생성 계보를 남깁니다. `marqo.model`, `marqo.embedding_backend`, `marqo.gemini_embedding_url`, Gemini 모델/차원 값은 품질 리포트, CSV 색인, Marqo resource 설정 계약 검사와 Gemini 텍스트+이미지 probe 검사에 같은 값으로 전달되므로 운영 env와 실제 인덱스 생성 설정에 맞춰 둡니다. 서버 호스트에서 수집기를 실행할 때는 `marqo.url=http://127.0.0.1:8882`, `marqo.gemini_embedding_url=http://127.0.0.1:8098`을 쓰고, Docker 서비스 env에는 `MARQO_URL=http://marqo-api:8882`, `HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098`을 유지합니다. `contracts/operational_evidence.env.example`은 `/etc/haeorum-ai-search/operational-evidence.env`처럼 권한이 제한된 위치에 `0600`으로 설치해 실제 key/connection string으로 채우고 `--env-file`로 넘깁니다. `contracts/quality_cases.example.json`은 `/etc/haeorum-ai-search/quality-cases.json`으로 복사한 뒤 실제 PoC 텍스트, 이미지, 혼합 검색 케이스와 기준 이미지 파일 경로로 채웁니다. `load.image_file`은 이미지/혼합/850-user 부하와 API scale 부하에 사용할 실제 기준 이미지 파일 경로로 채우며, Gemini/이미지 경로 캐시 편향을 줄이려면 `load.image_files`에 추가 실제 기준 이미지를 넣습니다. dry-run은 이 파일들이 지원 포맷의 디코딩 가능한 이미지인지 먼저 검사합니다. `load.mixed_traffic.mall_sample_size`는 기본 50으로, 850-user 혼합 부하에서 `malls.json`의 여러 enabled mall API key/Origin/product URL prefix를 순환 검증합니다. `--env-file`을 지정한 수집기는 secret 참조와 하위 증거 명령의 `HAEORUM_*` 환경값을 해당 파일 기준으로만 구성하므로, 운영자의 셸에 남아 있는 이전 `HAEORUM_*` 값이 증적에 섞이지 않습니다. POSIX 운영 서버에서는 collector env 파일이 `0600`보다 열려 있거나 서비스 env 파일이 `0640`보다 열려 있으면 evidence collection/env/security readiness가 통과하지 않습니다. `env_check.env_file`이 존재하면 dry-run도 `env_check.py` 계약을 먼저 실행해 production engine, CORS, admin key, Redis scale, mall config path, settings-load blocker를 `invalid_input_files`로 차단합니다. 예시 설정은 `input_preparation.enabled=true`로 시작하므로, 수집기는 실제 MSSQL View에서 `/data/haeorum-ai-search/products-full.csv`를 export하고 그 CSV로 `/data/haeorum-ai-search/poc-products.csv`를 만든 뒤 나머지 이미지 URL, Marqo 색인, 품질, API, 부하, 보안 증거를 이어서 수집합니다. 이 선행 단계는 `mssql-export.json`과 `poc-dataset.json`도 함께 남기며, 이미 외부 배치가 두 CSV를 보장하는 운영이라면 `input_preparation.enabled=false`로 바꾸고 dry-run에서 해당 파일 존재 여부를 확인합니다. `HAEORUM_SYNC_ALERT_WEBHOOK_URL`이 운영 서비스 env 파일이나 collector `--env-file`에 실제 HTTPS URL로 설정되어 있으면 수집기는 sync alerting 입력을 충족한 것으로 봅니다. 이 웹훅을 쓰지 않고 외부 모니터링 알림으로 충족하는 경우에만 `security.sync_alerting_configured=true`로 바꿉니다. 수집기는 `replace-with...`, `<...>`, `...`, `sample`, `dummy`, `dev-key` placeholder 값을 누락 설정으로 처리하고, 운영 서비스 env 파일, `products_csv`, `poc_products_csv`, `quality_cases_file`, `load.image_file`, `load.image_files`, `mall_config_source`, `mall_config`, `representative_sites_config`, `api_scale` 입력 리포트, Nginx/systemd/logrotate 설정 파일과 sync alerting 확인이 준비됐는지도 dry-run에서 확인합니다. 존재하는 `products_csv`/`poc_products_csv`는 CSV 파싱, 300개 이상 active 상품, 카테고리 다양성, 중복 product_id, active 상품 이미지 URL, built-in sample 재사용 여부를 먼저 통과해야 합니다. 존재하는 `api_scale` 입력 리포트는 같은 운영 `base_url`/`origin`/`mall_id`, 850-user `mixed-traffic`, 실제 `image_input.source=file`/`files`, `mode_counts` 대비 `response_contract.query_type_counts`, Marqo-only `response_contract.engine_counts`, `server_metrics.after.snapshot.engine_backend=marqo` 조건을 먼저 통과해야 합니다. 운영 `base_url`과 `origin`은 HTTPS 공개 절대 URL만 허용하며 credentials, 공백, query string, fragment, non-public host, 잘못된 port가 있으면 dry-run과 운영 번들 검증에서 차단됩니다. `marqo.url`과 `marqo.gemini_embedding_url`은 host-reachable endpoint이므로 HTTP(S)를 허용하지만 credentials, query string, fragment, 공백, 역슬래시, 잘못된 port, link-local/unspecified host는 허용하지 않습니다. `origin`은 대표 쇼핑몰의 브라우저 origin이므로 path 없이 `https://shop001.haeorumgift.com` 형식으로 둡니다. 수집기 리포트의 `blocking_inputs`와 Markdown의 `Blocking Inputs` 표는 skipped 단계에 필요한 config/env/file, 해소 방법, 영향 단계를 함께 요약하므로, 운영자는 이 표를 먼저 비우면 됩니다. Mall export, CSV export, PoC CSV, 품질 케이스, 기준 이미지, 1/2대 API 부하 리포트처럼 다른 증거보다 먼저 만들어야 하는 입력 파일은 해소 문구에 생성 명령 예시가 포함됩니다. Markdown의 `Execution Runbook` 섹션은 같은 설정으로 dry-run, 실제 수집, readiness 재집계, 최종 audit를 다시 실행하는 명령을 남깁니다. `operational-evidence.config.json`의 `missing_commands.project_root`와 `missing_commands.evidence_dir`는 누락 증거 실행 스크립트, `operational-readiness.md`, `requirements-blockers.md`, `requirements-audit.md`, `evidence-collection-plan.md`의 redacted 명령/evidence 경로를 운영 서버 기준으로 고정합니다. blocker가 없는 dry-run은 `ready_to_execute=true`로 실행 준비만 나타내며, planned 단계는 실행 전 증거가 아니므로 `evidence_complete=false`이고 planned 단계의 `ok`는 성공으로 표시하지 않습니다. 수집기 리포트에는 secret 옵션이 마스킹된 명령만 남으며, 하위 명령 실패로 남는 `stdout_tail`/`stderr_tail`도 API key, admin key, connection string password, authorization token, URL credentials를 다시 마스킹합니다. 명령이 exit 0이어도 기대한 증거 JSON이나 선행 생성 CSV를 만들지 못하면 해당 단계는 실패합니다. 상대 `--evidence-dir`를 넘겨도 절대 경로로 정규화한 뒤 하위 증거 명령에 전달합니다.

`mssql_connection_string_env`가 가리키는 collector MSSQL connection string은 `Server`, `Database`, `Encrypt=yes`, `TrustServerCertificate=no`, `ApplicationIntent=ReadOnly` 조건을 dry-run에서도 통과해야 합니다.

API scale 입력 리포트 dry-run은 `response_contract.engine_counts`와 query type coverage에 더해 `response_shape`의 유효/무효 성공 응답 및 최소 결과 수를 확인합니다. 빈 관련 상품이나 카테고리 추천을 숨긴 오래된 리포트는 `invalid_input_files`로 차단됩니다.

존재하는 Nginx/systemd/logrotate 보안 파일은 `security_check.py` 실행 전 dry-run에서 한 번 더 검사합니다. Nginx는 업로드 body size, upstream failover/load balancing, keepalive, `X-Forwarded-For`/`X-Real-IP` 덮어쓰기와 표준 `Forwarded` 헤더 제거를 확인하고, systemd는 API/sync/reindex unit의 non-root 실행, restart/hardening, `LimitNOFILE=65535` 이상, log write path, nightly 03:00 timer를 확인하며, logrotate는 JSONL 로그 범위와 rotate/directive를 확인합니다.
`mssql_query`도 dry-run에서 단일 read-only `SELECT`/`WITH` 쿼리인지 먼저 확인합니다. 주석, 다중 statement, `INSERT`/`UPDATE`/`DELETE`/`MERGE`, DDL, `EXEC`, `SET` 같은 쓰기 또는 권한 변경 가능 키워드는 운영 수집 전에 차단됩니다.

```powershell
python scripts\collect_operational_evidence.py `
  --config /etc/haeorum-ai-search/operational-evidence.config.json `
  --env-file /etc/haeorum-ai-search/operational-evidence.env `
  --evidence-dir /var/log/haeorum-ai-search `
  --dry-run `
  --output /var/log/haeorum-ai-search/evidence-collection-plan.json `
  --markdown-output /var/log/haeorum-ai-search/evidence-collection-plan.md `
  --local-acceptance-report /var/log/haeorum-ai-search/local-acceptance.json `
  --requirements-audit-output /var/log/haeorum-ai-search/requirements-audit.json `
  --requirements-audit-markdown-output /var/log/haeorum-ai-search/requirements-audit.md `
  --requirements-blocker-checklist-output /var/log/haeorum-ai-search/requirements-blockers.md
```

dry-run 리포트의 `blocking_inputs`가 비고 `ready_to_execute=true`가 되면 `--dry-run`을 제거하고 실제 운영 증거를 수집합니다. 이 실행은 `operational-readiness.json`까지 생성하고, `--requirements-audit-output`이 있으면 최종 요구사항 감사도 같은 흐름에서 갱신합니다.

```powershell
python scripts\collect_operational_evidence.py `
  --config /etc/haeorum-ai-search/operational-evidence.config.json `
  --env-file /etc/haeorum-ai-search/operational-evidence.env `
  --evidence-dir /var/log/haeorum-ai-search `
  --output /var/log/haeorum-ai-search/evidence-collection.json `
  --markdown-output /var/log/haeorum-ai-search/evidence-collection.md `
  --local-acceptance-report /var/log/haeorum-ai-search/local-acceptance.json `
  --requirements-audit-output /var/log/haeorum-ai-search/requirements-audit.json `
  --requirements-audit-markdown-output /var/log/haeorum-ai-search/requirements-audit.md `
  --requirements-blocker-checklist-output /var/log/haeorum-ai-search/requirements-blockers.md
```

```powershell
python scripts\operational_readiness.py `
  --evidence-dir /var/log/haeorum-ai-search `
  --expected-malls 1700 `
  --required-sites 3 `
  --output /var/log/haeorum-ai-search/operational-readiness.json `
  --markdown-output /var/log/haeorum-ai-search/operational-readiness.md `
  --missing-commands-shell bash `
  --missing-commands-project-root /opt/haeorum-ai-search `
  --missing-commands-evidence-dir /var/log/haeorum-ai-search `
  --missing-commands-output /var/log/haeorum-ai-search/missing-evidence.sh
```

`--evidence-dir`는 `api-smoke.json`, `mssql-export.json`, `poc-dataset.json`, `mssql-view.json`, `image-url-check.json`, `quality-report.json`, `csv-index.json`, `mall-config-build.json`, `mall-config-check.json`, `marqo-resource.json`, `server-preflight.json`, `env-check.json`, `load-text.json`, `load-image.json`, `load-mixed.json`, `load-mixed-traffic.json`, `api-scale.json`, `representative-sites.json`, `security.json` 표준 운영 파일명을 자동으로 찾습니다. `widget-dom.json`은 local acceptance/handoff용 DOM 계약 증거이며 운영 readiness는 실제 대표 사이트 `representative-sites.json`으로 위젯 동작을 판정합니다. 리포트가 누락되면 readiness JSON/Markdown의 `command_hint`에 해당 증거를 생성하는 명령 예시가 같이 기록됩니다. API smoke 리포트는 검색 응답 `meta.engine=marqo`, `/admin/metrics`의 `engine_backend=marqo`, `/admin/sync-status`의 `sync_status_engine=marqo`와 non-empty `sync_status_index`를 포함해야 합니다. 부하 리포트는 `response_contract.engine_counts`와 `non_marqo_engine_responses=0`, `/admin/metrics` 기반 `server_metrics`의 `engine_backend=marqo`, cache backend/TTL/error count와 `cache_clear_errors` 스냅샷 및 delta를 포함해야 readiness에서 통과합니다. `data_lineage`는 `mall-config-build.json` validation과 `mall-config-check.json`의 enabled mall ID/origin/template/API key hash가 같은지, API smoke, load, API scale, 대표 사이트, security 증거가 같은 API base URL을 가리키는지 비교하고, 단일 가맹점 API smoke/load/API scale 증거는 같은 `origin`과 `mall_id`인지도 확인합니다. 또한 `security.json`의 `cors_origins`가 API smoke/load/API scale 및 대표 사이트 증거에 등장한 모든 origin을 포함하지 않으면 `security_cors_missing_api_origins`로 실패하고, 대표 사이트 `mall_id`가 `mall-config-check.json`의 `enabled_mall_ids`에 없으면 `representative_mall_id_not_enabled`, 대표 사이트 `origin`이 해당 mall의 `enabled_mall_origins`에 없으면 `representative_origin_not_allowed_for_mall`, 대표 사이트 `api_key_hash`가 해당 mall의 `enabled_mall_api_key_hashes`와 맞지 않으면 `representative_api_key_not_matching_mall_config`, 대표 사이트 실제 `product_url`이 해당 mall의 `enabled_mall_product_url_prefixes`와 맞지 않으면 `representative_product_url_not_matching_mall_template`로 실패합니다. `--requirements-audit-output`은 저장된 수집 리포트, `local-acceptance.json`, `operational-readiness.json`을 묶어 요구사항별 최종 판정을 같이 남깁니다. `--requirements-blocker-checklist-output`은 같은 감사 결과에서 운영 blocker만 별도 체크리스트 Markdown으로 뽑고, config의 `missing_commands` 값을 사용해 수집 명령과 evidence 경로를 `/opt`/`/var/log` 같은 배포 경로로 치환합니다. `--missing-commands-output`은 누락/실패 항목만 모은 체크리스트를 생성하며, `--missing-commands-shell bash` 또는 `powershell`을 선택할 수 있습니다. 체크리스트는 먼저 `--missing-commands-project-root`로 지정한 AI 검색 서버 코드 루트로 이동한 뒤 `scripts/...` 명령을 실행합니다. `--missing-commands-evidence-dir`는 체크리스트 내부의 `--output`/리다이렉션 경로를 지정하므로, Windows 로컬에서 Linux 운영 서버용 체크리스트를 만들 때도 운영 경로를 유지할 수 있습니다. 특정 파일명이 다르면 기존처럼 `--api-smoke-report`, `--mssql-export-report`, `--poc-dataset-report`, `--mall-config-build-report` 같은 개별 옵션으로 덮어씁니다.

`representative-sites.json`은 운영 템플릿에 삽입한 대표 1~3개 사이트의 PC/모바일 위젯 열기, 텍스트/이미지/혼합 검색, 가맹점별 product URL 규칙, 상세 이동, 클릭 로그 확인 결과를 `{"ok": true, "image_input": {"source": "file"}, "sites": [{"ok": true, "mall_id": "shop001"}]}` 형태로 남깁니다. readiness는 `image_input.source=file`과 실제 이미지 파일 메타데이터가 없으면 생성 이미지 기반 증거로 보고 실패 처리합니다.
`quality-report.json`은 `quality_ok`, `response_time_ok`, `dataset_ready`, `custom_cases`가 모두 true여야 합니다. 기본 응답시간 기준은 텍스트 최대 3000ms, 이미지/혼합 최대 5000ms이며, 리포트의 `response_time.by_query_type`에 모드별 count/avg/max/threshold가 남습니다. readiness는 기본 내장 케이스, skipped expected check, 실제 기준 이미지 파일(`image_source=case_file`)이 없는 이미지/혼합 케이스를 운영 품질 증거로 인정하지 않습니다. collector dry-run은 `quality.cases_file`의 케이스 계약과 참조 `image_path` 존재 여부를 먼저 확인해 텍스트/이미지/혼합/저신뢰/오타·동의어 케이스가 부족한 파일을 실행 전에 차단합니다.
`image-url-check.json`은 대표 이미지 URL 표본 점검 결과와 함께 `csv`, `source`, `checked`, `failed`, `warning_count`, `failure_category_counts`, `warning_type_counts`, `blocking_warning_count`, `blocking_warning_type_counts`, `attempts`, `concurrency`, `timeout_seconds`, `retry_count`, `max_mb`, `min_dimension`을 남깁니다. readiness는 이미지 서버 부하 방지를 위해 동시성 1~5개 범위, 최소 이미지 변 16px 이상, 최소 100개 표본 점검, placeholder/sample 대표 이미지 경고 0건만 통과로 인정하며, 번들 `sample_products.csv`나 샘플에서 복사된 CSV 기반 리포트는 운영 이미지 URL 증거로 인정하지 않습니다.
`mssql-view.json`은 `column_report.ok=true`, `missing_required_columns=[]`, 샘플 row 파싱, `updated_at` 품질과 함께 `permission_report`를 남깁니다. `permission_report.checked=true`, `permission_report.ok=true`여야 하며 `db_datawriter`/`db_owner` 같은 위험 role 또는 `UPDATE`/`INSERT`/`DELETE`/`ALTER`/`CREATE` 계열 권한이 발견되면 운영 readiness에서 실패합니다.
`mssql-export.json`은 실제 read-only MSSQL View에서 정규화된 전체 상품 CSV를 만든 증거입니다. readiness는 active 상품 300개 이상, parse error 0건, 번들 샘플 CSV가 아닌 출력 경로, 삭제/비노출 정리 신호인 `inactive_products > 0` 및 `source_deletion_signal_ok=true`를 확인합니다. 새 export 리포트에 `column_report.ok=false`가 있으면 필수 컬럼/별칭 문제로 실패합니다.
`poc-dataset.json`은 전체 상품 CSV에서 300개 이상 균형 잡힌 PoC CSV를 만든 증거입니다. readiness는 sample-derived/local-only 리포트, 권장 카테고리 누락/부족, 선택된 PoC CSV의 권장 카테고리 편향, 이미지 URL 누락, unsafe/non-HTTPS 이미지 URL, 중복 상품번호를 통과시키지 않습니다. 선행 `mssql-export.json`도 행 수 일관성을 검증하므로 `active_products + inactive_products`가 `exported_products`와 다르거나, `inactive_products=0`이라 삭제/비노출 상품 신호가 없거나, mall 설정 대조가 active 상품 전체를 검사하지 않은 리포트는 운영 증거로 인정되지 않습니다.
`csv-index.json`은 `csv_index.py --mode reindex --engine marqo --validate-images`처럼 Marqo 영구 검색 인덱스에 실제 반영하고 이미지 검증까지 수행한 결과여야 합니다. readiness는 dry-run, local engine, `engine`이 `marqo`가 아닌 예비/대체 어댑터 리포트를 통과시키지 않고, active 상품 300개 이상, `indexed == active_products`, 실패 0건, active 상품 이미지 누락/unsafe/non-HTTPS 0건, 중복 상품번호 0건, `validate_images=true`, `post_index_document_count_ok=true`를 확인합니다. 또한 색인 리포트에는 검증된 `marqo_url`과 `marqo_model`이 남아야 합니다. 데이터 계보 검사는 `quality-report.json`의 `source.marqo_url`/`source.index_name`/`source.marqo_model`, `csv-index.json`의 `marqo_url`/`index`/`marqo_model`, `marqo-resource.json`의 `marqo_url`/`index`, `api-smoke.json`의 `sync_status_index`가 같은지도 확인합니다. `marqo-resource.json`의 `numberOfDocuments`가 `csv-index.json`의 active/indexed 상품 수와 정확히 맞지 않으면 stale 문서 또는 다른 인덱스 증거로 보고 실패합니다. 같은 운영 인수 묶음 안에서 API smoke/load/API scale/대표 사이트/security가 서로 다른 API 서버 증거를 섞으면 `api_base_url_mismatch`, `api_origin_mismatch`, `api_mall_id_mismatch`로 최종 readiness가 실패합니다. `security.json`의 CORS origin 목록이 해당 API/대표 사이트 origin을 덮지 못하면 `security_cors_missing_api_origins`로 실패하고, 대표 사이트 `mall_id`/`origin`/`api_key_hash`/실제 `product_url`이 enabled mall config와 맞지 않으면 `representative_mall_id_not_enabled`, `representative_origin_not_allowed_for_mall`, `representative_api_key_not_matching_mall_config`, `representative_product_url_not_matching_mall_template`로 실패합니다.
`api-scale.json`은 `load_compare.py`가 생성한 API 서버 1대/2대 이상 비교 리포트입니다. readiness는 양쪽 모두 같은 HTTPS 비로컬 `base_url`, 같은 `mall_id`/`origin`, 같은 실제 이미지 파일 조건, 같은 `engine_index`/`marqo_model`/`embedding_backend`, 850 active user `mixed-traffic`, 요청 850건 이상, 동시성 100 이상, `response_contract.ok=true`, 성공 응답 `meta.engine=marqo`, `mode_counts` 대비 `response_contract.query_type_counts`/`expected_query_type_counts`, query type별 latency, `engine_backend=marqo`, `image_input.source=file`/`files`, admin metrics 포함 조건을 만족하고 같은 워크로드/런타임으로 비교됐는지 확인합니다. `load_compare.py`는 양쪽 load report의 response engine 분포와 query type coverage/latency, runtime identity, client transport keep-alive 재사용, server metrics delta의 검색/이미지 검색 이벤트 증가분, 검색 후보 재조회 지표, Gemini query vector/cache/cache clear/cache miss lock/singleflight/queue wait 카운터가 빠지거나, 검색 이벤트 증가분이 성공 응답 수보다 작거나, `/admin/metrics`의 engine backend가 Marqo가 아니면 scale 증거를 실패 처리합니다. 특히 `engine_underfilled_after_max_candidates_events`, `gemini_query_vector_wait_timeouts`, `cache_lock_errors`, `cache_lock_release_errors`, `cache_lock_wait_timeouts`, `singleflight_wait_timeouts` delta가 0보다 크면 후보 상한 부족, Gemini query vector coalescing, API 서버 간 Redis miss lock이나 프로세스 내부 duplicate-miss coalescing이 부하 중 정상 동작하지 않은 것으로 보고 실패합니다. 다만 `/admin/metrics`가 최근 tail 요약이라 포화된 경우에는 같은 실행의 `server_metrics.run_log_coverage`가 성공 응답 수 이상 검색/이미지 검색 이벤트를 증명하면 검색 이벤트 delta undercount를 보완 증거로 인정하며, readiness도 제출된 `api-scale.json`의 동일 필드를 다시 검증합니다. multi API 구성은 기본적으로 전체 p95와 p99가 1대 구성 대비 각각 25% 넘게 악화되지 않고 RPS가 80% 아래로 떨어지지 않아야 하며, text/image/text_image 중 한 경로만 부하 임계값의 p95 또는 p99를 넘는 리포트도 실패합니다.
multi API 리포트에는 `api_instance_coverage.ok=true`와 `server_metrics.admin_metrics_source_coverage.ok=true`가 포함되어야 합니다. readiness와 `load_compare.py`는 multi report에서 `X-Haeorum-API-Instance` header가 누락되거나 distinct 인스턴스 수가 `--api-server-count`보다 적거나 한 인스턴스가 성공 응답의 5% 미만만 처리하면 scale 증거를 실패 처리합니다. 또한 `/admin/metrics` 수집 source 수나 distinct `process.instance_id` 수가 `--api-server-count`보다 적으면 로드밸런서 뒤 한 인스턴스만 측정한 증거로 보고 실패합니다.
`api-scale.json`의 `response_shape`는 `response_contract.ok`가 true여도 별도 게이트입니다. readiness는 `min_top_count`, `min_item_count`, `min_category_count`가 모두 1 이상이고 invalid successful response가 0인지 재검증합니다.
`marqo-resource.json`은 Marqo root health, 인덱스 stats, `/indexes/{index}/settings`, Gemini `/health`, 실제 Gemini 텍스트+이미지 `/embed` 1건씩의 프로브, Docker `stats` 기반 컨테이너 `cpu_percent`, `memory_usage_bytes`, `memory_limit_bytes`, `memory_percent`, `resource_thresholds`, `docker exec <storage-container> df -B1 -P <storage-path>` 기반 `storage_usage`와 `storage_thresholds` 판정을 readiness 증거로 남깁니다. readiness는 인덱스 이름과 `numberOfDocuments` 300개 이상, `csv-index.json`의 active/indexed 상품 수와 정확히 같은지, Gemini/native 인덱스 설정과 Gemini health/텍스트+이미지 probe 모델·차원이 운영 설정과 맞는지, CPU/RAM/저장소 사용률이 지정 임계치 이하인지 확인하므로 빈 인덱스, PoC 색인 수보다 적거나 많은 stale 인덱스, 설정이 다른 인덱스, Gemini 불일치/미준비 상태, 자원 포화 상태, 저장소 포화 상태, Docker stats나 storage usage를 생략한 리포트는 통과하지 않습니다.
`widget-dom.json`은 local acceptance와 handoff에서 대표 위젯 설정이 기존 검색어 prefill과 재오픈 시 최신 검색어 반영, 트리거 툴팁, 팝업 제목/안내/업로드/로딩 문구, JPG/PNG/WEBP 업로드 제한 안내, Top 3/카테고리/관련 상품 섹션 제목, 이미지 업로드/드래그 앤 드롭, 정상 이미지 미리보기, 이미지 삭제 후 다음 검색 payload 정리, 용량 초과/최소 크기 미달/깨진 이미지 오류, 닫기 버튼/배경/Escape 닫기와 포커스 복귀, 키보드 포커스 순환, 검색 중 로딩 표시와 버튼 비활성화, 429 오류 시 stale 결과 정리, 반응형 결과 그리드, 유사도/상품번호/상품명/가격/상세 링크 표시, 상품 이미지 링크와 `상세 보기` 링크의 상세 URL 및 클릭 로그 payload를 확인한 로컬 DOM 계약 증거입니다. 운영 readiness는 local-only `widget-dom.json`을 필수 증거로 보지 않고, 대표 3개 운영 사이트의 실제 페이지/위젯/검색/클릭 로그를 담은 `representative-sites.json`으로 통과 여부를 판정합니다.
`server-preflight.json`은 운영 API/동기화 서버의 Linux 여부, 지원 Linux 배포판 기준, Python 3.11 이상, FastAPI/Uvicorn/Pydantic/Pillow/Redis/psutil/pyodbc 모듈, `ODBC Driver 18 for SQL Server`, Docker 24 이상, Docker Compose, CPU/RAM/디스크 여유 기준을 확인한 증거입니다. 지원 기준은 Ubuntu 20.04+, Debian 11+, RHEL/CentOS/Rocky/Alma/Oracle Linux 8+이며, 2016년대 서버처럼 오래된 배포판이면 신규 서버 또는 OS 업그레이드를 먼저 검토해야 합니다. readiness는 Docker/Compose/pyodbc와 SQL Server ODBC Driver 18 확인을 필수로 실행하고 `supported_linux_release.required=true`인 리포트만 통과시킵니다. 또한 `host_resources.requirements`와 실제 CPU/RAM/Disk 값을 다시 계산해 API 서버 기준 4 vCPU, RAM 8GB, 여유 디스크 20GB 미만이면 `host_resources.*` 문제로 실패합니다.
`security.json`은 HTTPS, CORS 제한, 전역 CORS HTTPS 및 공개 origin, 전역 CORS와 mall별 `allowed_origins` 일치, mall별 `allowed_origins` 공개 origin, 전역/mall 상품 URL 템플릿 HTTPS 및 공개 URL 안전성, 관리자 key, 가맹점 API key 존재/placeholder 여부와 key 강도, MSSQL IP 제한, production 환경, production 검색엔진, 1시간 이하 동기화 주기, Nginx `client_max_body_size`, Nginx upstream failover/keepalive, Nginx `X-Forwarded-For`/`X-Real-IP` 덮어쓰기 및 `Forwarded` 제거, systemd API 재시작 정책, sync worker 서비스, nightly reindex service/timer, logrotate 설정 확인 결과를 `{"ok": true, "https": true, "cors_restricted": true, "cors_origins_https": true, "cors_origins_safe_public": true, "cors_covers_allowed_origins": true, "allowed_origins": true, "allowed_origins_safe_public": true, "product_url_templates_https": true, "product_url_templates_safe_public": true, "admin_key": true, "mall_api_key": true, "mall_api_key_strength": true, "mssql_ip_restricted": true, "production_env": true, "production_search_engine": true, "sync_interval_hourly": true, "nginx_client_max_body_size": true, "nginx_upstream_resilience": true, "nginx_forwarded_for_safety": true, "systemd_restart_policy": true, "systemd_sync_worker": true, "systemd_reindex_service": true, "systemd_reindex_timer": true, "logrotate_config": true}` 형태로 남깁니다. readiness는 이 boolean만 보지 않고 `failed_checks=[]`, `malls_with_weak_api_keys=[]`, `public_base_url_report.ok=true`, `environment=production`, `engine_backend=marqo`, `sync_interval_seconds<=3600`, 비어 있지 않은 CORS/mall 수, sync alerting 상세 근거, Nginx/systemd/logrotate/service env 권한 하위 리포트의 `ok=true`와 `path`도 다시 확인합니다.

## 9. 장애 대응 기준

- API `GET /health` 실패: API 서버 또는 Marqo 연결을 확인합니다.
- 검색 결과 0건 급증: `GET /admin/metrics`의 `search.zero_result_events`, 동기화 상태, active/status 값, 인덱스 문서 수를 확인합니다.
- 운영 경고 확인: `GET /admin/metrics`의 `alerts`에서 `engine_unhealthy`, `sync_last_error`, `sync_product_failures`, `sync_batch_failures`, `sync_lock_contention`, `search_cache_invalidation_failures`, `redis_rate_limit_fallback`, `redis_rate_limit_backoff_active`, `search_cache_errors`, `search_cache_redis_backoff_active`, `api_errors_seen`, `rate_limited_requests`, `search_p95_high`, `disk_usage_high`, `system_memory_high` 항목을 확인합니다. 검색엔진 health 호출 자체가 실패하면 `engine.ok=false`, `engine.error_type`, `engine.error`로 원인을 남기고 `engine_unhealthy`를 발생시킵니다. Redis rate limit 저장소가 실패하면 `rate_limit.fallback_events`, `rate_limit.redis_backoff_*`, `rate_limit.last_error`와 Prometheus `haeorum_rate_limit_fallback_events`, `haeorum_rate_limit_redis_backoff_*`로 확인합니다. 검색 캐시 Redis가 실패하면 `cache.error_count`, `cache.redis_backoff_*`, `cache.last_error_operation`, `cache.last_error`와 Prometheus `haeorum_search_cache_error_events`, `haeorum_search_cache_redis_backoff_*`로 확인합니다. sync/reindex 후 캐시 무효화가 실패하면 `sync.events.cache_invalidation_failed_events`, `logs/sync.jsonl`의 `type=search_cache_clear_failed`, Prometheus `haeorum_sync_recent_search_cache_clear_failed_events`를 확인합니다. Prometheus는 같은 상태를 `GET /admin/metrics.prom`의 `haeorum_engine_up=0`, `haeorum_operational_alerts`, `haeorum_operational_alert{code=...,level=...}`로 수집할 수 있습니다.
- 관리자 로그/메트릭 조회 지연: `GET /admin/search-log`, `GET /admin/error-log`, `GET /admin/sync-log`, `GET /admin/metrics`는 JSONL 파일 끝에서 필요한 tail 범위만 읽습니다. 그래도 느리면 `limit`을 낮추고 logrotate 적용 여부와 로그 디스크 I/O를 확인합니다.
- 이미지 검색 지연 증가: `GET /admin/metrics`의 `search.image_search_events`, `search.p95_elapsed_ms`, `image_queue.max_wait_ms`, `image_queue.avg_wait_ms`, `search_queue.max_wait_ms`, `errors.rate_limited_events`, 검색 로그의 `cached`, `image_hash`, `image_perceptual_hash`, `image_size_bytes`, `image_normalized`, `image_quality_warnings`, IP별 `HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE`, mall별 `HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE`, `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`, 업로드 용량, Redis 공유 캐시, Marqo CPU/RAM을 확인합니다.
- 전체 검색/클릭 로그 429 증가: `GET /admin/metrics`의 `errors.rate_limited_events`, `rate_limit.fallback_events`, IP별 `HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE`/`HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE`, mall별 `HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE`/`HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE`, Redis 공유 여부, Redis 연결 상태, Nginx/LB IP가 `HAEORUM_TRUSTED_PROXY_IPS`에 포함되어 실제 사용자 IP로 bucket이 나뉘는지 확인합니다.
- 이미지 검색 품질 저하: `scripts/image_url_check.py`의 `warning_count`, `failure_category_counts`, `logs/sync.jsonl`의 `image_quality_warning`, 대표 이미지의 배경/워터마크/샘플 이미지 여부, unsafe redirect와 non-public DNS 해석 실패 여부를 확인합니다.
- 특정 가맹점 결과 오류: `sample_malls.json` 형태의 운영 mall 설정에서 `mall_id`, `api_key`, URL 템플릿, `excluded_product_ids`, `excluded_categories`, 가격 정책 필드를 확인합니다.
- 동기화 실패: `GET /admin/sync-status`, `GET /admin/sync-log`, `GET /admin/metrics`의 `sync.events`, `logs/sync.jsonl`, worker stdout/stderr를 확인합니다. source 조회 자체가 실패하면 `type=sync_batch_failed`, `action=fetch_products` 또는 단일 상품 `fetch_product`로 MSSQL 연결, read-only View 쿼리, CSV 경로, row 필수값 누락 여부를 추적합니다. lock 충돌은 `type=sync_batch_failed`, `action=acquire_sync_lock`, `sync.events.sync_lock_busy_events`, Prometheus `haeorum_sync_recent_lock_busy_events`로 확인합니다. 상품 단위 원인은 `type=sync_product_failed`, `action=upsert_to_index/delete_from_index`, `product_id`, `reason`으로 추적합니다. 삭제/비노출 정리와 단일 상품 재색인에서 source에 없는 stale 문서 삭제는 `type=sync_product_event`, `action=delete_from_index`, `reason=source_product_missing`에서 원인을 확인합니다. 상품 변경 후 캐시 무효화는 `type=search_cache_cleared`로 남고, 실패하면 `type=search_cache_clear_failed`와 `sync.events.cache_invalidation_failed_events`로 확인합니다. API 서버와 worker의 `HAEORUM_SYNC_LOG_PATH`가 같아야 상태 조회에 worker 결과가 반영되며, 별도 worker가 API 서버 Redis 캐시를 비우려면 `HAEORUM_REDIS_URL`/`HAEORUM_REDIS_KEY_PREFIX`도 같아야 합니다.
- API 오류 증가: `GET /admin/metrics`의 `errors.status_code_counts`와 `GET /admin/error-log` 또는 `logs/error.jsonl`의 `status_code`, `path`, `detail`을 확인합니다.
- 디스크 사용량 증가: `GET /admin/metrics`의 `disk.used_percent`와 `logs.*.bytes`를 확인하고 로그 로테이션 정책을 점검합니다.
- API 서버 CPU/RAM 증가: `GET /admin/metrics`의 `process.cpu_percent`, `process.memory_rss_bytes`, `system.memory_used_percent`를 확인하고 API 서버 수평 확장 또는 이미지 검색 rate limit 조정을 검토합니다.





