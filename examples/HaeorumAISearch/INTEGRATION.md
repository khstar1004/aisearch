# 기존 쇼핑몰 연동 가이드

이 문서는 현재 `examples/HaeorumAISearch` 구현을 해오름기프트 운영 쇼핑몰 템플릿에 붙일 때 필요한 최소 계약을 정리합니다. API 전체 계약은 `contracts/openapi.json`, 위젯 삽입 예시는 `contracts/widget_init.example.html`을 기준으로 관리합니다.

## 1. 가맹점 설정

API 서버에는 가맹점별 `mall_id`, 공개 API key, 상품 상세 URL 템플릿을 등록합니다. 특정 가맹점에서 노출하지 않을 상품/카테고리나 가격 표시 규칙이 있으면 같은 설정에 정책을 둡니다. 기존 쇼핑몰에서 식별자를 `site_id`로 부르는 경우 검색/클릭 요청에 `site_id`를 보내도 내부 `mall_id`로 정규화됩니다.

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

운영에서는 `HAEORUM_MALL_CONFIG_PATH`가 이 파일을 가리켜야 합니다. `HAEORUM_CORS_ORIGINS`에는 실제 쇼핑몰 도메인만 넣고, 가맹점별 `allowed_origins`에는 해당 가맹점 템플릿이 서비스되는 정확한 origin을 넣습니다. mall별 `allowed_origins`는 전역 `HAEORUM_CORS_ORIGINS`에도 포함되어야 합니다.
API 서버 운영 배포에서는 `HAEORUM_ENV=production`을 설정합니다. 이 모드에서는 개발/placeholder 관리자 key, wildcard CORS, 비어 있는 mall 설정, 활성 가맹점의 `api_key`/`allowed_origins`/URL 템플릿 누락, sample/placeholder 공개 API key, mall별 wildcard `allowed_origins`, 전역 CORS에 포함되지 않은 mall별 origin을 서버 시작 전에 거절합니다.

상품 row가 모든 가맹점에서 공통으로 쓰이면 `HAEORUM_FILTER_BY_MALL_ID=false`를 유지합니다. 운영 View가 가맹점별 상품 노출 정책을 `mall_id` row로 분리해서 제공하는 경우에만 `true`로 설정합니다.
`excluded_product_ids`, `excluded_categories`는 검색엔진 결과를 API 응답으로 바꾸기 전에 적용되므로 공통 상품 row 구조에서도 사용할 수 있습니다.
`hide_prices`, `price_multiplier`, `price_adjustment`, `price_round_to`는 공통 상품 row의 가격을 가맹점별 표시 정책에 맞게 숨기거나 보정할 때만 지정합니다.
위 예시의 `replace-with-shop001-public-key`는 그대로 배포하면 안 됩니다. 운영 mall 설정과 위젯의 `apiKey`에는 같은 실제 공개 key를 넣어야 하며, sample/placeholder 공개 key는 `mall_config_check.py`, `env_check.py`, production 기동 검증에서 실패합니다.

가맹점 목록이 CSV로 관리되는 경우에는 먼저 표준 설정 파일을 생성합니다. CSV 헤더는 `mall_id`/`site_id`, `domain`/`origin`, `api_key`를 인식하며, 선택적으로 `product_url_template`, `excluded_product_ids`, `excluded_categories`, `hide_prices`, `price_multiplier`, `price_adjustment`, `price_round_to`를 받을 수 있습니다.

```powershell
python examples\HaeorumAISearch\scripts\mall_config_builder.py `
  --csv /path/to/haeorum-malls.csv `
  --output /path/to/malls.json `
  --report-output /path/to/mall-config-build.json `
  --min-count 1700 `
  --sort-by-mall-id
```

대량 가맹점 적용 전에는 생성된 설정 파일을 검증합니다.

```powershell
python examples\HaeorumAISearch\scripts\mall_config_check.py `
  --config /path/to/malls.json `
  --min-count 1700
```

운영 readiness는 이 리포트의 `problems=[]`도 확인합니다. 개수만 맞춘 설정이 아니라 enabled 가맹점별 실제 공개 API key, HTTPS 공개 `allowed_origins`, 안전한 `product_url_template`까지 준비되어야 통과합니다.

## 2. 템플릿 삽입 위치

기존 검색창 오른쪽, 검색 버튼 옆에 AI 검색 버튼을 붙입니다. 템플릿 수정 여지가 있으면 위젯 컨테이너를 추가하는 방식도 사용할 수 있고, 기존 입력창과 검색 버튼 selector만 넘겨 자동 삽입할 수도 있습니다.

```html
<form id="searchForm" action="/goods/goods_search.asp" method="get">
  <input id="keyword" name="sword" type="text" value="">
  <button type="submit">검색</button>
  <span id="haeorum-ai-search"></span>
</form>
```

페이지 하단에는 위젯 스크립트를 로드하고 가맹점 값을 주입합니다. 별도 결과 페이지 방식에서는 `ai-search.html`, `widget.js`, `haeorum-logo.jpg`를 기존 쇼핑몰 서버의 같은 폴더에 올리고 `resultPageUrl`을 그 페이지 경로로 지정합니다.

```html
<script src="/ai-search/widget.js"></script>
<script>
  HaeorumAISearch.init({
    target: "#haeorum-ai-search",
    mallId: "shop001",
    apiKey: "replace-with-shop001-public-key",
    apiBaseUrl: "https://ai-search.haeorumgift.com",
    resultPageUrl: "/ai-search/ai-search.html",
    limit: 20,
    maxImageMb: 5,
    minImageDimension: 16
  });
</script>
```

컨테이너를 추가하기 어렵다면 기존 검색 input과 검색 버튼 selector를 사용합니다. 이 경우 AI 검색 팝업을 열 때마다 현재 기존 검색창의 값을 팝업 입력창에 자동으로 채웁니다.

```html
<script src="/ai-search/widget.js"></script>
<script>
  HaeorumAISearch.init({
    target: "",
    attachToSearchInput: "#keyword",
    attachAfterSelector: "#searchForm button[type='submit']",
    mallId: "shop001",
    apiKey: "replace-with-shop001-public-key",
    apiBaseUrl: "https://ai-search.haeorumgift.com",
    resultPageUrl: "/ai-search/ai-search.html"
  });
</script>
```

inline script 변경이 어렵거나 CSP로 제한되는 템플릿은 script 태그 하나로 자동 초기화할 수 있습니다.

```html
<script
  src="/ai-search/widget.js"
  data-hai-auto-init="true"
  data-mall-id="shop001"
  data-api-key="replace-with-shop001-public-key"
  data-api-base-url="https://ai-search.haeorumgift.com"
  data-result-page-url="/ai-search/ai-search.html"
  data-attach-to-search-input="#keyword"
  data-attach-after-selector="#searchForm button[type='submit']"
  data-trigger-title="AI검색"></script>
```

`apiKey`는 브라우저에 노출되는 가맹점 공개 key입니다. 검색 요청과 클릭 로그는 `X-API-Key` 헤더를 사용합니다. 위젯은 access log에 key가 남지 않도록 `apiKey`가 설정된 클릭 로그는 `fetch(..., keepalive: true)`로 보내고, 공개 key가 없는 개발/비인증 가맹점에서만 `sendBeacon`을 사용합니다. API 서버는 URL query string의 `api_key`뿐 아니라 JSON/multipart body의 `api_key`/`apiKey`도 거절합니다.
`apiBaseUrl`을 명시하면 절대 HTTP(S) API 서버 주소만 허용합니다. credentials, query string, fragment, 공백, 역슬래시, 잘못된 port가 포함되거나 상대 URL/`javascript:` URL이면 위젯 초기화가 실패합니다. `apiBaseUrl`을 생략하면 위젯은 현재 로드된 절대 HTTP(S) `widget.js` script origin을 API base URL로 사용합니다. 운영에서는 `script src="https://ai-search.haeorumgift.com/widget.js"`처럼 API 서버의 절대 HTTPS URL을 사용해야 합니다.
쇼핑몰 템플릿에 CSP가 있으면 `script-src` 또는 `script-src-elem`에서 위젯 `widget.js` origin을 허용하고, `connect-src`에서는 검색/클릭 로그 요청을 보낼 API base URL origin을 허용해야 합니다. `connect-src`가 없으면 `default-src`가 API 호출 허용 여부를 결정합니다.
`data-hai-auto-init="true"` 방식에서는 camelCase 옵션을 kebab-case `data-*` 속성으로 넘깁니다. 예를 들어 `attachToSearchInput`은 `data-attach-to-search-input`, `apiBaseUrl`은 `data-api-base-url`입니다.
기존 사이트 개발자 연락이 늦어지면 `scripts/widget_integration_probe.py`에 저장한 PC/모바일 HTML을 넣어 검색창 selector와 `data-hai-auto-init` 스니펫을 먼저 산출합니다. 이 도구는 CSP 때문에 inline `init()`가 막힐 가능성, 외부 `widget.js` 또는 API base URL `connect-src` 차단 위험, 상대 `/widget.js` 사용으로 API base URL을 추론하지 못하는 문제, HTTP/localhost/credential/query/fragment가 섞인 API base/widget/page URL, 검색 버튼 selector 누락과 중복 ID/class로 selector가 모호한 문제를 사전 리스크로 표시합니다. `--allow-fallback-floating`을 함께 쓰면 검색창이 없거나 추천 selector가 모호할 때 selector를 쓰지 않는 우측 하단 플로팅 스니펫을 대안으로 생성합니다. `--snippets-output-dir`를 지정하면 페이지별 스니펫, 저장 HTML에 스니펫을 삽입한 `previews/*.preview.html`, preview 삽입 marker와 `data-hai-auto-init` 중복 여부 및 추천 selector가 preview DOM에서 각각 하나만 매칭되는지 확인한 `preview-validation.json`/`.md`, `manual-install-plan.json`/`.md`가 생성되어 삽입 모드, selector 신뢰도, 수동 검토 여부, CSP allowlist 힌트를 전달할 수 있습니다.
가맹점 설정에 `allowed_origins`가 있으면 브라우저 요청의 `Origin` 헤더가 목록과 일치해야 합니다. 이 검사는 CORS와 함께 동작하는 운영 안전장치이며, 운영 smoke/load 테스트에는 `--origin https://shop001.haeorumgift.com`처럼 실제 origin을 넣어야 합니다. `allowed_origins` 값은 path/query/fragment가 없는 origin 형식이어야 하고, 상품 URL 템플릿은 `{product_id}`를 포함한 HTTP(S) URL로 검증됩니다.

## 3. 위젯 옵션

| 옵션 | 필수 | 설명 |
| --- | --- | --- |
| `data-hai-auto-init` | data 속성 방식만 | `true`이면 inline `init()` 없이 script 태그의 `data-*` 속성으로 자동 초기화 |
| `target` | 예 | 버튼을 삽입할 CSS selector |
| `mallId` | 예 | API 서버의 가맹점 ID. 서버 API에서는 같은 값의 `site_id` 별칭도 허용 |
| `siteId` | 아니오 | 기존 템플릿에서 `site_id` 용어를 쓰는 경우의 별칭. `mallId`가 없을 때 `mallId`로 정규화 |
| `apiKey` | 운영 권장 | 가맹점 공개 API key |
| `apiBaseUrl` | 조건부 | AI 검색 API 서버 절대 HTTP(S) 주소. 생략하면 절대 HTTP(S) `widget.js` script origin을 사용. credentials/query/fragment/공백/역슬래시/잘못된 port는 허용하지 않음 |
| `limit` | 아니오 | 검색 결과 개수, 기본 20 |
| `maxImageMb` | 아니오 | 브라우저 업로드 제한, 기본 5MB |
| `minImageDimension` | 아니오 | 브라우저 이미지 가로/세로 최소 픽셀 검증, 기본 16px. 서버 `HAEORUM_MIN_IMAGE_DIMENSION`과 같은 값으로 맞춤 |
| `attachToSearchInput` | 아니오 | 기존 검색 input selector. `target`이 없으면 이 입력창 근처에 AI 버튼을 자동 삽입하고 팝업 검색어를 미리 채움 |
| `attachAfterSelector` | 아니오 | AI 버튼을 삽입할 기준 요소 selector. 검색 버튼 뒤에 붙일 때 사용 |
| `fallbackFloating` | 아니오 | `true`이면 명시 target이나 자동 검색창을 찾지 못해도 `mountWaitMs` 대기 후 우측 하단 플로팅 AI 버튼으로 최소 검색 UI를 표시 |
| `triggerTitle` | 아니오 | 카메라 버튼 마우스 오버 툴팁. 기본 `AI검색` |
| `triggerAriaLabel` | 아니오 | 카메라 버튼 접근성 label. 기본 `AI 상품 검색` |
| `accentColor` | 아니오 | 주요 버튼/유사도 색상 |
| `accentTextColor` | 아니오 | 주요 버튼 글자 색상 |
| `accentSoftColor` | 아니오 | 드래그 중 업로드 영역 배경색 |
| `zIndex` | 아니오 | 쇼핑몰 레이아웃과 충돌할 때 팝업 레이어 우선순위 조정 |

위젯은 텍스트 검색어와 업로드 이미지를 `multipart/form-data`로 전송합니다. 사용자는 텍스트만, 이미지만, 텍스트+이미지 조합으로 검색할 수 있습니다. 브라우저는 파일 형식, 용량, 이미지 로드 가능 여부, 최소 가로/세로 픽셀을 먼저 확인합니다. 검색 요청이 진행 중이면 새 검색, 카테고리 재검색, 더보기 submit은 무시해 Enter 연타나 중복 클릭이 같은 브라우저에서 동시 검색 요청을 만들지 않게 합니다. 서버는 같은 이미지를 다시 검증한 뒤 JPEG EXIF 방향을 실제 픽셀 방향으로 정규화하고, `HAEORUM_MAX_IMAGE_DIMENSION`보다 큰 이미지를 비율 유지 리사이즈해 검색엔진 요청 크기를 줄입니다.

## 4. 검색 API 계약

텍스트 JSON 요청:

```http
POST /api/ai-search HTTP/1.1
Host: ai-search.haeorumgift.com
Content-Type: application/json
X-API-Key: replace-with-shop001-public-key
```

```json
{
  "mall_id": "shop001",
  "q": "검은 우산",
  "limit": 20
}
```

운영 연동 점검에서는 같은 요청을 아래처럼 실행합니다.

```bash
curl -fsS https://ai-search.haeorumgift.com/api/ai-search \
  -H "Content-Type: application/json" \
  -H "Origin: https://shop001.haeorumgift.com" \
  -H "X-API-Key: replace-with-shop001-public-key" \
  --data '{"mall_id":"shop001","q":"검은 우산","limit":20}'
```

`mall_id` 대신 `site_id`를 보내도 같은 요청으로 처리됩니다. 두 값을 함께 보내는 경우 공백 제거 후 값이 같아야 하며, 다르면 400으로 거절됩니다.

```bash
curl -fsS https://ai-search.haeorumgift.com/api/ai-search \
  -H "Content-Type: application/json" \
  -H "Origin: https://shop001.haeorumgift.com" \
  -H "X-API-Key: replace-with-shop001-public-key" \
  --data '{"site_id":"shop001","q":"텀블러","limit":20}'
```

입력 문자열은 앞뒤 공백 제거 후 검증됩니다. `mall_id`/`site_id`는 최대 64자, 검색어 `q`와 클릭 `query`는 최대 200자, `category`는 최대 100자, 클릭 `product_id`는 최대 100자, 클릭 `product_url`은 최대 1000자입니다. 클릭 로그의 `product_url`은 절대 HTTP(S) URL만 허용되며, 상대 URL, `javascript:` URL, URL 사용자 정보, 잘못된 포트, 제어문자/공백이 섞인 URL은 400으로 거절됩니다.
검색 요청에는 선택적으로 `category`, `print_method`, `material`, `color`, `min_price`, `max_price`, `quantity` 또는 `order_qty`, `max_delivery_days`를 넣을 수 있습니다. 예를 들어 `quantity=100`은 최소 주문 수량이 100개 이하인 상품만 남기고, `max_delivery_days=3`은 납기 3일 이내로 확인된 상품만 남깁니다. 같은 필드는 JSON과 `multipart/form-data` 검색 요청에서 모두 지원됩니다.
공개 API는 API key 오류를 401로, 허용되지 않은 가맹점 ID 또는 Origin을 403으로 반환합니다. 연동 시 이 오류는 사용자에게 일반 실패 문구로 표시하고, 운영자는 API 오류 로그에서 원인을 확인합니다. 기본 위젯은 401/403/413/429/5xx 응답을 사용자용 안내 문구로 변환하고, 새 검색 실패 시 이전 결과와 더보기 상태를 정리합니다.

JSON body로 이미지 base64를 보내야 하는 클라이언트는 `image_base64`를 사용합니다. 가능한 경우에는 아래 multipart 방식을 우선 사용합니다.

```json
{
  "mall_id": "shop001",
  "image_base64": "data:image/png;base64,...",
  "limit": 20
}
```

이미지 또는 혼합 검색은 `multipart/form-data`를 권장합니다.

```text
mall_id=shop001
q=검은색
limit=20
print_method=UV
quantity=100
image=<JPG|PNG|WEBP 파일>
```

이미지만 검색:

```bash
curl -fsS https://ai-search.haeorumgift.com/api/ai-search \
  -H "Origin: https://shop001.haeorumgift.com" \
  -H "X-API-Key: replace-with-shop001-public-key" \
  -F "mall_id=shop001" \
  -F "limit=20" \
  -F "image=@./sample-product.webp;type=image/webp"
```

텍스트+이미지 혼합 검색:

```bash
curl -fsS https://ai-search.haeorumgift.com/api/ai-search \
  -H "Origin: https://shop001.haeorumgift.com" \
  -H "X-API-Key: replace-with-shop001-public-key" \
  -F "mall_id=shop001" \
  -F "q=검은색" \
  -F "text_weight=0.4" \
  -F "image_weight=0.6" \
  -F "print_method=UV" \
  -F "quantity=100" \
  -F "image=@./sample-product.jpg;type=image/jpeg"
```

응답은 항상 아래 구조를 유지합니다.

```json
{
  "top": [],
  "items": [],
  "suggested_categories": [],
  "meta": {
    "query_type": "text",
    "elapsed_ms": 123.4,
    "engine": "marqo",
    "limit": 20,
    "mall_id": "shop001",
    "text_weight": null,
    "image_weight": null,
    "low_confidence": false,
    "notice": null
  }
}
```

`top`은 상위 유사 상품 최대 3개이고, `items`는 나머지 관련 상품 리스트입니다. `meta.low_confidence=true`이면 위젯은 `meta.notice`를 사용자에게 표시합니다.

## 5. 클릭 로그 계약

상품 카드의 이미지 또는 상세 보기 링크를 누르면 위젯이 클릭 로그를 남깁니다.

```json
{
  "mall_id": "shop001",
  "product_id": "P001",
  "product_url": "https://shop001.haeorumgift.com/product_view.asp?p_idx=P001",
  "position": 1,
  "query": "검은 우산",
  "query_type": "text_image"
}
```

서버는 `{"ok": true}`를 반환하고, 검색 로그 파일에는 `type: "click"`으로 기록합니다. 위젯은 상품 카드가 렌더링된 검색 응답의 `meta.query_type`을 보존해 클릭 로그에 넣으므로, 검색 후 이미지를 삭제해도 클릭 로그의 검색 타입이 바뀌지 않습니다.

## 6. 관리자 연동

운영 배포 후 첫 색인은 관리자 key로 실행합니다.

```powershell
curl -X POST https://ai-search.haeorumgift.com/admin/reindex -H "X-Admin-Key: <admin-key>"
```

정기 동기화는 별도 worker에서 실행합니다.

```powershell
python -m app.sync_worker --mode sync
```

운영 DB는 read-only 계정과 AI 검색용 View만 사용합니다. View 컬럼과 `updated_at` 샘플 품질 점검은 `scripts/mssql_view_check.py`로 실행하고, 대표 이미지 URL 점검은 `scripts/image_url_check.py`로 실행합니다.
운영 후 검색 품질 튜닝용 집계는 `GET /admin/search-insights?min_searches=3&limit=50`에서 조회할 수 있습니다. 응답에는 무결과/낮은 유사도/클릭 없는 쿼리, 많이 클릭된 상품, 혼합 검색 가중치 조합별 성과와 추천 액션이 포함됩니다.

## 7. 배포 전 확인

1. `contracts/openapi.json`이 연동팀에 공유되었는지 확인합니다.
2. `sample_malls.json` 형식으로 운영 가맹점 설정을 만들고 API 서버에 적용합니다.
3. `HAEORUM_CORS_ORIGINS`에 운영 쇼핑몰 도메인을 설정합니다.
4. `python examples\HaeorumAISearch\scripts\contract_check.py`를 통과시킵니다.
5. `python examples\HaeorumAISearch\scripts\api_smoke_test.py --base-url <api> --mall-id <mall> --api-key <key> --origin <origin> --admin-key <admin>`를 실행합니다. 이 스모크는 정상 검색/클릭 로그와 함께 `site_id` 별칭/충돌 거절, JSON·multipart 이미지 크기 제한, 배포된 OpenAPI의 클릭 로그 429 계약, 잘못된 공개 API key, URL/JSON/multipart의 공개 API key 및 관리자 key 별칭, 허용되지 않은 Origin, 잘못된 payload와 깨진 JSON 본문 400 거절, 잘못된 관리자 key 거절, 관리자 상태/검색 로그/동기화 로그/오류 로그/검색 인사이트/메트릭 조회도 확인합니다. 운영 readiness 증거에는 격리된 mall/key 또는 staging에서 `--expect-click-rate-limit --click-rate-limit-probe-count <configured_click_rate_limit_plus_1>`를 추가해 클릭 로그 429도 남깁니다.
   JSON 검색/클릭 로그의 알 수 없는 필드도 `additionalProperties=false` 계약에 따라 400으로 거절되어야 합니다.
6. `POST /admin/reindex` 후 `/admin/sync-status`의 `failed`와 `last_error`를 확인합니다.
7. 쇼핑몰 템플릿에서 위젯 버튼, 팝업, 이미지 업로드, 상품 상세 링크 이동, 클릭 로그가 동작하는지 확인합니다.
