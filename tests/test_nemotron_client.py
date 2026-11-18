import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from python_controller.llm.nvidia_client import NvidiaError, NvidiaPlanner


MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
COMMAND = "앞으로 15cm 이동하고 오른쪽으로 10cm 가"


class NemotronClientTests(unittest.TestCase):
    def request(self, call, *, thinking=False, content="{}", finish="stop", total_tokens=50):
        requests = []

        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "model": MODEL,
                "choices": [{"index": 0, "finish_reason": finish, "message": {
                    "role": "assistant", "content": content, "reasoning_content": "private internal trace",
                }}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": total_tokens,
                          "completion_tokens_details": {"reasoning_tokens": 12}},
            })

        with httpx.Client(transport=httpx.MockTransport(reply)) as http:
            with patch("python_controller.llm.nvidia_client.OpenAI",
                       side_effect=lambda **options: OpenAI(http_client=http, **options)):
                planner = NvidiaPlanner(api_key="test-key", model=MODEL, enable_thinking=thinking)
                result = call(planner)
        self.assertEqual(len(requests), 1)
        return result, requests[0]

    def test_planner_sends_nonstreaming_off_profile_and_extracts_only_final_content(self):
        result, request = self.request(lambda planner: planner.plan(COMMAND))
        self.assertEqual(result, "{}")
        self.assertEqual(request["model"], MODEL)
        self.assertEqual(request["temperature"], .2)
        self.assertEqual(request["max_tokens"], 1024)
        self.assertIs(request["stream"], False)
        self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(request["messages"][-1]["content"], COMMAND)

    def test_command_probe_keeps_metadata_and_passes_on_profile(self):
        result, request = self.request(lambda planner: planner.probe(COMMAND), thinking=True)
        self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["messages"][-1]["content"], COMMAND)
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["reasoning_tokens"], 12)
        self.assertEqual(result["usage"]["total_tokens"], 50)
        self.assertTrue(result["reasoning_present"])
        self.assertEqual(result["text"], "{}")
        self.assertNotIn("private internal trace", json.dumps(result))

    def test_missing_final_content_or_length_finish_does_not_use_reasoning(self):
        with self.assertRaisesRegex(NvidiaError, "NVIDIA_EMPTY_RESPONSE"):
            self.request(lambda planner: planner.plan(COMMAND), content=None)
        result, request = self.request(lambda planner: planner.probe(), thinking=True, content=None, finish="length")
        self.assertEqual(request["max_tokens"], 32)
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["finish_reason"], "length")
        self.assertFalse(result["success"])
        self.assertIsNone(result["text"])

    def test_probe_keeps_only_numeric_usage_in_diagnostic(self):
        result, _ = self.request(lambda planner: planner.probe(), total_tokens="test-key")
        self.assertTrue(result["success"])
        self.assertIsNone(result["usage"]["total_tokens"])
        self.assertNotIn("test-key", json.dumps(result))
