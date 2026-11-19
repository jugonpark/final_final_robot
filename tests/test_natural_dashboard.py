import importlib.util
import json
import os
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
class NaturalDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, folder, preview=False):
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
        from python_controller.visualization.dashboard import Dashboard
        config = load_config()
        config["communication"]["host"] = "127.0.0.1"
        engine = DashboardEngine(config, folder, "SYNTHETIC_PREVIEW" if preview else "LIVE_CAMERA", logger=False)
        window = Dashboard(config, engine=engine, stream=PreviewStream())
        # Retained independent-executor compatibility is verified by this fixture.
        engine.central_ui = False
        window.language_panel.refresh()
        window.timer.stop()
        self.addCleanup(window.close)
        window.show()
        window.tabs.setCurrentIndex(window.language_tab_index)
        self.app.processEvents()
        return window

    def wait_phase(self, window, expected):
        end = monotonic() + 2
        while monotonic() < end:
            window.poll()
            self.app.processEvents()
            if window.engine.language.phase == expected:
                return
            sleep(.01)
        self.fail(f"Expected {expected}, got {window.engine.language.phase}")

    @staticmethod
    def plan(command):
        from python_controller.planning.validator import ValidatedPlan, parse_operator_command
        return ValidatedPlan(parse_operator_command(command))

    def test_preview_typing_and_plan_use_no_udp_and_cannot_execute(self):
        from PySide6.QtTest import QTest

        with TemporaryDirectory() as folder, patch("socket.socket", side_effect=AssertionError("No sockets")):
            window = self.make_window(folder, preview=True)
            panel = window.language_panel
            panel.command.setFocus()
            self.app.processEvents()
            QTest.keyClicks(panel.command, "one space")
            self.assertEqual(panel.command.text(), "one space")
            self.assertFalse(window.engine.c.estop_latched)
            panel.command.setText("앞으로 5cm 이동해")
            with patch.object(window.engine.language, "_prepare", side_effect=self.plan):
                panel.plan_button.click()
                self.wait_phase(window, "READY")
            self.assertIn("5 cm", panel.preview.toPlainText())
            self.assertFalse(panel.execute_button.isEnabled())
            with self.assertRaisesRegex(ValueError, "합성 미리보기"):
                window.engine.start_natural_motion("127.0.0.1", True)
            window.close()

    def test_hardware_approval_is_single_use_and_estop_cancels_worker(self):
        from python_controller.llm.central_control import ControlError

        with TemporaryDirectory() as folder, patch("socket.socket", side_effect=AssertionError("No sockets")):
            window = self.make_window(folder)
            panel = window.language_panel
            control = window.engine.language
            panel.command.setText("앞으로 5cm 이동해")
            with patch.object(control, "_prepare", side_effect=self.plan):
                panel.plan_button.click()
                self.wait_phase(window, "READY")
            self.assertFalse(panel.execute_button.isEnabled())
            with self.assertRaisesRegex(ValueError, "HARDWARE_PREPARATION_REQUIRED"):
                window.engine.start_natural_motion("127.0.0.1", False)
            window.engine.client = object()
            try:
                with self.assertRaisesRegex(ValueError, "연결을 먼저 해제"):
                    window.engine.start_natural_motion("127.0.0.1", True)
            finally:
                window.engine.client = None
            panel.prepared.setChecked(True)
            self.assertTrue(panel.execute_button.isEnabled())
            panel.command.setText("오른쪽으로 10cm 가")
            self.assertFalse(panel.prepared.isChecked())
            self.assertIsNone(control.plan)
            with patch.object(control, "_prepare", side_effect=self.plan):
                panel.plan_button.click()
                self.wait_phase(window, "READY")

            started = Event()
            def execute(host, plan):
                self.assertEqual(host, "127.0.0.1")
                self.assertEqual(plan.actions[0].direction, "right")
                started.set()
                self.assertTrue(control._stop.wait(1))
                raise ControlError("OPERATOR_STOP")

            with patch.object(control, "_execute", side_effect=execute) as worker:
                panel.prepared.setChecked(True)
                panel.execute_button.click()
                self.assertTrue(started.wait(1))
                self.assertFalse(panel.prepared.isChecked())
                self.assertFalse(panel.execute_button.isEnabled())
                with self.assertRaisesRegex(ValueError, "자연어 장치"):
                    window.engine.connect_motor_test("127.0.0.1", True)
                window.estop.click()
                self.wait_phase(window, "FAILED")
                self.assertEqual(control.detail, "OPERATOR_STOP")
                worker.assert_called_once()
                self.assertIsNone(control.plan)
                self.assertFalse(panel.execute_button.isEnabled())
            window.close()

    def test_approved_dashboard_plan_runs_shared_executor_against_mock_firmware(self):
        from python_controller.execution.motion_executor import LLMMotionExecutor
        from test_llm_execution import MockESP32

        with TemporaryDirectory() as folder, patch("socket.socket", side_effect=AssertionError("No real sockets")):
            window = self.make_window(folder)
            panel = window.language_panel
            control = window.engine.language
            panel.command.setText("앞으로 15cm 이동하고 오른쪽으로 10cm 가")
            with patch.object(control, "_prepare", side_effect=self.plan):
                panel.plan_button.click()
                self.wait_phase(window, "READY")
            robot = MockESP32()
            def build_executor(host, emit, **kwargs):
                return LLMMotionExecutor(host, emit, clock=lambda: robot.now,
                                         socket_factory=lambda: robot, **kwargs)

            with patch("python_controller.execution.motion_executor.LLMMotionExecutor", side_effect=build_executor):
                panel.prepared.setChecked(True)
                panel.execute_button.click()
                self.wait_phase(window, "DONE")
            self.assertTrue(robot.closed)
            self.assertEqual(robot.moves, 2)
            events = [json.loads(line)["event"] for line in control.log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events.count("STOP_CONFIRMED"), 3)
            self.assertEqual(events[-1], "COMPLETED")
            self.assertFalse(panel.execute_button.isEnabled())
            window.close()

    def test_approval_reused_for_next_plan_in_same_session_and_cleared_on_estop(self):
        with TemporaryDirectory() as folder, patch("socket.socket", side_effect=AssertionError("No real sockets")):
            window = self.make_window(folder)
            panel = window.language_panel
            control = window.engine.language
            with patch.object(control, "_prepare", side_effect=self.plan), patch.object(control, "_execute") as execute:
                panel.command.setText("앞으로 5cm 가")
                panel.plan_button.click()
                self.wait_phase(window, "READY")
                panel.prepared.setChecked(True)
                panel.execute_button.click()
                self.wait_phase(window, "DONE")
                panel.command.setText("오른쪽으로 10cm 가")
                panel.plan_button.click()
                self.wait_phase(window, "READY")
                self.assertFalse(panel.prepared.isChecked())
                self.assertTrue(panel.execute_button.isEnabled())
                panel.execute_button.click()
                self.wait_phase(window, "DONE")
                self.assertEqual(execute.call_count, 2)
                window.estop.click()
                panel.plan_button.click()
                self.wait_phase(window, "READY")
                self.assertFalse(panel.execute_button.isEnabled())
            window.close()
