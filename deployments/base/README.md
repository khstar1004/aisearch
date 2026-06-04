# Base Deployment Templates

공통 Docker/systemd/nginx/logrotate 템플릿 위치다.

현재 실제 해오름 compose/systemd/nginx 파일은 `examples/HaeorumAISearch` 아래에 있다. 제품 전환 후에는 고객명 없는 템플릿을 이 폴더로 이동한다.

## 템플릿 원칙

- compose project name은 고객별 env에서 받는다.
- 서비스 이름은 `aisearch-api`, `aisearch-sync-worker`, `aisearch-embedding-proxy`, `marqo-api`, `vespa`처럼 고객명 없이 둔다.
- 고객명, 도메인, path, index name, env file path는 tenant bundle 또는 env로만 주입한다.
- Docker image는 공통 제품 image를 사용한다.
- 고객별 secret은 image 안에 넣지 않는다.

## 다음 전환 대상

- `examples/HaeorumAISearch/compose-haeorum-marqo.yaml`
- `examples/HaeorumAISearch/deploy/nginx/haeorum-ai-search.conf`
- `examples/HaeorumAISearch/deploy/systemd/*.service`
- `examples/HaeorumAISearch/deploy/logrotate/haeorum-ai-search`

