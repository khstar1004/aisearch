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

- SSH host:
- SSH port:
- SSH user:
- sudo allowed: yes/no
- Docker Engine version:
- Docker Compose plugin version:
- Linux release:
- CPU cores:
- RAM:
- Free SSD/NVMe disk path and size:
- Public inbound ports allowed:
- Outbound HTTPS allowed: yes/no
- API/Marqo/Gemini internal bind/listen policy:
- Nginx forwarded header policy:
- Docker log rotation values:
- Reverse proxy owner:
- Production API subdomain:
- TLS certificate method:

Required policy:

- Only Nginx `80/443` is public.
- AI API, Marqo, and Gemini embedding proxy ports are private to localhost or Docker network.
- Nginx overwrites `X-Forwarded-For` with `$remote_addr`.
- Docker json logs rotate with `HAEORUM_DOCKER_LOG_MAX_SIZE` and `HAEORUM_DOCKER_LOG_MAX_FILE`.

## 2. MSSQL

- SQL Server host and port:
- Database:
- Read-only username:
- Password delivery method:
- ODBC driver version allowed:
- Encryption required: yes/no
- `TrustServerCertificate` allowed: yes/no
- Read-only View name:
- Incremental sync timestamp column:
- Product deletion/hidden/sold-out rules:
- Mall identifier column:
- Product detail URL template:

Connection string must include:

```text
Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly
```

Required View columns:

```text
product_id, product_name, price, category_name, main_image_url, product_url,
status, updated_at, is_deleted or display_yn, mall_id
```

## 3. Gemini

- Production auth method: API key or ADC
- If API key: key stored only in `/etc/haeorum-ai-search/haeorum-ai-search.env`
- If ADC: quota project ID and read-only credential mount path
- Internal Gemini proxy key delivery method:
- Gemini quota page checked for `gemini-embedding-2`:
- Budget alert configured:
- Usage dashboard owner:

## 4. Haeorum Site Integration

- First rollout page(s):
- Exact CORS origins:
- Public API key per mall/site:
- Widget insertion location:
- Fallback behavior if AI API is down:
- Admin contact for rollback:

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
