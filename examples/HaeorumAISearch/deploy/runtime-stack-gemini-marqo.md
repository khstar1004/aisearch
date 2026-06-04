# Runtime Stack: Marqo + Gemini

이 파일이 현재 반입 기준입니다. 운영/로컬 검증 모두 검색엔진은 Marqo, 임베딩 provider는 Gemini API입니다.

## 실행 중이어야 하는 컨테이너

기본 API stack은 상시 5개 컨테이너입니다. 운영에서는 DB 증분 동기화를 위해 `--profile sync up -d --build --no-deps sync-worker`까지 실행하므로 상시 6개가 됩니다. `sync-worker`를 올릴 때는 이미 색인된 Marqo/Vespa 의존 컨테이너가 재생성되지 않도록 `--no-deps`를 유지합니다.

| 역할 | 컨테이너 |
| --- | --- |
| 공개/관리자 API | `haeorum-ai-search-marqo-ai-search-1` |
| Gemini embedding proxy | `haeorum-ai-search-marqo-gemini-embedding-1` |
| Marqo API | `haeorum-ai-search-marqo-marqo-api-1` |
| Marqo inference orchestrator | `haeorum-ai-search-marqo-mioc-1` |
| Vespa storage/search backend | `haeorum-ai-search-marqo-vespa-1` |
| MSSQL 증분 sync worker | `haeorum-ai-search-marqo-sync-worker-1` |

`vespa-init`는 Vespa 애플리케이션 패키지를 배포하는 1회성 초기화 컨테이너입니다. `Exited (0)`이면 정상이고, 계속 실행 중일 필요가 없습니다.

`reindex-once`는 full reindex 때만 `--profile reindex run --rm reindex-once`로 뜨는 1회성 컨테이너입니다. 정상 종료 후 삭제되므로 상시 컨테이너 수에는 포함하지 않습니다.

MSSQL 자체는 외부 DB(`221.143.49.208:1433`)를 read-only로 읽습니다. AI 검색 서버 안에 DB 컨테이너를 추가로 띄우지 않습니다.

Vespa 데이터는 `vespa-data` named volume을 `/opt/vespa/var`에 mount해서 보존합니다. 일반 재시작이나 sync-worker rollout 중 이 volume을 삭제하지 않습니다.

## 실행되면 안 되는 것

- 로컬 GPU 임베딩 컨테이너
- 임시 벡터 데모 서버 또는 검색 API와 다른 색인/포트를 바라보는 테스트 서버
- 검색 API와 다른 포트/다른 인덱스를 바라보는 중간 테스트 스택

## 정상 확인 명령

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" |
  Select-String -Pattern "haeorum-ai-search-marqo|NAMES"

Invoke-RestMethod http://127.0.0.1:8120/health |
  Select-Object engine, ready, embedding_backend, gemini_model, gemini_embedding_dimensions

$headers = @{ "X-Admin-Key" = "<admin-key>" }
Invoke-WebRequest http://127.0.0.1:8120/admin/metrics.prom -Headers $headers -UseBasicParsing |
  Select-Object -ExpandProperty Content |
  Select-String -Pattern "haeorum_gemini_query_vector|service=`"gemini`""
```

정상 기준은 `/health.embedding_backend=gemini`, `/health.gemini_model=gemini-embedding-2`, `/health.gemini.proxy_auth_configured=true`, Prometheus backend label `service="gemini"`입니다.

## 운영 배포 기준

- production env는 `GEMINI_AUTH_MODE=api_key`와 보호된 `GEMINI_API_KEY`를 기본으로 사용합니다.
- `GEMINI_PROXY_API_KEY`는 Gemini embedding proxy의 내부 `/embed` shared secret이며 API/reindex/sync 컨테이너의 `HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY`와 같은 값이어야 합니다.
- 로컬 테스트 override(`compose-haeorum-marqo-gemini-localtest.yaml`)도 API key를 요구하며, ADC 파일 mount는 사용하지 않습니다.
- API 서버가 2대 이상이면 `HAEORUM_REDIS_URL`을 반드시 설정해 rate limit과 캐시를 공유합니다.
- API, Marqo, and Gemini proxy ports may be published only to `127.0.0.1`, never publicly; 외부 공개는 Apache/Nginx 같은 reverse proxy `80/443`만 허용합니다.
- Docker compose host port는 `127.0.0.1:${HAEORUM_AI_SEARCH_PORT:-8000}`와 `127.0.0.1:${MARQO_PORT:-8882}`처럼 loopback에만 bind되어야 합니다.
- 실제 반입 전 `scripts/pre_handoff_audit.py --require-runtime`를 통과해야 합니다.
