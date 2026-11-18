import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
import httpx

from korean_command_cases import AMBIGUOUS, CONNECTORS, DENIED, DISTANCES, ENDINGS
from python_controller.llm.nvidia_client import NvidiaError, NvidiaPlanner
from python_controller.planning.validator import PlanError, parse_operator_command, validate_plan


TEST_MODEL = "qwen/qwen3-next-80b-a3b-instruct"


def response(*actions, clarify=False):
    return json.dumps(dict(needs_clarification=clarify, explanation="이동 계획", actions=actions))


def move(direction="forward", distance=15):
    return dict(type="move", direction=direction, distance_cm=distance)


class NvidiaTests(unittest.TestCase):
    def test_real_sdk_shapes_request_against_mock_http(self):
        def reply(request):
            self.assertEqual(str(request.url), "https://integrate.api.nvidia.com/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertEqual(json.loads(request.content)["model"], "qwen/qwen3-next-80b-a3b-instruct")
            return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop",
                                                          "message": {"role": "assistant", "content": "{}"}}]})

        http = httpx.Client(transport=httpx.MockTransport(reply))
        with patch("python_controller.llm.nvidia_client.OpenAI",
                   side_effect=lambda **kwargs: OpenAI(http_client=http, **kwargs)):
            self.assertEqual(NvidiaPlanner(api_key="test-key", model=TEST_MODEL).plan("앞으로 15cm"), "{}")

    @patch("python_controller.llm.nvidia_client.OpenAI")
    def test_non_streaming_chat_completions_with_no_retry(self, sdk):
        result = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}", tool_calls=None))
        client = sdk.return_value.__enter__.return_value
        client.chat.completions.create.return_value = SimpleNamespace(choices=[result])
        planner = NvidiaPlanner(api_key="test-key", model=TEST_MODEL)
        self.assertEqual(planner.plan("앞으로 15cm 가"), "{}")
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
        self.assertEqual(sdk.call_args.kwargs["timeout"], 20)
        request = client.chat.completions.create.call_args.kwargs
        self.assertFalse(request["stream"])
        self.assertEqual(request["model"], "qwen/qwen3-next-80b-a3b-instruct")
        self.assertEqual(request["messages"][-1]["content"], "앞으로 15cm 가")

    @patch("python_controller.llm.nvidia_client.OpenAI")
    def test_errors_and_incomplete_responses_are_closed(self, sdk):
        create = sdk.return_value.__enter__.return_value.chat.completions.create
        planner = NvidiaPlanner(api_key="test-key", model=TEST_MODEL)
        request = httpx.Request("POST", "https://example.test")
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                create.side_effect = APIStatusError("private", response=httpx.Response(status, request=request), body=None)
                with self.assertRaisesRegex(NvidiaError, f"HTTP_{status}"):
                    planner.plan("앞으로 15cm")
        for error in (APITimeoutError(request), APIConnectionError(request=request)):
            create.side_effect = error
            with self.assertRaises(NvidiaError):
                planner.plan("앞으로 15cm")
        create.side_effect = None
        for reason, content in (("length", "{}"), ("stop", ""), ("tool_calls", "{}")):
            result = SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content, tool_calls=None))
            create.return_value = SimpleNamespace(choices=[result])
            with self.assertRaises(NvidiaError):
                planner.plan("앞으로 15cm")


class PlanTests(unittest.TestCase):
    def test_reference_scenario_and_units(self):
        command = parse_operator_command("앞으로 15cm 가고 오른쪽으로 0.1m 이동해")
        plan = validate_plan(response(move(), move("right", 10)), command)
        self.assertEqual([item.distance_cm for item in plan.actions], [15, 10])
        self.assertEqual(parse_operator_command("뒤로 50 mm 가")[0].distance_cm, 5)

    def test_exact_operator_intent_required(self):
        command = parse_operator_command("앞으로 15cm 가")
        for actions in ((move("backward"),), (move(distance=25),), (move(), {"type": "stop"})):
            with self.subTest(actions=actions), self.assertRaises(PlanError):
                validate_plan(response(*actions), command)

    def test_strict_json_and_policy(self):
        command = parse_operator_command("앞으로 15cm")
        valid = response(move())
        invalid = [
            "```json\n" + valid + "\n```", valid + valid, valid[:-1], "[]",
            valid.replace('"type": "move"', '"type": "stop", "type": "move"'),
            response(move(distance=True)), response(move(distance="15")), response(move(distance=1e99)),
            response(move(distance=float("nan"))), response(move(distance=float("inf"))),
            response(dict(move(), pwm=120)), response({"type": "rotate"}), response(move(distance=4)),
            response(move(distance=51)), response(move(), clarify=True), response(),
            valid.replace('"distance_cm": 15', '"distance_cm": 15.00000000000000000000000000001'),
            response(move(), {"type": "stop", "direction": "left"}),
            response({"type": "stop"}, move()), response(*[move()] * 5),
            response(*[move(distance=50)] * 3),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(PlanError):
                validate_plan(raw, command)

    def test_clarification_validates_only_without_actions(self):
        plan = validate_plan(response(clarify=True), parse_operator_command("앞으로 15cm"))
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.actions)

    def test_normal_and_compound_korean_corpus(self):
        for forms, tail in ((ENDINGS, ()), (CONNECTORS, (move("right", 10),))):
            self.assertEqual(len(forms) * len(DISTANCES), 25)
            for form in forms:
                for phrase, direction, distance in DISTANCES:
                    command = form.format(phrase)
                    with self.subTest(command=command):
                        requested = parse_operator_command(command)
                        plan = validate_plan(response(move(direction, distance), *tail), requested)
                        self.assertEqual(len(plan.actions), 1 + len(tail))

    def test_ambiguous_and_out_of_policy_korean_corpus(self):
        for cases in (AMBIGUOUS, DENIED):
            self.assertEqual(len(cases), 25)
            for command in cases:
                with self.subTest(command=command), self.assertRaises(PlanError):
                    parse_operator_command(command)


if __name__ == "__main__":
    unittest.main()
