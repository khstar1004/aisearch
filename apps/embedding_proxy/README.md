# Embedding Proxy App

Gemini/OpenAI 등 외부 embedding provider를 내부 API로 감싸는 proxy app 위치다.

현재 제품 패키지 실행 형태:

```bash
uvicorn apps.embedding_proxy.main:app --host 0.0.0.0 --port 8098
```

운영 호환 경로 `examples/HaeorumAISearch/app/gemini_embedding_proxy.py`도 아직 유지한다.
