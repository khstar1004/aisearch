import unittest
from unittest.mock import patch

from scripts import go_live_scenario_check


class GoLiveScenarioCheckTest(unittest.TestCase):
    def test_static_scenarios_pass_for_current_tree(self) -> None:
        report = go_live_scenario_check.build_report(go_live_scenario_check.parse_args([]))

        self.assertTrue(report["ok"], report["failed_checks"])
        self.assertEqual(len(go_live_scenario_check.SCENARIOS), report["summary"]["scenario_checks"])
        self.assertEqual(1, report["summary"]["operator_surface_checks"])

    def test_operator_surface_reports_legacy_runtime_terms(self) -> None:
        with patch.object(go_live_scenario_check, "read_text", return_value="Use HAEORUM_EMBEDDING_BACKEND=qwen here"):
            result = go_live_scenario_check.check_operator_surface()

        self.assertFalse(result["ok"])
        self.assertEqual("operator_surface_gemini_only", result["id"])
        self.assertTrue(result["findings"])

    def test_scenario_reports_missing_required_token(self) -> None:
        scenario = {
            "id": "missing_probe",
            "risk": "probe",
            "required_tokens": {"README.md": ["token-that-should-not-exist-in-readme"]},
        }

        result = go_live_scenario_check.check_scenario(scenario)

        self.assertFalse(result["ok"])
        self.assertEqual("README.md", result["missing"][0]["file"])

    def test_runtime_checks_require_gemini_marqo_and_observability(self) -> None:
        args = go_live_scenario_check.parse_args(
            ["--base-url", "http://127.0.0.1:8120", "--admin-key", "admin-key"]
        )

        def fake_fetch_json(url, **_kwargs):
            if url.endswith("/health"):
                return {
                    "data": {
                        "ready": True,
                        "engine": "marqo",
                        "embedding_backend": "gemini",
                        "gemini_ready": True,
                        "gemini": {"proxy_auth_configured": True},
                        "stats": {"numberOfDocuments": 1000},
                    }
                }
            return {
                "data": {
                    "engine": {
                        "transport": {"marqo": {}, "gemini": {}},
                        "gemini": {"proxy_auth_configured": True},
                        "gemini_query_embedding_cache": {},
                    },
                    "rate_limit": {},
                    "search_queue": {},
                    "image_queue": {},
                    "cache": {},
                    "alerts": [{"level": "warning", "code": "rate_limited_requests"}],
                }
            }

        with patch.object(go_live_scenario_check, "fetch_json", side_effect=fake_fetch_json):
            checks = go_live_scenario_check.build_runtime_checks(args)

        self.assertTrue(all(check["ok"] for check in checks), checks)

    def test_runtime_checks_fail_on_critical_alerts(self) -> None:
        args = go_live_scenario_check.parse_args(
            ["--base-url", "http://127.0.0.1:8120", "--admin-key", "admin-key"]
        )

        def fake_fetch_json(url, **_kwargs):
            if url.endswith("/health"):
                return {
                    "data": {
                        "ready": True,
                        "engine": "marqo",
                        "embedding_backend": "gemini",
                        "gemini_ready": True,
                        "gemini": {"proxy_auth_configured": True},
                    }
                }
            return {
                "data": {
                    "engine": {
                        "transport": {"marqo": {}, "gemini": {}},
                        "gemini": {"proxy_auth_configured": True},
                        "gemini_query_embedding_cache": {},
                    },
                    "rate_limit": {},
                    "search_queue": {},
                    "image_queue": {},
                    "cache": {},
                    "alerts": [{"level": "critical", "code": "engine_unhealthy"}],
                }
            }

        with patch.object(go_live_scenario_check, "fetch_json", side_effect=fake_fetch_json):
            checks = go_live_scenario_check.build_runtime_checks(args)

        by_id = {check["id"]: check for check in checks}
        self.assertFalse(by_id["runtime_critical_alerts_absent"]["ok"])

    def test_runtime_public_search_checks_utf8_and_malformed_query(self) -> None:
        args = go_live_scenario_check.parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8120",
                "--admin-key",
                "admin-key",
                "--mall-id",
                "shop001",
                "--origin",
                "http://127.0.0.1:3000",
                "--public-api-key",
                "public-key",
            ]
        )

        def fake_fetch_json(url, **_kwargs):
            if url.endswith("/health"):
                return {
                    "status": 200,
                    "data": {
                        "ready": True,
                        "engine": "marqo",
                        "embedding_backend": "gemini",
                        "gemini_ready": True,
                        "gemini": {"proxy_auth_configured": True},
                    },
                }
            return {
                "status": 200,
                "data": {
                    "engine": {
                        "transport": {"marqo": {}, "gemini": {}},
                        "gemini": {"proxy_auth_configured": True},
                        "gemini_query_embedding_cache": {},
                    },
                    "rate_limit": {},
                    "search_queue": {},
                    "image_queue": {},
                    "cache": {},
                    "alerts": [],
                },
            }

        def fake_post_json(_url, payload, **_kwargs):
            if payload["q"] == "?? ??":
                return {
                    "status": 422,
                    "data": {"detail": "q appears to be malformed or incorrectly encoded; send UTF-8 text"},
                }
            return {
                "status": 200,
                "data": {"meta": {"query_type": "text", "engine": "marqo", "embedding_backend": "gemini"}},
            }

        with patch.object(go_live_scenario_check, "fetch_json", side_effect=fake_fetch_json), patch.object(
            go_live_scenario_check, "post_json", side_effect=fake_post_json
        ):
            checks = go_live_scenario_check.build_runtime_checks(args)

        by_id = {check["id"]: check for check in checks}
        self.assertTrue(by_id["runtime_utf8_korean_query_accepted"]["ok"])
        self.assertTrue(by_id["runtime_malformed_query_rejected"]["ok"])


if __name__ == "__main__":
    unittest.main()
