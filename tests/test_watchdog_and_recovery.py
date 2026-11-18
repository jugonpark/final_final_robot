"""Timing, recovery and independent transport evidence; no real UDP sockets."""
from concurrent.futures import Future
import json
from queue import Queue, Empty
import time
import unittest
from unittest.mock import patch

from python_controller.communication.esp32_udp import TelemetryGuard
from python_controller.communication.robot_session import RobotSession
from python_controller.config import load_config
from python_controller.execution.llm_telemetry import GoalTelemetryGuard, TelemetryError


HOST = '127.0.0.1'
SOURCE = (HOST, 8888)


def telemetry(session=123, seq=1, uptime=1000, **changes):
    data = dict(type='telemetry', boot_id=99, session_id=session, last_seq=seq,
                uptime_ms=uptime, command_age_ms=20, mode='NETWORK', state='STOPPED',
                fault='NONE', cmd_vx=0, cmd_vy=0, cmd_w=0, wheel_pwm=[0, 0, 0],
                wheel_speed=[0, 0, 0], wheel_target=[0, 0, 0], encoder_count=[0, 0, 0],
                motion_id=None, goal_reached=False, goal_active=False)
    data.update(changes)
    return json.dumps(data).encode()


V2 = dict(protocol_version=2, heartbeat_timeout_ms=600, velocity_ttl_ms=300,
          heartbeat_period_ms=100, heartbeat_age_ms=0, velocity_age_ms=0, encoder_policy='STRICT',
          controller_alive=True,motion_permit=True,execution_token=1)


class FakeSocket:
    def __init__(self, on_send=None):
        self.incoming = Queue()
        self.sent = []
        self.on_send = on_send
        self.closed = False

    def sendto(self, raw, target):
        assert target == SOURCE
        packet = json.loads(raw)
        self.sent.append(packet)
        if self.on_send:
            self.on_send(packet)
        return len(raw)

    def recvfrom(self, _):
        try:
            return self.incoming.get_nowait()
        except Empty as exc:
            raise BlockingIOError() from exc

    def bind(self, _):
        pass

    def setblocking(self, _):
        pass

    def close(self):
        self.closed = True


class WatchdogTimingTests(unittest.TestCase):
    def test_v2_distance_age_450_is_valid_in_both_guards(self):
        raw = telemetry(**dict(V2, state='DISTANCE_ACTIVE', command_age_ms=450, heartbeat_age_ms=450,
                               goal_active=True, motion_id=4, goal_target_cm=30, goal_progress_cm=2,
                               remaining_cm=28, lateral_error_cm=0, overshoot_cm=0))
        guard = TelemetryGuard(123, HOST, .7)
        guard.allowed_states += ('DISTANCE_ACTIVE',)
        self.assertTrue(guard.receive(raw, SOURCE, 1, 1))
        goal = GoalTelemetryGuard(SOURCE, 123, .7)
        self.assertIsNotNone(goal.receive(raw, SOURCE, 1, {1: ('heartbeat', .9)}))

    def test_legacy_301_and_v2_stale_velocity_fail_independently(self):
        cases = [({}, 'STOPPED', {'command_age_ms': 301}, 'COMMAND_AGE_EXCEEDED'),
                 (V2, 'VELOCITY', {'heartbeat_age_ms': 1, 'velocity_age_ms': 300}, 'VELOCITY_EXPIRED'),
                 (V2, 'VELOCITY', {'heartbeat_age_ms': 600, 'velocity_age_ms': 1}, 'HEARTBEAT_EXPIRED'),
                 (V2, 'VELOCITY', {'heartbeat_age_ms': 1, 'velocity_age_ms': 301}, 'VELOCITY_EXPIRED'),
                 (V2, 'VELOCITY', {'heartbeat_age_ms': 601, 'velocity_age_ms': 1}, 'HEARTBEAT_EXPIRED')]
        for profile, state, changes, reason in cases:
            with self.subTest(reason=reason):
                guard = TelemetryGuard(123, HOST, .7)
                raw = telemetry(**dict(profile, state=state, **changes))
                self.assertFalse(guard.receive(raw, SOURCE, 1, 1))
                self.assertEqual(guard.detail, reason)
                self.assertFalse(guard.healthy(1))
        goal = GoalTelemetryGuard(SOURCE, 123, .7)
        with self.assertRaisesRegex(TelemetryError, 'COMMAND_AGE_EXCEEDED'):
            goal.receive(telemetry(command_age_ms=301), SOURCE, 1, {1: ('stop', .9)})

    def test_unsupported_timing_or_protocol_never_becomes_ready(self):
        for change, expected in [({'heartbeat_timeout_ms': 300}, 'FIRMWARE_TIMING_MISMATCH'),
                                 ({'velocity_ttl_ms': 600}, 'FIRMWARE_TIMING_MISMATCH'),
                                 ({'heartbeat_period_ms': True}, 'FIRMWARE_TIMING_MISMATCH'),
                                 ({'protocol_version': 3}, 'UNSUPPORTED_PROTOCOL_VERSION')]:
            with self.subTest(change=change):
                raw = telemetry(**dict(V2, **change))
                guard = TelemetryGuard(123, HOST, .7)
                self.assertFalse(guard.receive(raw, SOURCE, 1, 1))
                self.assertEqual(guard.detail, expected)
                self.assertFalse(guard.ready(1))
                with self.assertRaisesRegex(TelemetryError, expected):
                    GoalTelemetryGuard(SOURCE, 123, .7).receive(raw, SOURCE, 1, {1: ('stop', .9)})


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.
        cfg = load_config()
        cfg['communication']['host'] = HOST
        self.session = RobotSession(cfg, clock=lambda: self.now, evidence_source='MOCK_UDP')
        self.session.tx, self.session.rx = FakeSocket(), FakeSocket()
        self.addCleanup(self.session.close)
        self.session.configure_owner('DIRECT_CONTROL')

    def receive(self, **changes):
        self.now += .02
        raw = telemetry(session=self.session.protocol.session_id, seq=self.session.protocol.seq,
                        uptime=int(self.now * 1000), **changes)
        self.session.rx.incoming.put((raw, SOURCE))
        self.session.tick()

    def ready(self):
        self.session.tick()
        self.receive()
        self.assertTrue(self.session.snapshot()['ready'])

    def test_encoder_fault_stop_proof_does_not_grant_movement(self):
        self.ready()
        self.session.stop('USER_STOP')
        self.session.tick()
        self.receive(state='FAULT', fault='ENCODER_JUMP')
        self.receive(state='FAULT', fault='ENCODER_JUMP')
        status = self.session.snapshot()
        self.assertTrue(status['stop_verified'])
        self.assertTrue(status['link_alive'])
        self.assertFalse(status['ready'])
        self.assertFalse(status['connected'])
        self.assertEqual(status['validation_detail'], 'ESP32_FAULT_ENCODER_JUMP')
        self.assertFalse(self.session.arm())

    def ready_v2(self):
        self.session.tick()
        self.receive(**V2)
        self.assertFalse(self.session.snapshot()['ready'])
        self.assertEqual(self.session.tx.sent[-1].get('protocol_version'),2)
        self.receive(**V2)
        self.assertTrue(self.session.snapshot()['ready'])

    def test_stopped_current_token_ends_hold_after_fault_already_cleared(self):
        self.ready_v2()
        self.session.operator_alive()
        self.session.begin_hold('FORWARD')
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['type'], 'cmd_vel')
        self.receive(**dict(V2, execution_token=0, motion_permit=False))
        self.assertTrue(self.session.active)
        self.receive(**dict(V2, execution_token=self.session.execution_token, motion_permit=False,
                            last_fault='ENCODER_JUMP', last_fault_at_ms=1010))
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.reason, 'DEVICE_STOPPED_DURING_HOLD')
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertEqual(self.session.guard.data['last_fault'], 'ENCODER_JUMP')

    def test_policy_is_sent_only_stationary_and_applied_state_comes_from_device(self):
        self.ready_v2()
        future=self.session.set_encoder_policy('FILTERED_TEST')
        self.session.tick()
        self.assertTrue(future.done())
        self.assertEqual(future.result()[0]['type'],'set_test_profile')
        self.assertEqual(self.session.snapshot()['encoder_policy'],'STRICT')
        self.receive(**dict(V2,encoder_policy='FILTERED_TEST'))
        self.assertEqual(self.session.snapshot()['encoder_policy'],'FILTERED_TEST')
        self.session.active=True
        with self.assertRaises(ValueError):
            self.session.set_encoder_policy('STRICT')

    def test_clean_encoder_fault_recovery_uses_reset_then_stop_without_motion(self):
        self.ready_v2()
        fault=dict(V2,state='FAULT',fault='ENCODER_JUMP',encoder_good_samples=10)
        self.receive(**fault)
        self.receive(**fault)
        self.assertTrue(self.session.snapshot()['stop_verified'])
        self.session.recover()
        self.session.tick()
        self.receive(**fault)
        self.session.tick()
        self.assertIn('reset_fault',[p['type'] for p in self.session.tx.sent])
        self.receive(**V2)
        self.receive(**V2)
        self.assertEqual(self.session.recovery_state,'RECOVERED_WAITING_NEW_INPUT')
        self.assertTrue(self.session.snapshot()['ready'])
        self.assertFalse(self.session.active)
        self.assertFalse(any(p['type'] in ('cmd_move','cmd_vel') for p in self.session.tx.sent))

    def test_boot_recovery_requires_new_stop_and_never_replays_old_move(self):
        self.ready()
        self.session.select_mode('LLM_COMMAND')
        self.session.controller_alive()
        channel = self.session.distance_socket()
        channel.dispatch(json.dumps(dict(type='cmd_move', motion_id=1, vx=5, vy=0, w=0,
                                         status='SLOW', target_distance_cm=30)).encode())
        self.assertTrue(self.session.active)
        pending = Future()
        self.session.requests.put_nowait((dict(type='cmd_move', motion_id=2, vx=5, vy=0, w=0,
                                              status='SLOW', target_distance_cm=5), pending,
                                         self.session.recovery_generation))
        self.receive(boot_id=100)
        status = self.session.snapshot()
        self.assertEqual(status['recovery_state'], 'SYNC_STOP')
        self.assertTrue(status['session_lost'])
        self.assertFalse(status['ready'])
        self.assertTrue(self.session.distance_cancelled)
        self.assertIsNone(self.session.active_motion_id)
        stop_seq = self.session.stop_seq
        generation = status['recovery_generation']
        self.receive(boot_id=99)
        self.assertEqual(self.session.guard.boot_id, 100)
        self.assertEqual(self.session.rx_rejections['RETIRED_BOOT'], 1)
        self.assertEqual(self.session.recovery_generation, generation)
        self.assertFalse(self.session.snapshot()['ready'])
        self.assertTrue(pending.done())
        with self.assertRaisesRegex(OSError, 'CONTROL_CANCELLED'):
            pending.result()
        self.assertEqual(sum(packet['type'] == 'cmd_move' for packet in self.session.tx.sent), 1)
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertGreaterEqual(self.session.tx.sent[-1]['seq'], stop_seq)
        self.receive(boot_id=100)
        status = self.session.snapshot()
        self.assertEqual(status['recovery_state'], 'RECOVERED_WAITING_NEW_INPUT')
        self.assertFalse(status['session_lost'])
        self.assertTrue(status['stop_verified'])
        self.assertTrue(status['ready'])
        self.assertFalse(status['armed'])
        self.assertFalse(self.session.active)
        self.assertEqual(sum(packet['type'] == 'cmd_move' for packet in self.session.tx.sent), 1)
        channel.close()


class IndependentTransportTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.005)
        self.fail('Independent fake-socket transport did not reach expected state')

    def test_background_heartbeat_survives_goal_pause_then_expires_each_control_source(self):
        for expired in ('controller', 'execution'):
            with self.subTest(expired=expired), patch('socket.socket', side_effect=AssertionError('No real sockets')):
                rx = FakeSocket()
                uptime = [1000]
                goal = [None]
                def respond(packet):
                    uptime[0] += 1
                    if packet['type'] == 'cmd_move':
                        goal[0] = packet['motion_id']
                    elif packet['type'] == 'stop':
                        goal[0] = None
                    state = 'DISTANCE_ACTIVE' if goal[0] is not None else 'STOPPED'
                    raw = telemetry(session=packet['session_id'], seq=packet['seq'], uptime=uptime[0],
                                    **dict(V2, state=state, goal_active=goal[0] is not None,
                                           motion_id=goal[0], command_age_ms=450 if goal[0] else 0))
                    rx.incoming.put((raw, SOURCE))
                tx = FakeSocket(respond)
                sockets = iter((tx, rx))
                cfg = load_config()
                cfg['communication']['host'] = HOST
                session = RobotSession(cfg, socket_factory=lambda *_: next(sockets), evidence_source='MOCK_UDP')
                try:
                    session.configure_owner('DIRECT_CONTROL')
                    session.start()
                    self.wait_for(lambda: session.snapshot()['ready'])
                    self.assertEqual(session.snapshot()['heartbeat_timeout_ms'], 600)
                    self.assertEqual(session.snapshot()['velocity_ttl_ms'], 300)
                    session.select_mode('LLM_COMMAND')
                    session.controller_alive()
                    session.operator_alive()
                    channel = session.distance_socket()
                    channel.dispatch(json.dumps(dict(type='cmd_move', motion_id=7, vx=5, vy=0, w=0,
                                                     status='SLOW', target_distance_cm=30)).encode())
                    moves = sum(p['type'] == 'cmd_move' for p in tx.sent)
                    start = time.monotonic()
                    while time.monotonic() - start < 1:
                        session.controller_alive()
                        session.execution_alive()
                        session.operator_alive()
                        if sum(p['type'] == 'heartbeat' and p.get('motion_permit', False) for p in tx.sent) >= 3:
                            break
                        time.sleep(.01)
                    heartbeats = [p for p in tx.sent if p['type'] == 'heartbeat' and p.get('motion_permit')]
                    self.assertGreaterEqual(len(heartbeats), 3)
                    self.assertEqual({p['motion_id'] for p in heartbeats}, {7})
                    self.assertEqual(sum(p['type'] == 'cmd_move' for p in tx.sent), moves)
                    start = time.monotonic()
                    while time.monotonic() - start < .85 and not session.distance_cancelled:
                        session.operator_alive()
                        if expired == 'controller':
                            session.execution_alive()
                        else:
                            session.controller_alive()
                        time.sleep(.01)
                    self.assertTrue(session.distance_cancelled)
                    self.assertEqual(session.reason, 'CONTROLLER_LEASE_EXPIRED')
                    self.assertEqual(tx.sent[-1]['type'], 'stop')
                    self.assertFalse(session.active)
                    self.assertTrue(session.thread.is_alive())
                    self.assertEqual(sum(p['type'] == 'cmd_move' for p in tx.sent), moves)
                    self.assertEqual([p['seq'] for p in tx.sent], sorted(set(p['seq'] for p in tx.sent)))
                    channel.close()
                finally:
                    session.close()
                self.assertTrue(tx.closed and rx.closed)
