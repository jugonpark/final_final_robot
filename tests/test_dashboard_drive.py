import json
from threading import Event
import unittest

from python_controller.communication.motor_test import body_test_command, expected_wheels
from python_controller.config import load_config
from python_controller.control.hand_drive import DEFAULT_MAPPING, HandDrive, HandDriveClient
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.models import PerceptionFrame
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from test_llm_execution import MockESP32
from test_motor_test import Socket
from test_protocol import telemetry


HOST = '127.0.0.1'


def sample(label, score=.95):
    return dict(hand_count=int(label is not None),
                raw=[] if label is None else [dict(name=label, confidence=score, associated_target=None)])


class DashboardDriveTests(unittest.TestCase):
    def test_individual_wheel_inverse_targets_only_selected_wheel(self):
        config = load_config()['motor_test']
        speed = min(config['linear_cm_s'], 2.5)
        for i in range(3):
            cmd = body_test_command(f'M{i+1}', config)
            for j, actual in enumerate(expected_wheels(cmd, config['body_radius_cm'])):
                self.assertAlmostEqual(actual, speed if i == j else 0.)

    def test_hand_mapping_fresh_distinct_frames_and_immediate_stop(self):
        for name, direction in DEFAULT_MAPPING.items():
            with self.subTest(name=name):
                drive = HandDrive(hold=.1)
                self.assertEqual(drive.observe(sample(name), 1., 1., .4)['direction'], 'STOP')
                self.assertEqual(drive.observe(sample(name), 1., 1.2, .4)['direction'], 'STOP')
                self.assertEqual(drive.observe(sample(name), 1.2, 1.2, .4)['direction'], direction)
                self.assertEqual(drive.observe(sample(None), 1.3, 1.3, .4)['direction'], 'STOP')
        drive = HandDrive(mapping={**DEFAULT_MAPPING, 'Thumb_Up': 'TURN_RIGHT'})
        drive.observe(sample('Thumb_Up'), 1., 1., .4)
        self.assertEqual(drive.observe(sample('Thumb_Up'), 1.3, 1.3, .4)['direction'], 'TURN_RIGHT')
        self.assertEqual(drive.observe(sample('Thumb_Up'), 1.3, 1.8, .4)['direction'], 'STOP')
        self.assertEqual(drive.observe(sample('Unknown'), 2., 2., .4)['direction'], 'STOP')
        self.assertEqual(drive.observe(sample('Thumb_Up', .2), 2.1, 2.1, .4)['direction'], 'STOP')
        self.assertEqual(drive.observe(sample('Closed_Fist', .2), 2.2, 2.2, .4)['reason'], 'GESTURE_STOP')
        with self.assertRaises(ValueError):
            HandDrive(mapping={**DEFAULT_MAPPING, 'Closed_Fist': 'FORWARD'})

    def make_hand_engine(self):
        now = [1.]

        def factory(config):
            client = HandDriveClient(config, clock=lambda: now[0])
            client.tx, client.rx = Socket(), Socket()
            client.start = lambda: None
            return client

        engine = DashboardEngine(load_config(), '.tmp', client_factory=factory, logger=False)
        engine.connect_hand_drive(HOST, confidence=.75, frames=2, hold=.1, mapping=DEFAULT_MAPPING)
        client = engine.client
        client.tick()
        client.rx.incoming.append((telemetry(session=client.protocol.session_id, seq=client.protocol.seq), (HOST, 8888)))
        client.tick()
        return engine, client, now

    def test_hand_motion_requires_neutral_then_stops_on_hand_loss_and_camera_failure(self):
        engine, client, now = self.make_hand_engine()

        def frame(t, name, *, hand_only=True, error=None):
            now[0] = t
            result = dict(frame=PerceptionFrame(t, 640, 480, ()), vision=sample(name), completed_at=t,
                          hand_only=hand_only, fps=None, latency_ms=0)
            engine.tick(result, t, pipeline_error=error)
            client.tick()
            return client.tx.sent[-1]

        self.assertEqual(frame(1.03, None, hand_only=False)['type'], 'stop')
        self.assertEqual(frame(1.05, 'Thumb_Up')['type'], 'stop')
        self.assertFalse(client.armed)
        frame(1.07, None)
        self.assertTrue(client.armed)
        self.assertEqual(frame(1.09, 'Thumb_Up')['type'], 'stop')
        packet = frame(1.21, 'Thumb_Up')
        self.assertEqual((packet['type'], packet['vy']), ('cmd_vel', 2.5))
        self.assertIsNotNone(client.snapshot()['last_tx_frame_at'])
        self.assertEqual(frame(1.23, None)['type'], 'stop')
        self.assertEqual(frame(1.30, 'Open_Palm')['type'], 'stop')
        self.assertEqual(frame(1.42, 'Open_Palm')['type'], 'stop')  # Prior STOP echo predates release.
        client.rx.incoming.append((telemetry(session=client.protocol.session_id, seq=client.protocol.seq, uptime=1500), (HOST, 8888)))
        self.assertEqual(frame(1.43, 'Open_Palm')['type'], 'stop')
        self.assertEqual(frame(1.45, 'Open_Palm')['type'], 'cmd_vel')
        self.assertEqual(frame(1.46, 'Open_Palm', error='CAMERA_FAILED')['type'], 'stop')
        self.assertFalse(client.armed)
        self.assertEqual(frame(1.47, None)['type'], 'stop')
        self.assertFalse(client.armed)
        self.assertEqual(frame(1.48, 'Open_Palm', error='CAMERA_FAILED')['type'], 'stop')
        with self.assertRaisesRegex(ValueError, '연결을 먼저 해제'):
            engine.start_natural_motion(HOST, True)

    def test_disconnect_waits_off_gui_thread_and_preserves_owner_until_verified(self):
        engine, client, _ = self.make_hand_engine()
        entered, gate = Event(), Event()

        def release():
            entered.set()
            self.assertTrue(gate.wait(2))
            return dict(host=HOST, socket_closed=True, stop_verified=True)

        client.stop_and_release = release
        engine.disconnect_async()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(engine.finish_disconnect())
            self.assertIs(engine.client, client)
            window_status = engine.tick(None, 1.)
            self.assertEqual(window_status['active_control_mode'], 'GESTURE_DIRECT')
            with self.assertRaises(ValueError):
                engine.start_natural_motion(HOST, True)
        finally:
            gate.set()
            engine.release_future.result(1)
        self.assertTrue(engine.finish_disconnect())
        self.assertIsNone(engine.client)

    def test_fault_abort_uses_only_stationary_stop_echo_in_same_boot_session(self):
        class EncoderFault(MockESP32):
            def __init__(self, corrupt):
                super().__init__()
                self.corrupt = corrupt

            def recvfrom(self, size):
                raw, source = super().recvfrom(size)
                data = json.loads(raw)
                if self.moves:
                    data.update(state='FAULT', fault='ENCODER_JUMP', goal_active=False, goal_reached=False,
                                encoder_count=[20, 0, -10])
                    if self.last_packet['type'] == 'stop':
                        if self.corrupt == 'pwm':
                            data['wheel_pwm'] = [1, 0, 0]
                        elif self.corrupt == 'boot':
                            data['boot_id'] += 1
                        elif self.corrupt == 'session':
                            data['session_id'] ^= 1
                return json.dumps(data).encode(), source

        for corrupt in (None, 'pwm', 'boot', 'session'):
            with self.subTest(corrupt=corrupt):
                robot = EncoderFault(corrupt)
                events = []
                executor = LLMMotionExecutor(HOST, lambda event, **fields: events.append((event, fields)),
                                             clock=lambda: robot.now, socket_factory=lambda: robot)
                plan = ValidatedPlan(parse_operator_command('앞으로 15cm 가고 오른쪽으로 10cm 이동해'))
                with self.assertRaisesRegex(MotionError, 'ESP32_FAULT_ENCODER_JUMP'):
                    executor.run(plan, approved=True)
                self.assertEqual(robot.moves, 1)
                self.assertEqual(executor.release['stop_verified'], corrupt is None)
                if corrupt is None:
                    self.assertEqual(executor.release['fault'], 'ENCODER_JUMP')
                fault = next(fields for name, fields in events if name == 'SESSION_DIAGNOSTIC')
                self.assertEqual(fault['telemetry']['encoder_count'], [20, 0, -10])
