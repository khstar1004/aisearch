# 해오름기프트 제공/반입 체크리스트

## 고객에게 제공할 파일

- `widget.js`
- `ai-search.html`
- 웹사이트 삽입 문구: `docs/website-integration-message.ko.md`
- 운영 env template: `env.example`
- 가맹점 설정 template: `sites.json`
- 동의어 설정: `query-synonyms.json`
- MSSQL 표준 mapping query 초안: `sql/product-mapping.sql`

## 고객에게 받는 정보

- AI 검색 API 도메인: 예 `https://ai-search.haeorumgift.com`
- 쇼핑몰 검색창 HTML
- PC/mobile 검색창 selector
- 가맹점별 `site_id` 또는 `mall_id`
- 가맹점별 origin
- 가맹점별 public API key 발급 방식
- 상품 상세 URL 규칙
- MSSQL read-only connection 정보
- AI 검색용 View 또는 SELECT query
- 삭제/비노출/품절 상태 정의
- 대표 이미지 URL 접근 가능 여부
- Google Gemini 인증 방식과 quota

## 서버 반입 후 확인

1. env 파일 권한 확인
2. `env_check.py` production 통과
3. `/health` 통과
4. `/admin/metrics` 확인
5. 전체 reindex 실행
6. 텍스트 검색 smoke
7. 이미지 검색 smoke
8. 텍스트+이미지 혼합 검색 smoke
9. 클릭 로그 smoke
10. widget PC/mobile DOM 확인
11. Nginx/TLS/CORS 확인
12. sync worker timer 확인
13. 롤백 절차 확인

## 고객 설명 핵심 문구

소스 코드를 고객사 서버에서 수정하는 구조가 아닙니다. 공통 AI 검색 Docker image를 설치하고, 해오름 전용 설정 파일과 MSSQL View/query, widget snippet만 주입합니다. 검색창 위치, 색상, 가맹점별 URL, 상품 노출 정책은 설정으로 바꾸며, 신규 기능이나 공통 버그 수정은 제품 image 업데이트로 반영합니다.

