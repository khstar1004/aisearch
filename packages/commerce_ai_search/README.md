# commerce_ai_search

공통 AI 상품 검색 제품 패키지다.

현재 `examples/HaeorumAISearch/app` 구현을 이 패키지의 `commerce_ai_search/` 아래로 복제해 제품형 entrypoint에서 실행할 수 있게 했다.

```text
commerce_ai_search/
  api/               # FastAPI router, dependency wiring
  config/            # Settings, TenantConfig, env loader
  domain/            # ProductDocument, SearchRequest, SearchResponse
  search/            # AISearchService, ranking, cache, policy
  engines/           # SearchEngine, Local, Marqo, Qdrant, Typesense
  ingestion/         # ProductSource, Csv, MSSQL, mapper, sync
  embeddings/        # Gemini/OpenAI/Qwen clients and proxy contracts
  security/          # public key, origin, rate limit, URL safety
  observability/     # metrics, logs, insights
  contracts/         # OpenAPI/schema generation
```

## 실행

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search;D:\aisearch"
uvicorn commerce_ai_search.main:app --host 0.0.0.0 --port 8000
```

또는 app wrapper를 사용할 수 있다.

```powershell
$env:PYTHONPATH="D:\aisearch\packages\commerce_ai_search;D:\aisearch"
uvicorn apps.search_api.main:app --host 0.0.0.0 --port 8000
```

## 호환성

- `AISEARCH_*` 환경변수를 우선 제품형 이름으로 사용한다.
- 기존 해오름 운영을 위해 `HAEORUM_*`도 계속 읽는다.
- legacy scripts가 사용하는 `app.*`, `scripts.*` import는 migration window 동안 package alias로 지원한다.

## 남은 정리

- `examples/HaeorumAISearch`를 wrapper와 해오름 reference 문서로 축소
- `legacy/scripts`를 `tools/aisearch_cli` 하위 명령으로 정리
- metric/OpenAPI/문서의 해오름 명칭을 tenant 설정 기반으로 치환
