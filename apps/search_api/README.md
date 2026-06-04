# Search API App

공통 `commerce_ai_search` 패키지를 실행하는 FastAPI entrypoint 위치다.

전환 완료 후 실행 형태:

```bash
uvicorn apps.search_api.main:app --host 0.0.0.0 --port 8000
```

제품 패키지 직접 실행 형태:

```bash
uvicorn commerce_ai_search.main:app --host 0.0.0.0 --port 8000
```

현재 운영 호환 실행 경로도 아직 유지한다:

```bash
cd examples/HaeorumAISearch
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
