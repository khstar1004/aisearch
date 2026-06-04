# Server and DB Intake Form

Use this when asking the existing developer or infra owner for the final production inputs. Do not paste secrets into chat logs or git; put them in the protected env file on the deployment server.

After filling this file, validate it before deployment:

```bash
python scripts/server_db_intake_check.py \
  --intake-file deploy/server-db-intake.md \
  --output logs/server-db-intake-check.json \
  --markdown-output logs/server-db-intake-check.md \
  --print-summary
```

The check must report `ready_for_env_and_server_preflight` before creating the production env file or connecting to MSSQL.

## 1. Server 82

- SSH host: 222.236.45.35
- SSH port: 22
- SSH user: root
- sudo allowed: yes
- Docker Engine version: Docker version 26.1.4, build 5650f9b
- Docker Compose plugin version: Docker Compose version v2.27.1
- Linux release: CentOS Linux release 7.6.1810 (Core), unsupported baseline accepted only for server82 go-live exception
- CPU cores: 8
- RAM: 8GB installed, 7.5GiB usable, 15GB swap
- Free SSD/NVMe disk path and size: /home/docker on SSD /dev/sda3, 97GB free after Docker data-root change
- Public inbound ports allowed: 80,443 public; 22 admin access only; AI API/Marqo/Gemini ports not public
- Outbound HTTPS allowed: yes
- API/Marqo/Gemini internal bind/listen policy: AI API binds 127.0.0.1:8120; Marqo 127.0.0.1:8122; Gemini 127.0.0.1:8098; Docker service-to-service traffic stays private/internal
- Nginx forwarded header policy: Apache/Nginx reverse proxy must overwrite X-Forwarded-For with $remote_addr and clear Forwarded before proxying
- Docker log rotation values: max-size=20m, max-file=5, Docker Root Dir=/home/docker
- Reverse proxy owner: Apache httpd 2.4 on server82; proxy/headers/ssl modules enabled; use deploy/apache/haeorum-ai-search.conf unless Nginx is installed later
- Production API subdomain: ai-search.haeorumgift.com
- TLS certificate method: Let's Encrypt certbot or existing Apache SSL certificate for ai-search.haeorumgift.com; exact certificate path must be confirmed before Apache reload

Required policy:

- Only Nginx `80/443` is public.
- AI API, Marqo, and Gemini embedding proxy ports are private to localhost or Docker network.
- Nginx overwrites `X-Forwarded-For` with `$remote_addr`.
- Docker json logs rotate with `HAEORUM_DOCKER_LOG_MAX_SIZE` and `HAEORUM_DOCKER_LOG_MAX_FILE`.

## 2. MSSQL

- SQL Server host and port: 221.143.49.208:1433
- Database: jclgift
- Read-only username: readonly_user
- Password delivery method: stored only in protected /etc/haeorum-ai-search/haeorum-ai-search.env on server82; not stored in git or this markdown
- ODBC driver version allowed: ODBC Driver 18 for SQL Server in Docker image; local diagnostic used ODBC Driver 17
- Encryption required: yes
- `TrustServerCertificate` allowed: temporary yes for server82 go-live only; risk accepted until SQL Server certificate chain is fixed
- Read-only View name: dbo.v_ai_search_products
- Incremental sync timestamp column: updated_at
- Product deletion/hidden/sold-out rules: status=1 or status label 승인 is active/sale; status 0/2/3/4/5/6/7 or 승인대기/삭제/승인보류/관리비미납/일시품절/가맹점상품/가맹점삭제/알수없음 is inactive and should be deleted from search index
- Mall identifier column: mall_id, fallback to haeorumgift when blank
- Product detail URL template: https://www.haeorumgift.com/product_view.asp?p_idx={product_id}

Connection string must include:

```text
Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly
```

Target hardened connection string after DBA certificate fix:

```text
Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly
```

Required View columns:

```text
product_id, product_name, price, category_name, main_image_url, product_url,
status, updated_at, is_deleted or display_yn, mall_id
```

- SQL Server host firewall status: PASS - server82 outbound IP 222.236.45.35 can open TCP 1433 to 221.143.49.208
- SQL TLS certificate status: RISK ACCEPTED - strict connection with Encrypt=yes;TrustServerCertificate=no fails because SQL Server presents a self-signed certificate; server82 go-live uses Encrypt=yes;TrustServerCertificate=yes as a temporary exception

## 3. Gemini

- Production auth method: API key
- If API key: key stored only in `/etc/haeorum-ai-search/haeorum-ai-search.env`
- If ADC: quota project ID and read-only credential mount path
- Internal Gemini proxy key delivery method: matching GEMINI_PROXY_API_KEY and HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY stored only in protected server env
- Gemini quota page checked for `gemini-embedding-2`: no - STOP until Google Console quota page is checked
- Budget alert configured: no - STOP until Google Cloud billing budget alert is configured
- Usage dashboard owner: haeorumgift operations owner to confirm in Google Cloud console

## 4. Haeorum Site Integration

- First rollout page(s): https://www.haeorumgift.com search/result flow, with https://haeorumgift.com origin allowed if used
- Exact CORS origins: https://www.haeorumgift.com, https://haeorumgift.com
- Public API key per mall/site: generated and stored in protected /etc/haeorum-ai-search/malls.json on server82; not stored in git or this markdown
- Widget insertion location: existing search entry plus /ai-search/ai-search.html result page
- Fallback behavior if AI API is down: keep existing classic search path and remove/disable AI widget script to restore 기존 검색 immediately
- Admin contact for rollback: haeorumgift operations/server root admin

## 5. Acceptance Evidence

Before go-live, collect:

- `env_check.py` production report
- `/health` and `/admin/metrics` screenshots or JSON
- text/image/mixed smoke tests
- real-server text 100 concurrent load report
- real-server image/mixed 30 concurrent load report
- Gemini usage/admin metrics capture
- Marqo/Vespa resource report
- rollback test confirmation
