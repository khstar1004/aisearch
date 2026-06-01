from __future__ import annotations

import base64
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gemini_embeddings import (  # noqa: E402
    GeminiEmbeddingSettings,
    GeminiProviderError,
    embed_inputs_with_gemini,
    gemini_content_for_input,
    gemini_model_resource,
    image_bytes_from_data_url,
    normalize_embedding_inputs,
    read_gemini_response_limited,
    transient_gemini_provider_error,
)
from app.gemini_embedding_proxy import app as proxy_app  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe"
    b"\x02\xfeA\xe2\x1d\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class GeminiEmbeddingProxyTests(unittest.TestCase):
    def test_model_resource_adds_models_prefix(self) -> None:
        self.assertEqual("models/gemini-embedding-2", gemini_model_resource("gemini-embedding-2"))
        self.assertEqual("models/custom", gemini_model_resource("models/custom"))

    def test_normalize_inputs_accepts_text_or_image(self) -> None:
        self.assertEqual([{"text": "검은 우산"}], normalize_embedding_inputs([{"text": "  검은 우산  "}]))
        self.assertEqual(
            [{"image": "data:image/png;base64,AA=="}],
            normalize_embedding_inputs([{"image": "data:image/png;base64,AA=="}]),
        )
        with self.assertRaises(ValueError):
            normalize_embedding_inputs([{"text": "x", "image": "y"}])
        with self.assertRaises(ValueError):
            normalize_embedding_inputs([{}])

    def test_data_url_image_is_converted_to_gemini_inline_data(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode("ascii")
        mime_type, raw = image_bytes_from_data_url(data_url, max_bytes=1024)
        self.assertEqual("image/png", mime_type)
        self.assertEqual(PNG_1X1, raw)
        content, kind, download_ms = gemini_content_for_input(
            {"image": data_url},
            settings=GeminiEmbeddingSettings(api_key="test", dimensions=3),
        )
        self.assertEqual("image", kind)
        self.assertGreaterEqual(download_ms, 0.0)
        inline = content["parts"][0]["inline_data"]
        self.assertEqual("image/png", inline["mime_type"])
        self.assertEqual(base64.b64encode(PNG_1X1).decode("ascii"), inline["data"])

    def test_embed_inputs_uses_transport_and_validates_dimensions(self) -> None:
        settings = GeminiEmbeddingSettings(api_key="test", dimensions=3)
        calls = []

        def fake_transport(method, payload, settings):  # type: ignore[no-untyped-def]
            calls.append(
                (
                    method,
                    payload["content"]["parts"][0]["text"],
                    payload["embedContentConfig"]["taskType"],
                )
            )
            return {"embedding": {"values": [0.1, 0.2, 0.3]}}

        embeddings, stats = embed_inputs_with_gemini(
            [{"text": "검은 우산"}],
            settings=settings,
            prompt="Retrieve products",
            transport=fake_transport,
        )
        self.assertEqual([[0.1, 0.2, 0.3]], embeddings)
        self.assertEqual(1, stats.text_inputs)
        self.assertEqual(0, stats.image_inputs)
        self.assertEqual([("embedContent", "검은 우산", "RETRIEVAL_QUERY")], calls)

    def test_gemini_provider_response_read_is_bounded(self) -> None:
        self.assertEqual(b"ok", read_gemini_response_limited(io.BytesIO(b"ok"), max_bytes=2))
        with self.assertRaises(GeminiProviderError) as raised:
            read_gemini_response_limited(io.BytesIO(b"toolarge"), max_bytes=3)
        self.assertFalse(transient_gemini_provider_error(raised.exception))

    def test_proxy_metrics_endpoint_loads_settings_and_reports_limits(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_AUTH_MODE": "api_key"}, clear=False):
            response = TestClient(proxy_app).get("/metrics")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertTrue(data["ready"])
        self.assertEqual("gemini", data["provider"])
        self.assertEqual("api_key", data["auth_mode"])
        self.assertEqual(33554432, data["max_response_bytes"])
        self.assertIn("provider_retry_count", data["limits"])
        self.assertIn("max_response_bytes", data["limits"])

    def test_proxy_allows_zero_retry_count_and_delay(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_AUTH_MODE": "api_key",
                "GEMINI_PROVIDER_RETRY_COUNT": "0",
                "GEMINI_PROVIDER_RETRY_DELAY_SECONDS": "0",
            },
            clear=False,
        ):
            response = TestClient(proxy_app).get("/health")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertTrue(data["ready"])
        self.assertEqual(0, data["limits"]["provider_retry_count"])
        self.assertEqual(0.0, data["limits"]["provider_retry_delay_seconds"])

    def test_proxy_health_rejects_invalid_auth_mode(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_AUTH_MODE": "invalid"}, clear=False):
            response = TestClient(proxy_app).get("/health")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertFalse(data["ready"])
        self.assertIn("GEMINI_AUTH_MODE", data["loadError"])

    def test_proxy_health_reports_invalid_numeric_env_without_500(self) -> None:
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "GEMINI_AUTH_MODE": "api_key", "GEMINI_EMBEDDING_DIMENSIONS": "bad"},
            clear=False,
        ):
            response = TestClient(proxy_app).get("/health")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertFalse(data["ready"])
        self.assertIn("GEMINI_EMBEDDING_DIMENSIONS", data["loadError"])

    def test_proxy_embed_missing_auth_is_service_unavailable(self) -> None:
        with patch.dict(os.environ, {"GEMINI_AUTH_MODE": "api_key"}, clear=True):
            response = TestClient(proxy_app).post("/embed", json={"inputs": [{"text": "검은 우산"}]})

        self.assertEqual(503, response.status_code)
        self.assertIn("not configured", response.json()["detail"])

    def test_proxy_embed_invalid_numeric_env_is_service_unavailable(self) -> None:
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "GEMINI_AUTH_MODE": "api_key", "GEMINI_MAX_RESPONSE_BYTES": "bad"},
            clear=True,
        ):
            response = TestClient(proxy_app).post("/embed", json={"inputs": [{"text": "검은 우산"}]})

        self.assertEqual(503, response.status_code)
        self.assertIn("GEMINI_MAX_RESPONSE_BYTES", response.json()["detail"])

    def test_proxy_embed_requires_internal_proxy_key_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_AUTH_MODE": "api_key",
                "GEMINI_PROXY_API_KEY": "internal-secret",
            },
            clear=True,
        ):
            missing = TestClient(proxy_app).post("/embed", json={"inputs": [{"text": "검은 우산"}]})
            wrong = TestClient(proxy_app).post(
                "/embed",
                json={"inputs": [{"text": "검은 우산"}]},
                headers={"X-Embedding-Proxy-Key": "wrong"},
            )

        self.assertEqual(401, missing.status_code)
        self.assertEqual(401, wrong.status_code)
        self.assertIn("authentication required", missing.json()["detail"])

    def test_proxy_embed_accepts_internal_proxy_key_before_provider_call(self) -> None:
        class FakeStats:
            def __init__(self) -> None:
                self.provider_elapsed_ms = 1.0
                self.image_downloads = 0

        def fake_embed(inputs, *, settings, prompt=None):  # type: ignore[no-untyped-def]
            return [[0.1, 0.2, 0.3]], FakeStats()

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_AUTH_MODE": "api_key",
                "GEMINI_EMBEDDING_DIMENSIONS": "3",
                "GEMINI_PROXY_API_KEY": "internal-secret",
            },
            clear=True,
        ):
            with patch("app.gemini_embedding_proxy.embed_inputs_with_gemini", fake_embed):
                response = TestClient(proxy_app).post(
                    "/embed",
                    json={"inputs": [{"text": "검은 우산"}]},
                    headers={"X-Embedding-Proxy-Key": "internal-secret"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual([[0.1, 0.2, 0.3]], response.json()["embeddings"])


if __name__ == "__main__":
    unittest.main()
