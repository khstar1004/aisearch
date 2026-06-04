# 해오름기프트 웹사이트 개발자 전달 문구

아래 내용 그대로 기존 웹사이트 개발자에게 전달하면 됩니다.

---

안녕하세요. 해오름기프트 AI 상품 검색 연동을 위해 기존 쇼핑몰 HTML 템플릿에 아래 작업을 부탁드립니다.

## 1. 추가할 파일

쇼핑몰 웹서버의 정적 파일 경로에 아래 파일 2개를 추가해 주세요.

```text
/ai-search/widget.js
/ai-search/ai-search.html
```

- `widget.js`: 검색창 옆 AI 검색 버튼과 팝업을 붙이는 스크립트입니다.
- `ai-search.html`: 별도 결과 페이지 방식이 필요할 때 사용하는 AI 검색 페이지입니다.

정적 파일 경로가 `/ai-search/`가 아니어야 한다면 실제 경로를 알려주세요. 스크립트 경로와 `resultPageUrl`만 맞춰서 변경하겠습니다.

## 2. 기존 검색창 옆에 삽입할 컨테이너

기존 검색 form 안에서 검색 버튼 바로 옆 또는 오른쪽에 아래 span을 추가해 주세요.

```html
<span id="haeorum-ai-search"></span>
```

예시:

```html
<form id="searchForm" action="/goods/goods_search.asp" method="get">
  <input id="keyword" name="sword" type="text" value="">
  <button type="submit">검색</button>
  <span id="haeorum-ai-search"></span>
</form>
```

현재 검색 input의 selector가 `#keyword`가 아니거나 검색 버튼 selector가 `#searchForm button[type='submit']`와 다르면 실제 selector를 알려주세요.

## 3. 페이지 하단에 추가할 script

검색창이 있는 공통 header/footer 템플릿 또는 검색 페이지 하단에 아래 script를 추가해 주세요.

운영 반영 전 `mallId`, `siteId`, `apiKey`, `apiBaseUrl` 값은 실제 가맹점 값으로 교체합니다.

```html
<script src="/ai-search/widget.js"></script>
<script>
  window.addEventListener("DOMContentLoaded", function () {
    HaeorumAISearch.init({
      target: "#haeorum-ai-search",
      attachToSearchInput: "#keyword",
      attachAfterSelector: "#searchForm button[type='submit']",
      autoAttach: true,
      fallbackFloating: false,
      mountWaitMs: 3000,
      mallId: "shop001",
      siteId: "shop001",
      apiKey: "replace-with-shop001-public-key",
      apiBaseUrl: "https://ai-search.haeorumgift.com",
      resultPageUrl: "/ai-search/ai-search.html",
      limit: 20,
      maxImageMb: 5,
      minImageDimension: 16,
      triggerTitle: "AI검색",
      triggerAriaLabel: "AI 상품 검색 열기",
      accentColor: "#0f766e",
      accentSoftColor: "#ecfdf5",
      zIndex: 2147483000
    });
  });
</script>
```

## 4. inline script가 막힌 경우

사이트 CSP 정책 때문에 inline script를 넣기 어렵다면 아래처럼 script 태그 하나로 자동 초기화할 수 있습니다.

```html
<script
  src="/ai-search/widget.js"
  data-hai-auto-init="true"
  data-hai-target="#haeorum-ai-search"
  data-hai-attach-to-search-input="#keyword"
  data-hai-attach-after-selector="#searchForm button[type='submit']"
  data-hai-mall-id="shop001"
  data-hai-site-id="shop001"
  data-hai-api-key="replace-with-shop001-public-key"
  data-hai-api-base-url="https://ai-search.haeorumgift.com"
  data-hai-result-page-url="/ai-search/ai-search.html"
  data-hai-limit="20"
  data-hai-trigger-title="AI검색"
  data-hai-trigger-aria-label="AI 상품 검색 열기"
  data-hai-accent-color="#0f766e"
  data-hai-accent-soft-color="#ecfdf5"></script>
```

## 5. CSP 허용이 필요한 경우

사이트에 CSP가 있다면 아래 origin을 허용해 주세요.

```text
script-src 또는 script-src-elem: https://ai-search.haeorumgift.com
connect-src: https://ai-search.haeorumgift.com
img-src: data: https:
```

`widget.js`를 쇼핑몰 자체 서버의 `/ai-search/widget.js`에 올리는 경우에는 `script-src`는 기존 same-origin으로 충분할 수 있지만, API 호출 때문에 `connect-src`에는 `https://ai-search.haeorumgift.com`이 필요합니다.

## 6. 기존 개발자에게 확인받을 값

아래 값만 회신해 주세요.

```text
1. 검색 input selector
2. 검색 버튼 selector
3. AI 버튼을 넣을 위치의 HTML 조각
4. PC/mobile 공통 템플릿 여부
5. inline script 허용 여부
6. CSP 사용 여부
7. 실제 가맹점 site_id/mall_id 목록
8. 각 가맹점 origin
9. 상품 상세 URL 규칙
10. 롤백 시 widget script 제거 가능 담당자
```

## 7. 롤백 방법

문제가 생기면 아래 두 줄만 제거하면 기존 검색은 그대로 동작합니다.

```html
<span id="haeorum-ai-search"></span>
<script src="/ai-search/widget.js"></script>
```

inline init script 또는 `data-hai-auto-init` script도 함께 제거하면 됩니다.

---

감사합니다.

