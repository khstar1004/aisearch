# 해오름기프트 AI 상품 검색 예제

상위 저장소의 `../../docs/plan.md`와 `../../docs/development_plan.md` MVP 범위를 기준으로 만든 참조 구현입니다. Marqo를 검색엔진으로 쓰되, `SearchEngine` 인터페이스를 통해 로컬 개발용 엔진과 교체 가능한 구조를 제공합니다.

현재 반입 기준 기본 구성은 **Marqo + Gemini embedding API**입니다. 로컬/운영 실행에 필요한 컨테이너는 `ai-search`, `gemini-embedding`, `marqo-api`, `mioc`, `vespa` 다섯 개입니다. 로컬 GPU 임베딩 컨테이너는 이 구성에서 띄우지 않습니다. 실행 상태 기준은 `deploy/runtime-stack-gemini-marqo.md`에 고정했습니다.

## 포함 기능

- `POST /api/ai-search` 통합 검색 API
- 텍스트, 이미지, 텍스트+이미지 혼합 검색 요청 모델
- 상위 유사 상품 3개, 관련 상품 리스트, 비슷한 카테고리 추천 응답
- 판매중/노출중 상품만 결과에 표시
- `mall_id`/`site_id` 기반 URL 생성과 가맹점 확장 파라미터
- 가맹점별 제외 상품/카테고리 정책 필터와 가격 표시/조정 정책
- Marqo 검색엔진 어댑터와 로컬 개발용 엔진
- CSV PoC 색인과 MSSQL read-only 동기화 소스
- 관리자 동기화 API와 검색/동기화/오류 로그
- 검색 결과 클릭 로그 API
- 검색/클릭 로그 기반 품질 인사이트 리포트
- 검색 캐시, IP/가맹점별 전체 검색/클릭 로그/이미지 검색 rate limit, API 프로세스별 이미지 검색 동시성 제한. `HAEORUM_REDIS_URL`을 설정하면 캐시와 rate limit을 API 서버 여러 대가 공유합니다.
- 업로드 이미지 포맷/손상/용량/최소 크기 검증, JPEG EXIF 방향 정규화, 대형 이미지 리사이즈, 비정상적으로 큰 디코딩 크기 차단. JSON `image_base64`와 `multipart/form-data` 업로드는 `Content-Length`를 먼저 확인하고, `Content-Length`가 없는 JSON 본문도 스트리밍 중 최대 크기를 넘으면 중단합니다. 공개 검색/클릭 로그 요청은 body 파싱 전에 header API key/Origin 후보 검증과 IP rate limit을 먼저 적용합니다. 이 header 후보 검증은 1,700개 mall 설정을 API key/origin 인덱스로 재사용해 요청마다 전체 mall 목록을 선형 탐색하지 않습니다. multipart 업로드는 `Content-Length`가 없으면 거절하고, multipart 파일 bytes는 공개 API 인증과 이미지 rate limit 통과 후 제한 크기까지만 chunk로 읽어 과대 파일이 메모리에 한 번에 올라가지 않게 차단합니다.
- 상품 대표 이미지 URL 검증 중 품질 경고 기록. 투명/컷아웃 배경, 낮은 대비/단색 배경, URL 기반 워터마크/샘플 이미지 힌트를 로그와 점검 리포트에 남깁니다.
- 기존 검색창 옆에 삽입 가능한 JS 위젯

## 로컬 실행

현재 예제는 FastAPI 의존성이 필요합니다.

```powershell
cd examples\HaeorumAISearch
python -m pip install -r requirements.txt
$env:HAEORUM_SEARCH_ENGINE="local"
$env:HAEORUM_ADMIN_API_KEY="dev-admin-key"
uvicorn app.main:app --reload --port 8000
```

로컬 엔진은 `sample_products.csv`를 시작 시 읽어 검색합니다. 브라우저에서 `widget/demo.html`을 열면 기존 검색창 옆 카메라 버튼 형태의 위젯을 확인할 수 있습니다.

Docker로도 로컬 데모 API를 실행할 수 있습니다.

```powershell
docker compose -f compose-haeorum-demo.yaml up --build
```

Docker 이미지에서 MSSQL source를 직접 쓰려면 `INSTALL_MSSQL_ODBC=true`와 `ACCEPT_MS_ODBC_EULA=true` build arg를 함께 지정합니다. 이 경로는 Microsoft Debian package repo를 등록해 `msodbcsql18`, unixODBC, Python `pyodbc`를 설치합니다. 빌드 후에는 `server_preflight_check.py --require-pyodbc --expected-odbc-driver "ODBC Driver 18 for SQL Server"`로 Driver 18 등록 여부를 확인합니다.

```powershell
docker build --build-arg INSTALL_MSSQL_ODBC=true --build-arg ACCEPT_MS_ODBC_EULA=true -t haeorum-ai-search:mssql .
```

Marqo까지 포함한 통합 데모는 별도 compose 파일을 사용합니다.

```powershell
docker compose -f compose-haeorum-marqo.yaml up --build -d
docker compose -f compose-haeorum-marqo.yaml --profile reindex up --build reindex-once
python scripts\api_smoke_test.py --base-url http://localhost:8000 --mall-id shop001 --api-key public-shop001-dev-key --origin https://shop001.haeorumgift.com --admin-key dev-admin-key --allow-local-target
```

위 `public-shop001-dev-key`와 `dev-admin-key`는 compose 로컬 데모 전용 값입니다. 운영 증거 수집과 배포 설정에서는 실제 key로 교체해야 하며, `dev-key` 계열 값은 preflight에서 실패합니다.

운영 배포 순서와 인수 게이트는 `OPERATIONS.md`를 참고하세요. 계획 문서 요구사항별 구현/미검증 상태는 `REQUIREMENTS_TRACE.md`에 정리했습니다.
서버/DB 정보를 받기 전 기존 개발자에게 보낼 문구는 `deploy/server-db-request.ko.md`, 최종 입력 요청서는 `deploy/server-db-intake.md`, 운영 장애 시나리오 표는 `deploy/go-live-failure-scenarios.md`, 장애 대응 런북은 `deploy/production-incident-runbook.md`, 운영 리스크 체크리스트는 `deploy/operational-risk-register.md`, 실제 반입 직전 자동 점검은 `scripts/go_live_scenario_check.py`와 `scripts/pre_handoff_audit.py`를 사용합니다. 이 점검은 Gemini 기본 compose, 로컬 GPU 임베딩 컨테이너 미실행, Nginx/secret 기본 보안값, `/health`, `/admin/metrics`, 정상 UTF-8 한글 검색, 깨진 한글 검색어 거절, 실제 `--mall-id` 대표 검색, 데모 페이지를 한 번에 확인합니다.

기존 쇼핑몰 템플릿 삽입 절차는 `INTEGRATION.md`를 참고하세요. 연동팀에 공유할 고정 API 계약은 `contracts/openapi.json`, 위젯 삽입 예시는 `contracts/widget_init.example.html`에 있습니다.
리눅스 서버 직접 배포용 systemd/Nginx/logrotate/env 템플릿은 `deploy/` 아래에 있습니다. Nginx 템플릿은 `upstream haeorum_ai_search_api` 기반이라 API 서버를 2대 이상으로 늘릴 때 upstream `server` 항목을 추가해 수평 확장할 수 있습니다.

## 운영 전 가상 데이터 시뮬레이션

실제 MSSQL, 운영 Marqo, 1,700개 가맹점 설정을 받기 전에는 아래 명령으로 가상 운영 데이터셋을 만들어 리스크를 먼저 점검할 수 있습니다.

```powershell
python scripts\operational_simulation.py
```

기본 실행은 `logs/simulation/` 아래에 1,800개 상품 CSV, 300개 PoC CSV, 1,700개 가맹점 설정, 가맹점 export CSV에서 `malls.json`을 재생성하는 `mall-config-build.json`, production 형태 env 파일, 품질 케이스 이미지, 로컬 검색 부하 프로브 리포트, `search-insights.json/md` 품질 튜닝 리허설 리포트를 생성합니다. `mssql-alias-compatibility.json`은 `goods_no`/`shop_code` 같은 레거시 영문 컬럼과 `상품번호`/`가맹점ID` 같은 한글 export 컬럼이 같은 상품 파서와 View 샘플 검증을 통과하는지 비교합니다. 로컬 검색 부하 프로브는 일부 정상 text/mixed 검색의 top 결과 클릭 로그도 함께 남겨 `search-insights`의 click attribution, CTR, top-clicked product 분석이 빈 리포트로 끝나지 않는지 확인합니다. `sync-lifecycle.json`은 변경분 동기화, `updated_at` cutoff와 정확히 같은 행의 포함, 숨김 상품 색인 제거, 원본에서 사라진 상품의 단건 재색인 삭제, 전체/단건 재색인의 중복 상품번호 fail-closed 처리, 검색 캐시 무효화 로그, sync lock 충돌 로그, stale sync lock 자동 회수를 로컬 엔진으로 검증합니다. 또한 `operational-risk-probes.json`에는 너무 긴 상품번호, 중복 상품번호, 누락/오류/미래 `updated_at`, active-only export의 삭제/비노출 신호 누락, stale Marqo 문서 수, 잘못된 가격 범위, unsafe 이미지/상품 URL, active 상품 이미지 누락, placeholder/sample 대표 이미지 경고, invalid mall ID, wildcard/HTTP origin, 중복 또는 샘플 API key, 중복 allowed origin, 중복 상품 URL prefix, unsafe 상품 URL template, 쓰기 권한 DB 계정, unsafe API/widget/page URL, CSP 외부 widget 차단, 검색창 미검출, 대표 사이트 중복 mall/url/origin/API key 설정, 대표 사이트 저장 PC/mobile HTML 중복 캡처, 부하/API scale 클라이언트 keep-alive 재사용 누락, backend active request slot 포화 시 fast-fail 503과 circuit breaker 분리, Redis cache miss lock 오류와 follower wait timeout, singleflight wait timeout, 대표 사이트 관련 상품 wrong-mall URL, 대표 사이트 응답 `mall_id` 불일치가 각 검증기에서 차단 또는 리스크 표시되는지 확인하는 negative control 결과가 들어갑니다. 모든 산출물에는 `SIMULATED_ONLY_NOT_OPERATIONAL_EVIDENCE` 마커가 들어가며, `operational_readiness.py`는 이 마커가 있는 파일을 운영 인수 증거로 거절합니다. 따라서 이 경로는 개발자 부재 상황에서 사전 결함을 찾기 위한 보조 수단이고, 최종 납품 판정은 실제 운영 증거로 다시 받아야 합니다.

1,700개 가맹점 origin은 단일 환경변수 길이 제한에 걸릴 수 있으므로 운영 env에서는 `HAEORUM_CORS_ORIGINS_FILE=/etc/haeorum-ai-search/cors-origins.txt`처럼 파일 경로를 쓰는 구성을 권장합니다. 파일은 origin을 한 줄에 하나씩 두거나 comma-separated 형식으로 둘 수 있습니다.

## Marqo + Gemini 연동

운영 후보는 Marqo 검색엔진 + Gemini embedding API 프록시입니다. 상품은 미리 Gemini로 벡터화해 Marqo에 저장하고, 검색 시에는 사용자의 검색어/업로드 이미지만 Gemini로 임베딩한 뒤 Marqo에서 벡터 검색합니다.

```powershell
$env:HAEORUM_SEARCH_ENGINE="marqo"
$env:MARQO_URL="http://localhost:8882"
$env:HAEORUM_EMBEDDING_BACKEND="gemini"
$env:HAEORUM_GEMINI_EMBEDDING_URL="http://localhost:8098"
$env:HAEORUM_GEMINI_EMBEDDING_DIMENSIONS="1536"
$env:HAEORUM_GEMINI_MODEL="gemini-embedding-2"
$env:HAEORUM_INDEX_NAME="haeorum-products"
uvicorn app.main:app --port 8000
```

`MARQO_URL`은 API 서버에서 접근하는 내부 Marqo endpoint입니다. 개발·단일 호스트 구성의 `http://localhost:8882`는 허용하지만, 값은 절대 HTTP(S) URL이어야 하며 credentials, query string, fragment, 공백, 역슬래시, 잘못된 port, link-local/unspecified host는 설정 로딩과 env preflight에서 거절됩니다.
운영 env preflight와 production 런타임 설정 로딩은 기본값에 의존하지 않도록 `HAEORUM_SEARCH_ENGINE`, `MARQO_URL`, `HAEORUM_MARQO_MODEL`, `HAEORUM_INDEX_NAME`, `HAEORUM_MALL_CONFIG_PATH`, MSSQL/CSV 데이터 소스, `HAEORUM_EMBEDDING_BACKEND=gemini`, `HAEORUM_GEMINI_EMBEDDING_URL`, `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY` 또는 `GEMINI_PROXY_API_KEY`, `HAEORUM_GEMINI_EMBEDDING_DIMENSIONS`, `HAEORUM_GEMINI_MODEL`이 env 파일에 명시됐는지도 확인합니다. `...`, `<...>`, `replace-with...`, `change-me` 같은 예시/placeholder 값도 production 기동에서 거절됩니다. `HAEORUM_MALL_CONFIG_PATH`가 번들 `sample_malls.json`을 가리키는 것도 production 기동에서 거절됩니다. CSV 데이터 소스로 운영할 때는 `HAEORUM_PRODUCT_CSV`가 존재해야 하며 번들 `sample_products.csv`는 production 기동에서 거절됩니다.

초기 색인은 관리자 API로 실행합니다.

```powershell
curl -X POST http://localhost:8000/admin/reindex -H "X-Admin-Key: dev-admin-key"
```

`/admin/reindex` 또는 `/admin/sync`가 처음 실행될 때 Marqo 인덱스가 없으면 예제 서비스가 인덱스를 생성합니다. 인덱스 settings 조회가 404를 반환할 때만 자동 생성하며, 5xx/timeout/연결 오류는 backend 장애로 보고 색인을 중단합니다. 운영 기본값은 `gemini-embedding-2` split-vector 구조입니다. `HAEORUM_EMBEDDING_BACKEND=native`로 바꾸면 기존 Marqo 네이티브 모델 경로를 사용할 수 있습니다.

Marqo 어댑터는 상품 문서를 다음 필드로 저장합니다.

- Gemini 벡터 필드: API 응답, 관리자 화면, Prometheus, 로드테스트 리포트는 `gemini_text_vector`, `gemini_image_vector` 기준으로 표시합니다. 기존 개발 중 생성된 인덱스와의 호환 때문에 Marqo 내부 설정에 레거시 필드명이 남은 경우가 있어도, 운영 런타임의 provider와 호출 경로는 Gemini입니다.
- 검색/보조 필드: `product_name`, `category_name`, `description`, `keywords`, `print_methods`, `materials`, `colors`, `main_image_url`
- 필터/응답 필드: `product_id`, `price`, `price_min`, `price_max`, `min_order_qty`, `delivery_days`, `product_group_id`, `status`, `display_yn`, `mall_id`, `product_url`, `updated_at`

색인 payload의 `main_image_url`은 안전한 절대 HTTP(S) URL만 남기고, `product_url`은 안전한 절대 HTTP(S) URL 또는 `/product_view.asp?...` 같은 루트 상대경로만 남깁니다. `javascript:`, credential 포함 URL, protocol-relative URL처럼 운영 응답에서 쓸 수 없는 값은 Marqo 문서에도 저장하지 않습니다.

Gemini split-vector 모드에서는 텍스트 검색도 먼저 이미지 벡터에 라우팅해 `노란부채`처럼 이미지에 보이는 상품을 찾습니다. 같은 텍스트 query vector로 `gemini_text_vector`를 보조 검색하고, 기본 12%만 혼합 점수에 반영해 이미지 벡터가 주도하되 텍스트와 크게 어긋난 후보는 내려갑니다. 이미지-only 검색은 이미지 벡터만 사용하고, 텍스트+이미지 혼합 검색은 텍스트 query vector와 이미지 query vector를 모두 만들고 `HAEORUM_MIXED_TEXT_WEIGHT`/`HAEORUM_MIXED_IMAGE_WEIGHT`로 정규화한 weighted `context.tensor`를 이미지 벡터에 검색한 뒤 텍스트 벡터를 보조 신호로 반영합니다. `HAEORUM_EMBEDDING_BACKEND`, `HAEORUM_GEMINI_EMBEDDING_DIMENSIONS`, `HAEORUM_MARQO_MODEL`을 바꾸면 기존 인덱스와 호환되지 않으므로 인덱스를 삭제하거나 새 `HAEORUM_INDEX_NAME`으로 `POST /admin/reindex`를 실행합니다.
검색 API는 `app/query_normalizer.py`의 공통 오타/띄어쓰기 보정으로 `텐블러 -> 텀블러`, `포스트 잇 -> 포스트잇`, `유에스비 -> usb` 같은 표현을 먼저 정규화합니다. `app/category_intent.py`는 정규화 검색어에서 `수건 -> 타올`, `포스트잇 -> 점착메모지` 같은 카테고리 의도를 추론해 검색 점수에 반영합니다. 검색엔진에는 정규화 검색어와 원문을 함께 넘기고, 검색 로그에는 원문 `q`, `normalized_query`, `inferred_categories`를 모두 남깁니다. 이미지/혼합 검색 로그에는 `image_hash`, `image_perceptual_hash`, `image_width`, `image_height`, `image_size_bytes`, `image_normalized`, `image_quality_warnings`도 남겨 반복 이미지, 리사이즈 여부, 저품질/투명 배경 같은 업로드 품질 이슈를 추적합니다.
로컬 개발 엔진은 동의어 확장과 한 글자 오타 허용을 포함해 PoC 스모크를 재현합니다. 운영 검색 품질은 Marqo 모델과 실제 상품 데이터로 별도 품질 리포트를 확인해야 합니다.

JCLGift 실제 상품 CSV를 Gemini split-vector 인덱스와 위젯 데모에서 확인할 때는 Gemini compose override를 사용합니다.

```bash
export GEMINI_AUTH_MODE=adc
export GEMINI_QUOTA_PROJECT=<google-cloud-project-id>
export GOOGLE_APPLICATION_CREDENTIALS_HOST=/root/.config/gcloud/application_default_credentials.json
export HAEORUM_EMBEDDING_BACKEND=gemini
export HAEORUM_GEMINI_EMBEDDING_DIMENSIONS=1536
export HAEORUM_GEMINI_MODEL=gemini-embedding-2
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml -f compose-haeorum-marqo-gemini-localtest.yaml up --build -d
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml -f compose-haeorum-marqo-gemini-localtest.yaml --profile reindex run --rm reindex-once
```

데모 페이지는 `marqo_gemini_exact_demo.html`이며 관리자 화면은 `/admin-ui`입니다.

검색엔진 교체 지점은 `app/engine_factory.py`입니다.

- `HAEORUM_SEARCH_ENGINE=marqo`: 현재 운영 후보입니다.
- `HAEORUM_SEARCH_ENGINE=local`: 로컬 개발/테스트용입니다.
- `HAEORUM_SEARCH_ENGINE=typesense`: 예비 어댑터 자리입니다. `health()`는 `reserved_adapter=true`, `ready=false`를 반환하고 검색/색인 호출은 `ReservedSearchEngineUnavailable`로 실패합니다.
- `HAEORUM_SEARCH_ENGINE=qdrant`: Qdrant/OpenCLIP 전환용 예비 어댑터 자리입니다. `health()`는 필요한 구성요소를 반환하고 검색/색인 호출은 명시적으로 실패합니다.

`HAEORUM_ENV=production`에서는 검색 요청이 실제로 처리되는 `marqo`만 허용합니다. `local`, `typesense`, `qdrant`는 운영 기동 전에 설정 검증에서 거절되고, `app/engine_factory.py`도 production 런타임에서 직접 엔진을 만들지 못하게 막습니다. `scripts/env_check.py`도 배포 가능한 검색엔진과 예약된 예비 어댑터를 구분해, `typesense`/`qdrant`가 실수로 배포 승인 증거가 되지 않게 막습니다.

### Gemini embedding API proxy

Google Gemini embedding API를 쓰려면 API 서버가 직접 Google을 호출하지 않고, 내부 `embedding-service` 프록시의 `/health`, `/embed` 계약을 호출합니다. 운영에서는 `HAEORUM_EMBEDDING_BACKEND=gemini`와 `HAEORUM_GEMINI_*` 설정을 사용합니다. 새 배포 env에는 Gemini 이름만 채웁니다.
`GEMINI_PROXY_API_KEY`는 `embedding-service`의 `/embed` 내부 호출을 막는 shared secret입니다. Compose는 같은 값을 API 컨테이너의 `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`로 전달하므로, 운영 env에서 두 값이 서로 다르면 검색/색인이 401로 실패합니다.

```powershell
$env:GEMINI_API_KEY="..."
$env:GEMINI_EMBEDDING_MODEL="gemini-embedding-2"
$env:GEMINI_EMBEDDING_DIMENSIONS="1536"
python -m uvicorn app.gemini_embedding_proxy:app --host 127.0.0.1 --port 8098
```

운영이나 유료 프로젝트 quota를 쓰는 테스트에서는 API key 대신 ADC를 씁니다. Google Cloud project에 billing을 연결하고 Generative Language API 또는 Vertex AI API를 활성화한 뒤, 아래처럼 ADC를 구성합니다.

```bash
gcloud auth application-default login \
  --scopes='https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/generative-language.retriever'
export GEMINI_AUTH_MODE=adc
export GEMINI_QUOTA_PROJECT='<google-cloud-project-id>'
export GEMINI_EMBEDDING_MODEL=gemini-embedding-2
export GEMINI_EMBEDDING_DIMENSIONS=1536
python -m uvicorn app.gemini_embedding_proxy:app --host 127.0.0.1 --port 8098
```

검색 API 쪽은 아래처럼 맞춥니다. 모델/차원을 바꾸면 기존 벡터 인덱스와 호환되지 않으므로 새 인덱스를 쓰거나 재색인합니다.

```powershell
$env:HAEORUM_EMBEDDING_BACKEND="gemini"
$env:HAEORUM_GEMINI_EMBEDDING_URL="http://127.0.0.1:8098"
$env:HAEORUM_GEMINI_EMBEDDING_DIMENSIONS="1536"
$env:HAEORUM_GEMINI_MODEL="gemini-embedding-2"
```

임베딩 API 단독 지연시간은 아래 명령으로 확인합니다.

```powershell
python scripts/gemini_embedding_benchmark.py --repeat 3 --texts "검은 우산" "스텐 텀블러" "고급 볼펜"
```

Docker로 Marqo stack과 Gemini 프록시를 함께 띄우려면 override compose를 함께 사용합니다. 운영 API key 방식은 `compose-haeorum-gemini.yaml`만 추가하고, ADC 로컬 테스트는 `compose-haeorum-marqo-gemini-localtest.yaml`까지 추가합니다.

```bash
export GEMINI_AUTH_MODE=api_key
export GEMINI_API_KEY='<protected-gemini-api-key>'
export HAEORUM_EMBEDDING_BACKEND=gemini
export HAEORUM_GEMINI_EMBEDDING_DIMENSIONS=1536
export HAEORUM_GEMINI_MODEL=gemini-embedding-2
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml up --build -d
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml --profile reindex run --rm reindex-once
```

ADC 로컬 테스트는 아래처럼 별도 override를 추가합니다.

```bash
export GEMINI_AUTH_MODE=adc
export GEMINI_QUOTA_PROJECT='<google-cloud-project-id>'
export GOOGLE_APPLICATION_CREDENTIALS_HOST=/root/.config/gcloud/application_default_credentials.json
docker compose -f compose-haeorum-marqo.yaml -f compose-haeorum-gemini.yaml -f compose-haeorum-marqo-gemini-localtest.yaml up -d
```

기존 JCL 1,795개 데이터로 Gemini 품질 스모크와 체험용 미니 검색 UI를 만들 수 있습니다.

```powershell
python scripts/gemini_vector_quality_smoke.py --mode focused
python scripts/gemini_vector_demo.py --index logs/gemini-focused-vector-index.json --port 8099
```

혼합 검색은 native Marqo 경로에서는 Marqo weighted query 형식을, Gemini split-vector 경로에서는 같은 가중치를 `context.tensor`의 텍스트/이미지 vector entry로 사용합니다.
Marqo 원점수가 1을 넘는 경우 API는 사용자 표시용 `score`/`score_percent`를 0~1/0~100 범위로 매핑하고, 원본 점수는 `source_scores`에 남깁니다.

```json
{
  "q": {
    "data:image/png;base64,...": 0.6,
    "검은색": 0.4
  },
  "searchMethod": "TENSOR"
}
```

## API 예시

텍스트 검색:

```json
{
  "mall_id": "shop001",
  "q": "검은 우산",
  "limit": 20
}
```

이미지 검색:

```json
{
  "mall_id": "shop001",
  "image_base64": "data:image/png;base64,...",
  "limit": 20
}
```

혼합 검색:

```json
{
  "mall_id": "shop001",
  "q": "검은색",
  "image_base64": "data:image/png;base64,...",
  "limit": 20
}
```

응답은 `top`, `items`, `suggested_categories`, `meta`로 구성됩니다. `top`은 상위 3개, `items`는 나머지 관련 상품 리스트입니다.
요청의 `limit`은 `items` 관련 상품 리스트의 최대 개수로 적용됩니다. 따라서 `limit: 20`이면 상위 3개와 별도로 관련 상품을 최대 20개까지 받을 수 있습니다.
`offset`은 `top` 3개를 제외한 관련 상품 리스트 기준 시작 위치입니다. 응답의 `meta.has_more=true`이면 `meta.next_offset`으로 다음 related items 페이지를 요청할 수 있습니다.
검색 API와 클릭 로그 API는 가맹점 식별값으로 `mall_id`와 `site_id`를 모두 받을 수 있습니다. 내부 응답과 로그에서는 `mall_id`로 정규화합니다. 두 값을 함께 보내는 경우 공백 제거 후 값이 같아야 하며, 다르면 400으로 거절합니다.
입력 문자열은 앞뒤 공백을 제거한 뒤 검증합니다. `mall_id`/`site_id`는 최대 64자의 영문/숫자/하이픈 식별자이며 양끝은 영문 또는 숫자여야 합니다. 검색어 `q`와 클릭 `query`는 최대 200자, `category`는 최대 100자, 클릭 `product_id`는 최대 100자, 클릭 `product_url`은 최대 1000자입니다. 클릭 로그의 `product_url`은 운영 로그에 남는 값이므로 절대 HTTP(S) URL만 허용하고 상대 URL, `javascript:` URL, URL 사용자 정보, 잘못된 포트, 제어문자/공백이 섞인 URL은 400으로 거절합니다.
검색 요청은 `category`, `print_method`, `material`, `color`, `min_price`, `max_price`, `quantity`/`order_qty`, `max_delivery_days` 필터를 받을 수 있습니다. `quantity`는 사용자가 원하는 주문 수량이며 상품의 `min_order_qty`가 이 값 이하인 상품만 남깁니다. `print_method`, `material`, `color`는 동의어 확장을 포함한 부분 일치로 처리되고, Marqo 어댑터는 필터 적용에 필요한 후보를 더 넓게 가져온 뒤 서버에서 최종 필터링합니다.
상품 row에 `product_group_id`가 있으면 같은 상품군은 검색 결과에서 최고 순위 대표 1개만 먼저 노출해 색상/용량/공급사 중복이 Top 3과 관련 상품 리스트를 채우지 않게 합니다.
중복 상품군 collapse 전에 API는 `top 3 + offset + limit + has_more 판정`에 2배 후보 여유를 두고 Marqo/local 엔진에 요청합니다. collapse 이후에도 필요한 개수가 부족하고 검색엔진이 후보 한도만큼 결과를 돌려준 경우에는 Marqo 후보 상한 안에서 후보 수를 단계적으로 늘려 한 번 더 조회합니다. 그래서 실제 응답 개수보다 엔진 후보 수가 더 클 수 있으며, 이는 옵션 상품이 앞순위를 차지해도 관련 상품 페이지가 비지 않게 하기 위한 의도된 동작입니다.
검색 로그와 `/admin/metrics`에는 `engine_search_attempts`, `engine_adaptive_refetches`, `engine_candidate_limits`, `engine_underfilled_after_max_candidates`가 남습니다. 부하 중 `engine_adaptive_refetches`가 많이 늘면 특정 키워드/상품군에서 옵션 중복이 backend 호출 수를 키우는 신호이고, `engine_underfilled_after_max_candidates`가 1 이상이면 Marqo 후보 상한까지 넓혀도 관련 상품 페이지를 채울 상품 다양성이 부족한 상태입니다. 운영 readiness는 load/API scale 리포트의 `server_metrics.delta.engine_search_attempts`, `engine_adaptive_refetches`, `engine_adaptive_refetch_searches`, `engine_underfilled_after_max_candidates_events`와 after snapshot의 평균/최대 후보 지표가 없으면 검색 후보 보강 관측성이 빠진 증거로 보고 실패합니다.
공개 API에서 API key가 틀리면 401, 가맹점 ID 또는 Origin이 허용되지 않으면 403을 반환합니다. 이 응답은 `contracts/openapi.json`에도 명시되어 있습니다.
공개 `/api/ai-search`와 `/api/click-log` 라우트는 rate limit 저장소 조회, 동기 검색 엔진 호출, 캐시 조회, 클릭 로그 기록을 FastAPI threadpool에서 실행해 느린 Redis/검색/로그 I/O가 이벤트 루프를 막지 않게 합니다.

## 관리자 API

모든 관리자 API는 `X-Admin-Key` 또는 `Authorization: Bearer ...` 인증이 필요합니다. 운영 API smoke는 실제 변경 작업을 실행하지 않고, 잘못된 관리자 key로 동기화/재색인/삭제 엔드포인트가 모두 401로 차단되는지 확인합니다.

- `POST /admin/sync`
- `POST /admin/reindex`
- `POST /admin/reindex/{product_id}`
- `POST /admin/reindex-product`
- `DELETE /admin/product/{product_id}`
- `POST /admin/delete-product`
- `GET /admin/sync-status`
- `GET /admin/search-log`
- `GET /admin/sync-log`
- `GET /admin/search-insights`
- `GET /admin/error-log`
- `GET /admin/metrics`
- `GET /admin/metrics.prom`
- `GET /health`

단일 상품 재색인/삭제의 `product_id` 경로는 `/`를 포함한 상품번호도 받을 수 있습니다. 예를 들어 상품번호가 `P/001`이면 `POST /admin/reindex/P/001?mall_id=shop001`, `DELETE /admin/product/P/001?mall_id=shop001`처럼 호출합니다. 공백, `?`, `#` 등 URL 예약 문자가 들어간 상품번호는 클라이언트에서 URL encode한 값을 사용합니다. 운영 프록시나 호출 도구가 예약 문자를 path에서 다르게 처리하면 JSON body 기반 `POST /admin/reindex-product`, `POST /admin/delete-product`를 사용합니다. 1,700개 가맹점처럼 다중 몰 운영이거나 `HAEORUM_FILTER_BY_MALL_ID=true`이면 상품번호만으로는 대상 문서가 모호하므로 `mall_id`가 없을 때 단건 재색인/삭제가 실패합니다.

```powershell
curl -X POST http://localhost:8000/admin/reindex-product -H "X-Admin-Key: dev-admin-key" -H "Content-Type: application/json" -d "{\"mall_id\":\"shop001\",\"product_id\":\"P/001?color=black#main\"}"
curl -X POST http://localhost:8000/admin/delete-product -H "X-Admin-Key: dev-admin-key" -H "Content-Type: application/json" -d "{\"mall_id\":\"shop001\",\"product_id\":\"P/001?color=black#main\"}"
```

사용자 검색 결과 클릭은 위젯에서 `POST /api/click-log`로 전송되며, 검색 로그와 같은 JSONL 파일에 `type: "click"`으로 기록됩니다. 가맹점 공개 API key가 설정된 경우 위젯은 클릭 로그도 `X-API-Key` 헤더와 `fetch(..., keepalive: true)`로 보내 URL query string에 key가 남지 않게 합니다. 서버는 `api_key`, `apiKey`, `apikey`, `api-key`, `x-api-key`와 관리자 key 별칭이 query string에 빈 값으로 존재하는 경우도 거절하며, JSON/multipart body의 같은 필드도 지원하지 않습니다.
API 요청 검증 실패, 인증 실패, rate limit, 서버 예외는 `HAEORUM_ERROR_LOG_PATH`의 JSONL 파일에 `type: "api_error"`로 기록되고 `/admin/error-log`에서 조회할 수 있습니다.
로그 기록 전 이메일, 전화번호, 주민등록번호 형태의 흔한 개인정보 패턴과 비밀번호/API 키/토큰 형태의 민감값은 마스킹하고 과도하게 긴 문자열, 큰 배열/객체, 깊은 중첩은 잘라 저장합니다.
관리자 로그/메트릭 조회의 `limit`은 1~1000 범위로 제한되며, JSONL tail 조회는 전체 파일을 한 번에 리스트로 만들지 않고 최근 행만 유지합니다. `scripts/load_test.py`는 `/admin/metrics` 전후 delta가 tail 포화로 부족하게 보일 때를 구분할 수 있도록, 부하 시작 기준 시각 이후의 `/admin/search-log` tail도 `server_metrics.run_log_coverage` 증거로 남깁니다.
배포 후 검색 품질 튜닝은 검색/클릭 JSONL을 `scripts/search_insights.py`로 집계합니다. 검색 로그에는 원문 `q`, `normalized_query`, `inferred_categories`, 이미지 검색의 `image_hash`/`image_perceptual_hash`와 크기/정규화/품질 경고 필드, 검색 당시 normalized `text_weight`/`image_weight`, 상위 결과의 `top_source_scores`, 응답 `elapsed_ms`, cache hit 여부가 남습니다. 이 리포트는 전체 클릭률, 무결과 쿼리, 낮은 유사도 쿼리, 클릭 없는 반복 쿼리, query type/cache 상태별 latency, 느린 검색 샘플, 많이 클릭된 상품, 이미지 품질 경고 빈도, 혼합 검색 가중치 조합별 성과와 `recommendations` 액션 후보를 뽑아 검색어 동의어, 카테고리 매핑, 상품 이미지/설명 보강 후보와 성능 병목 후보를 정리합니다. 또한 `synonym_seed_candidates`는 `query-synonyms.json`에 검토 후 병합할 후보를, `quality_case_candidates`는 `quality-cases.json`에 추가할 회귀 케이스 초안을, `mixed_weight_recommendation`은 품질 케이스로 재검증해야 할 혼합 검색 가중치 A/B 후보를 제공합니다. `--slow-text-ms`, `--slow-image-ms`, `--slow-mixed-ms`로 느린 검색 기준을 운영 SLO에 맞춰 조정할 수 있고, `--synonyms-output`과 `--quality-cases-output`은 후보를 검토용 JSON으로 따로 저장합니다. 두 파일은 `review_required=true`와 `not_operational_readiness=true`가 붙은 초안이므로 그대로 운영 인수 증거로 쓰지 말고 사람이 검토한 뒤 실제 설정/품질 케이스에 병합합니다.
운영자는 같은 집계를 `GET /admin/search-insights?min_searches=3&limit=50`로도 조회할 수 있습니다.

```powershell
python scripts\search_insights.py `
  --search-log /var/log/haeorum-ai-search/search.jsonl `
  --min-searches 3 `
  --slow-text-ms 3000 `
  --slow-image-ms 5000 `
  --slow-mixed-ms 5000 `
  --json-output /var/log/haeorum-ai-search/search-insights.json `
  --markdown-output /var/log/haeorum-ai-search/search-insights.md `
  --synonyms-output /var/log/haeorum-ai-search/query-synonyms.seed.json `
  --quality-cases-output /var/log/haeorum-ai-search/quality-cases.seed.json
```

`GET /admin/sync-status`는 API 프로세스의 현재 실행 상태와 `HAEORUM_SYNC_LOG_PATH`의 최신 동기화 결과를 함께 반영하므로, 별도 sync worker가 남긴 마지막 성공/실패 상태도 조회할 수 있습니다.
`GET /admin/sync-log?limit=100`은 같은 동기화 JSONL의 최근 행을 관리자 API로 조회합니다. 실행 요약 외에도 상품 단위 `sync_product_event`/`sync_product_failed`가 기록됩니다. 삭제/비노출/이미지 검증 실패로 색인에서 제거 요청된 상품은 `action: "delete_from_index"`와 `reason`에 원인을 남기고, Marqo 배치 응답에서 상품별 실패가 확인되면 `action: "upsert_to_index"` 또는 `delete_from_index`와 함께 `product_id`, 실패 사유를 남깁니다. 단일 상품 재색인에서 source에 상품이 없으면 stale 인덱스 문서 삭제를 요청하고 `reason: "source_product_missing"`으로 기록합니다.
MSSQL/CSV source 조회 자체가 실패한 경우에도 `action: "fetch_products"`인 `sync_batch_failed` 로그와 실패 상태를 남깁니다.
`GET /admin/metrics`는 검색/클릭 수, 이미지 검색 수, 캐시 hit 수/비율, low-confidence 결과 수, p95/p99 지연시간, API 오류와 rate limit 차단 수, Redis rate limit fallback 상태, 검색 캐시 backend/TTL/Redis 오류 카운터, 동기화 상품별 실패/삭제 이벤트 수, 검색 캐시 무효화 실패 수, batch 실패 수, lock 충돌 수, Marqo/Gemini embedding backend HTTP 연결 재사용·시도·idle/stale 재연결 수, gzip 응답 수, Retry-After 응답 수/초, 요청/응답 body bytes, backend circuit breaker 상태, 로그 파일 크기, 디스크 사용량, API 프로세스 CPU/RAM/uptime 정보를 요약합니다. 검색/오류/동기화 JSONL은 요청한 tail 범위만 파일 끝에서 읽으므로 운영 로그가 커져도 최근 1000건 기준 메트릭 조회가 전체 파일 scan으로 느려지지 않습니다. 검색엔진 health 호출이 실패하거나 Gemini 프록시의 `/health` ready/model/dimensions 계약이 운영 설정과 다르면 메트릭 응답은 `engine.ok=false`, `gemini_health_problems`, `error_type`, `error`를 포함해 반환되며, 응답의 `alerts`에는 엔진 장애, backend stale keep-alive 재연결, backend Retry-After backoff, backend circuit open/short-circuit, 동기화 실패, 검색 캐시 무효화 실패, sync lock 충돌, Redis rate limit fallback, 검색 캐시 오류, 최근 API 오류/rate limit, p95 지연, 디스크/RAM 고사용률 같은 운영 경고가 들어갑니다. Marqo/Gemini root health와 index stats probe는 `HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS` 동안 짧게 캐시해 Prometheus scrape가 검색엔진에 불필요한 health 트래픽을 만들지 않게 하며, transport counter와 queue/cache 지표는 매 응답에서 최신값을 유지합니다. Prometheus scrape에는 같은 데이터를 `GET /admin/metrics.prom`의 text format으로 사용할 수 있으며, backend 연결 지표는 `haeorum_backend_http_*{service="marqo|gemini"}`, `haeorum_gemini_query_vector_*`, `haeorum_engine_health_cache_*`로 노출됩니다.

## MSSQL 연동

운영에서는 read-only 계정과 AI 검색용 View를 사용합니다.

```powershell
$env:HAEORUM_MSSQL_READONLY_CONNECTION_STRING="DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;UID=readonly;PWD=...;Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly"
$env:HAEORUM_MSSQL_QUERY="SELECT product_id, product_name, price, price_min, price_max, category_name, print_methods, materials, colors, min_order_qty, delivery_days, product_group_id, main_image_url, product_url, status, updated_at, is_deleted, display_yn, mall_id FROM dbo.v_ai_search_products"
# 운영 View가 상품번호를 p_idx로만 제공하는 경우:
# $env:HAEORUM_MSSQL_PRODUCT_ID_COLUMN="p_idx"
# $env:HAEORUM_MSSQL_UPDATED_AT_COLUMN="updated_at"
```

운영 OS 직접 배포에서는 `pyodbc`와 SQL Server ODBC 드라이버를 서버에 설치해야 합니다. Docker 배포에서는 위 MSSQL build arg를 사용해 이미지에 포함할 수 있습니다. 어떤 방식이든 실제 SQL Server 연결은 운영 OS 또는 컨테이너에 `ODBC Driver 18 for SQL Server`가 등록돼 있어야 합니다.

운영 View 초안은 `sql/v_ai_search_products_template.sql`을 참고하세요. 실제 DB 스키마에 맞게 테이블명과 컬럼명을 바꾼 뒤 DBA가 적용합니다.
`HAEORUM_MSSQL_READONLY_CONNECTION_STRING`는 운영 read-only 계정 연결 문자열입니다. 기존 배포 호환을 위해 `HAEORUM_MSSQL_CONNECTION_STRING`도 fallback으로 읽지만, 새 배포 템플릿과 운영 증거 수집은 read-only 이름을 사용합니다. Production 설정 로딩, env preflight, collector dry-run, MSSQL evidence CLI는 연결 문자열에 `Server`, `Database`, `Encrypt=yes` 또는 `Encrypt=mandatory/strict`, `TrustServerCertificate=no`, `ApplicationIntent=ReadOnly`가 있는지 확인해 암호화와 read-only intent가 빠진 연결 문자열을 차단합니다. `HAEORUM_MSSQL_QUERY`는 `SELECT` 또는 `WITH`로 시작하는 단일 read-only 문장만 허용됩니다. 쓰기/실행 키워드, `SELECT INTO`, `USE`, `DECLARE`, `SET`, 트랜잭션 제어문, SQL 주석이 포함되면 설정 로딩, env preflight, 동기화 source 생성 단계에서 차단합니다.
View 샘플 점검, 증분 동기화, 단일 상품 재색인, CSV export는 이 쿼리를 `ai_products` 파생 테이블로 감싼 뒤 필요한 `TOP`/`updated_at`/`product_id` 조건과 정렬을 붙입니다. 운영 View가 상품번호를 `p_idx` 같은 기존 컬럼명으로 노출하면 `HAEORUM_MSSQL_PRODUCT_ID_COLUMN=p_idx`로 단일 상품 재색인과 CSV export 정렬 컬럼을 바꿀 수 있습니다. 변경 시각 컬럼명도 `HAEORUM_MSSQL_UPDATED_AT_COLUMN`으로 바꿀 수 있으며, 두 값은 단순 컬럼 식별자만 허용합니다. 증분 조회의 `--since`/worker 기준값은 ISO-8601로 검증한 뒤 UTC 기준 datetime 파라미터로 바인딩하므로, 운영 View의 `updated_at`도 UTC 기준 `datetime`/`datetime2` 값으로 맞춥니다. `WITH` CTE 쿼리도 `WITH ... SELECT * FROM (<최종 SELECT>) AS ai_products` 형태로 감싸 `FROM (WITH ...)` 같은 SQL Server 비호환 문법이 생성되지 않도록 처리합니다.

View 컬럼과 샘플 row 파싱은 아래 스크립트로 점검합니다. 이 스크립트도 read-only 단일 `SELECT`/`WITH` 문장만 허용하며, `product_id` 대신 `p_idx`/`id`/`product_no`/`goods_no`/`상품번호`, `product_name` 대신 `name`/`title`/`goods_name`/`상품명`, `main_image_url` 대신 `image_url`/`대표이미지URL`, `mall_id` 대신 `site_id`/`shop_code`/`가맹점ID` 같은 영문·한글 별칭을 인정합니다. `column_report`에는 실제 매칭된 컬럼, canonical 컬럼명으로 다시 노출해야 하는 `noncanonical_required_aliases`, DBA가 View projection에 붙일 수 있는 `suggested_select_list`가 함께 남습니다. 가격/수량/납기/속성 필터가 운영 데이터에서도 실제로 동작하도록 `price_min`/`price_max`, `print_methods`, `materials`, `colors`, `min_order_qty`, `delivery_days`도 필수 View 컬럼으로 점검하고, 샘플/CSV export의 `domain_filter_coverage`에서 active 상품의 가격 범위, 최소 주문 수량, 납기, 인쇄 방식, 소재, 색상 값이 비어 있지 않은지 확인합니다. 샘플 row가 없거나 상품번호/상품명/`updated_at` 누락 또는 형식 오류/미래값, 중복 상품번호, active 상품의 카테고리/대표 이미지 URL/상품 URL/mall 식별값 누락, active 상품 음수 가격, active row 부재가 있으면 실패합니다. active 상품의 대표 이미지 URL은 credentials, 공백, 역슬래시, 잘못된 포트, localhost/loopback/private/link-local/reserved/multicast/unspecified host, 상대 URL, data URL이 없는 공개 HTTPS 절대 URL이어야 하며 HTTP 이미지는 운영 HTTPS 사이트의 mixed-content 리스크로 차단됩니다. 이미지 probe는 다운로드 전 DNS가 non-public address로 풀리는 호스트와 다운로드 redirect 대상을 같은 safe HTTP(S) 규칙으로 다시 검사해 외부 이미지 URL이 내부망/메타데이터 주소로 우회되는 요청을 차단합니다. `product_url`은 공개 HTTP(S) 절대 URL 또는 `/product_view.asp?...` 같은 루트 상대경로만 허용하며 `javascript:`, protocol-relative URL, credentials, 공백/제어문자/역슬래시는 View 샘플과 CSV export 단계에서 실패합니다. 또한 `fn_my_permissions`와 DB role membership을 조회해 `db_datawriter`, `db_owner`, `UPDATE`, `INSERT`, `DELETE`, `ALTER`, `CREATE` 같은 쓰기/DDL 권한이 보이면 `permission_report.ok=false`로 운영 readiness에서 차단합니다. 연결, 드라이버, 쿼리 검증이 실패해도 `--output` JSON은 `ok=false`와 마스킹된 오류만 남기며 connection string password와 URL credentials는 기록하지 않습니다.

```powershell
python examples\HaeorumAISearch\scripts\mssql_view_check.py `
  --connection-string "DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;UID=readonly;PWD=..." `
  --query "SELECT product_id, product_name, price, price_min, price_max, category_name, print_methods, materials, colors, min_order_qty, delivery_days, product_group_id, main_image_url, product_url, status, updated_at, is_deleted, display_yn, mall_id FROM dbo.v_ai_search_products" `
  --sample-size 20
```

검증된 View를 PoC CSV 또는 전체 색인 CSV로 내보내려면 아래 스크립트를 사용합니다. SQL은 read-only 단일 `SELECT`/`WITH` 문장만 허용됩니다. export는 ODBC `fetchmany` batch로 row를 가져오며 `fetch_size`, `fetch_batches`, `max_fetch_batch_rows`, `batched_fetch`를 리포트에 남깁니다. export 리포트도 `source_columns`와 `column_report`를 남겨, View check를 건너뛰고 export부터 실행해도 필수 컬럼/별칭 상태를 확인할 수 있습니다. 전체 row 기준 중복 상품번호, `updated_at` 누락/형식 오류/미래값, active 상품 카테고리/이미지/상품 URL/mall 식별값 누락, active 상품 음수 가격과 unsafe URL 건수를 리포트에 남기며, 하나라도 있으면 `ok=false`입니다. View가 active 상품만 반환하면 삭제/비노출 상품을 색인에서 정리할 근거가 없어 `source_deletion_signal_ok=false` 및 `ok=false`가 됩니다. export가 실패해도 `--report-output` JSON은 생성되며 connection string 원문과 password는 오류 메시지에서 마스킹됩니다.

```powershell
python examples\HaeorumAISearch\scripts\mssql_export_csv.py `
  --connection-string "DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;UID=readonly;PWD=..." `
  --query "SELECT product_id, product_name, price, price_min, price_max, category_name, print_methods, materials, colors, min_order_qty, delivery_days, product_group_id, main_image_url, product_url, status, updated_at, is_deleted, display_yn, mall_id FROM dbo.v_ai_search_products" `
  --limit 500 `
  --product-id-column product_id `
  --updated-at-column updated_at `
  --fetch-size 1000 `
  --output-csv examples\HaeorumAISearch\logs\mssql-products.csv `
  --report-output examples\HaeorumAISearch\logs\mssql-export.json
```

## 운영 설정

주요 환경 변수:

- `HAEORUM_ENV`: `development` 또는 `production`입니다. `production`에서는 Marqo 외 검색엔진, 개발/placeholder 관리자 key, wildcard/HTTP/로컬 CORS origin, HTTP 또는 비공개/로컬 상품 URL 템플릿, 비어 있는 mall 설정, 가맹점별 `api_key`/`allowed_origins`/URL 템플릿 누락, sample/placeholder 또는 짧고 단순한 공개 API key, mall별 wildcard/HTTP/localhost/private/link-local/reserved `allowed_origins`, HTTP 또는 localhost/private/link-local/reserved `product_url_template`, 전역 CORS에 포함되지 않은 mall별 origin, 3600초 초과 동기화 주기, 잘못된 동기화 알림 webhook URL을 기동 전에 거절합니다.
- `HAEORUM_CORS_ORIGINS`: 허용 도메인 CSV. 값은 `https://shop.example.com`처럼 scheme/host/port만 있는 origin이어야 하며 path/query/계정 정보는 거절됩니다. 기본 포트와 대소문자는 정규화되고 중복은 제거됩니다. 운영에서는 `*` 대신 실제 쇼핑몰 도메인을 지정합니다. 가맹점별 `allowed_origins`도 설정하면 공개 검색/클릭 API가 `Origin` 헤더를 추가로 확인하며, 각 mall의 `allowed_origins`는 전역 `HAEORUM_CORS_ORIGINS`에도 포함되어야 합니다.
- `HAEORUM_MARQO_MODEL`: Marqo 인덱스 생성 시 사용할 모델명입니다. 모델 변경 후에는 전체 재색인이 필요합니다.
- `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS`: 공개 검색 요청에서 Marqo `/search` 호출을 기다리는 최대 시간입니다. 기본 15초이며, 느린 Marqo 호출이 API worker를 장시간 점유하지 않도록 100 concurrent 부하 전 운영 p95와 함께 조정합니다.
- `HAEORUM_MARQO_SEARCH_RETRY_COUNT`, `HAEORUM_MARQO_SEARCH_RETRY_DELAY_SECONDS`, `HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS`: Marqo/Gemini 검색 경로와 Marqo 색인/삭제, Gemini embedding 경로의 408/429/5xx, 연결 오류, timeout 같은 일시 오류 재시도 횟수, 초기 대기 시간, backend `Retry-After` 반영 상한입니다. 기본은 1회, 0.1초, 최대 2초입니다.
- `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS`: Marqo/Gemini thread-local keep-alive 연결이 이 시간보다 오래 쉬면 재사용 전 새 연결로 교체합니다. 기본 55초이며, 운영 proxy/backend idle timeout보다 낮게 두면 첫 요청 stale 재시도 지연을 줄일 수 있습니다. `0`이면 선제 교체를 끕니다.
- `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS`: API process 하나에서 Marqo/Gemini으로 동시에 나가는 backend active request 슬롯 수입니다. 기본 96이며, `0`이면 제한을 끕니다.
- `HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS`: backend active request 슬롯이 모두 사용 중일 때 기다릴 시간입니다. 기본 1초이며, 초과 시 검색 API는 503으로 fail-fast 처리합니다.
- `HAEORUM_BACKEND_CIRCUIT_FAILURE_THRESHOLD`, `HAEORUM_BACKEND_CIRCUIT_COOLDOWN_SECONDS`, `HAEORUM_BACKEND_CIRCUIT_HALF_OPEN_MAX_CALLS`: Marqo/Gemini timeout, 연결 오류, 408/429/5xx가 연속될 때 backend circuit breaker를 열고 cooldown 동안 후속 backend 호출을 503 fail-fast로 처리합니다. 기본은 5회 실패, 5초 cooldown, half-open probe 1개입니다.
- `HAEORUM_ADMIN_METRICS_HEALTH_CACHE_SECONDS`: `/admin/metrics`와 `/admin/metrics.prom`에서 Marqo root health, index stats, Gemini health probe를 재사용하는 짧은 TTL입니다. 기본은 2초입니다. `0`으로 두면 매 scrape마다 직접 확인합니다.
- `HAEORUM_MARQO_ADD_DOCUMENTS_BATCH_SIZE`, `HAEORUM_MARQO_ADD_DOCUMENTS_MAX_REQUEST_BYTES`: Marqo `/documents` 색인 요청을 나누는 상품 수와 JSON body soft cap입니다. 기본은 128개, 8MiB입니다. 직접 Marqo embedding 모드도 전체 CSV를 한 번에 메모리에 올리지 않고 batch generator로 색인하며, Gemini split-vector 모드는 embedding 후 Marqo add-documents 요청을 같은 한도로 다시 쪼갭니다. 색인 문서에는 rerank용 `search_text` 합성 필드를 같이 저장해 텍스트 검색 후보 100개를 받을 때 `description`/`keywords`/속성 텍스트 여러 필드 대신 compact field 하나만 가져오므로 Marqo 응답 bytes와 Python parse 비용을 줄입니다. Marqo/Gemini backend 요청은 compact JSON으로 직렬화해 대량 벡터 payload의 wire byte와 batch split 압력을 줄입니다. `csv_index.py` 리포트의 `indexing.batch_count`, `max_batch_size`, `max_request_body_bytes`로 실제 배치 크기를 확인합니다.
- `HAEORUM_MARQO_DELETE_DOCUMENTS_BATCH_SIZE`: 숨김/삭제/이미지 검증 실패 상품을 Marqo `delete-batch`로 정리할 때 요청당 최대 document ID 수입니다. 기본은 512입니다. active-only View를 잘못 쓰다가 대량 stale 문서를 정리해야 하는 상황에서도 단일 삭제 요청이 과도하게 커지지 않게 합니다.
- `MARQO_API_GZIP_MINIMUM_SIZE`: reference Marqo API가 gzip 응답을 적용할 최소 JSON 응답 크기입니다. 기본 1024 byte이며, 앱 클라이언트의 gzip transport 지표와 함께 큰 검색 응답의 wire byte 증가를 확인합니다.
- `HAEORUM_API_GZIP_MINIMUM_SIZE`: 공개 AI 검색 API가 gzip 응답을 적용할 최소 응답 크기입니다. 기본 1024 byte이며, `0`이면 앱 레벨 압축을 끕니다. Nginx가 압축하더라도 앱 단독/내부 부하 테스트에서 위젯 검색 JSON의 wire byte 증가를 놓치지 않도록 기본값을 유지합니다.
- `HAEORUM_API_INSTANCE_ID`: 공개 AI 검색 API가 응답 헤더 `X-Haeorum-API-Instance`에 남길 API 서버/container 식별자입니다. 기본값은 host/container 이름 기반 hash이며, 같은 host에서 여러 API 인스턴스를 띄우는 운영이면 인스턴스마다 명시적으로 다른 값을 지정해야 API scale 증거가 실제 분산을 확인할 수 있습니다.
- `HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS`: Gemini embedding backend 사용 시 검색 쿼리 텍스트/이미지 임베딩 호출 timeout입니다. 색인용 긴 embedding 작업과 분리되어 공개 검색 worker 보호에만 적용됩니다.
- `HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM`: Gemini split-vector 혼합 검색에서 텍스트 query vector와 이미지 query vector를 동시에 계산하는 shared worker 수입니다. 기본값은 8입니다. `0`이면 순차 계산으로 되돌립니다.
- `HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES`, `HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES`: Gemini query embedding 런타임 LRU 캐시의 텍스트/이미지 별도 quota입니다. 기본값은 2048/512입니다. 운영 preflight는 Gemini split-vector 검색에서 텍스트 캐시가 100 concurrent, 이미지 캐시가 30 concurrent 기준보다 작으면 실패시켜 이미지 업로드가 몰릴 때 텍스트 반복 검색 캐시가 같이 밀려나는 문제를 미리 잡습니다.
  공개 검색 worker thread는 Marqo/Gemini JSON HTTP 연결을 thread-local keep-alive로 재사용하고, 요청 body는 compact JSON으로 보내며, backend가 지원하면 gzip 응답을 받아 wire bytes를 줄입니다. 공개 API 응답도 `HAEORUM_API_GZIP_MINIMUM_SIZE` 이상이면 gzip으로 압축되어 위젯이 받는 큰 검색 JSON의 전송량을 줄입니다. `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS`보다 오래 쉰 연결은 재사용 전 새 연결로 교체하고, `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS`/`HAEORUM_BACKEND_HTTP_CONNECTION_ACQUIRE_TIMEOUT_SECONDS`로 process별 backend active request 슬롯을 제한해 backend 대기열이 worker thread를 장시간 붙잡지 않게 합니다. 연결이 끊기거나 timeout/HTTP protocol 오류가 나면 해당 thread의 연결을 닫고 stale 연결은 새 연결로 1회 복구한 뒤 재시도 정책에 따라 처리하므로, 100 concurrent 텍스트 부하에서 매 요청마다 TCP 연결을 새로 여는 비용과 운영 중 idle 연결 종료로 생기는 일시 실패를 줄입니다. 429/503 같은 일시 응답에 `Retry-After`가 있으면 `HAEORUM_BACKEND_RETRY_AFTER_MAX_SECONDS` 안에서 재시도 대기를 늘려 과부하 backend를 즉시 재타격하지 않게 합니다. timeout/연결 오류/408/429/5xx가 연속되면 backend circuit breaker가 열려 같은 process의 후속 backend 호출을 cooldown 동안 즉시 503으로 실패시키고, half-open probe 성공 시 닫습니다. `/admin/metrics`의 `engine.transport`와 Prometheus `haeorum_backend_http_*` 지표로 backend 슬롯 부족, 연결 churn, backend 응답시간, payload 크기 증가, 과부하 backoff, 장애 fail-fast 상태를 확인할 수 있습니다. Gemini query embedding은 런타임 LRU와 in-flight dedupe로 반복 텍스트/이미지 질의를 재사용합니다. 텍스트와 이미지 런타임 cache quota를 분리해 고유 이미지 검색이 많이 들어와도 자주 반복되는 텍스트 query vector를 보존합니다. raw Prometheus 지표 이름은 Gemini 모드에서 `haeorum_gemini_query_vector_*`로 노출됩니다. API 1대/2대 확장 비교는 `request_profile`의 고유 요청 signature, 반복률, query type별 고유 요청 수, mall별 고유 요청 수까지 같아야 통과하므로 한쪽만 캐시가 과하게 유리한 부하를 확장성 증거로 제출할 수 없습니다. 또한 multi API 리포트는 성공 응답의 `X-Haeorum-API-Instance` 분포가 `--api-server-count` 이상으로 갈라지고 각 인스턴스가 최소 5% 이상 응답해야 통과하므로, 로드밸런서가 한 API 서버로만 붙는 구성을 scale 증거로 제출할 수 없습니다.
  `compose-haeorum-marqo.yaml`의 Marqo API 컨테이너는 API 2대 기준 기본 검색 동시성까지 내부 HTTP pool이 먼저 병목이 되지 않도록 `VESPA_POOL_SIZE`와 `MARQO_INFERENCE_POOL_SIZE`를 기본 128로 설정하고, Marqo API worker는 기본 2개, 검색 throttling 호환값 `MARQO_MAX_CONCURRENT_SEARCH`는 기본 100으로 설정합니다. AI 검색 API 컨테이너는 anyio threadpool 기본 40개가 검색 queue 64개보다 먼저 병목이 되지 않도록 `HAEORUM_API_THREADPOOL_TOKENS`를 기본 96으로 설정하고, backend active request 슬롯도 `HAEORUM_BACKEND_HTTP_MAX_ACTIVE_REQUESTS=96`으로 설정합니다. Vespa 기본 1초 timeout이 운영 p95 목표보다 먼저 끊기지 않도록 `VESPA_SEARCH_TIMEOUT_MS`도 기본 5000으로 설정합니다. Marqo API keep-alive는 `MARQO_API_KEEPALIVE_TIMEOUT=75`, gzip 최소 크기는 `MARQO_API_GZIP_MINIMUM_SIZE=1024`, 응답 직렬화는 Marqo 의존성에 포함된 `ORJSONResponse`를 기본으로 사용하고, 앱의 backend idle rotation은 `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS=55`가 기본이라 앱이 idle 연결을 서버 timeout 전에 먼저 새로 엽니다. reference compose는 주요 컨테이너에 `ulimits.nofile=65535`를 두고, systemd 직접 배포 템플릿은 API/sync/reindex 서비스에 `LimitNOFILE=65535`를 둡니다. 운영 preflight는 API/Marqo 역할에서 현재 프로세스 open-file limit도 65535 이상인지 확인합니다. 운영에서 `HAEORUM_SEARCH_MAX_CONCURRENCY`나 API 서버 수를 올리면 Marqo/Vespa 자원과 이 pool 값도 같이 조정합니다. `scripts/env_check.py --api-server-count N`은 production Marqo 환경에서 두 pool 값을 명시하지 않거나 `HAEORUM_SEARCH_MAX_CONCURRENCY * N`보다 작게 두면 실패시키고, `MARQO_API_WORKERS`/`MARQO_MAX_CONCURRENT_SEARCH`가 required load concurrency를 받지 못하거나 API threadpool token이 검색/이미지 queue보다 작거나 backend active request 슬롯이 한 API 서버의 검색 동시성보다 작거나 `VESPA_SEARCH_TIMEOUT_MS`가 텍스트 p95 목표보다 작거나 `HAEORUM_MARQO_SEARCH_TIMEOUT_SECONDS` 예산을 초과하거나 `HAEORUM_BACKEND_HTTP_MAX_IDLE_SECONDS >= MARQO_API_KEEPALIVE_TIMEOUT`, gzip 최소 크기가 꺼져 있거나 너무 크면 실패시켜 API threadpool, Marqo 내부 대기열, backend slot, 조기 timeout, stale keep-alive, 큰 응답 payload 병목을 배포 전에 잡습니다.
- `HAEORUM_MAX_IMAGE_MB`: 업로드 이미지 최대 용량입니다.
- `HAEORUM_MIXED_TEXT_WEIGHT`, `HAEORUM_MIXED_IMAGE_WEIGHT`: 텍스트+이미지 혼합 검색의 기본 가중치입니다. 기본값은 기획서의 초기값인 텍스트 0.4, 이미지 0.6이며, 두 값은 정규화되어 사용됩니다.
- `HAEORUM_TEXT_AUXILIARY_WEIGHT`, `HAEORUM_TEXT_AUXILIARY_CANDIDATE_MULTIPLIER`, `HAEORUM_TEXT_AUXILIARY_SEARCH_PARALLELISM`: 텍스트가 포함된 Gemini 검색에서 `gemini_text_vector`를 보조 점수로 반영하는 가중치, 보조 검색 후보 배수, Marqo 보조 검색 병렬 worker 수입니다. 기본값은 0.12/1.0/8이며, 최종 점수는 이미지 벡터 1차 점수 88%와 텍스트 보조 점수 12%의 혼합으로 계산합니다.
- `HAEORUM_LOW_SCORE_THRESHOLD`: 최상위 결과 점수가 이 값보다 낮으면 낮은 유사도 안내 문구를 응답합니다.
- `HAEORUM_CATEGORY_SUGGESTION_LIMIT`: 비슷한 카테고리 추천 최대 개수입니다. 기획서 기준인 10~15개 범위에 맞춰 최대 15개까지 허용합니다.
- `HAEORUM_MAX_OFFSET`: related items 더보기에서 허용하는 최대 `offset`입니다. 기본값은 200이고, Marqo candidate fan-out을 제한하기 위해 설정/런타임 hard cap은 500입니다. 속성 후처리와 깊은 페이지가 겹쳐도 Marqo `/search` 후보 요청 수는 2000개로 capped됩니다.
- `HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE`: IP별 분당 전체 검색 요청 제한입니다. `0`이면 비활성화합니다. 운영 env preflight는 850 active user 혼합 부하 증거를 만들 수 있도록 0 또는 850 이상을 요구합니다.
- `HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE`: `mall_id`별 분당 전체 검색 요청 제한입니다. `0`이면 비활성화합니다. 운영 env preflight는 0 또는 850 이상을 요구합니다.
- `HAEORUM_CLICK_RATE_LIMIT_PER_MINUTE`: IP별 분당 클릭 로그 요청 제한입니다. `0`이면 비활성화합니다.
- `HAEORUM_MALL_CLICK_RATE_LIMIT_PER_MINUTE`: `mall_id`별 분당 클릭 로그 요청 제한입니다. `0`이면 비활성화합니다.
- `HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE`: IP별 분당 이미지 검색 제한입니다. `0`이면 비활성화합니다. 운영 env preflight는 기본 70/10/20 mixed traffic의 이미지+혼합 요청 30%를 감안해 0 또는 255 이상을 요구합니다.
- `HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE`: `mall_id`별 분당 이미지 검색 제한입니다. `0`이면 비활성화합니다. 운영 env preflight는 0 또는 255 이상을 요구합니다.
- `HAEORUM_RATE_LIMIT_MAX_BUCKETS`: Redis가 없거나 Redis rate limit 저장소가 실패해 로컬 bucket fallback을 쓸 때 프로세스당 보관할 최대 IP/mall rate limit bucket 수입니다. 기본값은 10000이고 오래된 bucket부터 정리합니다. Redis rate limit 경로는 카운터 증가와 TTL 갱신을 같이 수행해 만료 없는 제한 key가 남지 않게 합니다.
- `HAEORUM_RATE_LIMIT_PRUNE_INTERVAL_SECONDS`: 로컬 rate limit bucket fallback의 stale bucket 전체 정리 주기입니다. 기본값은 1초입니다. `0`이면 max bucket 초과 시 정리만 수행합니다. 현재 요청 key의 만료 타임스탬프는 매 요청마다 제자리에서 정리해 고빈도 동일 IP 요청의 리스트 재할당을 줄입니다.
- `HAEORUM_SEARCH_MAX_CONCURRENCY`: API 프로세스 1개가 동시에 검색엔진에 실행할 전체 검색 수입니다. `0`이면 전체 검색 실행 동시성 제한을 끕니다. 텍스트 100 concurrent 부하에서도 Marqo로 들어가는 순간 동시 호출 수를 제한하기 위한 보호장치입니다.
- `HAEORUM_SEARCH_QUEUE_TIMEOUT_SECONDS`: 전체 검색 동시성 한도에 도달했을 때 요청이 빈 슬롯을 기다리는 최대 시간입니다. 초과하면 429를 반환합니다. `/admin/metrics`와 Prometheus의 `haeorum_search_queue_wait_*` 지표로 429가 발생하기 전 슬롯 대기 시간이 커지는지도 확인합니다.
- `HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY`: API 프로세스 1개가 동시에 처리할 이미지/혼합 검색 수입니다. `0`이면 동시성 제한을 끕니다.
- `HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS`: 동시성 한도에 도달했을 때 이미지/혼합 검색 요청이 빈 슬롯을 기다리는 최대 시간입니다. 초과하면 429를 반환합니다. 이 gate는 multipart 업로드 bytes 읽기, 이미지 디코딩/리사이즈/해시 계산, 실제 검색엔진 호출처럼 무거운 이미지 구간에 적용됩니다. 같은 이미지 cache miss가 동시에 몰릴 때 follower 요청은 singleflight 대기 중 image gate를 점유하지 않으므로 슬롯이 backend owner 요청에 집중됩니다. `haeorum_image_search_queue_wait_*` 지표가 상승하면 이미지 전처리, Gemini embedding/Marqo 병목 또는 API 서버 수 부족을 먼저 의심합니다.
- `HAEORUM_API_THREADPOOL_TOKENS`: 공개 API의 blocking 작업을 실행하는 FastAPI/anyio threadpool token 수입니다. rate limit, 이미지 전처리, 검색엔진 호출이 `run_in_threadpool`을 쓰므로 이 값이 `HAEORUM_SEARCH_MAX_CONCURRENCY + HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY + 8`보다 작으면 API threadpool이 Marqo보다 먼저 병목이 됩니다. reference compose 기본값은 96입니다.
- `HAEORUM_API_GZIP_MINIMUM_SIZE`: 공개 API gzip 압축 최소 응답 크기입니다. 기본값은 1024 byte입니다.
- `HAEORUM_MIN_IMAGE_DIMENSION`: 업로드 이미지와 동기화 중 검증하는 대표 이미지의 최소 가로/세로 픽셀입니다. 너무 작은 이미지는 검색/색인에서 제외합니다.
- `HAEORUM_IMAGE_VALIDATION_CACHE_TTL_SECONDS`, `HAEORUM_IMAGE_VALIDATION_CACHE_MAX_ENTRIES`: 같은 API 프로세스에서 반복되는 동일 이미지 업로드의 디코딩/리사이즈/해시 검증 결과를 짧게 LRU 캐시합니다. 기본값은 30초/32개입니다. 검색 응답 캐시 hit를 확인하기 전에도 이미지 SHA256이 필요하므로, 이 캐시는 동일 이미지 반복 검색에서 캐시 hit 경로의 CPU와 image queue 점유를 줄입니다. `/admin/metrics`의 `image_validation.cache_*`와 Prometheus `haeorum_image_validation_cache_*`로 hit/miss/entry/eviction을 확인합니다.
- `HAEORUM_CACHE_TTL_SECONDS`: 동일 검색 요청 캐시 TTL입니다. `0`이면 캐시를 끄지만, API 서버 2대 이상 운영에서는 공유 캐시가 실제로 켜져 있어야 하므로 `env_check.py`, `load_compare.py`, `operational_readiness.py`가 양수 TTL을 요구합니다. 이미지/혼합 검색은 충돌 위험을 피하기 위해 정규화된 이미지 SHA256을 캐시 키로 사용하며, 유사/중복 이미지 분석용 perceptual hash는 검색 로그의 `image_perceptual_hash`로 남깁니다. 이미지 업로드 요청은 인증과 이미지 rate limit을 먼저 통과하고, 업로드 bytes 읽기/이미지 전처리/backend 호출 구간만 image queue로 제한합니다. 이미지 검증 cache miss에서도 품질 경고와 perceptual hash를 단일 PIL feature 분석 pass에서 계산해 첫 이미지 요청의 중복 디코딩을 줄입니다. 캐시 hit는 backend 호출을 건너뛰고, 같은 이미지 cache miss follower는 singleflight 대기 중 image queue 슬롯을 붙잡지 않아 동일 이미지 반복 검색의 검색엔진 부하와 이미지 큐 점유를 줄입니다. 캐시 키에는 엔진명, 백엔드, 인덱스명, Marqo 모델명, mall별 URL/가격/노출 정책 지문, query synonym 설정, 카테고리 추천 수 설정, 정규화 검색어와 추론 카테고리도 포함되어, Redis를 공유해도 인덱스/모델/정책/동의어/응답 구성값이 다른 응답이 섞이지 않습니다. mall별 정책 fingerprint 토큰은 실제 mall 정책이 있는 ID만 캐시에 저장하고 알 수 없는 mall_id는 공통 base token을 재사용해 오입력/공격성 mall_id가 프로세스 메모리를 계속 키우지 않게 합니다. Redis 캐시는 API 서버 여러 대에서 같은 검색어가 동시에 miss될 때 짧은 miss lock으로 한 서버만 Marqo를 호출하게 하고, 나머지는 캐시가 채워질 때까지 짧게 기다려 검색엔진 순간 부하를 낮춥니다. 메모리 캐시는 `HAEORUM_CACHE_MAX_ENTRIES` 기준 LRU로 오래된 항목을 제거해 고유 검색어 폭주가 프로세스 메모리를 계속 키우지 않게 하며, eviction 수는 `/admin/metrics`와 Prometheus에 노출됩니다. 동기화/재색인/삭제가 상품 인덱스를 바꾸면 검색 캐시를 즉시 비우고 `search_cache_cleared` 또는 `search_cache_clear_failed` sync log 이벤트를 남깁니다.
- `HAEORUM_CACHE_MAX_ENTRIES`: Redis 없이 메모리 검색 캐시를 쓸 때 프로세스당 보관할 최대 검색 응답 수입니다. 기본값은 10000이고 최소 1입니다. Redis 검색 캐시에는 TTL이 적용되므로 이 값은 메모리 backend 보호용입니다.
- `HAEORUM_CACHE_MISS_LOCK_SECONDS`, `HAEORUM_CACHE_MISS_WAIT_SECONDS`, `HAEORUM_CACHE_MISS_POLL_SECONDS`: Redis 캐시 사용 시 동일 cache miss를 API 서버 간에 합치는 lock TTL, follower 대기 시간, polling 주기입니다. 기본값은 각각 35초, 5초, 0.05초입니다. lock TTL은 Marqo 검색 timeout, retry 횟수, exponential retry delay를 합친 최악 backend 예산보다 길어야 느린 Marqo 응답 중 다른 API 서버가 같은 cache miss를 다시 실행하지 않습니다. `env_check.py`의 `cache_miss_coordination` 검사는 API 서버 2대 이상 또는 Redis 캐시가 설정된 Marqo API 구성에서 이 조건을 사전에 실패 처리합니다. 같은 API 프로세스 안의 동일 miss는 singleflight로 먼저 합쳐지고, backend 실패도 현재 대기 요청들이 공유해 장애 중 동일 쿼리 재호출을 줄입니다. 로컬 singleflight 또는 Redis miss-lock follower 대기가 `HAEORUM_CACHE_MISS_WAIT_SECONDS`를 초과하면 같은 miss를 다시 백엔드로 보내지 않고 429로 fail-fast 처리해, 느린 Marqo/Gemini 상태에서 follower 요청이 중복 backend storm으로 번지는 것을 막습니다. lock 획득/경합/오류와 follower wait/timeout 지표는 `/admin/metrics`와 Prometheus `haeorum_search_cache_lock_*`로 확인하고, 로컬 singleflight 대기/timeout은 `singleflight` 객체와 `haeorum_search_singleflight_*` 지표로 확인합니다.
- `HAEORUM_REDIS_URL`: 선택 값입니다. 설정하면 검색 캐시와 전체 검색/클릭 로그/이미지 검색 rate limit을 Redis에 저장해 API 서버 여러 대가 공유합니다. 값은 `redis://redis:6379/0` 또는 TLS 사용 시 `rediss://redis:6380/0` 같은 절대 Redis URL이어야 하며, placeholder, 잘못된 port, link-local/unspecified host, URL fragment, 잘못된 DB path는 설정 로딩과 env preflight에서 거절됩니다. 검색 캐시는 Redis 읽기/쓰기/lock 실패나 손상된 캐시 값을 miss로 처리해 검색 요청 자체가 실패하지 않게 하고, 실패 횟수와 마지막 오류는 `/admin/metrics`의 `cache` 및 Prometheus `haeorum_search_cache_*` 지표로 노출합니다. Redis rate limit 저장소가 일시적으로 실패하면 해당 API 프로세스의 메모리 bucket으로 fallback해 요청을 계속 제한합니다. `HAEORUM_REDIS_SOCKET_TIMEOUT_SECONDS`, `HAEORUM_REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`, `HAEORUM_REDIS_FAILURE_BACKOFF_SECONDS`는 Redis 장애가 검색 worker를 오래 붙잡거나 매 요청마다 즉시 재시도하는 상황을 줄입니다.
- `HAEORUM_REDIS_KEY_PREFIX`: Redis key prefix입니다.
- `HAEORUM_TRUSTED_PROXY_IPS`: `X-Forwarded-For`/표준 `Forwarded`/`X-Real-IP`를 신뢰할 프록시 IP 또는 CIDR 목록입니다. Docker로 API 포트를 publish하고 호스트 Nginx가 프록시하면 기본값은 `127.0.0.1,::1,172.16.0.0/12`입니다. 운영에서는 실제 reverse proxy peer만 남기고, API 포트는 방화벽/보안그룹으로 외부 직접 접근을 막아야 합니다. 신뢰되지 않은 peer가 보낸 forwarded header는 IP별 rate limit과 오류 로그에 사용하지 않습니다. 배포 Nginx 템플릿은 클라이언트가 보낸 기존 `X-Forwarded-For`를 이어 붙이지 않고 `$remote_addr`로 덮어쓰며, `X-Real-IP`도 `$remote_addr`로 덮어쓰고 표준 `Forwarded` 헤더는 제거합니다.
- `HAEORUM_SYNC_INTERVAL_SECONDS`: 동기화 worker 반복 주기입니다. 기본값은 3600초입니다. production runtime, env preflight, 운영 security evidence는 이 값이 3600초 이하인지 확인하므로, 1시간보다 긴 주기로 배포하면 기동 또는 readiness가 실패합니다.
- `HAEORUM_MSSQL_SYNC_FETCH_SIZE`: sync worker가 운영 MSSQL View를 읽을 때 ODBC `fetchmany`로 가져올 row 수입니다. 기본값은 1000입니다. 큰 View를 `fetchall()`로 한 번에 당기지 않고 batch로 읽어 DB driver/client 메모리 피크를 낮춥니다.
- `HAEORUM_SEARCH_LOG_PATH`: 검색/클릭 JSONL 로그 경로입니다.
- `HAEORUM_ERROR_LOG_PATH`: API 오류 JSONL 로그 경로입니다.
- `HAEORUM_SYNC_LOG_PATH`: 동기화 JSONL 로그 경로입니다.
- `HAEORUM_LOG_KEEP_OPEN_SECONDS`: 검색/클릭/오류 로그 파일 핸들을 마지막 쓰기 후 짧게 유지하는 시간입니다. 앱 기본값은 0초이고 reference compose/env는 운영 burst 부하의 파일 open/close 비용을 줄이기 위해 1초로 둡니다. 로그는 매 write마다 flush되고 배포 logrotate는 `copytruncate`를 쓰므로 운영 회전과 충돌하지 않습니다. `/admin/metrics`의 `logs.search.output_opens`, `output_reuses`, `idle_closes`, `buffer_open`으로 효과를 확인합니다.
- `HAEORUM_SYNC_LOCK_STALE_SECONDS`: 동기화 lock 파일이 이 시간보다 오래됐고 같은 host의 소유 프로세스가 살아 있지 않으면 stale lock으로 보고 자동 회수합니다. 기본값은 21600초이며, `0`이면 자동 회수를 끕니다.
- `HAEORUM_PRODUCT_URL_TEMPLATE`: 가맹점별 상품 상세 URL 템플릿입니다. `{product_id}` 치환 결과는 credentials, 공백, 역슬래시, localhost/loopback/private/link-local/reserved/multicast/unspecified host가 없는 공개 HTTP(S) 절대 URL이어야 하며 production에서는 HTTPS여야 합니다.
- `HAEORUM_FILTER_BY_MALL_ID`: 상품 row가 가맹점별로 분리되어 있을 때만 `true`로 설정해 검색엔진 단계에서 `mall_id` 필터를 강제합니다. 동일 DB 상품을 모든 가맹점이 공유하면 기본값 `false`를 유지합니다. 기본 공유 모드에서는 상품 row에 `mall_id` 값이 있어도 API 요청의 `mall_id`는 상품 URL/가격/노출 정책 선택에만 쓰고 검색 결과 필터로 쓰지 않습니다.
- `HAEORUM_MALL_CONFIG_PATH`: 가맹점별 API key, URL 템플릿, 제외 상품/카테고리 정책, 가격 표시 정책을 담은 JSON 설정 파일입니다.
- `HAEORUM_QUERY_SYNONYM_PATH`: 검색어 동의어/유사 표현 JSON 설정 파일입니다. 텍스트 검색과 텍스트+이미지 혼합 검색에서 양방향으로 확장됩니다.
- `HAEORUM_VALIDATE_PRODUCT_IMAGES`: 동기화 시 대표 이미지 URL을 다운로드/검증해 실패 상품을 색인에서 제외합니다.
- `HAEORUM_PRODUCT_IMAGE_PROBE_TIMEOUT_SECONDS`: 대표 이미지 URL 검증 타임아웃입니다.
- `HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_COUNT`: 대표 이미지 URL 검증 다운로드 실패 후 재시도 횟수입니다. `0`이면 최초 1회만 시도하고, HTTP 4xx는 재시도하지 않습니다.
- `HAEORUM_PRODUCT_IMAGE_PROBE_RETRY_DELAY_SECONDS`: 대표 이미지 URL 검증 재시도 사이 대기 시간입니다.
- `HAEORUM_PRODUCT_IMAGE_DOWNLOAD_THREAD_COUNT`: Marqo 색인 중 대표 이미지 URL을 동시에 다운로드할 worker 수입니다. 이미지 서버 부하를 줄이기 위해 기본값은 3입니다. Gemini split-vector backend에서는 상품 텍스트/이미지 embedding 요청도 Marqo document batch 단위로 나누고, Marqo add-documents 요청은 byte cap으로 한 번 더 분할해 큰 export 색인 시 단일 backend 요청이 과도하게 커지지 않게 합니다.
- `HAEORUM_SYNC_ALERT_WEBHOOK_URL`: 동기화 실패 시 JSON payload를 POST할 알림 웹훅 URL입니다. 값이 있으면 `https://` 절대 URL이어야 하며 URL 사용자 정보, fragment, 공백은 허용하지 않습니다. 운영 readiness의 보안 증거는 이 값을 채우거나, 별도 모니터링에서 sync failure 알림이 구성됐음을 `security_check.py --sync-alerting-configured`로 명시해야 통과합니다.
- `HAEORUM_SYNC_ALERT_TIMEOUT_SECONDS`: 동기화 실패 알림 웹훅 호출 타임아웃입니다.
- `HAEORUM_MAX_IMAGE_DIMENSION`: 사용자가 업로드한 이미지의 가장 긴 변이 이 값을 넘으면 검색 요청 전에 비율을 유지해 리사이즈합니다.

운영 배포용 환경 변수 예시는 `.env.example`에 있습니다.
서버 시작 시 주요 숫자 설정도 검증합니다. `default_limit`, 카테고리 추천 수, 이미지 용량/최소 크기/리사이즈 한도, 이미지 검증 cache entry 상한, 동기화 주기, 이미지 URL probe timeout, Marqo 이미지 다운로드 worker 수, 알림 timeout은 양수여야 하고, production 동기화 주기는 3600초 이하여야 합니다. rate limit/cache TTL/이미지 검증 cache TTL/이미지 검색 동시성/stale lock 회수 시간은 `0` 이상이어야 하며, rate limit bucket 상한은 1 이상이어야 합니다. 카테고리 추천 수는 최대 15개이고, 최대 이미지 변 길이는 최소 이미지 변 길이 이상이어야 하며, 혼합 검색 text/image 가중치는 음수가 될 수 없고 둘 다 `0`일 수 없습니다.

배포 env 파일은 서비스 시작 전에 별도 preflight로도 확인할 수 있습니다. 이 검사는 placeholder secret, production 필수 변수, Marqo/Redis URL 형식, Gemini embedding backend/URL/model/dimension, CORS wildcard, mall/synonym 파일 경로, MSSQL/CSV source와 MSSQL read-only TLS 연결 문자열, boolean/숫자 설정, trusted proxy IP/CIDR, 동기화 알림 webhook URL 형식과 비로컬 공개 host 여부, 2대 이상 API 서버의 Redis 공유 설정, 실제 `load_settings()` 기동 가능 여부를 JSON/Markdown 증거로 남깁니다.

```powershell
python examples\HaeorumAISearch\scripts\env_check.py `
  --env-file examples\HaeorumAISearch\.env `
  --role api `
  --api-server-count 2 `
  --output examples\HaeorumAISearch\logs\env-check.json `
  --markdown-output examples\HaeorumAISearch\logs\env-check.md
```

`HAEORUM_MALL_CONFIG_PATH`를 지정하면 검색 API와 클릭 로그 API는 `mall_id`가 설정에 존재하고 활성화되어 있는지 확인합니다. 해당 가맹점에 `api_key`가 있으면 위젯에서 `X-API-Key`로 같은 값을 보내야 합니다. 관리자 key는 관리자 API의 `X-Admin-Key` 또는 `Authorization: Bearer ...`에서만 받습니다. URL query string의 `api_key`/`apiKey`/`apikey`/`api-key`/`x-api-key`와 `admin_key`/`admin-key` 계열 별칭은 값이 비어 있어도 access log 노출 경로를 막기 위해 거절합니다. JSON/multipart body의 같은 필드도 로그/프록시/검증 오류에 섞일 여지를 줄이기 위해 400으로 거절합니다.

```json
{
  "malls": [
    {
      "mall_id": "shop001",
      "api_key": "replace-with-shop001-public-key",
      "product_url_template": "https://shop001.haeorumgift.com/product_view.asp?p_idx={product_id}",
      "allowed_origins": ["https://shop001.haeorumgift.com"],
      "enabled": true,
      "excluded_product_ids": ["P017"],
      "excluded_categories": ["상패"],
      "hide_prices": false,
      "price_multiplier": 1.0,
      "price_adjustment": 0,
      "price_round_to": 10
    }
  ]
}
```

`allowed_origins`는 해당 가맹점 위젯이 호출할 수 있는 브라우저 origin 목록입니다. enabled 가맹점에는 반드시 설정해야 하며, 누락되면 `mall_config_check.py`와 production 기동 검증이 실패합니다. 값이 있으면 `Origin` 헤더가 없거나 목록에 없는 공개 검색/클릭 요청은 403으로 거절합니다. 값은 `https://shop001.haeorumgift.com`처럼 scheme/host/port만 포함해야 하며 path, query, fragment는 허용하지 않습니다.
`product_url_template`은 `{product_id}`를 포함해야 하고, 실제 값으로 치환했을 때 credentials, 공백, 역슬래시, localhost/loopback/private/link-local/reserved/multicast/unspecified host가 없는 공개 HTTP(S) 절대 URL이어야 합니다. Production에서는 전역 `HAEORUM_PRODUCT_URL_TEMPLATE`과 enabled mall별 `product_url_template`이 모두 HTTPS여야 합니다. API 응답 단계에서는 `{product_id}` 치환값을 URL 인코딩하므로 운영 DB의 상품번호에 `&`, `?`, `/`, 공백이 섞여도 상세 URL의 query/path 구조를 깨지 않습니다. MSSQL View의 `product_url`이 `/product_view.asp?...` 같은 상대 URL이면 API 응답 단계에서 해당 mall의 `product_url_template` 도메인 기준 절대 URL로 변환합니다. `javascript:`, `//external.example/...`, URL 사용자 정보, mall 템플릿과 scheme/host/port 또는 path prefix가 다른 절대 URL처럼 안전하지 않은 상세 URL 값은 사용하지 않고 mall 템플릿으로 대체합니다. API 응답과 Marqo 색인 payload의 상품 이미지 URL도 절대 HTTP(S)만 사용합니다. JS 위젯도 응답 URL을 다시 확인해 절대 HTTP(S)가 아닌 상품 링크와 상품 이미지 URL, inline data 이미지 URL을 렌더링하지 않고 클릭 로그에도 원문 위험 URL을 보내지 않습니다.
`excluded_product_ids`와 `excluded_categories`는 해당 가맹점에서 검색 결과에 노출하지 않을 상품번호/카테고리를 지정합니다. 상품 row가 가맹점별로 분리되지 않은 공통 DB 구조에서도 최소한의 몰별 노출 정책을 적용할 수 있습니다.
가격 정책은 상품 row 가격을 응답에 표시하기 전에 적용됩니다. `hide_prices=true`이면 가격을 `null`로 내려 위젯이 `견적문의상품`으로 표시하고, `price_multiplier`, `price_adjustment`, `price_round_to`는 공통 DB 가격에 가맹점별 표시 가격 규칙을 적용합니다.

가맹점 목록이 CSV/엑셀 export로 관리된다면 `mall_config_builder.py`로 표준 `malls.json`을 먼저 생성합니다. 입력은 `.csv`, `.txt`, `.xlsx`, `.xlsm`을 받을 수 있으며 별도 엑셀 라이브러리 없이 첫 번째 worksheet를 읽습니다. 헤더에는 `mall_id`/`site_id`/`가맹점ID`, `domain`/`origin`/`도메인`, `api_key`/`공개API키`를 기본으로 넣고, 선택적으로 `product_url_template`/`상품링크템플릿`, `excluded_product_ids`, `excluded_categories`, `hide_prices`/`가격비공개`, 가격 보정 컬럼을 추가할 수 있습니다. `사용여부` 같은 한글 boolean 컬럼은 `사용`/`미사용`, `예`/`아니오` 값을 받을 수 있습니다. 운영 mall `allowed_origins`는 HTTPS 공개 origin이어야 하며 localhost/loopback/private/link-local/reserved/multicast/unspecified host는 생성/검증 단계에서 차단됩니다. 운영 배포 전에는 `replace-with...`, `<...>`, `...`, `sample`, `dummy`, `*-dev`, `*dev-key*` 형태의 공개 API key와 짧거나 문자 다양성이 낮은 key를 실제 난수형 값으로 교체해야 합니다. `--generate-missing-api-keys`는 API key가 비어 있는 enabled mall에 운영용 난수 key를 생성하므로, 생성 결과를 기존 쇼핑몰 템플릿 배포 목록에도 같이 반영해야 합니다.

```powershell
python examples\HaeorumAISearch\scripts\mall_config_builder.py `
  --input C:\deploy\haeorum-malls.xlsx `
  --output C:\deploy\malls.json `
  --report-output C:\deploy\mall-config-build.json `
  --min-count 1700 `
  --sort-by-mall-id
```

`HAEORUM_QUERY_SYNONYM_PATH`는 운영 검색 로그와 `/admin/search-insights`에서 반복되는 무결과/저신뢰 검색어를 반영할 때 사용합니다. 값은 `{ "synonyms": { "파우치": ["가방", "백"], "에코백": "친환경 가방, 장바구니" } }` 형식이며, 각 그룹은 양방향으로 확장됩니다. 예를 들어 `파우치` 검색은 `가방`, `백`도 함께 고려하고, `가방` 검색도 `파우치`를 함께 고려합니다. 설정을 바꾸면 검색 캐시 키도 달라져 이전 동의어 기준 응답과 섞이지 않습니다.

운영 가맹점 설정 파일은 적용 전 검증합니다. 대량 적용 전에는 실제 가맹점 수에 맞춰 `--min-count`를 지정합니다. collector dry-run도 `mall_config_source` export가 있으면 builder 입력이 1,700개 enabled mall, 실제 API key, HTTPS 공개 origin, 상품 URL template을 만들 수 있는지 먼저 보고, `mall_config` 파일이 있으면 중복/placeholder/약한 API key, 중복 enabled `allowed_origins`, 중복 enabled 상품 URL prefix, `allowed_origins`, `product_url_template`, enabled count를 검사해 `invalid_input_files` blocker로 표시합니다. 최종 readiness는 `mall-config-build.json`에서 fallback API key 생성이 0건이고 validation 결과가 1,700개 key hash를 증명해야 하며, `mall-config-check.json`의 `enabled_mall_ids`, `enabled_mall_origins`, `enabled_mall_product_url_prefixes`, `enabled_mall_api_key_hashes`가 1,700개 이상이고 `api_key_strength.required=true`, `weak_api_key_mall_ids=[]`, `problems`가 비어 있어야만 통과합니다. 따라서 1,700개 enabled count가 맞아도 builder 증거 누락, placeholder/약한 API key, 중복 origin/prefix, 누락/위험 `allowed_origins`, 누락/위험 `product_url_template`, 대표 사이트 mall_id/origin/API key/product URL 불일치가 남으면 운영 완료로 보지 않습니다.

```powershell
python examples\HaeorumAISearch\scripts\mall_config_check.py `
  --config examples\HaeorumAISearch\sample_malls.json `
  --min-count 2
```

정기 동기화 worker는 API 서버와 별도 프로세스로 실행합니다.

```powershell
cd examples\HaeorumAISearch
$env:HAEORUM_SEARCH_ENGINE="marqo"
python -m app.sync_worker --mode sync
```

한 번만 실행하려면 `--once`를 추가합니다. 전체 재색인은 `--mode reindex`를 사용합니다.
리눅스 운영 배포에서는 `deploy/systemd/haeorum-ai-sync.service`로 1시간 이하 주기의 변경 동기화를 실행하고, `deploy/systemd/haeorum-ai-reindex.timer`로 매일 03:00 전체 재색인을 예약합니다.
API 서버와 worker가 같은 `HAEORUM_SYNC_LOG_PATH`를 공유해야 `/admin/sync-status`에서 worker의 최신 결과를 확인할 수 있습니다. API 서버 여러 대 또는 별도 sync worker를 운영할 때는 같은 `HAEORUM_REDIS_URL`/`HAEORUM_REDIS_KEY_PREFIX`를 써야 재색인 직후 Redis 검색 캐시가 함께 비워집니다.
동기화, 전체 재색인, 단일 상품 재색인, 수동 삭제는 `HAEORUM_SYNC_LOG_PATH` 옆의 `.lock` 파일로 중복 실행을 막습니다. 이미 다른 worker나 관리자 작업이 실행 중이면 새 작업은 실패 결과와 `sync_batch_failed/action=acquire_sync_lock` 로그를 남기고 인덱스를 건드리지 않습니다. 오래된 lock은 `HAEORUM_SYNC_LOCK_STALE_SECONDS` 기준으로 자동 회수해 강제 종료 뒤 정기 동기화가 영구 중단되지 않게 합니다. `/admin/metrics`는 lock 충돌을 `sync.events.sync_lock_busy_events`와 `sync_lock_contention` 알림으로 노출하고 Prometheus는 `haeorum_sync_recent_lock_busy_events`로 수집할 수 있습니다.
sync worker는 `--since`를 직접 주지 않으면 `HAEORUM_SYNC_LOG_PATH`에서 마지막 성공한 `sync` 또는 `reindex` 실행의 `last_started_at`을 읽어 다음 변경 동기화의 `updated_at` 하한으로 사용합니다. 이전 실행 중 수정된 상품을 놓치지 않기 위해 완료 시각이 아니라 시작 시각을 사용합니다.
CSV source의 증분 동기화도 `updated_at`을 ISO-8601 datetime으로 파싱해 UTC 기준으로 비교합니다. `updated_at`이 비어 있거나 잘못된 형식이면 변경 동기화가 실패하므로, 운영 CSV export에는 `updated_at`을 반드시 포함해야 합니다. 색인 문서 ID는 `mall_id + product_id` 복합키로 생성하므로 서로 다른 몰의 같은 상품번호는 충돌하지 않습니다. 같은 몰 안에서 같은 `product_id`가 반복되면 해당 상품들은 색인/삭제하지 않고 `sync_product_failed` 로그에 `validate_source`/`duplicate_product_id`로 남깁니다. 단일 상품 재색인/삭제에서 상품번호가 여러 몰에 존재하면 요청 body 또는 query에 `mall_id`를 함께 넘겨 대상을 지정합니다.

## 위젯 삽입

기존 검색창 옆에 전용 컨테이너를 둘 수 있으면 아래처럼 삽입합니다.

```html
<div id="haeorum-ai-search"></div>
<script src="https://ai-search.haeorumgift.com/widget.js"></script>
<script>
HaeorumAISearch.init({
  target: "#haeorum-ai-search",
  mallId: "shop001",
  apiKey: "replace-with-shop001-public-key",
  apiBaseUrl: "https://ai-search.haeorumgift.com",
  minImageDimension: 16
});
</script>
```

위젯의 `mallId`/`siteId`는 서버와 같은 규칙으로 검증됩니다. 최대 64자의 영문/숫자/하이픈만 허용하고 양끝은 영문 또는 숫자여야 하며, 둘을 함께 넣으면 같은 값이어야 합니다. `apiBaseUrl`을 명시하면 절대 HTTP(S) API 서버 주소여야 합니다. credentials, query string, fragment, 공백, 역슬래시, 잘못된 port가 포함되거나 상대 URL/`javascript:` URL이면 위젯 초기화가 실패합니다. `apiBaseUrl`을 생략한 경우에는 현재 로드된 `https://.../widget.js` script origin을 API base URL로 추론하므로, 운영에서는 위젯 script를 API 서버의 절대 HTTPS URL로 넣어야 합니다.

컨테이너 추가가 어렵다면 기존 검색 input과 검색 버튼 selector만 넘겨도 됩니다.

```html
<script src="https://ai-search.haeorumgift.com/widget.js"></script>
<script>
HaeorumAISearch.init({
  target: "",
  attachToSearchInput: "#keyword",
  attachAfterSelector: "#searchForm button[type='submit']",
  autoAttach: true,
  mountWaitMs: 3000,
  mallId: "shop001",
  apiKey: "replace-with-shop001-public-key",
  apiBaseUrl: "https://ai-search.haeorumgift.com",
  triggerTitle: "AI검색",
  triggerAriaLabel: "AI 상품 검색 열기",
  accentColor: "#0f766e",
  accentSoftColor: "#ecfdf5",
  minImageDimension: 16
});
</script>
```

inline script 추가가 어렵거나 CSP 때문에 제한되는 템플릿은 script `data-*` 속성만으로 자동 초기화할 수 있습니다. `data-hai-auto-init="true"`를 명시한 경우 위젯은 로드 즉시 같은 속성의 설정으로 초기화하며, `data-api-base-url`이 없으면 script origin을 API base URL로 사용합니다.

```html
<script
  src="https://ai-search.haeorumgift.com/widget.js"
  data-hai-auto-init="true"
  data-mall-id="shop001"
  data-api-key="replace-with-shop001-public-key"
  data-attach-to-search-input="#keyword"
  data-attach-after-selector="#searchForm button[type='submit']"
  data-trigger-title="AI검색"></script>
```

기존 사이트 개발자와 바로 협업하기 어려우면 쇼핑몰 PC/모바일 HTML을 저장하거나 URL을 직접 넘겨 아래 사전 점검으로 검색창 selector와 삽입 스니펫을 먼저 뽑습니다. 이 리포트는 운영 readiness 증거가 아니라, meta/HTTP `Content-Security-Policy`의 `script-src-elem`/`script-src`/`default-src`가 inline init 또는 외부 API 서버 `widget.js`를 막는지, 상대 `/widget.js`로 인해 API base URL을 추론할 수 없는지, HTTP/localhost/credential/query/fragment가 섞인 API base/widget/page URL인지, 검색 버튼 selector가 누락됐는지 같은 삽입 리스크를 사전에 찾는 용도입니다.

```powershell
python scripts\widget_integration_probe.py `
  --input saved-pc.html `
  --input saved-mobile.html `
  --mall-id shop001 `
  --api-key replace-with-shop001-public-key `
  --api-base-url https://ai-search.haeorumgift.com `
  --output logs\widget-integration-probe.json `
  --markdown-output logs\widget-integration-probe.md `
  --snippets-output-dir logs\widget-snippets
```

여러 대표 사이트를 한 번에 점검하려면 `contracts/representative_sites.example.json`을 복사해 실제 값으로 채운 뒤 각 site에 선택적으로 `widget_probe_source` 또는 `widget_probe_sources`를 넣습니다. 상대 경로는 대표 사이트 config 파일 위치 기준으로 해석되므로, 기존 사이트 개발자 연락이 안 될 때는 PC/모바일 HTML을 config 옆에 저장해 넣을 수 있습니다. `widget_probe_sources`는 문자열 배열도 허용하지만 운영 collector dry-run에서 PC/모바일 커버리지를 증명하려면 `{"variant":"pc","source":"saved-pc.html"}`, `{"variant":"mobile","source":"saved-mobile.html"}` 형태나 pc/mobile 표시가 있는 파일명을 사용합니다. 값이 없으면 site의 `url`을 직접 가져와 검사합니다.

```powershell
python scripts\widget_integration_probe.py `
  --sites contracts\representative_sites.example.json `
  --api-base-url https://ai-search.haeorumgift.com `
  --output logs\widget-integration-probe.json `
  --markdown-output logs\widget-integration-probe.md `
  --snippets-output-dir logs\widget-snippets
```

JSON/Markdown의 `data_auto_init_ready`와 `pages_ready_for_data_auto_init`은 추천 스니펫을 그대로 붙였을 때 위젯 script/CORS API 호출이 CSP에 막히지 않고 selector가 모호하지 않은지를 요약합니다. `--allow-fallback-floating`을 쓰면 검색창이 없거나 추천 selector가 중복 ID/class 때문에 모호한 페이지에서는 selector 없는 플로팅 스니펫을 대안으로 추천합니다. `--snippets-output-dir`은 페이지별 `data-hai-auto-init` HTML 조각, 저장 HTML에 스니펫을 삽입한 `previews/*.preview.html`, preview 삽입 marker와 `data-hai-auto-init` 중복 여부 및 추천 `attachToSearchInput`/`attachAfterSelector`가 preview DOM에서 각각 1개 요소만 잡는지 확인한 `preview-validation.json`/`.md`, `manifest.json`, `manual-install-plan.json`, `manual-install-plan.md`를 따로 쓰며, 설치 계획에는 페이지별 삽입 모드, selector 신뢰도, 수동 검토 여부, CSP allowlist 힌트, fallback floating 사용 여부가 남습니다. 이 manifest에는 `review_required=true`, `not_operational_readiness=true`, `contains_public_api_keys=true`가 남습니다. `blocking_risks`가 비어 있지 않으면 운영 사이트 삽입 전 CSP allowlist, API/widget URL, 검색창 selector를 먼저 고쳐야 합니다.
운영 evidence collector dry-run도 `representative_sites_config`에 로컬 `widget_probe_source`가 있으면 저장 HTML을 먼저 분석해 `data_auto_init_ready=false`, 검색창 미검출, 숨김/비활성/읽기전용 검색창 후보, CSP 차단, unsafe selector를 `invalid_input_files` blocker로 표시합니다. 저장 HTML을 제공했는데 PC 또는 모바일 variant가 빠진 경우도 `saved PC/mobile coverage incomplete`로 막습니다. 추천 스니펫을 저장 HTML에 삽입한 preview 기준으로도 marker와 `data-hai-auto-init` 개수가 맞지 않으면 `preview validation failed`로 차단하므로, 기존 위젯 script가 이미 붙은 템플릿에 같은 스니펫을 중복 설치하는 실수를 dry-run에서 잡을 수 있습니다. 이 단계는 실제 대표 사이트 접속 전 오프라인으로 막을 수 있는 템플릿 문제를 찾기 위한 보조 검증입니다.

위젯은 기존 검색창 옆 카메라 아이콘 트리거, 텍스트 입력, 이미지 업로드, 드래그 앤 드롭, 미리보기, 업로드 이미지 삭제, 로딩 표시, 오류 표시, Top 3, 카테고리 추천, 관련 상품 그리드와 더보기를 포함합니다. 브라우저에서 파일 형식, 용량, 이미지 로드 가능 여부, `minImageDimension` 최소 가로/세로 픽셀을 먼저 확인하고 서버에서도 같은 정책을 다시 검증합니다. `attachToSearchInput`을 쓰면 팝업을 열 때마다 현재 기존 검색창 값을 다시 읽어 AI 검색 입력창에 반영합니다. `target`/`attachToSearchInput`/`attachAfterSelector`를 지정하지 않은 최소 초기화에서는 `autoAttach: true` 기본값으로 `type=search`, `name=q`, `keyword`, `검색` 같은 대표 검색 input을 찾아 검색 버튼 뒤에 자동 삽입합니다. `init()`이 검색창 DOM보다 먼저 실행된 경우에는 `DOMContentLoaded`까지 한 번 기다린 뒤 같은 설정으로 다시 mount합니다. DOM 준비 후에도 검색창이 JS로 늦게 렌더링되면 `MutationObserver`로 최대 `mountWaitMs` 밀리초 동안 mount target을 기다리며, 기본값은 3000ms입니다. 같은 페이지에서 `init()`이 반복 호출되면 기존 루트와 모달을 교체해 버튼/팝업 중복을 방지합니다. 명시 selector가 끝까지 나타나지 않으면 자동 부착으로 숨기지 않고 초기화 실패 또는 대표 사이트 검증 실패로 드러납니다. `#ctl00:keyword`, `#legacyForm #ctl00:keyword`처럼 CSS selector로는 예외가 날 수 있는 기존 템플릿 ID도 ID 부분을 escape해서 다시 찾고, 단순 ID selector라면 `getElementById` fallback도 사용합니다. 가맹점 템플릿에 맞춰 `triggerTitle`, `triggerAriaLabel`, `accentColor`, `accentSoftColor`, `accentTextColor`, `zIndex`로 툴팁, 접근성 label, 주요 색상/레이어 우선순위를 조정할 수 있습니다. 기존 템플릿에서 `site_id` 용어를 쓰면 `siteId` 옵션을 넘겨도 위젯이 내부 `mallId`로 정규화합니다.

## 테스트

FastAPI 없이도 핵심 검색/동기화 로직은 표준 `unittest`로 검증할 수 있습니다.

```powershell
python -m unittest discover examples\HaeorumAISearch\tests
```

API 요청/응답 계약 fixture는 아래 명령으로 확인합니다.

```powershell
python examples\HaeorumAISearch\scripts\contract_check.py
```

이 검사는 텍스트/이미지/혼합 검색 fixture, 검색 응답 예시, `contracts/openapi.json`의 주요 경로와 스키마, 배포 구성 파일을 함께 검증합니다.

문서의 1차 인수 기준을 로컬 샘플 데이터 기준으로 점검하려면 아래 스크립트를 실행합니다.

```powershell
python examples\HaeorumAISearch\scripts\acceptance_check.py
```

이 스크립트는 텍스트 검색, 한 글자 오타 검색, 이미지 검색, 혼합 검색, Top 3, 카테고리 추천, 관련 상품 리스트, 상품 URL 생성, active 필터, 가맹점 API key 설정, 합성 1,700개 가맹점 설정 생성/검증, 동기화 로그, 100-query 로컬 성능 스모크를 확인합니다.

로컬 인수 검사를 한 번에 실행하고 JSON/Markdown 증거로 남기려면 아래 래퍼를 사용합니다. 이 리포트는 `local_only=true`, `not_operational_readiness=true`로 표시되며 운영 readiness 증거를 대체하지 않습니다. 로컬 래퍼에는 검색/이미지/혼합 검색, 동기화, 검색 인사이트 품질 루프, 운영 번들 템플릿이 필수 파일, 설치 명령, placeholder 교체 안내, env/config 변수명 정합성, 로컬 데모 key 제거 기준을 만족하는지 확인하는 `operational_bundle_check.py`도 포함됩니다.

```powershell
python examples\HaeorumAISearch\scripts\local_acceptance.py `
  --output examples\HaeorumAISearch\logs\local-acceptance.json `
  --markdown-output examples\HaeorumAISearch\logs\local-acceptance.md
```

문서의 1차 인수 기준과 개발 산출물별 증거 상태를 감사하려면 아래 명령을 실행합니다. 운영 readiness 리포트가 아직 없으면 각 항목은 로컬 증거만 있는 `local_only` 상태로 남습니다. `evidence-collection-plan.json`이 있으면 placeholder secret, 누락 CSV/config, 보안 설정 파일 같은 수집 전 blocker를 상단 `Operational Blockers` 요약과 요구사항별 `Collection Blockers` 열에 함께 표시합니다. 최종 납품 판정은 JSON top-level의 `completion_ready=true`와 `ok=true`를 함께 확인하며, 이 값은 모든 요구사항 통과, local acceptance 최신성, `operational-readiness.json ok=true`, evidence collection 완료가 모두 충족될 때만 true가 됩니다.

```powershell
python examples\HaeorumAISearch\scripts\requirements_audit.py `
  --evidence-collection-report examples\HaeorumAISearch\logs\evidence-collection-plan.json `
  --output examples\HaeorumAISearch\logs\requirements-audit.json `
  --markdown-output examples\HaeorumAISearch\logs\requirements-audit.md `
  --blocker-checklist-output examples\HaeorumAISearch\logs\requirements-blockers.md
```

운영 담당자에게 넘길 handoff 산출물을 반복 생성하려면 아래 래퍼를 사용합니다. 이 명령은 local acceptance, 로컬 `quality-report.json`, `widget-dom.json`, `csv-index.json` 갱신, `server-db-intake.md` 템플릿 검사, production compose 포트 노출 검사, 운영 장애 시나리오 점검, 운영 evidence dry-run, readiness, requirements audit, operational bundle 생성, bundle check를 순서대로 실행합니다. 생성 번들에는 서버/DB 인수 양식, `server_db_intake_check.py`, `compose_exposure_check.py`, `go_live_scenario_check.py`, Gemini+Marqo 런타임 기준, 운영 장애 시나리오표, 운영 위험표, 서버 82 runbook이 포함됩니다. 로컬 품질/위젯/색인 리포트는 최신 소스 기준 blocker 설명을 남기기 위한 것이며 운영 품질·대표 사이트·영구 색인 증거를 대체하지 않습니다. 운영 secret과 실제 `/etc`, `/data`, `/var/log` 증거가 아직 없으면 readiness/audit는 실패로 남는 것이 정상이며, 최종 `handoff_ok=true`, `operational_signoff_ok=false`는 “handoff 묶음은 준비됐지만 운영 완료 증거는 아직 없음”을 뜻합니다.

```powershell
python examples\HaeorumAISearch\scripts\prepare_handoff.py `
  --output examples\HaeorumAISearch\logs\handoff-report.json `
  --markdown-output examples\HaeorumAISearch\logs\handoff-report.md
```

기본 리포트는 운영 전달용으로 command exit/status와 요약만 남깁니다. 개별 스크립트 stdout/stderr tail까지 보존해 디버깅하려면 `--include-command-output`을 추가합니다.

위젯이 대표 가맹점 설정에서 기존 검색창 selector 또는 자동 감지 검색창 뒤에 AI 버튼을 삽입하고 기존 검색어를 팝업에 미리 채우며, 팝업 재오픈 시 바뀐 기존 검색어를 다시 반영하고, `mallId`/`siteId`, 공개 API key, 사이트별 툴팁/색상/z-index를 유지하는 동작은 아래 스모크로 확인합니다. 이 스모크는 `mallId`/`siteId` 충돌 설정 및 URL 구조를 바꿀 수 있는 식별자 거절, 팝업 제목/안내/업로드/로딩 문구, JPG/PNG/WEBP 업로드 안내, Top 3/카테고리/관련 상품 섹션 제목, Escape 닫기와 트리거 포커스 복귀, Tab 포커스 순환, 검색 진행 중 중복 submit 차단, 카테고리 추천 버튼 재검색, 관련 상품 더보기 offset, 이미지 미리보기/삭제, 검색 실패 시 stale 결과 정리와 429 안내, 상품 이미지 링크와 `상세 보기` 링크의 상세 URL 및 클릭 로그 payload, 검색 당시 `query_type` 보존도 함께 점검합니다.

```powershell
node examples\HaeorumAISearch\scripts\widget_dom_check.js
```

PoC 품질/응답속도 리포트는 아래 명령으로 생성합니다. `sample_products.csv`는 로컬 PoC 게이트를 재현할 수 있도록 활성 상품 300개 이상과 권장 카테고리 분포를 포함합니다. 운영에서는 실제 MSSQL/상품 관리 시스템에서 추출한 CSV를 Marqo 인덱스에 색인한 뒤 같은 `--strict --min-products 300 --engine marqo` 조건, 텍스트 3초, 이미지/혼합 5초 응답시간 기준, 그리고 `--cases /etc/haeorum-ai-search/quality-cases.json`에 정의한 실제 PoC 텍스트 2개 이상, 이미지-only 1개 이상, 혼합 1개 이상 케이스를 통과시켜야 합니다. 이미지-only와 혼합 케이스는 `image_path`가 실제 기준 이미지 파일을 가리켜야 하며 readiness는 `image_cases_with_file_source >= 1`, `mixed_cases_with_file_source >= 1`만 운영 품질 이미지 증거로 인정합니다. 텍스트 케이스 중 최소 1개는 오타/동의어/표현 변형 검색을 검증하도록 `typo_or_synonym` 태그를 붙입니다. positive case는 기대 카테고리 또는 기대 1위 상품과 `expected_min_results >= 3`을 검증해야 하며, 최소 1개는 저품질 또는 엉뚱한 기준 이미지로 `expected_low_confidence=true`를 검증해야 운영 readiness 증거로 인정됩니다. `quality_report.py`의 전체 `ok`도 이 case contract가 통과해야만 true가 됩니다.

```powershell
python examples\HaeorumAISearch\scripts\quality_report.py `
  --csv examples\HaeorumAISearch\sample_products.csv `
  --engine local `
  --strict `
  --min-products 300 `
  --max-text-ms 3000 `
  --max-image-ms 5000 `
  --max-mixed-ms 5000 `
  --json-output examples\HaeorumAISearch\logs\quality-report.json `
  --markdown-output examples\HaeorumAISearch\logs\quality-report.md
```

운영 MSSQL 또는 상품 관리 시스템에서 추출한 전체 상품 CSV로 300개 PoC 샘플을 균형 있게 만들고 결격 사유를 점검하려면 아래 스크립트를 사용합니다.

```powershell
python examples\HaeorumAISearch\scripts\poc_dataset_builder.py `
  --csv exports\haeorum-products-full.csv `
  --target-size 300 `
  --min-products 300 `
  --min-per-category 10 `
  --output-csv examples\HaeorumAISearch\logs\poc-products.csv `
  --report-output examples\HaeorumAISearch\logs\poc-dataset.json
```

이 리포트는 활성 상품 수, 선택된 상품 수, 권장 카테고리 누락, 카테고리별 최소 수량 부족, 이미지 URL 누락, HTTP/credential/내부망 같은 unsafe 대표 이미지 URL, 중복 상품번호를 확인합니다.

PoC CSV를 실제 인덱스에 반영하기 전에는 전용 색인 스크립트로 파싱 요약을 먼저 확인합니다. `--dry-run`은 색인을 건드리지 않고 active/inactive 수, 이미지 누락, 카테고리 분포를 리포트로 남깁니다. CSV 경로가 잘못되면 빈 데이터 성공으로 처리하지 않고 `ok=false`, `failed=1`, `error`를 포함한 리포트를 남깁니다.

```powershell
python examples\HaeorumAISearch\scripts\csv_index.py `
  --csv examples\HaeorumAISearch\logs\poc-products.csv `
  --engine marqo `
  --index-name haeorum-products-poc `
  --marqo-url http://marqo-host:8882 `
  --dry-run `
  --output examples\HaeorumAISearch\logs\csv-index-dry-run.json `
  --markdown-output examples\HaeorumAISearch\logs\csv-index-dry-run.md
```

드라이런과 품질 리포트가 통과한 PoC CSV는 같은 스크립트에서 `--dry-run`을 제거하고 `--mode reindex`로 색인합니다. 운영 색인 증거에는 `--validate-images`를 붙여 대표 이미지 다운로드/검증을 실제 실행해야 하며, `csv_index.py` 요약도 active 상품의 unsafe/non-HTTPS 대표 이미지 URL을 0건으로 확인합니다. reindex 직후 `csv_index.py`는 인덱스 stats를 다시 읽어 `post_index_document_count_ok`를 기록하므로, 같은 인덱스에 stale 문서가 남으면 색인 리포트 자체가 실패합니다. 로컬 엔진(`--engine local`)은 프로세스 메모리 검증용이라 영구 인덱스를 만들지 않습니다. 운영 readiness는 `csv-index.json`의 `engine=marqo`도 직접 확인하므로, 영구 인덱스처럼 보이는 non-Marqo/예비 어댑터 리포트는 운영 색인 증거가 될 수 없습니다.

```powershell
python examples\HaeorumAISearch\scripts\csv_index.py `
  --csv examples\HaeorumAISearch\logs\poc-products.csv `
  --engine marqo `
  --index-name haeorum-products-poc `
  --marqo-url http://marqo-host:8882 `
  --mode reindex `
  --validate-images `
  --output examples\HaeorumAISearch\logs\csv-index.json
```

실행 중인 API 서버를 대상으로 HTTP 스모크 테스트를 하려면 아래처럼 실행합니다.
이 스모크는 검색/클릭 로그 CORS preflight 허용/거절, 텍스트 검색, `site_id` 별칭과 충돌 거절, JSON base64 이미지 검색, 용량 초과 JSON 이미지 본문 413 거절, `multipart/form-data` 이미지 업로드 검색, 비이미지/손상/용량 초과/깨진 multipart 거절, 혼합 검색, 배포된 OpenAPI의 클릭 로그 429 계약과 도메인 필터 필드, 공개 API key/Origin/payload/도메인 필터/깨진 JSON 거절, URL/JSON/multipart의 공개 API key 및 관리자 key 별칭 거절, 클릭 로그와 관리자 상태/검색 로그/동기화 로그/오류 로그/검색 인사이트/JSON 메트릭/Prometheus 메트릭, 관리자 로그 응답의 key/data URL/base64 이미지 payload redaction, 잘못된 관리자 key 401 거절, 관리자 상태 엔드포인트의 URL key alias 400 거절, 잘못된 관리자 key로 동기화/전체 재색인/단일 상품 재색인/상품 삭제 API가 모두 401로 보호되는지 확인합니다. 성공 검색 응답은 `meta`, Top 3 개수, 1개 이상의 관련 상품 리스트, 1개 이상의 카테고리 추천, 상품번호/상품명/카테고리/가격/이미지/상세 URL/유사도 필드까지 검사합니다. 운영 readiness 증거로 쓰려면 `--base-url`은 HTTPS 비로컬 API 주소, `--origin`은 HTTPS 가맹점 origin이어야 하며 `--admin-key`를 반드시 지정합니다. readiness는 검색 응답의 `meta.engine=marqo`, `/admin/metrics`의 `engine.backend=marqo`, `/admin/sync-status`의 `engine=marqo`와 non-empty `index`도 확인하므로 local engine/API 스모크는 운영 증거가 될 수 없습니다. 클릭 로그 rate limit 증거는 격리된 mall/key 또는 staging에서 `--expect-click-rate-limit --click-rate-limit-probe-count <configured_click_rate_limit_plus_1>`로 남깁니다. 운영 이미지 제한을 10MB보다 크게 바꾼 경우에는 `--oversized-upload-mb`를 제한보다 큰 값으로 지정합니다.
`api_smoke_test.py`와 `load_test.py`는 성공 검색 응답의 `meta.mall_id`와 모든 결과 상품 `mall_id`가 요청 `mall_id` 또는 `site_id`와 같은지도 확인합니다. 실행 전에는 `--base-url`과 `--origin`의 credentials, query/fragment, 공백/역슬래시, 잘못된 port, non-public host를 거절하고 리포트의 `target_validation.ok` 증거를 남깁니다. `localhost`나 사설망 대상 로컬 리허설에는 `--allow-local-target`을 명시하고, 운영 evidence에는 이 플래그를 넣지 않습니다. `load_test.py --mall-sample-size N --mall-config malls.json`를 쓰면 혼합 트래픽 요청을 여러 enabled mall의 API key, Origin, 상품 URL prefix로 순환시켜 단일 mall 반복 부하로는 놓치는 1,700몰 인증/Origin/URL 규칙 문제를 같이 검증합니다. 운영 readiness와 API scale 비교는 이 검증 결과가 리포트의 `base_url`/`origin`과 일치해야 통과합니다.
OpenAPI의 `additionalProperties=false` 계약도 운영에서 확인하므로, JSON 검색 요청과 클릭 로그 요청에 알 수 없는 필드가 포함되면 `unsupported_json_field_rejected`, `unsupported_click_field_rejected` smoke check가 400 응답을 요구합니다.

클릭 로그의 `product_url`은 안전한 절대 HTTP(S) URL이어야 하고, 서버가 해당 mall의 `product_url_template`과 같은 scheme/host/port 및 `{product_id}` 앞 path/query prefix인지 다시 확인합니다. 운영 API smoke는 외부 도메인 주입을 `foreign_click_product_url_rejected`, 같은 도메인의 잘못된 상세 URL path/query prefix를 `click_product_url_template_prefix_mismatch_rejected`로 확인합니다.
아래 `replace-with...` 값은 실행 전에 실제 공개 API key와 관리자 key로 교체해야 합니다.

```powershell
python examples\HaeorumAISearch\scripts\api_smoke_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key replace-with-public-api-key `
  --origin https://shop001.haeorumgift.com `
  --admin-key replace-with-admin-key `
  --allow-local-target
```

간단한 텍스트 검색 부하 스모크는 아래 명령으로 확인합니다.

```powershell
python examples\HaeorumAISearch\scripts\load_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key replace-with-public-api-key `
  --admin-key replace-with-admin-key `
  --origin https://shop001.haeorumgift.com `
  --mode text `
  --requests 100 `
  --concurrency 100 `
  --p95-ms 3000 `
  --p99-ms 4800 `
  --request-timeout-seconds 10 `
  --max-server-wait-avg-ms 600 `
  --min-rps 8.3 `
  --allow-local-target
```

이미지/혼합 검색은 같은 스크립트의 `--mode image`, `--mode mixed`로 측정합니다. 운영 readiness 증거는 생성된 placeholder PNG가 아니라 `--image-file`로 넘긴 실제 기준 이미지 파일만 인정합니다.

```powershell
python examples\HaeorumAISearch\scripts\load_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key replace-with-public-api-key `
  --admin-key replace-with-admin-key `
  --origin https://shop001.haeorumgift.com `
  --mode image `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 90 `
  --concurrency 30 `
  --p95-ms 5000 `
  --p99-ms 8000 `
  --request-timeout-seconds 16 `
  --max-server-wait-avg-ms 1000 `
  --min-rps 1.5 `
  --max-error-rate 1 `
  --allow-local-target

python examples\HaeorumAISearch\scripts\load_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key replace-with-public-api-key `
  --admin-key replace-with-admin-key `
  --origin https://shop001.haeorumgift.com `
  --mode mixed `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 90 `
  --concurrency 30 `
  --p95-ms 5000 `
  --p99-ms 8000 `
  --request-timeout-seconds 16 `
  --max-server-wait-avg-ms 1000 `
  --min-rps 1.5 `
  --max-error-rate 1 `
  --allow-local-target
```

`--p99-ms`를 생략하면 `--p95-ms`의 1.6배가 자동 적용됩니다. `--request-timeout-seconds`를 생략하면 `max(10초, p99의 2배)`가 적용되고 운영 증거에서는 `max(10초, p99의 3배)`를 넘는 timeout을 실패 처리합니다. `--max-server-wait-avg-ms`를 생략하면 `max(250ms, p95의 20%)`가 적용됩니다. `--min-rps`를 생략하면 `max(1 RPS, concurrency / p95초 * 0.25)`가 적용됩니다. 운영 증거에는 텍스트 4800ms/10초/600ms/8.3RPS, 이미지/혼합 8000ms/16초/1000ms/1.5RPS, 850-user mixed traffic 8000ms/16초/1000ms/5RPS처럼 p99, 요청 timeout, 내부 평균 wait, 처리량 기준을 명시적으로 남겨 tail latency와 queue/cache 대기 포화가 숨지 않게 합니다.

운영 전에는 위 값을 기준으로 텍스트 검색 100 concurrent, 이미지/혼합 검색 30 concurrent 시나리오를 실제 서버에서 측정해야 합니다. `--admin-key`를 지정하면 부하 테스트 리포트에 `/admin/metrics` 전후 스냅샷이 포함되어 API 서버 CPU/RAM, 디스크 사용률, 검색 이벤트 증가분, `engine_backend`, `engine_index`, `marqo_model`, `embedding_backend`, Gemini 모델/차원, engine health cache 상태, Marqo/Gemini backend HTTP request attempts·connections opened·connection reuses·stale reconnects·error responses·connection-close responses·elapsed ms·request body bytes, Gemini query vector runtime cache quota/entry/wait/timeout, rate limit backend/Redis fallback, 검색 캐시 backend/Redis/TTL/오류 카운터, cache clear 오류 카운터, Redis cache miss lock claim/contention/error/release-error/wait-timeout, singleflight wait/timeout, 검색/이미지 queue full 및 wait 이벤트와 총 wait ms delta를 readiness 증거로 남깁니다. 부하 리포트와 Markdown은 Gemini alias(`backend_gemini_*`, `gemini_query_vector_*`)를 사용합니다. 텍스트 검색은 Marqo rerank가 이미 가져온 후보 창을 `product_group_id` collapse에 재사용하므로, 옵션 중복이 많아도 같은 `/search` 재호출이 불필요하게 늘지 않아야 합니다. 부하 리포트에는 전체 지연시간 외에 `expected_query_type_latency_ms`와 `response_query_type_latency_ms`가 함께 들어가므로 mixed traffic에서 text/image/text_image 중 어떤 경로가 p95 또는 p99를 끌어올리는지 바로 확인합니다. readiness/API scale 게이트는 각 query type의 응답 count, p95, p99, max가 누락되거나 query type별 p95 또는 p99가 부하 임계값을 넘으면 전체 p95가 통과해도 실패 처리합니다. 검색 요청 부하 클라이언트도 worker thread별 keep-alive 연결을 재사용하고 stale 연결은 1회 재연결하며 `client_transport.search_requests.connections_opened`/`connection_reuses`/`request_attempts`/`requests_sent`/`stale_reconnects`와 `gzip_responses`/wire bytes/decoded bytes를 남기므로, 테스트 도구의 TCP 재연결 비용이나 공개 API gzip 미적용이 서버 p95/p99 판단을 왜곡하는지 확인할 수 있습니다. 실행 delta에서 `backend_marqo_error_responses`, `backend_marqo_connection_close_responses`, Gemini compatibility key인 `backend_gemini_error_responses`, `backend_gemini_connection_close_responses`, `gemini_query_vector_wait_timeouts`, `cache_lock_errors`, `cache_lock_release_errors`, `cache_lock_wait_timeouts`, `singleflight_wait_timeouts`가 증가하면 backend HTTP 안정성, Gemini query vector coalescing, Redis miss lock, 또는 같은 프로세스 duplicate-miss coalescing이 부하 중 깨진 증거로 보고 실패합니다. readiness 게이트는 요청 수, 동시성, HTTPS 비로컬 `base_url`, HTTPS 가맹점 `origin`, 850 active user 혼합 트래픽의 text/image/mixed 포함 여부, `mode_counts`와 `response_contract.query_type_counts`/`expected_query_type_counts`의 일치 여부, query type별 latency, 오류율, p95/p99 지연시간, `response_contract.ok=true`, 모든 성공 응답의 `meta.engine=marqo`, 실제 파일 기반 `image_input.source=file`/`files`, `engine_backend=marqo`, 인덱스/모델/임베딩 백엔드 식별자, 성공 응답 수 이상으로 증가한 `search_events`, 이미지/혼합 성공 응답 수 이상으로 증가한 `image_search_events`, rate limit fallback 증가분, 검색 캐시 오류 및 cache clear 오류 카운터 존재 여부도 직접 확인합니다. `/admin/metrics`의 검색 이벤트 수는 최근 tail 요약이므로 로그가 포화된 경우 같은 실행의 `/admin/search-log` 기반 `server_metrics.run_log_coverage`가 성공 응답 수 이상 이벤트를 증명하면 검색 이벤트 delta undercount를 보완할 수 있습니다. 장기 모니터링은 `/admin/metrics.prom`을 Prometheus scrape 대상으로 등록합니다. Marqo 서버 자원은 별도 증거로 남깁니다. API 서버 1대/2대 비교 증거를 만들 때는 같은 HTTPS API 대상, 같은 `mall_id`/`origin`, 같은 `mixed-traffic`, 같은 이미지 파일 조건, 같은 `engine_index`/`marqo_model`/`embedding_backend` 조건을 각각 `--api-server-count 1`, `--api-server-count 2`로 실행한 뒤 `load_compare.py`로 `api-scale.json`을 생성합니다. 최종 readiness는 API smoke, load, API scale, 대표 사이트, security 증거의 API base URL이 같은 운영 API 대상을 가리키는지, 단일 가맹점 대상 API smoke/load/API scale 증거의 `origin`과 `mall_id`가 같은지도 함께 비교합니다.
API 서버 2대 이상 리포트는 공개 응답 header의 `api_instance_coverage`뿐 아니라 관리자 메트릭 수집 source도 분산돼야 합니다. 각 API 인스턴스의 직접 관리자 주소를 `--admin-metrics-base-url`로 반복 지정하면 `load_test.py`가 `/admin/metrics`와 `/admin/search-log`를 인스턴스별로 수집해 합산하고, 내부 전용 관리자 주소를 쓰는 경우 `--allow-private-admin-metrics-targets`를 함께 지정합니다. `server_metrics.admin_metrics_source_coverage`에는 source 수와 distinct `process.instance_id`가 남으며, 이 값이 `--api-server-count`보다 작으면 readiness/API scale은 로드밸런서 뒤 한 인스턴스만 측정한 증거로 보고 실패합니다.

부하 실행 중 `rate_limited_events`, `rate_limit_fallback_events`, `rate_limit_redis_backoff_*`, `cache_error_count`, `cache_clear_errors`, `cache_redis_backoff_*`, `search_queue_full_events`, `image_queue_full_events` delta가 0보다 크면 readiness/API scale은 실패합니다. 또한 `cache_lock_run_avg_wait_ms`, `singleflight_run_avg_wait_ms`, `gemini_query_vector_run_avg_wait_ms`, `search_queue_run_avg_wait_ms`, `image_queue_run_avg_wait_ms`가 `max_server_wait_avg_ms`를 넘으면 아직 timeout이 발생하지 않았더라도 내부 대기 포화로 간주합니다. 부하 종료 후 `singleflight_in_flight`, `search_queue_in_flight`, 이미지 부하의 `image_queue_in_flight`, Gemini compatibility key인 `gemini_query_vector_in_flight`가 남아 있거나 API 서버 `system_memory_used_percent`/`disk_used_percent`가 85% 이상이면 지연시간과 RPS가 통과해도 실패합니다. `/admin/metrics`의 before/after `process_memory_rss_bytes` 차이가 `max_process_rss_growth_mb`(기본 512MiB)를 넘는 리포트도 메모리 증가 위험으로 실패 처리합니다. 일부 요청이 성공했더라도 rate limit 오설정, Redis fallback/backoff, 캐시 장애, 검색 queue 포화, 작업 잔류, 리소스 포화, 프로세스 RSS 증가가 실제 부하 중 발생한 것이므로 납품 전에는 설정이나 서버 수를 조정한 뒤 같은 증거를 다시 만들어야 합니다.

`response_contract.ok=true`만으로는 운영 부하 증거가 인정되지 않습니다. readiness, collector dry-run, API scale 비교는 성공 응답의 Marqo engine 분포뿐 아니라 유효 성공 응답 수, `invalid_successful_responses=0`, 최소 Top/관련상품/카테고리 수, `mode_counts` 대비 실제 query type coverage를 다시 계산합니다.

운영 API 접근 전 로컬 리허설로 `python scripts\operational_simulation.py --output-dir logs\simulation-latest`를 실행하면 `mssql-export.json`에 가상 MSSQL export와 1,700개 mall config 기준 `mall_config_alignment`가 남아 상품 URL이 각 mall의 `product_url_template` prefix에 맞는지 미리 볼 수 있습니다. `mixed-weight-sweep.json`은 혼합 검색의 text/image 가중치 후보를 aligned/off-topic/conflicting 케이스로 비교해 현재 기본값이 로컬 synthetic case에서 경쟁력이 있는지와 충돌 민감도를 기록합니다. `local-search-load-probe.json`에는 text/image/text_image별 지연 통계, 시나리오별 p95/p99, 처리량, 느린 샘플이 남습니다. 로컬 검색 엔진은 strict `mall_id` 검색에서 mall별 버킷을 먼저 선택하므로 1,700개 mall 시뮬레이션에서도 대상 mall 상품만 가시성 검사를 수행하며, `operational-risk-probes.json`의 `local_engine_mall_bucket_*` 체크가 이 동작을 검증합니다. 이 값은 운영 readiness 증거가 아니라 병목 후보와 데이터 정책 오류를 미리 좁히는 참고 리포트입니다.

`quality_report.py`와 `csv_index.py`를 번들 샘플 CSV(`sample_products.csv`)로 실행한 결과는 로컬 회귀 확인용으로만 인정됩니다. 샘플 파일을 다른 경로로 복사하거나 `poc_dataset_builder.py`로 다시 저장해도 상품 ID가 번들 샘플과 90% 이상 겹치면 `local_only=true`, `not_operational_readiness=true`, `source.dataset_is_builtin_sample_derived=true`가 기록되며, 운영 readiness는 이를 실제 PoC/운영 품질·색인 증거로 통과 처리하지 않습니다. 운영 `mssql-export.json`은 `rows_read >= exported_products`, `active_products + inactive_products == exported_products`, `inactive_products > 0`, `source_deletion_signal_ok=true`, `mall_config_alignment.active_products_checked == active_products`처럼 행 수와 삭제/비노출 신호 및 mall 대조 수가 서로 맞아야 합니다. 운영 `poc-dataset.json`은 `selected_categories`가 권장 카테고리별 최소 수량을 충족해야 하며, 특정 카테고리로 편향된 300개 CSV는 `selected_missing_recommended_categories` 또는 `selected_thin_recommended_categories`로 차단됩니다. PoC로 선택된 active 상품의 대표 이미지는 누락뿐 아니라 unsafe URL과 HTTP URL도 0건이어야 합니다. 운영 `quality-report.json`은 기본 내장 케이스가 아니라 `quality-cases.json`의 실제 PoC 케이스를 사용해야 하고, 이미지-only와 혼합 검색 케이스는 실제 기준 이미지 파일을 `image_path`로 포함해야 합니다. collector dry-run은 `products_csv`와 `poc_products_csv`가 둘 다 있으면 PoC 상품번호가 전체 export에 존재하는지, hidden/inactive 상품을 active로 되살리지 않았는지, `category_name`, `main_image_url`, `product_url`, `mall_id`가 원본 export와 달라지지 않았는지도 먼저 비교합니다. collector dry-run은 `quality.cases_file`이 있으면 케이스 타입 수, 오타/동의어 텍스트 케이스, 저신뢰 이미지 케이스, `expected_min_results`, 참조 `image_path` 존재 여부를 먼저 검증하고, 기대 카테고리와 기대 상위 상품이 실제 `--mall-id`로 실행할 대표 mall의 active PoC 상품에 속하는지도 대조합니다. base64/data URL로 직접 넣은 이미지 케이스는 로컬 재현에는 쓸 수 있지만 운영 readiness의 `image_cases_with_file_source`/`mixed_cases_with_file_source` 요구를 충족하지 않습니다. 운영 bundle check와 readiness는 이 템플릿에 텍스트 2개 이상, 이미지-only 1개 이상, 혼합 검색 1개 이상이 있고, 텍스트 케이스 중 최소 1개가 `typo_or_synonym` 태그로 오타/동의어/표현 변형을 검증하며, positive case가 `expected_min_results >= 3` 및 기대 카테고리 또는 기대 상위 상품을 검증하고, 최소 1개 저품질/엉뚱한 이미지 case가 `expected_low_confidence=true`를 검증하는지 확인합니다. 운영 증거는 `/data/haeorum-ai-search/poc-products.csv`처럼 실제 MSSQL export에서 만든 300개 이상 PoC CSV로 다시 생성해야 하며, `mssql-export.json`/`image-url-check.json`/`poc-dataset.json`/`quality-report.json`/`csv-index.json`의 CSV SHA256 fingerprint가 같은 전체 상품 CSV와 같은 PoC CSV 계보를 증명해야 합니다. `quality-report.json`, `csv-index.json`, `marqo-resource.json`은 모두 같은 Marqo URL/index를 가리켜야 하며, 품질/색인 리포트의 Marqo model도 같아야 합니다.

850 active user 기준의 전체 혼합 트래픽은 `mixed-traffic` 시나리오와 같은 실제 `--image-file`로 별도 리포트를 남깁니다.

운영 API/동기화 서버에는 Python 패키지, MSSQL ODBC 드라이버, Docker/Compose, CPU/RAM/디스크, Linux 배포판 기준을 먼저 확인하는 preflight 증거를 남깁니다. 기본 지원 기준은 Ubuntu 20.04+, Debian 11+, RHEL/CentOS/Rocky/Alma/Oracle Linux 8+입니다. 운영 readiness는 `--require-docker`, `--require-compose`, `--require-pyodbc`를 붙이고 `--allow-non-linux`/`--allow-unsupported-os` 없이 실행한 리포트만 통과로 인정합니다.

```powershell
python examples\HaeorumAISearch\scripts\server_preflight_check.py `
  --role api `
  --require-docker `
  --require-compose `
  --require-pyodbc `
  --expected-odbc-driver "ODBC Driver 18 for SQL Server" `
  --output examples\HaeorumAISearch\logs\server-preflight.json
```

서비스 env 파일은 서버 preflight와 별개로 `env_check.py`로 검증합니다. 서버 preflight는 운영 API/Marqo 역할에서 Linux 배포판, Python/ODBC/Docker, CPU/RAM/디스크와 함께 현재 open-file limit이 65535 이상인지 확인합니다. systemd 서비스 파일은 `security_check.py`에서 `LimitNOFILE=65535` 이상을 요구하므로, 배포 뒤에는 `systemctl daemon-reload`와 서비스 재시작 후 preflight를 다시 실행해야 실제 런타임 한도 증거가 맞습니다. `env_check.py`는 `marqo_url` 항목으로 내부 Marqo endpoint가 credentials/query/fragment/공백/역슬래시/잘못된 port/link-local/unspecified host 없는 절대 HTTP(S) URL인지 확인합니다. `product_url_template` 항목으로 production 전역 상품 URL 템플릿의 공개 URL 안전성과 HTTPS 여부를 확인하고, `mall_security` 항목으로 enabled mall의 API key, `allowed_origins`, 상품 URL 템플릿, 전역 CORS 포함 여부를 구조화해 점검합니다. `sync_interval_hourly` 항목으로 `HAEORUM_SYNC_INTERVAL_SECONDS <= 3600`도 배포 전 blocker로 잡습니다. API 서버를 2대 이상으로 구성하는 배포에서는 `--api-server-count`를 실제 개수로 지정해 `HAEORUM_REDIS_URL` 누락을 배포 전 blocker로 잡습니다.

```powershell
python examples\HaeorumAISearch\scripts\env_check.py `
  --env-file examples\HaeorumAISearch\.env `
  --role api `
  --api-server-count 2 `
  --output examples\HaeorumAISearch\logs\env-check.json `
  --markdown-output examples\HaeorumAISearch\logs\env-check.md
```

Marqo 검색엔진 서버의 health, index stats, index settings 계약, Gemini health/텍스트+이미지 임베딩 프로브, Docker 컨테이너 CPU/RAM, Vespa 저장소 디스크 스냅샷은 다음처럼 수집합니다. 서버 호스트에서 직접 실행하는 운영 증거 명령은 loopback endpoint를 쓰고, Docker 컨테이너 env만 `marqo-api`/`gemini-embedding` 서비스 DNS를 씁니다. Gemini 백엔드에서는 `model=no_model`, `modelProperties.dimensions`, split-vector tensor fields, Gemini `/health`의 모델/차원과 실제 텍스트+이미지 `/embed` 프로브 차원이 운영 설정과 맞아야 하며, native 백엔드에서는 Marqo model과 이미지 URL 처리 설정이 맞아야 합니다. readiness는 CPU/RAM 또는 저장소 사용률이 지정 임계치를 넘는 resource evidence도 실패 처리합니다.

```powershell
python examples\HaeorumAISearch\scripts\marqo_resource_check.py `
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
  --output examples\HaeorumAISearch\logs\marqo-resource.json
```

```powershell
python examples\HaeorumAISearch\scripts\load_test.py `
  --base-url http://localhost:8000 `
  --mall-id shop001 `
  --api-key replace-with-public-api-key `
  --admin-key replace-with-admin-key `
  --origin https://shop001.haeorumgift.com `
  --scenario mixed-traffic `
  --active-users 850 `
  --traffic-mix text=70,image=10,mixed=20 `
  --image-file /data/haeorum-ai-search/quality-images/load-reference.jpg `
  --requests 850 `
  --concurrency 100 `
  --p95-ms 5000 `
  --max-error-rate 1 `
  --output examples\HaeorumAISearch\logs\load-mixed-traffic.json `
  --markdown-output examples\HaeorumAISearch\logs\load-mixed-traffic.md `
  --allow-local-target
```

부하 테스트는 2xx 응답만으로 성공 처리하지 않고 각 검색 응답의 `meta.query_type`/`elapsed_ms`/`engine`/`limit`/`offset`/`has_more`/`next_offset`/`mall_id`/`text_weight`/`image_weight`/`low_confidence`/`notice`, `top`, `items`, `suggested_categories`, 상품번호/상품명/카테고리/가격/이미지/상세 URL/유사도/`mall_id`/`source_scores` 필드 계약도 함께 검증합니다. 부하 중 응답 본문이 깨지거나 `top` 3개 제한을 넘거나 관련 상품/카테고리 추천이 비어 있거나 결과 필드가 누락되면 해당 요청은 오류로 집계됩니다. 리포트에는 `response_contract.ok`, 성공 응답의 `engine_counts`와 `non_marqo_engine_responses`, `/admin/metrics`의 `engine_backend`/`engine_index`/`marqo_model`/`embedding_backend`, 검색/이미지 queue 설정/포화 이벤트/대기 이벤트/총 대기 ms, `image_input`, `mode_counts`에서 계획한 text/image/mixed 요청 수와 대응되는 query type 분포, 유효 응답 수, 최소 Top/관련상품/카테고리 수가 남으며 readiness는 `response_contract.ok=true`, `non_marqo_engine_responses=0`, `engine_backend=marqo`, 인덱스/모델/임베딩 백엔드 식별자가 있는 리포트, 이미지 queue enabled 및 `image_queue_max_concurrency > 0`이고 이미지/혼합/850-user 리포트의 `image_input.source=file` 또는 `image_input.source=files`인 부하 리포트만 인정합니다.
성공 응답이 있는 부하 리포트에서 `server_metrics.delta.backend_marqo_request_attempts`가 0이면 캐시 hit만 측정했을 가능성이 있으므로 readiness와 API scale 비교가 실패합니다. 또한 리포트의 `request_profile.unique_request_signatures`와 `request_profile.min_backend_marqo_request_attempts` 기준보다 Marqo request attempt delta가 작으면, 고유 검색 조합 대부분이 사전 캐시에 가려진 증거로 보고 실패합니다. Gemini embedding backend의 이미지/혼합 성공 응답도 compatibility key인 `server_metrics.delta.backend_gemini_request_attempts`가 0이거나 `request_profile.min_backend_gemini_request_attempts`보다 작으면 실패해, 검색 성능 증거가 실제 Gemini backend 호출 경로를 포함하도록 합니다.
검색 로그에는 응답 결과의 `result_mall_ids`와 `result_mall_id_mismatch_count`도 기록됩니다. `HAEORUM_FILTER_BY_MALL_ID=true`인 운영에서는 Marqo 필터가 잘못 설정되어 다른 mall hit가 섞여도 서비스 레이어가 최종 응답에서 한 번 더 제거하며, 이 로그 필드로 격리 이상 신호를 추적할 수 있습니다.

API scale 입력 리포트도 같은 응답 형태 검증을 포함해야 합니다. `load_compare.py`가 생성하는 `response_shape`에는 유효/무효 성공 응답 수와 최소 Top/관련상품/카테고리 수가 남으며, readiness는 제출된 `api-scale.json`의 해당 값을 다시 계산합니다.

```powershell
python examples\HaeorumAISearch\scripts\load_compare.py `
  --single-report examples\HaeorumAISearch\logs\load-mixed-traffic-1-api.json `
  --multi-report examples\HaeorumAISearch\logs\load-mixed-traffic-2-api.json `
  --output examples\HaeorumAISearch\logs\api-scale.json `
  --markdown-output examples\HaeorumAISearch\logs\api-scale.md
```

운영 증거 파일을 모아 최종 readiness 리포트를 만들려면 아래 스크립트를 사용합니다. 이 리포트는 API 스모크, MSSQL 전체 CSV export, PoC dataset 생성, MSSQL View, 이미지 URL, PoC 품질/응답시간, PoC CSV 실제 색인, 1,700개 가맹점 설정, Marqo health/Gemini 텍스트+이미지 probe/CPU/RAM, 서버 preflight, 서버 자원 스냅샷이 포함된 텍스트/이미지/혼합/850 active user 부하 테스트, API 서버 1대/2대 비교, 대표 가맹점 위젯, 보안 체크 증거를 모두 요구합니다.

보안 증거 파일은 운영 환경 변수(`HAEORUM_ENV=production`)를 적용한 상태에서 아래처럼 생성합니다. MSSQL IP 제한은 방화벽/DB ACL 확인 후 `--mssql-ip-restricted`를 붙이고, `--nginx-config`, `--systemd-service`, `--sync-systemd-service`, `--reindex-systemd-service`, `--reindex-systemd-timer`, `--logrotate-config`는 실제 적용된 Nginx site/systemd/logrotate 설정 파일을 지정합니다. 보안 체크는 `--base-url`이 HTTPS 비로컬 공개 API 주소인지, 전역 CORS와 mall별 `allowed_origins`가 HTTPS origin인지, 전역/mall 상품 URL 템플릿이 HTTPS인지도 `security.json`에 남깁니다. `HAEORUM_SYNC_ALERT_WEBHOOK_URL`을 쓰는 운영에서는 보안 체크가 `https://` 절대 URL, credential 미포함, fragment/공백 미포함을 검증합니다. 이 값을 쓰지 않는 운영에서는 Prometheus/Grafana 등 외부 알림이 `sync_last_error`, `sync_product_failures`, `sync_batch_failures`, `sync_lock_contention`을 감시하도록 구성한 뒤 `--sync-alerting-configured`를 붙입니다. readiness는 `security.json`의 boolean 필드뿐 아니라 `failed_checks=[]`, 공개 base URL 상세 리포트, sync alerting 상세 근거, Nginx/systemd/logrotate/service env 권한 하위 리포트의 `ok=true`와 실제 `path`도 요구합니다.

```powershell
python examples\HaeorumAISearch\scripts\security_check.py `
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
  --output examples\HaeorumAISearch\logs\security.json `
  --markdown-output examples\HaeorumAISearch\logs\security.md
```

대표 가맹점 운영 사이트 증거는 `contracts/representative_sites.example.json`을 실제 URL/API key로 복사해 수정한 뒤 생성합니다. collector dry-run은 이 파일의 대표 사이트 수, HTTPS 비로컬 `url`/`origin`, 중복 `mall_id`/URL/origin/API key, API base URL, placeholder API key, 상품 URL prefix 형식을 먼저 검증하고, `mall_config`가 있으면 site별 API key fingerprint가 해당 mall 설정과 맞는지도 대조해 잘못된 설정을 `invalid_input_files` blocker로 표시합니다. 실제 검사는 먼저 site별 `url`, `origin`, API key, API base URL, 상품 URL 규칙 입력이 비어 있거나 `replace-with...` placeholder로 남아 있지 않은지 `site_config` 단계에서 확인합니다. API base URL은 query string 또는 fragment가 없는 HTTPS endpoint여야 하며 credentials, 공백, 역슬래시, non-public host, 잘못된 port를 포함하면 네트워크 검사 전에 실패합니다. `expected_product_url_prefix`는 query string 또는 fragment가 없는 HTTPS prefix여야 합니다. 설정이 유효할 때만 PC/모바일 HTML에 위젯 삽입 흔적, `widget_init`의 실제 `mallId`/`siteId` 설정값, inline `HaeorumAISearch.init(...)` 또는 `data-hai-auto-init="true"` script 자동 초기화 설정, 위젯 초기화 옵션의 `apiBaseUrl`이 운영 API base URL과 일치하거나 위젯 `script src`가 같은 API base URL의 절대 HTTPS `widget.js`인지, 그리고 `target`/`attachToSearchInput`/`attachAfterSelector` selector가 실제 HTML에 존재하거나 `autoAttach` 기본 동작이 대표 검색 input을 감지해 위젯이 mount 가능한지를 확인합니다. `apiBaseUrl` 옵션이 빠져 있어도 `script src="https://ai-search.../widget.js"`처럼 절대 API 도메인에서 위젯을 로드하면 위젯이 그 origin을 fallback으로 사용합니다. 반대로 상대 `/widget.js`나 다른 도메인의 script만 있으면 API base URL을 알 수 없어 대표 사이트 검증이 실패합니다. 대표 사이트 설정 파일의 `widget_target`, `attach_to_search_input`, `attach_after_selector`로 실제 템플릿 selector를 명시할 수 있으며, 명시 selector가 페이지에서 사라지면 `widget_init.missing_selectors` 증거로 실패합니다. 이후 각 mall 설정으로 텍스트/이미지/혼합 검색, 응답 `meta.engine=marqo`, `meta.mall_id`와 상품별 `mall_id`의 요청 mall 일치, 필수 필드, 상품의 raw `score`, 표시용 `score_percent`, `source_scores`, 텍스트 검색의 추천 카테고리로 다시 요청한 `text_category_refetch` 결과가 비어 있지 않고 다른 카테고리 상품을 섞지 않는지, 각 모드 첫 결과 상세 URL 및 `top`/`items` 전체 상품 URL이 `expected_product_url_prefix` 또는 `origin`의 가맹점 URL 규칙과 맞는지, 상세 URL 접근, 모드별 클릭 로그 호출까지 확인합니다. 상품 URL이 운영 HTTPS URL 검증이나 가맹점 URL 규칙을 통과하지 못하면 상세 URL fetch를 생략하고 해당 실패 사유를 증거에 남깁니다. 공유 상세 도메인을 쓰는 대표 사이트는 `expected_product_url_contains`나 `expected_product_url_pattern`으로 사이트별 규칙을 명시할 수 있습니다. readiness는 3개 이상의 서로 다른 `mall_id`, 사이트 URL, origin이 있는 HTTPS 비로컬 대표 사이트와 각 대표 사이트 검색 응답의 `meta.engine=marqo` 및 `text_all_product_url_rules`/`image_all_product_url_rules`/`mixed_all_product_url_rules` 통과를 요구하며, 원문 key를 노출하지 않는 `api_key_hash`로 `mall-config-check.json`의 `enabled_mall_api_key_hashes`와 대표 사이트 key 정합성을 비교하고, mall별 `product_url_template`에서 계산한 prefix와 대표 사이트의 실제 `text_product_url_rule`/`image_product_url_rule`/`mixed_product_url_rule` URL도 비교합니다. 같은 사이트 증거를 복제하거나 local engine 응답, 다른 mall API key/상세 URL 템플릿에서 나온 결과를 제출한 리포트는 통과하지 않습니다.

```powershell
python examples\HaeorumAISearch\scripts\representative_site_check.py `
  --sites examples\HaeorumAISearch\logs\representative-sites.config.json `
  --api-base-url https://ai-search.haeorumgift.com `
  --output examples\HaeorumAISearch\logs\representative-sites.json `
  --markdown-output examples\HaeorumAISearch\logs\representative-sites.md
```

운영 서버에 넘길 설정/배포 템플릿 묶음은 아래 명령으로 생성합니다. 이 명령은 지정한 디렉터리에 evidence config/env, 대표 사이트 config, 가맹점 설정 템플릿, 검색어 동의어 seed, 서비스 env, Nginx/systemd/logrotate 템플릿, Docker/Compose/requirements 참조 파일, 설치 및 증거 수집 체크리스트를 모읍니다. `local-acceptance.json`, `requirements-audit.json`, `operational-readiness.json`, `evidence-collection-plan.json`, `requirements-blockers.md`, `missing-evidence.sh`가 이미 생성되어 있으면 함께 넣어 로컬 회귀 증거, 현재 판정 근거, 남은 운영 증거 목록, 실행 명령을 전달합니다. 번들에 들어가는 `local-acceptance.json`은 전달용으로 `stdout_tail`/`stderr_tail` command output을 제거한 요약 JSON입니다. 번들에 들어가는 `malls.json`은 로컬 `sample_malls.json`의 구조를 사용하되 `public-...-dev-key` 값을 `replace-with-...-public-key` placeholder로 바꿔 생성합니다. 실제 `/etc`, `/data`, `/var/log`에는 직접 쓰지 않으므로 운영자가 값을 채운 뒤 체크리스트의 `install` 명령으로 반영합니다. `deploy/reference/*` 파일은 독립 build context가 아니라 운영 source root와 비교·검토할 참조 사본입니다. 번들 검증은 포함된 `local-acceptance.json`이 `local_only=true`, `not_operational_readiness=true`이고 필수 로컬 회귀 checks가 모두 통과했으며 command output tail이 없는지, handoff 리포트 JSON/Markdown 쌍이 함께 있고 운영 경로로 렌더링됐는지, `operational-evidence.config.json`의 key env 필드가 `operational-evidence.env` 변수명과 맞는지, Marqo URL/index/container와 PoC 품질 임계값이 운영 handoff 기준을 만족하는지도 확인합니다.

```powershell
python examples\HaeorumAISearch\scripts\prepare_operational_bundle.py `
  --output-dir examples\HaeorumAISearch\logs\operational-bundle `
  --local-acceptance-source examples\HaeorumAISearch\logs\local-acceptance.json `
  --local-acceptance-markdown-source examples\HaeorumAISearch\logs\local-acceptance.md `
  --requirements-audit-source examples\HaeorumAISearch\logs\requirements-audit.json `
  --requirements-audit-markdown-source examples\HaeorumAISearch\logs\requirements-audit.md `
  --operational-readiness-source examples\HaeorumAISearch\logs\operational-readiness.json `
  --operational-readiness-markdown-source examples\HaeorumAISearch\logs\operational-readiness.md `
  --evidence-collection-source examples\HaeorumAISearch\logs\evidence-collection-plan.json `
  --evidence-collection-markdown-source examples\HaeorumAISearch\logs\evidence-collection-plan.md `
  --blocker-checklist-source examples\HaeorumAISearch\logs\requirements-blockers.md `
  --missing-commands-source examples\HaeorumAISearch\logs\missing-evidence.sh `
  --json-output examples\HaeorumAISearch\logs\operational-bundle.json `
  --markdown-output examples\HaeorumAISearch\logs\operational-bundle.md
```

생성한 번들은 아래 명령으로 별도 검증할 수 있습니다.

```powershell
python examples\HaeorumAISearch\scripts\operational_bundle_check.py `
  --bundle-dir examples\HaeorumAISearch\logs\operational-bundle `
  --output examples\HaeorumAISearch\logs\operational-bundle-check.json `
  --markdown-output examples\HaeorumAISearch\logs\operational-bundle-check.md
```

운영 증거 19종을 설정 파일 하나로 순차 수집하려면 `contracts/operational_evidence.config.example.json`을 복사해 실제 경로와 key 환경 변수명을 채운 뒤 아래 명령을 실행합니다. `mall_config_source`에는 1,700개 가맹점 export CSV/XLSX를 지정해 `mall-config-build.json`과 `malls.json` 생성 계보를 남깁니다. `marqo.model`, `marqo.embedding_backend`, `marqo.gemini_embedding_url`, Gemini 모델/차원 값은 품질 리포트, CSV 색인, Marqo resource 설정 계약과 Gemini 텍스트+이미지 probe 검사에 같은 값으로 전달되므로 운영 env와 실제 인덱스 생성 설정에 맞춰 둡니다. 서버 호스트에서 collector를 실행할 때 `marqo.url=http://127.0.0.1:8882`, `marqo.gemini_embedding_url=http://127.0.0.1:8098`을 쓰고, Docker 서비스 env에는 `MARQO_URL=http://marqo-api:8882`, `HAEORUM_GEMINI_EMBEDDING_URL=http://gemini-embedding:8098`을 유지합니다. `contracts/operational_evidence.env.example`을 보호된 위치에 `0600`으로 설치하고 실제 secret으로 바꾼 다음 `--env-file`로 넘기면 `api_key_env`, `admin_key_env`, `mssql_connection_string_env` 값을 셸에 직접 export하지 않아도 됩니다. 이 MSSQL 연결 문자열은 `Server`, `Database`, `Encrypt=yes`, `TrustServerCertificate=no`, `ApplicationIntent=ReadOnly` 조건을 collector dry-run에서도 통과해야 합니다. `contracts/quality_cases.example.json`도 `/etc/haeorum-ai-search/quality-cases.json`으로 복사한 뒤 실제 PoC 텍스트, 이미지, 혼합 검색 케이스와 기준 이미지 파일 경로로 채워야 합니다. `load.image_file`은 이미지/혼합/850-user 부하와 API scale 부하에 사용할 실제 기준 이미지 파일 경로로 채워야 하며, collector dry-run은 지원 포맷과 디코딩 가능 여부를 먼저 검증합니다. 운영 `base_url`과 `origin`은 HTTPS 공개 절대 URL이어야 하며 credentials, 공백, query string, fragment, non-public host, 잘못된 port를 포함하면 dry-run에서 누락 설정으로 처리됩니다. `marqo.url`과 `marqo.gemini_embedding_url`은 host-reachable endpoint이므로 HTTP(S)를 허용하지만 credentials, query string, fragment, 공백, 역슬래시, 잘못된 port, link-local/unspecified host는 허용하지 않습니다. 먼저 `--dry-run`으로 누락된 설정/입력 파일과 실행될 명령을 secret 마스킹 상태로 확인합니다.

API scale 입력 리포트 dry-run은 `response_contract.engine_counts`와 query type coverage에 더해 `response_shape`의 유효/무효 성공 응답 및 최소 결과 수를 확인합니다. 빈 관련 상품이나 카테고리 추천을 숨긴 오래된 리포트는 `invalid_input_files`로 차단됩니다.

존재하는 Nginx/systemd/logrotate 보안 파일은 `security_check.py` 실행 전 dry-run에서 한 번 더 검사합니다. Nginx는 업로드 body size, upstream failover/load balancing, keepalive, `X-Forwarded-For`/`X-Real-IP` 덮어쓰기와 표준 `Forwarded` 헤더 제거를 확인하고, systemd는 API/sync/reindex unit의 non-root 실행, restart/hardening, `LimitNOFILE=65535` 이상, log write path, nightly 03:00 timer를 확인하며, logrotate는 JSONL 로그 범위와 rotate/directive를 확인합니다.
`mssql_query`도 dry-run에서 단일 read-only `SELECT`/`WITH` 쿼리인지 먼저 확인합니다. 주석, 다중 statement, `INSERT`/`UPDATE`/`DELETE`/`MERGE`, DDL, `EXEC`, `SET` 같은 쓰기 또는 권한 변경 가능 키워드는 운영 수집 전에 차단됩니다.

```powershell
python examples\HaeorumAISearch\scripts\collect_operational_evidence.py `
  --config examples\HaeorumAISearch\logs\operational-evidence.config.json `
  --env-file examples\HaeorumAISearch\logs\operational-evidence.env `
  --evidence-dir examples\HaeorumAISearch\logs `
  --dry-run `
  --output examples\HaeorumAISearch\logs\evidence-collection-plan.json `
  --markdown-output examples\HaeorumAISearch\logs\evidence-collection-plan.md `
  --local-acceptance-report examples\HaeorumAISearch\logs\local-acceptance.json `
  --requirements-audit-output examples\HaeorumAISearch\logs\requirements-audit.json `
  --requirements-audit-markdown-output examples\HaeorumAISearch\logs\requirements-audit.md `
  --requirements-blocker-checklist-output examples\HaeorumAISearch\logs\requirements-blockers.md
```

dry-run 리포트의 `blocking_inputs`가 비고 `ready_to_execute=true`가 되면 `--dry-run`을 제거하고 실제 운영 증거를 수집합니다. 이 실행은 `operational-readiness.json`까지 생성하고, `--requirements-audit-output`이 있으면 최종 요구사항 감사도 같은 흐름에서 갱신합니다.

```powershell
python examples\HaeorumAISearch\scripts\collect_operational_evidence.py `
  --config examples\HaeorumAISearch\logs\operational-evidence.config.json `
  --env-file examples\HaeorumAISearch\logs\operational-evidence.env `
  --evidence-dir examples\HaeorumAISearch\logs `
  --output examples\HaeorumAISearch\logs\evidence-collection.json `
  --markdown-output examples\HaeorumAISearch\logs\evidence-collection.md `
  --local-acceptance-report examples\HaeorumAISearch\logs\local-acceptance.json `
  --requirements-audit-output examples\HaeorumAISearch\logs\requirements-audit.json `
  --requirements-audit-markdown-output examples\HaeorumAISearch\logs\requirements-audit.md `
  --requirements-blocker-checklist-output examples\HaeorumAISearch\logs\requirements-blockers.md
```

```powershell
python examples\HaeorumAISearch\scripts\operational_readiness.py `
  --evidence-dir examples\HaeorumAISearch\logs `
  --expected-malls 1700 `
  --required-sites 3 `
  --output examples\HaeorumAISearch\logs\operational-readiness.json `
  --markdown-output examples\HaeorumAISearch\logs\operational-readiness.md `
  --missing-commands-shell bash `
  --missing-commands-project-root /opt/haeorum-ai-search `
  --missing-commands-evidence-dir /var/log/haeorum-ai-search `
  --missing-commands-output examples\HaeorumAISearch\logs\missing-evidence.sh
```

`--evidence-dir`는 표준 운영 증거 파일명(`api-smoke.json`, `mssql-export.json`, `poc-dataset.json`, `mssql-view.json`, `image-url-check.json`, `quality-report.json`, `csv-index.json`, `mall-config-build.json`, `mall-config-check.json`, `marqo-resource.json`, `server-preflight.json`, `env-check.json`, `load-text.json`, `load-image.json`, `load-mixed.json`, `load-mixed-traffic.json`, `api-scale.json`, `representative-sites.json`, `security.json`)을 자동으로 사용합니다. `widget-dom.json`은 local acceptance/handoff용 DOM 계약 증거이며 운영 readiness는 실제 대표 사이트 `representative-sites.json`으로 위젯 동작을 판정합니다. 누락된 리포트는 readiness 결과의 `command_hint`에 생성 명령 예시가 남고, `--missing-commands-output`을 지정하면 누락/실패 항목만 모은 체크리스트가 생성됩니다. 리눅스 운영 서버 인계용 기본 산출물은 `--missing-commands-shell bash`와 `missing-evidence.sh`를 사용합니다. 로컬 Windows 전용 실행 체크리스트가 필요할 때만 `--missing-commands-shell powershell`과 `.ps1` 출력을 별도로 지정합니다. 체크리스트는 먼저 `--missing-commands-project-root`로 지정한 코드 루트로 이동한 뒤 `scripts/...` 명령을 실행하므로 생성 위치와 실행 위치가 달라도 경로가 흔들리지 않습니다. Windows에서 Linux 운영용 체크리스트를 미리 만들 때는 `--missing-commands-evidence-dir /var/log/haeorum-ai-search`처럼 스크립트 내부의 증거 출력 경로를 별도로 지정합니다. 특정 파일명이 다르면 `--api-smoke-report`, `--mssql-export-report`, `--poc-dataset-report`, `--mall-config-build-report` 같은 개별 옵션으로 덮어씁니다.
`--requirements-audit-output`을 함께 지정하면 수집 리포트를 저장한 뒤 `local-acceptance.json`, `operational-readiness.json`, `evidence-collection-plan.json`을 묶어 최종 요구사항 감사 리포트까지 생성합니다. `--requirements-blocker-checklist-output`도 함께 지정하면 각 운영 blocker의 누락 config/file, 해소 방법, redacted 수집 명령, readiness 명령 템플릿을 체크리스트 Markdown으로 분리할 수 있습니다. 이 checklist는 evidence config의 `missing_commands.project_root`와 `missing_commands.evidence_dir`를 사용해 Windows에서 생성하더라도 `/opt/haeorum-ai-search`, `/var/log/haeorum-ai-search` 같은 운영 경로를 표시합니다. 운영 readiness의 `csv-index.json`은 `csv_index.py --mode reindex --engine marqo --validate-images`처럼 Marqo 영구 검색 인덱스에 실제 반영하고 이미지 검증까지 수행한 결과여야 하며, dry-run 리포트, 로컬 메모리 엔진 리포트, `engine`이 `marqo`가 아닌 예비/대체 어댑터 리포트는 통과하지 않습니다. 최소 active 상품 300개, 색인 실패 0건, active 상품 이미지 누락/unsafe/non-HTTPS 0건, 중복 상품번호 0건, `validate_images=true`, `post_index_document_count_ok=true`를 확인합니다. 데이터 계보 검사는 CSV fingerprint뿐 아니라 `quality-report.json`의 `source.marqo_url`/`source.index_name`/`source.marqo_model`, `csv-index.json`의 `marqo_url`/`index`/`marqo_model`, `marqo-resource.json`의 `marqo_url`/`index`, `api-smoke.json`의 `sync_status_index`가 같은지도 확인합니다. 또한 `mall-config-build.json` validation이 `mall-config-check.json`의 enabled mall ID/origin/template/API key hash와 같은지, API smoke/load/API scale/대표 사이트/security 증거가 서로 다른 API base URL을 섞지 않았는지, 단일 가맹점 API 증거의 `origin`/`mall_id`가 일관적인지, `security.json`의 `cors_origins`가 제출된 API/대표 사이트 origin을 모두 포함하는지, 대표 사이트 `mall_id`/`origin`/실제 `product_url`이 enabled mall config의 ID, origin, mall별 상품 URL 템플릿 prefix에 포함되는지도 `data_lineage`에서 재검증합니다.
운영 readiness의 `mssql-view.json`은 `column_report.ok=true`, `missing_required_columns=[]`, 샘플 row 파싱 성공, read-only permission 확인을 모두 요구합니다. `mssql-export.json`은 실제 read-only MSSQL View에서 정규화된 전체 상품 CSV를 만든 증거여야 하며, active 상품 300개 이상과 parse error 0건을 확인합니다. `poc-dataset.json`은 그 전체 상품 CSV에서 300개 이상 균형 잡힌 PoC CSV를 만든 증거여야 하며, sample-derived/local-only 리포트, 권장 카테고리 누락/부족, 이미지 URL 누락, 중복 상품번호를 통과시키지 않습니다. readiness는 이 리포트들과 이미지 URL/품질/색인 리포트의 `*_fingerprint.digest`를 비교해 전체 상품 CSV와 PoC CSV가 중간에 바뀌지 않았는지도 확인합니다. `server-preflight.json`은 Linux/Python/Docker/ODBC 확인뿐 아니라 `host_resources.requirements`와 실제 CPU/RAM/Disk 값을 포함해야 하며, readiness는 최소 4 vCPU, RAM 8GB, 여유 디스크 20GB를 다시 확인합니다.
운영 readiness의 `api-scale.json`은 같은 HTTPS 비로컬 API 대상, 같은 `mall_id`/`origin`, 같은 850 active user `mixed-traffic`, 같은 실제 `--image-file`/`--additional-image-file` 조건에서 API 서버 1대와 2대 이상 구성을 각각 측정한 `load_test.py` 리포트를 비교해야 합니다. 두 리포트 모두 성공 응답 `meta.engine=marqo`, 요청 mall과 응답 `meta.mall_id`/결과 상품 `mall_id` 일치, `mode_counts`와 실제 `meta.query_type` 분포의 일치, query type별 latency, admin metrics, `engine_backend=marqo`, 실제 파일 기반 `image_input.source=file` 또는 여러 파일 기반 `image_input.source=files`, `request_profile`, 실제 backend request attempt delta를 포함해야 하며, multi API 구성이 같은 워크로드에서 전체 p95/RPS와 text/image/text_image별 p95 기준을 악화시키지 않는지 확인합니다. `marqo-resource.json`은 Marqo health와 Docker CPU/RAM뿐 아니라 운영 PoC 최소 규모에 맞춰 인덱스 `numberOfDocuments` 300개 이상, `csv-index.json`의 active/indexed 상품 수와 정확히 일치함, `/indexes/{index}/settings`가 현재 Gemini/native 임베딩 계약과 맞음, Gemini `/health`와 실제 텍스트+이미지 `/embed` 프로브의 모델/차원이 운영 설정과 맞음, Docker CPU/RAM과 Vespa 저장소 사용률이 지정 임계치 아래임을 증명해야 합니다.

색인 전에 상품 대표 이미지 URL만 먼저 점검하려면 아래 스크립트를 사용합니다.

```powershell
python examples\HaeorumAISearch\scripts\image_url_check.py `
  --csv examples\HaeorumAISearch\sample_products.csv `
  --limit 100 `
  --concurrency 5 `
  --min-dimension 16 `
  --require-https
```

이 스크립트와 동기화 검증 로그는 실패 이미지와 별도로 `warning_count`와 `image_quality_warning`을 기록합니다. 워터마크/저대비 같은 경고는 상품을 색인에서 제외하지 않고 운영자가 후속 점검할 수 있게 남기지만, `placeholder_or_sample_image`는 `blocking_warning_count`로 분리되어 운영 readiness 차단 대상이 됩니다.
운영 readiness는 `image-url-check.json`에 `checked >= 100`, `failed == 0`, `require_https=true`, `non_https_active_image_url_count == 0`, `blocking_warning_count == 0`, `concurrency` 1~5 범위, `min_dimension >= 16`, `timeout_seconds`/`retry_count`/`max_mb` 실행 파라미터와 `csv`/`source` 프로필, `csv_fingerprint`가 남아 있어야 이미지 서버 부하를 제한한 표본 점검으로 인정합니다. 리포트에는 `failure_category_counts`, `warning_type_counts`, `blocking_warning_type_counts`, `attempts`도 남으므로 HTTP 4xx/5xx, MIME mismatch, decode 실패, 최소 크기 미달, unsafe redirect, DNS가 non-public address로 풀리는 CDN/이미지 호스트, HTTP 대표 이미지, watermark/placeholder 경고, retry 후 성공/실패를 먼저 분리해서 CDN/DB/상품 이미지 작업 대상을 좁힙니다. `sample_products.csv` 또는 샘플에서 복사된 CSV 기반 리포트는 운영 이미지 URL 증거로 인정하지 않습니다.




