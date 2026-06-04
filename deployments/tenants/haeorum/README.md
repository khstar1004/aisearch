# Haeorum Gift Tenant Bundle

해오름기프트 고객 설정 bundle이다. 앱 소스 코드는 이 폴더에 두지 않는다.

## 포함 파일

- `tenant.yaml`: 해오름 운영 설정의 중심 파일
- `sites.json`: 가맹점/site별 public key, origin, 상품 URL 정책
- `query-synonyms.json`: 검색어 동의어/보정
- `ranking.yaml`: 해오름 상품 특성에 맞춘 검색 튜닝 값
- `theme.css`: widget 색상/간격/폰트 보정
- `sql/product-mapping.sql`: 해오름 MSSQL 데이터를 표준 상품 스키마로 맞추는 View/query 초안
- `env.example`: 운영 env template
- `docs/website-integration-message.ko.md`: 기존 웹사이트 개발자에게 전달할 HTML 삽입 요청문
- `docs/handoff-checklist.ko.md`: 해오름 제공/반입 체크리스트

## 운영 반영 방식

공통 Docker image는 그대로 두고, 이 폴더의 설정 파일을 서버에 배치한다.

예상 서버 경로:

```text
/etc/aisearch/tenants/haeorum/tenant.yaml
/etc/aisearch/tenants/haeorum/sites.json
/etc/aisearch/tenants/haeorum/query-synonyms.json
/etc/aisearch/tenants/haeorum/ranking.yaml
/etc/aisearch/tenants/haeorum/theme.css
/etc/aisearch/tenants/haeorum/sql/product-mapping.sql
```

현재 구현은 `HAEORUM_*` 환경변수를 사용하므로, `env.example`은 기존 실행 경로에 맞춰 작성했다. 공통 제품 전환 후 `AISEARCH_*` alias로 치환한다.

