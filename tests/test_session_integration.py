import json
import unittest
from unittest.mock import Mock

from python_controller.communication.esp32_udp import UdpClient
from python_controller.config import load_config
from python_controller.control.state_machine import GestureDebouncer
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.models import GestureObservation
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from test_dashboard_runtime import result
from test_llm_execution import HOST, MockESP32
from test_protocol import telemetry


OLD = dict(host=HOST, tx_session_id=123, tx_seq=80, boot_id=42,
           socket_closed=True, stop_verified=True)


class HandoffESP32(MockESP32):
    def __init__(self, variant):
        super().__init__()
        self.variant = variant
        self.reports = 0

    def recvfrom(self, size):
        packet, source = super().recvfrom(size)
        data = json.loads(packet)
        self.reports += 1
        if self.reports == 1:
            data.update(session_id=OLD["tx_session_id"], last_seq=79)
            if self.variant == "collision":
                data["last_seq"] = 81
        if self.variant == "reboot" and self.reports > 1:
            data["boot_id"] = 43
        if self.variant == "foreign_goal" and data["state"] == "GOAL_REACHED":
            data["session_id"] = OLD["tx_session_id"]
        return json.dumps(data).encode(), source


class SessionIntegrationTests(unittest.TestCase):
    def executor(self, robot, events, previous=OLD):
        return LLMMotionExecutor(HOST, lambda event, **row: events.append((event, row)),
                                 clock=lambda: robot.now, socket_factory=lambda: robot, previous=previous)

    def plan(self):
        return ValidatedPlan(parse_operator_command("앞으로 5cm 가고 오른쪽으로 5cm 가"))

    def test_delayed_old_stopped_packet_never_approves_new_session(self):
        for prior in (OLD, None):
            with self.subTest(previous=prior):
                robot, rows = HandoffESP32("delay"), []
                executor = self.executor(robot, rows, prior)
                executor.run(self.plan(), approved=True)
                diagnostic = next(row for event, row in rows if event == "SESSION_DIAGNOSTIC")
                self.assertEqual(diagnostic["rx_session_id"], OLD["tx_session_id"])
                self.assertEqual(diagnostic["session_mismatch_reason"], "PREVIOUS_SESSION_DURING_STOP")
                self.assertFalse(diagnostic["stop_verified"])
                sent = [row for event, row in rows if event == "UDP_TX"]
                self.assertEqual(sent[0]["command"]["type"], "stop")
                self.assertNotEqual(diagnostic["tx_session_id"], diagnostic["rx_session_id"])
                self.assertEqual(robot.moves, 2)

    def test_old_controller_transmit_after_release_and_reboot_block_start(self):
        for mode, reason in (("collision", "SESSION_MISMATCH"), ("reboot", "ESP32_REBOOT")):
            with self.subTest(mode=mode):
                robot, rows = HandoffESP32(mode), []
                executor = self.executor(robot, rows)
                with self.assertRaisesRegex(MotionError, reason):
                    executor.run(self.plan(), approved=True)
                self.assertEqual(robot.moves, 0)
                self.assertTrue(robot.closed)
                self.assertEqual(robot.sent[-1][1]["type"], "stop")

    def test_foreign_goal_stops_and_does_not_resume_even_after_fresh_stop(self):
        robot, rows = HandoffESP32("foreign_goal"), []
        executor = self.executor(robot, rows)
        with self.assertRaisesRegex(MotionError, "SESSION_MISMATCH"):
            executor.run(self.plan(), approved=True)
        self.assertEqual(robot.moves, 1)
        self.assertTrue(executor.release["stop_verified"])
        self.assertNotIn("GOAL_REACHED", [event for event, _ in rows])
        with self.assertRaisesRegex(MotionError, "EXECUTOR_ALREADY_USED"):
            executor.run(self.plan(), approved=True)

    def test_dashboard_waits_for_manual_release_and_passes_boot_to_llm_and_gesture(self):
        engine = DashboardEngine(load_config(), ".tmp", logger=False)
        manual = Mock(manual_test_only=True)
        manual.snapshot.return_value = {}
        manual.drain_transport_events.return_value = []
        manual.drain_events.return_value = []
        manual.stop_and_release.return_value = OLD.copy()
        engine.client, engine.client_owner = manual, "MANUAL_MOTOR_TEST"
        with self.assertRaisesRegex(ValueError, "연결을 먼저 해제"):
            engine.start_natural_motion(HOST, True)
        engine.disconnect()
        manual.stop_and_release.assert_called_once()
        engine.language.start_motion = Mock()
        engine.start_natural_motion(HOST, True)
        self.assertEqual(engine.language.previous, OLD)

        updated = dict(OLD, tx_session_id=456)
        engine.language.release = updated
        follow = Mock()
        follow.manual_test_only = False
        follow.drain_transport_events.return_value = []
        engine.client_factory = Mock(return_value=follow)
        with self.assertRaisesRegex(ValueError, "시험 준비"):
            engine.connect_gesture_follow(HOST, True, False)
        engine.connect_gesture_follow(HOST, True, True)
        follow.configure_owner.assert_called_once_with("GESTURE_FOLLOW", updated)
        self.assertFalse(engine.c.armed)
        self.assertEqual(engine.active_control_mode, "GESTURE_FOLLOW")
        follow.stop_and_release.return_value = updated
        engine.close()
        follow.stop_and_release.assert_called_once()

    def test_unverified_transfer_blocks_movement_but_allows_explicit_stationary_probe(self):
        engine = DashboardEngine(load_config(), ".tmp", logger=False)
        engine.last_release = dict(OLD, stop_verified=False)
        with self.assertRaisesRegex(ValueError, "정지가 미확인"):
            engine.start_natural_motion(HOST, True)
        with self.assertRaisesRegex(ValueError, "정지가 미확인"):
            engine.connect_gesture_follow(HOST, True, True)
        motor = Mock()
        engine.client_factory = Mock(return_value=motor)
        engine.connect_motor_test(HOST, True)
        motor.configure_owner.assert_called_once_with("MANUAL_MOTOR_TEST", None)
        self.assertFalse(engine.c.armed)

    def test_gesture_frame_gap_uses_capture_intervals_and_preserves_freshness(self):
        config = load_config()["gesture"]
        debounce = GestureDebouncer(config)
        for index, capture in enumerate((1, 1.2, 1.4, 1.61)):
            gesture = GestureObservation("Open_Palm", .99, 7, capture)
            event = debounce.update(gesture, 7, capture + .15)
            self.assertEqual(event, "RESUME" if index == 3 else None)
        stale = GestureObservation("Closed_Fist", .99, 7, 2.0)
        self.assertIsNone(debounce.update(stale, 7, 2.5))

    def test_tracking_adjustment_is_bounded_and_requires_stopped_control(self):
        engine = DashboardEngine(load_config(), ".tmp", logger=False)
        with self.assertRaises(ValueError):
            engine.tune_tracking(.6, .45, 2, .95, 320)
        engine.tune_tracking(.5, .45, 2, .95, 320)
        self.assertEqual(engine.cfg["safety"]["perception_timeout_s"], .5)
        self.assertEqual(engine.cfg["vision"]["image_size"], 320)
        self.assertFalse(engine.c.armed)
        self.assertEqual(engine.tick(result(1), 1.7)["stop_reason"], "CAMERA_FAILED_OR_STALE")
        engine.client = object()
        with self.assertRaises(ValueError):
            engine.tune_tracking(.35, .45, 2, .95, 320)

    def test_gesture_follow_sends_only_after_stop_arm_and_release_and_latches_collision(self):
        class Wire:
            def __init__(self):
                self.incoming, self.sent = [], []
            def recvfrom(self, size):
                if not self.incoming:
                    raise BlockingIOError
                return self.incoming.pop(0)
            def sendto(self, packet, peer):
                self.sent.append(json.loads(packet))

        now = [1.]
        def factory(config):
            client = UdpClient(config, clock=lambda: now[0])
            client.start = lambda: None
            client.tx, client.rx = Wire(), Wire()
            return client

        engine = DashboardEngine(load_config(), ".tmp", client_factory=factory, logger=False)
        engine.connect_gesture_follow(HOST, True, True)
        client = engine.client
        client.tick()
        engine.tick(result(now[0]), now[0])
        engine.request("CONFIRM")
        engine.tick(result(now[0]), now[0])
        engine.request("ARM")
        engine.tick(result(now[0]), now[0])
        self.assertFalse(engine.c.armed)
        self.assertEqual(client.tx.sent[-1]["type"], "stop")
        source = (HOST, 8888)
        client.rx.incoming.append((telemetry(session=client.protocol.session_id), source))
        client.tick()
        engine.request("ARM")
        engine.tick(result(now[0]), now[0])
        engine.request("RESUME")
        engine.tick(result(now[0]), now[0])
        client.tick()
        self.assertEqual(client.tx.sent[-1]["type"], "cmd_vel")
        client.rx.incoming.append((telemetry(session=OLD["tx_session_id"], seq=40), source))
        client.tick()
        self.assertEqual(client.tx.sent[-1]["type"], "stop")
        self.assertTrue(client.session_lost)
        client.rx.incoming.append((telemetry(session=client.protocol.session_id, seq=client.protocol.seq,
                                            uptime=1100), source))
        client.tick()
        self.assertFalse(client.arm())
        self.assertEqual(client.snapshot()["session_diagnostic"]["rx_session_id"], OLD["tx_session_id"])
