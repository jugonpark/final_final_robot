import json
import socket
import unittest
from unittest.mock import patch

from python_controller.communication.esp32_udp import Protocol, TelemetryGuard
from python_controller.execution.control_lease import open_control_socket
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.planning.validator import ValidatedPlan, parse_operator_command


HOST = "127.0.0.1"


class MockESP32:
    """Firmware-shaped telemetry every 200ms using a virtual monotonic clock."""
    def __init__(self):
        self.now = 0
        self.next_telemetry = 0.2
        self.last_packet = self.goal = None
        self.moves = 0
        self.dropped = False
        self.sent = []
        self.closed = False
        self.mode = None
        self.move_started_at = 0
        self.closed_at = None

    def sendto(self, packet, target):
        assert target == (HOST, 8888)
        data = json.loads(packet)
        kind = data["type"]
        self.sent.append((self.now, data, packet))
        if self.mode == "send_error" and kind == "heartbeat" and self.moves:
            raise OSError("simulated send failure")
        if kind == "cmd_move" and self.mode in ("one_drop", "all_dropped") and not self.dropped:
            self.dropped = self.mode == "one_drop"
            return len(packet)
        if self.last_packet is None or data["seq"] > self.last_packet["seq"]:
            self.last_packet = data
            self.last_received = self.now
            if kind == "cmd_move":
                self.goal = data
                self.moves += 1
                self.move_started_at = self.now
            if kind == "stop":
                if self.mode != "stop_unconfirmed" or not self.moves:
                    self.goal = None
        return len(packet)

    def recvfrom(self, _):
        self.now += .01
        if self.goal and self.mode == "interrupt":
            self.mode = None
            raise KeyboardInterrupt
        if self.goal and self.mode == "internal_exception":
            raise RuntimeError("simulated internal exception")
        if self.goal and self.mode == "jitter":
            self.now += .31
            self.mode = None
            raise TimeoutError
        if self.now < self.next_telemetry or not self.last_packet or (self.goal and self.mode == "stale"):
            raise TimeoutError
        self.next_telemetry += .2
        goal, command = self.goal, self.last_packet
        reached = bool(goal and self.now - self.move_started_at >= .6 and self.mode != "never_reached")
        braking = goal and self.now - self.move_started_at >= .4
        state = ("GOAL_REACHED" if reached else "DISTANCE_BRAKING" if braking else
                 "DISTANCE_ACTIVE" if goal else "STOPPED")
        data = dict(
            type="telemetry", boot_id=42, uptime_ms=int(self.now * 1000),
            session_id=command["session_id"], last_seq=command["seq"],
            command_age_ms=int((self.now - self.last_received) * 1000),
            mode="NETWORK", state=state, fault="NONE", goal_reached=reached,
            goal_active=bool(goal and not reached),
            cmd_vx=0, cmd_vy=0, cmd_w=0, wheel_pwm=[0, 0, 0],
            wheel_speed=[0, 0, 0], encoder_count=[0, 0, 0],
            motion_id=goal["motion_id"] if goal else None,
            goal_target_cm=goal["target_distance_cm"] if goal else None,
            goal_progress_cm=(goal["target_distance_cm"] if reached else 0) if goal else None,
            remaining_cm=(0 if reached else goal["target_distance_cm"]) if goal else None,
            lateral_error_cm=0 if goal else None, overshoot_cm=0 if goal else None,
        )
        source = (HOST, 8888)
        if self.mode == "manual":
            data["mode"] = "MANUAL_TEST"
        if self.mode == "retained_goal" and not self.moves:
            data.update(motion_id=1, goal_target_cm=10, goal_progress_cm=10,
                        remaining_cm=0, lateral_error_cm=0, overshoot_cm=0)
        if goal:
            overrides = {
                "stall": {"fault": "MOTOR_STALL", "state": "FAULT"},
                "session": {"session_id": command["session_id"] ^ 1},
                "reboot": {"boot_id": 43},
                "wrong_goal": {"motion_id": 123},
                "bad_seq": {"last_seq": command["seq"] + 1},
                "bad_flag": {"goal_reached": not reached},
                "bad_odometry": {"remaining_cm": None},
                "replay": {"uptime_ms": 400},
                "command_age": {"command_age_ms": 301},
            }
            data.update(overrides.get(self.mode, {}))
            if reached and self.mode == "contradictory_completion":
                data.update(goal_progress_cm=0, remaining_cm=0, overshoot_cm=6, cmd_vx=5)
            if self.mode == "foreign_source":
                source = ("127.0.0.2", 8888)
        return json.dumps(data).encode(), source

    def close(self):
        self.closed = True
        self.closed_at = self.now


class ExecutionTests(unittest.TestCase):
    def setup_executor(self, mode=None):
        robot = MockESP32()
        robot.mode = mode
        events = []
        executor = LLMMotionExecutor(
            HOST, lambda event, **fields: events.append((event, fields)),
            clock=lambda: robot.now, socket_factory=lambda: robot,
        )
        plan = ValidatedPlan(parse_operator_command("앞으로 15cm 가고 오른쪽으로 10cm 이동해"))
        return robot, events, executor, plan

    def test_packet_limits_and_four_directions(self):
        protocol = Protocol(7)
        for index, (vx, vy) in enumerate(((5, 0), (-5, 0), (0, 5), (0, -5)), 1):
            raw = protocol.move(index, vx, vy, 15)
            self.assertEqual(json.loads(raw), dict(
                type="cmd_move", session_id=7, seq=index, status="SLOW",
                motion_id=index, vx=vx, vy=vy, w=0, target_distance_cm=15,
            ))
        for bad in ((True, 5, 0, 15), (2**31, 5, 0, 15), (1, 5, 5, 15),
                    (1, float("nan"), 0, 15), (1, 6, 0, 15), (1, 5, 0, 51)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                protocol.move(*bad)
        self.assertEqual(protocol.seq, 4)

    @patch("python_controller.execution.motion_executor.randbelow", return_value=0)
    def test_stop_heartbeat_and_sequential_goals_with_byte_identical_retry(self, _):
        robot, events, executor, plan = self.setup_executor("one_drop")
        executor.run(plan, approved=True)
        self.assertEqual(robot.moves, 2)
        self.assertTrue(robot.closed)
        moves = [record for record in robot.sent if record[1]["type"] == "cmd_move"]
        self.assertEqual(moves[0][2], moves[1][2])
        self.assertEqual([record[1]["motion_id"] for record in moves], [1, 1, 2, 2])
        self.assertEqual((moves[-1][1]["vx"], moves[-1][1]["vy"]), (0, -5))
        event_names = [name for name, _ in events]
        self.assertLess(event_names.index("HEARTBEAT_CONFIRMED"), event_names.index("MOVE_ACCEPTED"))
        self.assertEqual(event_names.count("STOP_CONFIRMED"), 3)
        self.assertEqual(event_names.count("GOAL_REACHED"), 2)
        self.assertIn("DISTANCE_BRAKING", [fields["telemetry"]["state"] for name, fields in events if name == "TELEMETRY"])

    @patch("python_controller.execution.motion_executor.randbelow", return_value=0)
    def test_goal_id_avoids_prior_session_id_retained_after_stop(self, _):
        robot, _, executor, plan = self.setup_executor("retained_goal")
        executor.run(plan, approved=True)
        ids = {entry[1]["motion_id"] for entry in robot.sent if entry[1]["type"] == "cmd_move"}
        self.assertEqual(ids, {2, 3})

    def test_motion_denied_without_approval_or_exclusive_port(self):
        _, _, executor, plan = self.setup_executor()
        executor.socket_factory = lambda: self.fail("socket opened without approval")
        with self.assertRaisesRegex(MotionError, "APPROVAL"):
            executor.run(plan, approved=False)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as existing:
            existing.bind(("", 8889))
            with self.assertRaises(OSError):
                open_control_socket()
        with open_control_socket():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as existing:
                with self.assertRaises(OSError):
                    existing.bind(("", 8889))

    def test_bad_feedback_aborts_and_never_starts_second_goal(self):
        outcomes = {
            "manual": "MODE_NOT_NETWORK", "stall": "MOTOR_STALL",
            "session": "SESSION_MISMATCH", "reboot": "ESP32_REBOOT",
            "wrong_goal": "MOTION_ID_MISMATCH", "bad_seq": "SEQUENCE_NOT_SENT",
            "bad_flag": "INVALID_GOAL_FLAGS", "bad_odometry": "INVALID_GOAL_FIELDS",
            "stale": "TELEMETRY_STALE", "foreign_source": "TELEMETRY_STALE",
            "replay": "TELEMETRY_STALE", "command_age": "COMMAND_AGE_EXCEEDED",
            "send_error": "UDP_IO_FAILED", "interrupt": "OPERATOR_STOP",
            "internal_exception": "EXECUTOR_FAILURE",
            "never_reached": "GOAL_TIMEOUT", "all_dropped": "MOVE_NOT_CONFIRMED",
            "stop_unconfirmed": "STOP_NOT_CONFIRMED", "jitter": "HEARTBEAT_DEADLINE",
        }
        for mode, reason in outcomes.items():
            with self.subTest(mode=mode):
                robot, events, executor, plan = self.setup_executor(mode)
                with self.assertRaisesRegex(MotionError, reason):
                    executor.run(plan, approved=True)
                self.assertTrue(robot.closed)
                self.assertEqual(robot.sent[-1][1]["type"], "stop")
                self.assertLessEqual(robot.moves, 1)
                self.assertNotIn("COMPLETED", [name for name, _ in events])

    def test_existing_guard_still_rejects_distance_states(self):
        robot = MockESP32()
        protocol = Protocol(7)
        robot.sendto(protocol.move(1, 5, 0, 15), (HOST, 8888))
        robot.now = .2
        packet, source = robot.recvfrom(2049)
        guard = TelemetryGuard(7, HOST, .8)
        self.assertFalse(guard.receive(packet, source, robot.now, protocol.seq))
        self.assertEqual(guard.detail, "INVALID_STATE_OR_FAULT")

    def test_contradictory_completion_stops_before_next_goal(self):
        robot, events, executor, plan = self.setup_executor("contradictory_completion")
        with self.assertRaisesRegex(MotionError, "INCONSISTENT_GOAL_COMPLETION"):
            executor.run(plan, approved=True)
        self.assertEqual(robot.moves, 1)
        self.assertEqual(robot.sent[-1][1]["type"], "stop")
        self.assertNotIn("GOAL_REACHED", [event for event, _ in events])

    def test_operator_cancel_during_distance_move_stops_and_cannot_continue(self):
        robot, _, executor, plan = self.setup_executor()
        with self.assertRaisesRegex(MotionError, "OPERATOR_STOP"):
            executor.run(plan, approved=True, stop_requested=lambda: robot.moves == 1)
        self.assertEqual(robot.moves, 1)
        self.assertEqual(robot.sent[-1][1]["type"], "stop")


if __name__ == "__main__":
    unittest.main()
