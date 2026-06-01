import unittest

from scripts.compose_exposure_check import check_config


class ComposeExposureCheckTest(unittest.TestCase):
    def test_accepts_loopback_ai_marqo_and_embedding_ports(self) -> None:
        checks = check_config(
            {
                "services": {
                    "ai-search": {"ports": [{"host_ip": "127.0.0.1", "target": 8000, "published": "8000"}]},
                    "marqo-api": {"ports": [{"host_ip": "127.0.0.1", "target": 8882, "published": "8882"}]},
                    "embedding-service": {"ports": [{"host_ip": "127.0.0.1", "target": 8098, "published": "8098"}]},
                }
            }
        )

        self.assertTrue(all(check["ok"] for check in checks), checks)

    def test_rejects_public_bind_and_missing_loopback_embedding_publication(self) -> None:
        checks = check_config(
            {
                "services": {
                    "ai-search": {"ports": [{"host_ip": "0.0.0.0", "target": 8000, "published": "8000"}]},
                    "marqo-api": {"ports": [{"host_ip": "", "target": 8882, "published": "8882"}]},
                    "embedding-service": {"ports": []},
                }
            }
        )
        by_name = {check["name"]: check for check in checks}

        self.assertFalse(by_name["protected_ports_loopback_only"]["ok"])
        self.assertFalse(by_name["expected_loopback_ports_present"]["ok"])
        self.assertFalse(by_name["embedding_proxy_loopback_only"]["ok"])

    def test_rejects_public_embedding_bind(self) -> None:
        checks = check_config(
            {
                "services": {
                    "ai-search": {"ports": [{"host_ip": "127.0.0.1", "target": 8000, "published": "8000"}]},
                    "marqo-api": {"ports": [{"host_ip": "127.0.0.1", "target": 8882, "published": "8882"}]},
                    "embedding-service": {"ports": [{"host_ip": "0.0.0.0", "target": 8098, "published": "8098"}]},
                }
            }
        )
        by_name = {check["name"]: check for check in checks}

        self.assertFalse(by_name["protected_ports_loopback_only"]["ok"])
        self.assertFalse(by_name["embedding_proxy_loopback_only"]["ok"])


if __name__ == "__main__":
    unittest.main()
