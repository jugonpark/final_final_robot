import json
import socket
import struct
import threading
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from pi_radar.publisher import MAGIC, FrameStream, HazardDetector, configure_sensor, read_uart
from python_controller.control.safety_supervisor import SafetySupervisor
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.execution.motion_executor import MotionError
from python_controller.models import ControlDecision, MotionCommand
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from python_controller.radar.udp_receiver import RadarReceiver
import test_v2_goal_transport as goal_fixture
import test_watchdog_and_recovery as wire_fixture


PI = '127.0.0.2'


def radar(seq, state='CLEAR', *, boot=1, epoch=0):
    return json.dumps(dict(type='radar_status', protocol_version=1, boot_id=boot,
                           seq=seq, hazard_epoch=epoch, state=state,
                           sensor_valid=state!='UNKNOWN',
                           min_distance_m=.30 if state=='EVADE' else None,
                           danger_points=3 if state=='EVADE' else 0)).encode()


def uart(number, distances):
    points = b''.join(struct.pack('<4f', d, 0, 0, 0) for d in distances)
    tlv = struct.pack('<2I', 1, len(points)) + points if points else b''
    return struct.pack('<8s8I', MAGIC, 1, 40+len(tlv), 1, number, 0,
                       len(distances), bool(tlv), 0) + tlv


class RadarPublisherTests(unittest.TestCase):
    def test_configuration_settles_and_flushes_each_command_before_reading(self):
        io = Mock()
        io.serial.readline.side_effect = [b'sensorStop\r\n', b'Done\r\n', b'Done\r\n']
        with patch('pi_radar.publisher.Path.read_text', return_value='sensorStop\nsensorStart\n'), \
             patch('pi_radar.publisher.time.sleep', io.sleep):
            configure_sensor(io.serial, 'test.cfg')
        self.assertEqual(io.mock_calls, [
            call.sleep(.5), call.serial.reset_input_buffer(),
            call.serial.write(b'sensorStop\n'), call.serial.flush(), call.sleep(.05),
            call.serial.readline(), call.serial.readline(),
            call.serial.write(b'sensorStart\n'), call.serial.flush(), call.sleep(.05),
            call.serial.readline(),
        ])

    def test_configuration_timeout_reports_command_and_stops_setup(self):
        serial_port = Mock()
        serial_port.readline.return_value = b'mmwDemo:/>\r\n'
        with patch('pi_radar.publisher.Path.read_text', return_value='sensorStop\nsensorStart\n'), \
             patch('pi_radar.publisher.time.sleep'), \
             patch('pi_radar.publisher.time.monotonic', side_effect=[0., .1, 2.]):
            with self.assertRaisesRegex(TimeoutError, '1/2 sensorStop.*mmwDemo'):
                configure_sensor(serial_port, 'test.cfg')
        serial_port.write.assert_called_once_with(b'sensorStop\n')

    def test_uart_fragmentation_danger_clear_and_invalid_frames(self):
        detector, parser = HazardDetector(), FrameStream()
        frame = uart(1, [])
        self.assertEqual(list(parser.feed(frame[:14])), [])
        detector.observe(next(parser.feed(frame[14:])), 1.)
        self.assertEqual(detector.snapshot(1.)['state'], 'UNKNOWN')
        detector.observe(next(parser.feed(uart(2, []))), 1.04)
        self.assertEqual(detector.snapshot(1.04)['state'], 'CLEAR')
        for n in (3, 4):
            detector.observe(next(parser.feed(uart(n, [.2,.3,.35]))), 1+n*.04)
        self.assertEqual(detector.snapshot(1.16)['state'], 'EVADE')
        self.assertEqual(detector.snapshot(1.16)['hazard_epoch'], 1)
        corrupted = uart(5, [float('nan')])
        self.assertEqual(list(parser.feed(corrupted)), [None])
        detector.observe(None, 1.17)
        self.assertFalse(detector.snapshot(1.17)['sensor_valid'])
        self.assertEqual(detector.snapshot(1.17)['hazard_epoch'], 2)

    def test_usb_error_publishes_unknown_and_duplicates_cannot_refresh_sensor(self):
        detector = HazardDetector()
        detector.observe(((1,0), []), 1.)
        detector.observe(((2,0), []), 1.05)
        detector.observe(((2,0), []), 1.27)
        self.assertEqual(detector.snapshot(1.31)['state'], 'UNKNOWN')
        detector.observe(((3,0), []), 1.4)
        detector.observe(((4,0), []), 1.45)
        self.assertEqual(detector.snapshot(1.45)['state'], 'CLEAR')
        done = threading.Event()
        def disconnected(*args, **kwargs):
            done.set()
            raise OSError('mock USB disconnect')
        read_uart(SimpleNamespace(data_port='mock'), detector, done, disconnected)
        self.assertEqual(detector.snapshot(1.5)['state'], 'UNKNOWN')


class RadarStateTests(unittest.TestCase):
    def test_receive_worker_keeps_receiving_and_warns_after_sender_stops(self):
        gate = SafetySupervisor('127.0.0.1', .15)
        receiver = RadarReceiver(gate, clock=Mock(side_effect=[1., 1.2, 1.4]))
        receiver.sock = Mock()
        count = [0]
        def receive(_):
            count[0] += 1
            if count[0] == 1:
                return radar(1), ('127.0.0.1', 12345)
            if count[0] == 2:
                gate.rearm(1.)
            else:
                receiver.done.set()
            raise socket.timeout()
        receiver.sock.recvfrom.side_effect = receive
        receiver._receive()
        state = gate.snapshot(1.4)
        self.assertEqual(receiver.sock.recvfrom.call_count, 3)
        self.assertEqual(state['state'], 'CLEAR')
        self.assertFalse(state['blocked'])
        self.assertEqual(state['warning'], 'RADAR_TIMEOUT')

    def test_source_sequence_timeout_reboot_and_rearm(self):
        gate = SafetySupervisor(PI, .30)
        self.assertFalse(gate.receive(radar(1), '127.0.0.3', 1.))
        self.assertTrue(gate.snapshot(1.)['blocked'])
        gate.receive(radar(10), PI, 1.)
        gate.rearm(1.)
        self.assertFalse(gate.receive(radar(9), PI, 1.2))
        self.assertFalse(gate.snapshot(1.31)['blocked'])
        self.assertEqual(gate.snapshot(1.31)['warning'], 'RADAR_TIMEOUT')
        gate.receive(radar(11), PI, 1.32)
        self.assertFalse(gate.snapshot(1.32)['blocked'])
        gate.rearm(1.32)
        gate.receive(radar(1, boot=2), PI, 1.33)
        self.assertEqual(gate.snapshot(1.33)['reason'], 'RADAR_REBOOT')
        self.assertFalse(gate.receive(radar(20, boot=1), PI, 1.34))
        self.assertTrue(gate.snapshot(1.34)['blocked'])
        gate.rearm(1.34)
        gate.receive(radar(2, boot=2, epoch=1), PI, 1.35)
        self.assertEqual(gate.snapshot(1.35)['reason'], 'RADAR_HAZARD_REPORTED')
        gate.receive(radar(3, 'UNKNOWN', boot=2, epoch=2), PI, 1.36)
        self.assertEqual(gate.snapshot(1.36)['reason'], 'RADAR_SENSOR_UNKNOWN')
        with self.assertRaises(ValueError):
            gate.rearm(1.36)

    def test_radar_snapshot_with_earlier_thread_timestamp_does_not_expire_new_packet(self):
        gate = SafetySupervisor(PI)
        gate.receive(radar(1), PI, 1.02)
        self.assertEqual(gate.snapshot(1.01)['state'], 'CLEAR')
        self.assertEqual(gate.snapshot(1.01)['age_s'], 0.)


class RadarMotionTests(unittest.TestCase):
    def ready_wire(self):
        fixture = wire_fixture.RecoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        session = fixture.session
        gate = session.safety = SafetySupervisor(PI)
        gate.receive(radar(1), PI, fixture.now)
        session.tick()
        fixture.receive(**wire_fixture.V2)
        fixture.receive(**wire_fixture.V2)
        self.assertTrue(session.snapshot()['stop_verified'])
        session.rearm_radar()
        return fixture, session, gate

    def test_direct_wheel_hand_backward_and_follow_all_stop_on_radar(self):
        for mode in ('DIRECT_CONTROL','MOTOR_TEST','GESTURE_DIRECT','GESTURE_FOLLOW'):
            with self.subTest(mode=mode):
                fixture, session, gate = self.ready_wire()
                session.select_mode(mode)
                session.operator_alive()
                if mode in ('DIRECT_CONTROL','MOTOR_TEST'):
                    session.begin_hold('FORWARD' if mode=='DIRECT_CONTROL' else 'M2')
                else:
                    self.assertTrue(session.arm())
                    direction = -2.5 if mode == 'GESTURE_DIRECT' else 2.5
                    session.submit(ControlDecision('FOLLOWING',MotionCommand(direction,0,0),'TEST',None),fixture.now)
                session.tick()
                self.assertEqual(session.tx.sent[-1]['type'], 'cmd_vel')
                # Simulate a trip after a packet has already been selected.
                packet = session._motion_packet('cmd_vel', dict(vx=2.5,vy=0,w=0,status='SLOW'))
                gate.receive(radar(2,'EVADE',epoch=1), PI, fixture.now)
                self.assertFalse(session._send_packet(packet, True))
                self.assertEqual(session.tx.sent[-1]['type'], 'stop')
                gate.receive(radar(3,epoch=1), PI, fixture.now)
                fixture.receive(**wire_fixture.V2)
                self.assertFalse(session.arm())
                self.assertFalse(session.active)
                self.assertTrue(session.snapshot()['radar']['blocked'])

    def test_queued_distance_cancelled_even_with_clear_before_next_tick(self):
        fixture, session, gate = self.ready_wire()
        session.select_mode('LLM_COMMAND')
        channel = session.distance_socket()
        self.addCleanup(channel.close)
        reply = Future()
        session.requests.put((dict(type='cmd_move', motion_id=5, vx=5, vy=0, w=0,
                                   target_distance_cm=5, status='SLOW'), reply, session.recovery_generation, channel))
        gate.receive(radar(2,'EVADE',epoch=1), PI, fixture.now)
        gate.receive(radar(3,epoch=1), PI, fixture.now)
        session.tick()
        self.assertEqual(session.tx.sent[-1]['type'], 'stop')
        session.tick()
        self.assertIsInstance(reply.exception(timeout=1), OSError)
        self.assertTrue(session.distance_cancelled)
        self.assertFalse(any(p['type']=='cmd_move' for p in session.tx.sent))

    def test_old_distance_request_is_rejected_even_after_new_channel_opens(self):
        _, session, _ = self.ready_wire()
        session.select_mode('LLM_COMMAND')
        session.controller_alive()
        previous = session.distance_socket()
        reply = Future()
        session.requests.put((dict(type='cmd_move', motion_id=5, vx=5, vy=0, w=0,
                                   target_distance_cm=5, status='SLOW'), reply, session.recovery_generation, previous))
        previous.close()
        current = session.distance_socket()
        self.addCleanup(current.close)
        session.tick()
        self.assertIsInstance(reply.exception(timeout=1), OSError)
        self.assertFalse(any(p['type']=='cmd_move' for p in session.tx.sent))

    def test_auto_releases_evade_only_after_fresh_stop_and_requires_new_input(self):
        fixture, session, gate = self.ready_wire()
        engine = DashboardEngine(session.cfg, '.tmp', radar=gate, logger=False)
        engine.client, engine.client_owner = session, 'DIRECT_CONTROL'
        engine.seen_recovery_generation = session.recovery_generation
        session.operator_alive()
        session.begin_hold('FORWARD')
        session.tick()
        old_generation = engine.control_generation
        gate.receive(radar(2, 'EVADE', epoch=1), PI, fixture.now)
        gate.receive(radar(3, 'CLEAR', epoch=1), PI, fixture.now)
        engine.tick(None, fixture.now)
        session.tick()
        self.assertEqual(session.tx.sent[-1]['type'], 'stop')
        self.assertTrue(gate.snapshot(fixture.now)['blocked'])
        self.assertGreater(engine.control_generation, old_generation)

        fixture.receive(**dict(wire_fixture.V2, motion_permit=False))
        engine.tick(None, fixture.now)
        self.assertTrue(gate.snapshot(fixture.now)['blocked'])
        for seq in range(4, 10):
            fixture.now += .18
            gate.receive(radar(seq, 'CLEAR', epoch=1), PI, fixture.now)
            fixture.receive(**dict(wire_fixture.V2, motion_permit=False))
            engine.tick(None, fixture.now)
        self.assertFalse(gate.snapshot(fixture.now)['blocked'])
        self.assertFalse(session.armed)
        session.tick()
        self.assertNotEqual(session.tx.sent[-1]['type'], 'cmd_vel')
        session.operator_alive()
        session.begin_hold('FORWARD')
        session.tick()
        self.assertEqual(session.tx.sent[-1]['type'], 'cmd_vel')

        fixture.now += gate.timeout + .01
        session.tick()
        self.assertEqual(gate.snapshot(fixture.now)['warning'], 'RADAR_TIMEOUT')
        self.assertFalse(gate.snapshot(fixture.now)['blocked'])
        gate.receive(radar(10, 'CLEAR', epoch=1), PI, fixture.now)
        self.assertTrue(gate.snapshot(fixture.now)['fresh'])

    def test_pi_reboot_sends_stop_without_operator_poll(self):
        fixture, session, gate = self.ready_wire()
        session.operator_alive()
        session.begin_hold('FORWARD')
        session.tick()
        self.assertEqual(session.tx.sent[-1]['type'], 'cmd_vel')
        gate.receive(radar(1, boot=2), PI, fixture.now)
        session.tick()
        self.assertEqual(session.tx.sent[-1]['type'], 'stop')
        self.assertEqual(session.radar_status()['reason'], 'RADAR_REBOOT')
        self.assertFalse(session.active)
        self.assertFalse(session.arm())

    def test_llm_first_move_and_between_moves_never_dispatch_second(self):
        for trigger in ('MOVE_ACCEPTED', 'STOP_CONFIRMED'):
            with self.subTest(trigger=trigger):
                robot, session, executor = goal_fixture.V2GoalTests().setup_transport()
                gate = session.safety = SafetySupervisor(PI)
                seq = [0]
                def clear():
                    seq[0] += 1
                    gate.receive(radar(seq[0]), PI, robot.now)
                for _ in range(20):
                    clear()
                    session.tick()
                    if session.snapshot()['stop_verified']:
                        break
                session.rearm_radar()
                tick = session.tick
                blocked_at = []
                def step():
                    if not blocked_at:
                        clear()
                    tick()
                session.tick = step
                def emit(event, **fields):
                    if event == trigger and robot.moves == 1 and not blocked_at:
                        seq[0] += 1
                        gate.receive(radar(seq[0], 'EVADE', epoch=1), PI, robot.now)
                        blocked_at.append(len(robot.sent))
                executor.emit = emit
                plan = ValidatedPlan(parse_operator_command('앞으로 5cm가고 뒤로 5cm가'))
                with self.assertRaisesRegex(MotionError, 'RADAR_EVADE'):
                    executor.run(plan, approved=True)
                self.assertEqual(robot.moves, 1)
                self.assertTrue(blocked_at)
                later = robot.sent[blocked_at[0]:]
                self.assertTrue(any(p['type']=='stop' for p in later))
                self.assertTrue(all(p['type'] not in ('cmd_move','cmd_vel') and
                                    p.get('motion_permit') is not True for p in later))
