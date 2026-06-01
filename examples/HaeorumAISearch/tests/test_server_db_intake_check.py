import tempfile
import unittest
from pathlib import Path

from scripts import server_db_intake_check


FILLED_INTAKE = """# Server and DB Intake Form

## 1. Server 82

- SSH host: ai82.example.com
- SSH port: 22
- SSH user: deploy
- sudo allowed: yes
- Docker Engine version: 26.1.0
- Docker Compose plugin version: 2.27.0
- Linux release: Ubuntu 22.04
- CPU cores: 8
- RAM: 16GB
- Free SSD/NVMe disk path and size: /data SSD 200GB
- Public inbound ports allowed: 80,443
- Outbound HTTPS allowed: yes
- API/Marqo/Gemini internal bind/listen policy: API, Marqo, and Gemini bind to 127.0.0.1 or Docker internal network only
- Nginx forwarded header policy: overwrite X-Forwarded-For with $remote_addr and clear Forwarded
- Docker log rotation values: max-size=20m, max-file=5
- Reverse proxy owner: infra
- Production API subdomain: ai-search.example.com
- TLS certificate method: certbot

## 2. MSSQL

- SQL Server host and port: sql.example.com:1433
- Database: haeorum
- Read-only username: ai_search_ro
- Password delivery method: protected env file on server
- ODBC driver version allowed: ODBC Driver 18
- Encryption required: yes
- `TrustServerCertificate` allowed: no
- Read-only View name: dbo.v_ai_search_products
- Incremental sync timestamp column: updated_at, UTC, changes on product update
- Product deletion/hidden/sold-out rules: is_deleted=1 or display_yn=N or status in hidden/soldout removes from index
- Mall identifier column: mall_id
- Product detail URL template: https://shop.example.com/product_view.asp?p_idx={product_id}

## 3. Gemini

- Production auth method: API key
- If API key: key stored only in `/etc/haeorum-ai-search/haeorum-ai-search.env`
- If ADC: quota project ID and read-only credential mount path
- Internal Gemini proxy key delivery method: set matching GEMINI_PROXY_API_KEY and HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY in protected env file
- Gemini quota page checked for `gemini-embedding-2`: yes
- Budget alert configured: yes
- Usage dashboard owner: ops@example.com

## 4. Haeorum Site Integration

- First rollout page(s): https://www.example.com/search
- Exact CORS origins: https://www.example.com, https://shop.example.com
- Public API key per mall/site: delivered via protected env file
- Widget insertion location: search form next to submit button
- Fallback behavior if AI API is down: keep existing classic search and hide AI widget
- Admin contact for rollback: ops@example.com
"""


class ServerDbIntakeCheckTest(unittest.TestCase):
    def test_template_shape_passes_with_template_ok(self) -> None:
        report = server_db_intake_check.build_report(
            server_db_intake_check.DEFAULT_INTAKE,
            require_filled=False,
        )

        self.assertTrue(report["ok"], report["failed_checks"])
        self.assertEqual("template_shape_ok", report["status"])

    def test_filled_intake_passes_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "intake.md"
            path.write_text(FILLED_INTAKE, encoding="utf-8")

            report = server_db_intake_check.build_report(path)

        self.assertTrue(report["ok"], report["checks"])
        self.assertEqual("ready_for_env_and_server_preflight", report["status"])

    def test_rejects_public_ai_api_port_and_unsafe_mssql_flags(self) -> None:
        bad = FILLED_INTAKE.replace("80,443", "80,443,8120").replace(
            "- `TrustServerCertificate` allowed: no",
            "- `TrustServerCertificate` allowed: yes",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "intake.md"
            path.write_text(bad, encoding="utf-8")

            report = server_db_intake_check.build_report(path)

        self.assertFalse(report["ok"])
        checks = {check["name"]: check for check in report["checks"]}
        self.assertIn("AI API/Marqo/Gemini ports must not be public inbound ports", checks["server_policy"]["details"]["problems"])
        self.assertIn("TrustServerCertificate must not be allowed", checks["mssql_policy"]["details"]["problems"])

    def test_rejects_missing_proxy_and_log_rotation_controls(self) -> None:
        bad = (
            FILLED_INTAKE.replace(
                "- Nginx forwarded header policy: overwrite X-Forwarded-For with $remote_addr and clear Forwarded",
                "- Nginx forwarded header policy: trust incoming client headers",
            )
            .replace(
                "- Docker log rotation values: max-size=20m, max-file=5",
                "- Docker log rotation values: default docker logging",
            )
            .replace(
                "- API/Marqo/Gemini internal bind/listen policy: API, Marqo, and Gemini bind to 127.0.0.1 or Docker internal network only",
                "- API/Marqo/Gemini internal bind/listen policy: public bind",
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "intake.md"
            path.write_text(bad, encoding="utf-8")

            report = server_db_intake_check.build_report(path)

        self.assertFalse(report["ok"])
        checks = {check["name"]: check for check in report["checks"]}
        problems = checks["server_policy"]["details"]["problems"]
        self.assertIn("Nginx forwarded header policy must overwrite X-Forwarded-For with $remote_addr", problems)
        self.assertIn("Docker log rotation values must include max-size and max-file", problems)
        self.assertIn("API/Marqo/Gemini internal bind/listen policy must keep API/Marqo/Gemini ports private", problems)

    def test_rejects_plaintext_secret_like_values(self) -> None:
        bad = FILLED_INTAKE + "\nPassword=plain-text-secret\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "intake.md"
            path.write_text(bad, encoding="utf-8")

            report = server_db_intake_check.build_report(path)

        self.assertFalse(report["ok"])
        self.assertIn("no_plaintext_secrets", report["failed_checks"])


if __name__ == "__main__":
    unittest.main()
