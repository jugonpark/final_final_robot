import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from python_controller.llm.nvidia_client import NvidiaError, NvidiaPlanner


class NvidiaDiagnosticTests(unittest.TestCase):
    def probe(self, reply):
        requests = []

        def record(request):
            requests.append(request)
            return reply(request)

        with httpx.Client(transport=httpx.MockTransport(record)) as http:
            with patch("python_controller.llm.nvidia_client.OpenAI",
                       side_effect=lambda **options: OpenAI(http_client=http, **options)):
                result = NvidiaPlanner(api_key="fixture-secret", model="qwen/probe",
                                       base_url="https://nim.example.test/v1").probe()
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://nim.example.test/v1/chat/completions")
        self.assertEqual(json.loads(requests[0].content)["max_tokens"], 32)
        self.assertNotIn("fixture-secret", json.dumps(result))
        return result

    def test_probe_reports_actual_response_metadata_and_redacts_key(self):
        result = self.probe(lambda _: httpx.Response(200, json={
            "model": "qwen/served", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "OK fixture-secret"}}],
        }))
        self.assertTrue(result["success"])
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["model"], "qwen/probe")
        self.assertEqual(result["returned_model"], "qwen/served")
        self.assertEqual(result["text"], "OK [redacted]")
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_probe_reports_distinct_http_errors_without_retry_or_error_body(self):
        for status in (401, 403, 404, 410, 429, 500, 503):
            with self.subTest(status=status):
                result = self.probe(lambda _: httpx.Response(status, json={
                    "error": {"message": "fixture-secret must stay private"},
                }))
                self.assertFalse(result["success"])
                self.assertEqual(result["http_status"], status)
                self.assertEqual(result["error_type"], f"NVIDIA_HTTP_{status}")
                self.assertIsNone(result["text"])

    def test_probe_distinguishes_timeout_from_connection_failure(self):
        for error, code in ((httpx.ReadTimeout, "NVIDIA_TIMEOUT"),
                            (httpx.ConnectError, "NVIDIA_NETWORK_ERROR")):
            def fail(request):
                raise error("fixture-secret", request=request)

            with self.subTest(code=code):
                result = self.probe(fail)
                self.assertFalse(result["success"])
                self.assertIsNone(result["http_status"])
                self.assertEqual(result["error_type"], code)

    @patch("python_controller.llm.nvidia_client.OpenAI")
    def test_missing_key_or_model_fails_before_network(self, sdk):
        for key, model, code in ((None, None, "NVIDIA_API_KEY_MISSING"),
                                 ("fixture-secret", None, "NVIDIA_MODEL_MISSING"),
                                 ("fixture-secret", "fixture-secret", "NVIDIA_MODEL_INVALID")):
            with self.subTest(code=code), self.assertRaisesRegex(NvidiaError, code):
                NvidiaPlanner(api_key=key, model=model)
        sdk.assert_not_called()
