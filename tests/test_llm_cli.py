import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from python_controller.llm_cli import main
from python_controller.llm.nvidia_client import NvidiaError


MODEL_REPLY = json.dumps(dict(needs_clarification=False, explanation="not for the log",
                              actions=[dict(type="move", direction="forward", distance_cm=15)]))


class CLITests(unittest.TestCase):
    def run_cli(self, *args, reply=MODEL_REPLY, api_error=None, answers=()):
        with TemporaryDirectory() as folder, redirect_stdout(StringIO()) as stdout:
            with (patch.dict(os.environ, {"NVIDIA_API_KEY": "test-secret", "NVIDIA_MODEL": "qwen/fixture",
                                         "ESP32_IP": "127.0.0.1"}, clear=True),
                  patch("python_controller.llm_cli.NvidiaPlanner") as planner,
                  patch("python_controller.llm_cli.LLMMotionExecutor") as executor,
                  patch("python_controller.llm_cli.sys.stdin.isatty", return_value=True),
                  patch("builtins.input", side_effect=answers)):
                plan = planner.return_value.plan
                plan.return_value, plan.side_effect = reply, api_error
                executor.return_value.run.side_effect = lambda *_a, **_kw: executor.call_args.args[1]("COMPLETED")
                code = main(list(args), log_dir=Path(folder))
                raw = next(Path(folder).glob("*.jsonl")).read_text(encoding="utf-8")
                self.assertNotIn("test-secret", raw)
                self.assertNotIn("not for the log", raw)
                self.assertNotIn("앞으로 15cm", raw)
                return code, stdout.getvalue(), [json.loads(line) for line in raw.splitlines()], planner, executor

    def test_default_dry_run_and_local_stop_do_not_open_udp(self):
        code, output, events, planner, executor = self.run_cli("앞으로 15cm 가")
        self.assertEqual(code, 0)
        self.assertIn("앞으로 15 cm", output)
        self.assertEqual(events[-1]["event"], "DRY_RUN")
        self.assertEqual(planner.call_count, 1)
        executor.assert_not_called()
        code, _, events, planner, executor = self.run_cli("정지")
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]["event"], "DRY_RUN")
        planner.assert_not_called()
        executor.assert_not_called()

    def test_unsafe_or_failed_model_and_rejected_approval_never_open_udp(self):
        cases = [
            ("앞으로 조금 가", {}, "REJECTED"),
            ("앞으로 15cm 가", {"reply": "invalid"}, "REJECTED"),
            ("앞으로 15cm 가", {"api_error": NvidiaError("NVIDIA_HTTP_404")}, "REJECTED"),
            ("앞으로 15cm 가", {"answers": ("EXECUTE", "no")}, "CANCELLED"),
        ]
        for command, kwargs, status in cases:
            with self.subTest(command=command, status=status):
                code, _, events, planner, executor = self.run_cli("--execute", command, **kwargs)
                self.assertNotEqual(code, 0)
                self.assertEqual(events[-1]["event"], status)
                executor.assert_not_called()
                if command == "앞으로 조금 가":
                    planner.assert_not_called()

    def test_separate_operator_confirmation_required_for_motion(self):
        code, _, events, _, executor = self.run_cli("--execute", "앞으로 15cm 가",
                                                    answers=("EXECUTE", "READY"))
        self.assertEqual(code, 0)
        self.assertIn("APPROVED", [row["event"] for row in events])
        executor.return_value.run.assert_called_once()
        self.assertIs(executor.return_value.run.call_args.kwargs["approved"], True)

    def test_explicit_standalone_stop_bypasses_model(self):
        code, _, _, planner, executor = self.run_cli("--execute", "정지")
        self.assertEqual(code, 0)
        planner.assert_not_called()
        executor.return_value.run.assert_called_once()

    def test_dry_run_config_overrides_and_unit_conversion_with_mock_http(self):
        def reply(request):
            self.assertEqual(str(request.url), "https://configured.example.test/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer fixture-secret")
            self.assertEqual(json.loads(request.content)["model"], "qwen/process-override")
            content = json.dumps({"needs_clarification": False, "explanation": "private model output",
                                  "actions": [
                                      {"type": "move", "direction": "forward", "distance_cm": 15},
                                      {"type": "move", "direction": "right", "distance_cm": 10},
                                  ]})
            return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop",
                                                          "message": {"role": "assistant", "content": content}}]})

        local = {"NVIDIA_API_KEY": "fixture-secret", "NVIDIA_MODEL": "qwen/file-config"}
        override = {"NVIDIA_MODEL": "qwen/process-override", "NVIDIA_BASE_URL": "https://configured.example.test/v1"}
        with (TemporaryDirectory() as folder, redirect_stdout(StringIO()) as stdout,
              httpx.Client(transport=httpx.MockTransport(reply)) as http,
              patch.dict(os.environ, override, clear=True),
              patch("python_controller.llm_cli.dotenv_values", return_value=local),
              patch("python_controller.llm.nvidia_client.OpenAI", side_effect=lambda **kw: OpenAI(http_client=http, **kw)),
              patch("python_controller.llm_cli.LLMMotionExecutor") as executor):
            self.assertEqual(main(["--dry-run", "앞으로 150mm 가고 오른쪽으로 0.1m 이동해"], log_dir=Path(folder)), 0)
            executor.assert_not_called()
            output = stdout.getvalue()
            self.assertLess(output.index("앞으로 15"), output.index("오른쪽으로 10"))
            raw = next(Path(folder).glob("*.jsonl")).read_text(encoding="utf-8")
            for secret in ("fixture-secret", "private model output"):
                self.assertNotIn(secret, raw)
                self.assertNotIn(secret, output)

    def test_unset_model_does_not_fall_back_to_another_endpoint(self):
        with (TemporaryDirectory() as folder, redirect_stdout(StringIO()) as stdout,
              patch.dict(os.environ, {}, clear=True),
              patch("python_controller.llm_cli.dotenv_values", return_value={"NVIDIA_API_KEY": "fixture-secret"}),
              patch("python_controller.llm.nvidia_client.OpenAI") as sdk,
              patch("python_controller.llm_cli.LLMMotionExecutor") as executor):
            self.assertEqual(main(["--dry-run", "앞으로 15cm"], log_dir=Path(folder)), 1)
            self.assertIn("NVIDIA_MODEL_MISSING", stdout.getvalue())
            self.assertNotIn("fixture-secret", stdout.getvalue())
            sdk.assert_not_called()
            executor.assert_not_called()
