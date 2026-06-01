import unittest
from unittest.mock import patch

from scripts import pre_handoff_audit


class PreHandoffAuditTest(unittest.TestCase):
    def test_security_defaults_pass_for_handoff_files(self) -> None:
        result = pre_handoff_audit.check_security_defaults()

        self.assertTrue(result["ok"], result["details"])

    def test_operator_visible_docs_do_not_reference_legacy_embedding(self) -> None:
        result = pre_handoff_audit.check_operator_visible_docs()

        self.assertTrue(result["ok"], result["details"])

    def test_admin_metrics_requires_gemini_prometheus_names(self) -> None:
        args = pre_handoff_audit.parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8120",
                "--admin-key",
                "admin-key",
            ]
        )

        with (
            patch.object(
                pre_handoff_audit,
                "fetch_json",
                return_value={
                    "data": {
                        "engine": {
                            "embedding_backend": "gemini",
                            "gemini_query_embedding_cache": {},
                            "transport": {"gemini": {}},
                        }
                    }
                },
            ),
            patch.object(
                pre_handoff_audit,
                "fetch_text",
                return_value={"text": "haeorum_gemini_query_vector_runtime_entries 1\n"},
            ),
        ):
            result = pre_handoff_audit.check_admin_metrics(args)

        self.assertTrue(result["ok"], result["details"])

    def test_admin_metrics_rejects_legacy_query_vector_prometheus_names(self) -> None:
        args = pre_handoff_audit.parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8120",
                "--admin-key",
                "admin-key",
            ]
        )

        with (
            patch.object(
                pre_handoff_audit,
                "fetch_json",
                return_value={
                    "data": {
                        "engine": {
                            "embedding_backend": "gemini",
                            "gemini_query_embedding_cache": {},
                            "transport": {"gemini": {}},
                        }
                    }
                },
            ),
            patch.object(
                pre_handoff_audit,
                "fetch_text",
                return_value={"text": "haeorum_qwen_query_vector_runtime_entries 1\n"},
            ),
        ):
            result = pre_handoff_audit.check_admin_metrics(args)

        self.assertFalse(result["ok"], result["details"])
        self.assertIn(
            "Prometheus metrics still expose qwen query vector names in Gemini mode",
            result["details"]["problems"],
        )

    def test_public_search_smoke_uses_configured_mall_id(self) -> None:
        args = pre_handoff_audit.parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8120",
                "--mall-id",
                "real-mall-82",
                "--api-key",
                "public-key",
                "--origin",
                "https://www.haeorumgift.com",
                "--query",
                "텀블러",
            ]
        )
        payloads = []

        def fake_fetch_json(*_args, **kwargs):
            payloads.append(kwargs.get("payload"))
            return {
                "data": {
                    "meta": {
                        "engine": "marqo",
                        "embedding_backend": "gemini",
                    },
                    "items": [{"name": "텀블러"}],
                }
            }

        with patch.object(pre_handoff_audit, "fetch_json", side_effect=fake_fetch_json):
            result = pre_handoff_audit.check_searches(args)

        self.assertTrue(result["ok"], result["details"])
        self.assertEqual("real-mall-82", payloads[0]["mall_id"])
        self.assertEqual("real-mall-82", result["details"]["mall_id"])

    def test_docker_port_binding_problems_reject_public_handoff_ports(self) -> None:
        problems = pre_handoff_audit.docker_port_binding_problems(
            {
                "haeorum-ai-search-marqo-ai-search-1": {"8000/tcp": [{"HostIp": "", "HostPort": "8120"}]},
                "haeorum-ai-search-marqo-marqo-api-1": {
                    "8882/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8122"}]
                },
                "haeorum-ai-search-marqo-gemini-embedding-1": {
                    "8098/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8098"}]
                },
            }
        )

        self.assertGreaterEqual(len(problems), 3)
        self.assertTrue(any("ai-search" in problem for problem in problems))
        self.assertTrue(any("marqo-api" in problem for problem in problems))
        self.assertTrue(any("8098/tcp" in problem for problem in problems))

    def test_docker_port_binding_problems_accept_loopback_handoff_ports(self) -> None:
        problems = pre_handoff_audit.docker_port_binding_problems(
            {
                "haeorum-ai-search-marqo-ai-search-1": {
                    "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8120"}]
                },
                "haeorum-ai-search-marqo-marqo-api-1": {
                    "8882/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8122"}]
                },
                "haeorum-ai-search-marqo-gemini-embedding-1": {
                    "8098/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8098"}]
                },
            }
        )

        self.assertEqual([], problems)


if __name__ == "__main__":
    unittest.main()
