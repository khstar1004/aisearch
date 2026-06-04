# Sync Worker App

상품 원본 DB/CSV/API에서 표준 상품 문서를 가져와 검색 인덱스에 반영하는 worker entrypoint 위치다.

제품 패키지 실행 형태:

```bash
python -m apps.sync_worker.main --mode sync
python -m apps.sync_worker.main --mode reindex --once
```

운영 호환 경로 `examples/HaeorumAISearch/app/sync_worker.py`도 아직 유지한다.
