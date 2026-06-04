# 신규 고객 반입 절차

신규 고객은 앱 코드를 복사하지 않고 tenant bundle만 만든다.

## 1. 고객 정보 수집

받아야 하는 정보:

- 고객명과 `tenant_id`
- 쇼핑몰 도메인/origin
- 검색창 HTML, PC/mobile selector
- 상품 DB 종류와 접속 방식
- AI 검색용 View 또는 SELECT query
- 상품 상세 URL 규칙
- 대표 이미지 URL 접근 가능 여부
- 삭제/숨김/품절/비노출 판정 규칙
- 검색 품질 예시 query 30~100개
- 원하는 widget 색상/버튼 위치

## 2. tenant bundle 생성

```powershell
python tools\aisearch_cli\aisearch_cli.py init kogift --display-name "KoGift"
```

생성 위치:

```text
deployments/tenants/kogift/
```

## 3. DB mapping 작성

`sql/product-mapping.sql`은 고객 DB를 표준 상품 스키마로 바꾸는 read-only query다.

필수 표준 필드:

```text
product_id
product_name
category_name
price
main_image_url
product_url
status
updated_at
display_yn 또는 is_deleted
mall_id 또는 site_id
```

가능하면 추가할 필드:

```text
price_min
price_max
print_methods
materials
colors
min_order_qty
delivery_days
product_group_id
```

## 4. 사이트 설정 작성

`sites.json`에 site별 값을 넣는다.

- `site_id`
- `api_key`
- `allowed_origins`
- `product_url_template`
- 제외 상품/카테고리
- 가격 표시 정책

## 5. 검색 튜닝 작성

- `query-synonyms.json`: 고객 업종의 동의어/오타/표현 차이
- `ranking.yaml`: text/image weight, category intent, collapse 정책
- `quality-cases.json`: 고객이 납득해야 하는 대표 검색어와 기대 상품

## 6. widget 설정 작성

- `theme.css`: 색상/시각 스타일
- `tenant.yaml`: 기본 target, result page, trigger title
- 고객 개발자에게 전달할 HTML snippet 작성

## 7. 검증

```powershell
python tools\aisearch_cli\aisearch_cli.py validate deployments\tenants\kogift
```

그 다음 샘플 상품으로 색인하고 다음을 확인한다.

- 텍스트 검색
- 이미지 검색
- 텍스트+이미지 혼합 검색
- 클릭 로그
- CORS/origin 제한
- widget PC/mobile 표시

## 8. 고객 제공

기본 제공물:

```text
Docker image 또는 image tar
Docker Compose/base deployment
tenant bundle
widget.js
ai-search.html
웹사이트 삽입 안내문
운영 점검 리포트
```

소스 repo 전체를 고객에게 제공하지 않는다. GitOps가 필요하면 고객별 deployment repo만 제공한다.

