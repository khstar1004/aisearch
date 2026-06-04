# AI Search Product Structure

이 저장소는 앞으로 고객별 납품 코드를 복사하지 않고, 공통 제품 코드와 고객별 배포 설정을 분리해서 운영한다.

## 목표 구조

```text
components/
  marqo/                         # 검색 엔진 컴포넌트. 고객별 수정 금지
  common/
  inference_orchestrator/
  model_management/

packages/
  commerce_ai_search/            # 공통 AI 상품 검색 제품 코드

apps/
  search_api/                    # FastAPI 실행 진입점
  embedding_proxy/               # Gemini/OpenAI 등 embedding proxy 실행 진입점
  sync_worker/                   # 정기 색인/동기화 worker 실행 진입점

widget/
  dist/                          # 고객 사이트에 제공할 공용 widget asset
  demo/

tools/
  aisearch_cli/                  # tenant 생성, env 점검, smoke/evidence CLI

deployments/
  base/                          # 공통 compose/nginx/systemd/logrotate 템플릿
  tenants/
    haeorum/                     # 해오름기프트 고객 설정/문서/SQL
    demo_shop/

tests/
  tenant_profiles/
    haeorum/                     # 해오름 설정 회귀 테스트
```

## 핵심 원칙

- 고객별 앱 소스 복사본을 만들지 않는다.
- 신규 고객은 `deployments/tenants/<tenant_id>/` 설정 bundle만 추가한다.
- DB 차이는 고객별 SQL/View 또는 connector 설정으로 흡수한다.
- 검색창/팝업 디자인 차이는 widget config, theme, CSS로 흡수한다.
- 상품 특징과 검색 품질 차이는 `ranking.yaml`, `query-synonyms.json`, `category-rules.yaml`, `quality-cases.json`으로 튜닝한다.
- 공통 제품 이미지는 Docker image로 배포하고, 고객별 설정은 volume/env로 주입한다.

## 현재 전환 상태

현재 동작하는 해오름 구현은 아직 `examples/HaeorumAISearch`에 남아 있다. 이 경로는 운영 중인 참조 구현이므로 당분간 유지한다.

완료된 전환:

- 제품형 scaffold 추가
- 해오름 tenant bundle 추가
- `examples/HaeorumAISearch/app` 구현을 `packages/commerce_ai_search/commerce_ai_search`로 복제
- 제품형 entrypoint 추가: `apps/search_api`, `apps/embedding_proxy`, `apps/sync_worker`
- 제품형 Dockerfile 추가: `packages/commerce_ai_search/Dockerfile`
- `AISEARCH_*` 환경변수 alias 지원 추가. 기존 `HAEORUM_*`도 유지
- tenant init/validate CLI 초안 추가

남은 전환:

- 기존 `examples/HaeorumAISearch`를 완전히 wrapper로 축소
- scripts 전체를 고객명 없는 `tools/aisearch_cli` 명령으로 재분류
- OpenAPI 계약 제목과 운영 metric 이름의 고객명 제거
- 테스트를 공통 제품 테스트와 해오름 tenant profile 테스트로 분리

## 고객별 배포 원칙

고객에게 제공하는 기본 산출물은 다음이다.

- `aisearch-api` Docker image
- `aisearch-sync-worker` Docker image
- `aisearch-embedding-proxy` Docker image
- Marqo/Vespa compose 또는 관리형 검색엔진 접속 설정
- 고객별 `tenant.yaml`, `sites.json`, `product-mapping.sql`, `query-synonyms.json`, `theme.css`
- 운영 env template
- 웹사이트 삽입용 widget snippet
- smoke/evidence 절차서

고객에게 제품 소스 전체를 `git clone`하게 하지 않는다. GitOps가 필요한 고객에게는 소스 repo가 아니라 고객별 deployment repo 또는 bundle만 제공한다.

## 신규 고객 작업 절차

1. tenant 생성

```powershell
python tools\aisearch_cli\aisearch_cli.py init kogift --display-name "KoGift"
```

2. 고객 DB 구조에 맞춰 `deployments/tenants/kogift/sql/product-mapping.sql` 작성
3. 고객 사이트 origin/API key/상품 URL 규칙을 `sites.json`에 입력
4. 검색어/카테고리 특성을 `query-synonyms.json`, `ranking.yaml`에 입력
5. 검색창 색상/삽입 옵션을 `theme.css`, `tenant.yaml`에 입력
6. tenant bundle 검증

```powershell
python tools\aisearch_cli\aisearch_cli.py validate deployments\tenants\kogift
```

7. 샘플 상품으로 색인/검색 품질 확인
8. Docker image와 tenant bundle로 고객 서버 반입
9. 웹사이트 개발자에게 widget 삽입 문구 전달
10. smoke/evidence 통과 후 운영 전환
