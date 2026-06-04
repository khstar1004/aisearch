# 내일 실서버 Go-Live 체크리스트

이 문서는 해오름기프트 AI 검색을 실서버에 올리고 MSSQL DB와 연동하기 위한 실행 순서입니다.
아래 순서대로 진행하고, `STOP` 항목이 하나라도 걸리면 다음 단계로 넘어가지 않습니다.

## 결론

- 운영 방식은 `Linux 서버 + Docker Compose + Marqo + Gemini embedding proxy + MSSQL read-only View`입니다.
- 상시 컨테이너는 기본 5개(`ai-search`, `gemini-embedding`, `marqo-api`, `mioc`, `vespa`)이고, 운영 DB 증분 동기화 `sync-worker`를 켜면 6개입니다. `vespa-init`와 `reindex-once`는 1회성입니다.
- 서버가 인터넷으로 Docker base image, Microsoft ODBC package, Gemini API에 접근 가능하면 Docker 이미지를 따로 넣을 필요 없이 서버에서 `docker compose up -d --build`로 빌드합니다.
- 서버 outbound가 막혀 있으면 그때만 별도 머신에서 이미지를 빌드해 `docker save` / `docker load`로 반입합니다. 기본값은 서버 빌드입니다.
- 운영 env 파일은 `/etc/haeorum-ai-search/haeorum-ai-search.env` 하나로 통일합니다.
- server82 기준 API host port는 `127.0.0.1:8120`입니다. 현재 서버는 Nginx가 아니라 Apache httpd가 80/443을 처리하므로 Apache가 이 포트로 proxy합니다.

## 0. 오늘 밤까지 반드시 받아야 하는 값

- [x] 서버 SSH host / port / user: `222.236.45.35:22`, `root`
- [x] sudo/root 가능
- [x] Linux/CPU/RAM/Docker disk 확인: CentOS 7.6, 8 core, 8GB installed, Docker root `/home/docker` 97GB free
- [x] Docker Engine 24+ 및 Docker Compose plugin 설치: Docker 26.1.4, Compose v2.27.1
- [x] 외부 inbound는 `80/443` Apache 사용, AI API/Marqo/Gemini 포트는 loopback만 사용
- [x] outbound HTTPS 허용: Gemini API endpoint HTTPS 응답 확인
- [ ] API 서브도메인 DNS: `ai-search.haeorumgift.com` -> `222.236.45.35`
- [ ] TLS 발급 방식과 인증서 path 확정
- [x] MSSQL host:port, database, read-only 계정 확인
- [x] MSSQL 서버에서 `222.236.45.35` IP의 `221.143.49.208:1433` 접근 허용 확인
- [x] MSSQL 로그인/View 조회 확인: `dbo.v_ai_search_products`, 진단 기준 total 약 204k row
- [ ] `RISK ACCEPTED` SQL Server 인증서가 self-signed라 `TrustServerCertificate=no`는 실패. 내일 오픈은 `Encrypt=yes;TrustServerCertificate=yes` 임시 예외로 진행
- [x] AI 검색용 View 또는 read-only SELECT query 확인
- [ ] 필수 컬럼: `product_id`, `product_name`, `price`, `category_name`, `main_image_url`, `product_url`, `status`, `updated_at`, `is_deleted` 또는 `display_yn`, `mall_id`
- [x] 삭제/숨김/품절/비노출 상품 규칙: `status=1` 또는 `승인`만 판매중, 나머지는 검색 인덱스 삭제/비노출
- [ ] `updated_at`이 증분 동기화 기준으로 신뢰 가능한지
- [x] Gemini 운영 인증 방식: `api_key`, 서버 env에 보호 저장 완료
- [ ] `STOP` Gemini quota: `gemini-embedding-2` RPM/TPM/RPD Google Console 확인
- [ ] `STOP` Google budget alert 설정
- [x] 최초 노출 origin: `https://www.haeorumgift.com`, 필요 시 `https://haeorumgift.com`
- [x] mall public API key 서버 `malls.json`에 생성 완료
- [ ] 장애 시 기존 검색으로 즉시 돌아가는 fallback/rollback 담당자

## 1. 로컬에서 지금 확인된 상태

- [x] `compose_exposure_check.py`: loopback bind 정책 통과
- [x] `go_live_scenario_check.py`: 정적 시나리오 통과
- [x] `server82.env.example`: 운영 튜닝 항목 보강 완료
- [x] reverse proxy upstream 포트 `8120`으로 정리 완료
- [x] Apache reverse proxy 템플릿 `deploy/apache/haeorum-ai-search.conf` 추가 완료
- [x] `server82-go-live.sh` 작성 완료
- [x] 서버 Docker data-root `/home/docker` 적용 및 로그 로테이션 적용 완료
- [x] `/etc/haeorum-ai-search/haeorum-ai-search.env`, `malls.json`, `cors-origins.txt`, `query-synonyms.json` 실서버 생성 완료
- [ ] `deploy/server-db-intake.md`: Gemini quota/budget 확인 전까지 실패 상태가 정상
- [x] DB 방화벽/IP 허용 완료
- [ ] `RISK ACCEPTED` SQL TLS 인증서 신뢰 문제는 운영 후 개선 항목으로 추적

## 2. 실서버에 파일 배치

서버에서 배포 디렉터리를 하나 정합니다.

```bash
sudo mkdir -p /opt/haeorum-ai-search
sudo chown -R "$USER":"$USER" /opt/haeorum-ai-search
```

코드를 `/opt/haeorum-ai-search`에 반입합니다. Git을 쓸 수 있으면 pull/checkout, 아니면 압축 파일로 복사합니다.

서버 설정 디렉터리를 만듭니다.

```bash
sudo mkdir -p /etc/haeorum-ai-search /var/log/haeorum-ai-search
sudo chmod 750 /etc/haeorum-ai-search /var/log/haeorum-ai-search
```

아래 파일을 실서버에 만듭니다.

```bash
sudo install -m 0640 deploy/server82.env.example /etc/haeorum-ai-search/haeorum-ai-search.env
sudo install -m 0640 deploy/server82-config/malls.example.json /etc/haeorum-ai-search/malls.json
sudo install -m 0640 deploy/server82-config/cors-origins.example.txt /etc/haeorum-ai-search/cors-origins.txt
sudo install -m 0640 deploy/server82-config/query-synonyms.example.json /etc/haeorum-ai-search/query-synonyms.json
```

그 다음 값을 실제 운영값으로 수정합니다.

```bash
sudoedit /etc/haeorum-ai-search/haeorum-ai-search.env
sudoedit /etc/haeorum-ai-search/malls.json
sudoedit /etc/haeorum-ai-search/cors-origins.txt
sudoedit /etc/haeorum-ai-search/query-synonyms.json
```

`STOP`: placeholder가 남아 있으면 진행하지 않습니다.

```bash
sudo grep -R "replace-with\\|change-me\\|dummy\\|sample" /etc/haeorum-ai-search && exit 1 || true
```

## 3. server-db-intake 작성

`deploy/server-db-intake.md`를 실제 값으로 채웁니다. secret은 이 파일에 쓰지 않습니다. secret 전달 방식만 씁니다.

검증합니다.

```bash
cd /opt/haeorum-ai-search/examples/HaeorumAISearch
python3 scripts/server_db_intake_check.py \
  --intake-file deploy/server-db-intake.md \
  --output logs/server-db-intake-check.json \
  --markdown-output logs/server-db-intake-check.md \
  --print-summary
```

`STOP`: status가 `ready_for_env_and_server_preflight`가 아니면 진행하지 않습니다.

## 4. 서버 bootstrap

Docker가 이미 설치되어 있어도 kernel/logrotate 기본값을 맞춥니다.

```bash
cd /opt/haeorum-ai-search/examples/HaeorumAISearch
sudo bash deploy/server82-bootstrap.sh
```

`STOP`: Docker/Compose 버전 출력이 안 나오면 진행하지 않습니다.

## 5. 한 번에 go-live 실행

아래 스크립트가 서버 preflight, env check, compose exposure, stack build/up, health, MSSQL View check, full reindex, sync worker, smoke, runtime audit를 순서대로 실행합니다.

```bash
cd /opt/haeorum-ai-search/examples/HaeorumAISearch
bash deploy/server82-go-live.sh
```

`STOP`: 중간에 실패하면 해당 단계에서 멈추는 것이 정상입니다. 실패 로그를 고치고 같은 명령을 다시 실행합니다.

## 6. Apache/TLS 연결

server82는 Apache가 80/443을 이미 사용 중입니다. Nginx가 아니라 Apache 템플릿을 적용합니다.

```bash
sudo cp deploy/apache/haeorum-ai-search.conf /etc/httpd/conf.d/haeorum-ai-search.conf
```

반드시 확인합니다.

- [ ] `ServerName`이 실제 API 도메인
- [ ] `SSLCertificateFile`, `SSLCertificateKeyFile`이 실제 TLS 인증서
- [ ] `ProxyPass`가 `http://127.0.0.1:8120/`
- [ ] `X-Forwarded-For`가 클라이언트 원격 주소로 덮어쓰기
- [ ] API/Marqo/Gemini port가 외부에 직접 공개되지 않음

적용합니다.

```bash
sudo apachectl configtest
sudo systemctl reload httpd
```

## 7. 공개 smoke

Apache 연결 후 공개 도메인으로 확인합니다.

```bash
curl -fsS https://ai-search.haeorumgift.com/health
curl -fsS -H "X-Admin-Key: <admin-key>" https://ai-search.haeorumgift.com/admin/metrics
```

위젯 삽입 전 API smoke를 실행합니다.

```bash
python3 scripts/api_smoke_test.py \
  --base-url https://ai-search.haeorumgift.com \
  --mall-id <mall-id> \
  --api-key <mall-public-api-key> \
  --origin https://www.haeorumgift.com \
  --mall-config /etc/haeorum-ai-search/malls.json \
  --admin-key <admin-key> \
  --output logs/public-api-smoke.json
```

`STOP`: search response `meta.engine=marqo`, `meta.embedding_backend=gemini`가 아니면 위젯 노출하지 않습니다.

## 8. 위젯 노출

운영 사이트에는 기존 검색 fallback을 유지한 채 AI 검색 버튼/페이지를 붙입니다.

필수 asset:

- `widget/widget.js`
- `widget/ai-search.html`
- `widget/haeorum-logo.jpg`
- `widget/haeorum-ai-hero-bg.jpg`

API base URL은 HTTPS 공개 도메인을 씁니다.

```html
<script
  src="https://ai-search.haeorumgift.com/widget.js"
  data-haeorum-ai-search
  data-api-base-url="https://ai-search.haeorumgift.com"
  data-mall-id="<mall-id>"
  data-api-key="<mall-public-api-key>">
</script>
```

## 9. 최초 운영 모니터링

처음 1시간은 아래를 계속 봅니다.

- [ ] `/admin-ui` Gemini card: `Proxy auth=true`
- [ ] Gemini provider failures `0`
- [ ] circuit open count `0`
- [ ] 429이 정상 사용자에게 발생하지 않음
- [ ] image search queue timeout `0`
- [ ] Marqo/Vespa memory와 disk 증가 추세
- [ ] `/var/log/haeorum-ai-search/error.jsonl`에 반복 오류 없음
- [ ] `/var/log/haeorum-ai-search/sync.jsonl`에 sync failure 없음

## 10. 즉시 롤백 조건

아래 중 하나면 위젯 노출을 끄고 기존 검색으로 되돌립니다.

- AI API 5xx 반복
- Gemini quota/error 급증
- Marqo/Vespa OOM 또는 container restart 반복
- MSSQL read-only query 지연이 기존 쇼핑몰 DB에 영향
- CORS/API key 설정 오류로 정상 사이트 검색 실패
- 상품 URL이 실제 상품 페이지가 아닌 잘못된 경로로 이동

롤백은 AI 위젯 삽입 script 제거 또는 기존 검색 버튼 fallback 우선으로 처리합니다. Docker stack은 원인 분석 전까지 유지해도 되지만, DB sync worker는 필요하면 먼저 중지합니다.

```bash
docker compose \
  --env-file /etc/haeorum-ai-search/haeorum-ai-search.env \
  -f compose-haeorum-marqo.yaml \
  -f compose-haeorum-gemini.yaml \
  -f compose-haeorum-existing-8gb.yaml \
  -f compose-haeorum-server82.yaml \
  --profile sync stop sync-worker
```
