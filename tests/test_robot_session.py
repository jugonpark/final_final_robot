import json
import os
from pathlib import Path
import tempfile
from time import monotonic, sleep
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from python_controller.communication.robot_session import RobotSession
from python_controller.communication.esp32_udp import Protocol
from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.control.hand_drive import HandDrive
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from python_controller.models import PerceptionFrame
from test_llm_execution import HOST, MockESP32


class SessionWire:
    def __init__(self, robot):
        self.robot = robot
        self.closed = False

    def sendto(self, packet, target):
        data = json.loads(packet)
        goal = self.robot.goal
        if (data.get('type') == 'cmd_move' and goal and data.get('motion_id') == goal.get('motion_id') and
            all(data.get(key) == goal.get(key) for key in ('vx','vy','w','target_distance_cm'))):
            # Firmware refreshes the same goal without resetting its progress.
            self.robot.sent.append((self.robot.now,data,packet))
            self.robot.last_packet,self.robot.last_received = data,self.robot.now
            return len(packet)
        return self.robot.sendto(packet, target)

    def recvfrom(self, size):
        try:
            raw, source = self.robot.recvfrom(size)
            data = json.loads(raw)
            data['wheel_target'] = [0, 0, 0]
            return json.dumps(data).encode(), source
        except TimeoutError as exc:
            raise BlockingIOError() from exc

    def close(self):
        self.closed = True


class RobotSessionTests(unittest.TestCase):
    def make_session(self):
        config = load_config()
        config['communication']['host'] = HOST
        robot = MockESP32()
        session = RobotSession(config, clock=lambda: robot.now)
        transport_tick = session.tick
        def cooperative_control_and_transport():
            session.controller_alive()
            transport_tick()
        session.tick = cooperative_control_and_transport
        session.tx, session.rx = SessionWire(robot), SessionWire(robot)
        session.configure_owner('DIRECT_CONTROL')
        session.tick()
        robot.now = .21
        session.tick()
        session.operator_alive()
        self.assertTrue(session.snapshot()['stop_verified'])
        return session, robot

    def echo_stop(self, session, robot):
        session.operator_alive()
        session.tick()
        robot.now += .21
        session.operator_alive()
        session.tick()
        self.assertTrue(session.snapshot()['stop_verified'])

    def test_timed_turn_stops_at_deadline_without_gui_release(self):
        session, robot = self.make_session()
        session.select_mode('MOTOR_TEST')
        session.begin_hold('TURN_LEFT', timed_trial=True)
        started = robot.now
        for elapsed in (0, .12, .24, .36, .48):
            robot.now = started + elapsed
            session.operator_alive()
            session.tick()
        self.assertIn('cmd_vel', [p['type'] for _, p, _ in robot.sent])
        robot.now = started + .5
        session.tick()
        self.assertEqual(robot.last_packet['type'], 'stop')
        self.assertFalse(session.active)
        self.assertEqual(session.trial['stop_reason'], 'TEST_DURATION_COMPLETE_REARM_REQUIRED')

    def test_hold_release_mode_switch_and_repeat_keep_one_session_and_socket(self):
        session, robot = self.make_session()
        initial = session.protocol.session_id
        wire = session.rx
        for mode, direction in (('DIRECT_CONTROL','FORWARD'), ('MOTOR_TEST','M2'), ('DIRECT_CONTROL','RIGHT')):
            session.select_mode(mode)
            session.begin_hold(direction)
            session.tick()
            self.assertEqual(robot.last_packet['type'], 'cmd_vel')
            with self.assertRaises(ValueError):
                session.select_mode('LLM_COMMAND')
            session.end_hold()
            self.echo_stop(session, robot)
        self.assertEqual(session.protocol.session_id, initial)
        self.assertIs(session.rx, wire)
        self.assertFalse(wire.closed)
        self.assertEqual({packet['session_id'] for _,packet,_ in robot.sent}, {initial})

    def test_distance_executor_reuses_connection_and_suppresses_velocity_while_heartbeat_continues(self):
        session, robot = self.make_session()
        session.select_mode('LLM_COMMAND')
        initial = session.protocol.session_id
        wire = session.rx
        events = []

        def emit(event, **fields):
            events.append(event)
            if event == 'HANDSHAKE':
                before = len(robot.sent)
                session.tick()
                self.assertTrue(all(packet['type'] in ('heartbeat','stop')
                                    for _,packet,_ in robot.sent[before:]))
                with self.assertRaises(ValueError):
                    session.begin_hold('FORWARD')

        executor = LLMMotionExecutor(HOST, emit, protocol=Protocol(session.protocol.session_id), clock=session.clock,
                                     socket_factory=session.distance_socket)
        executor.sent.update({p['seq']:(p['type'],p['host_sent_at']) for p in session.tx_history})
        executor.run(ValidatedPlan(parse_operator_command('앞으로 15cm 가고 오른쪽으로 10cm 이동해')), approved=True)
        self.assertEqual(robot.moves, 2)
        self.assertIn('COMPLETED', events)
        self.assertFalse(session.distance_leased)
        self.assertFalse(wire.closed)
        self.assertIs(session.rx, wire)
        self.assertEqual(session.protocol.session_id, initial)
        session.operator_alive()
        session.tick()
        self.assertTrue(session.snapshot()['stop_verified'])
        session.select_mode('DIRECT_CONTROL')
        session.begin_hold('LEFT')
        session.tick()
        self.assertEqual(robot.last_packet['type'], 'cmd_vel')

    def test_estop_cancels_distance_and_blocks_any_late_move(self):
        session, robot = self.make_session()
        session.select_mode('LLM_COMMAND')
        stopped = []

        def emit(event, **fields):
            if event == 'MOVE_ACCEPTED':
                stopped.append(robot.now)
                session.stop('EMERGENCY_STOP')

        executor = LLMMotionExecutor(HOST, emit, protocol=Protocol(session.protocol.session_id), clock=session.clock,
                                     socket_factory=session.distance_socket)
        executor.sent.update({p['seq']:(p['type'],p['host_sent_at']) for p in session.tx_history})
        plan = ValidatedPlan(parse_operator_command('앞으로 15cm 가고 오른쪽으로 10cm 이동해'))
        with self.assertRaises(MotionError):
            executor.run(plan, approved=True, stop_requested=lambda: session.distance_cancelled)
        self.assertEqual(robot.moves, 1)
        self.assertTrue(stopped)
        self.assertTrue(all(p['type']=='stop' for when,p,_ in robot.sent if when >= stopped[0]))
        self.assertFalse(session.distance_leased)
        self.assertFalse(session.rx.closed)

    def test_engine_pending_mode_retains_old_owner_until_stop_and_reuses_connection(self):
        session, robot = self.make_session()
        session.start = lambda: None
        engine = DashboardEngine(load_config(), '.tmp', logger=False, client_factory=lambda config: session)
        engine.connect_robot(HOST)
        engine.select_control_mode('GESTURE_DIRECT')
        self.assertEqual(engine.client_owner, 'DIRECT_CONTROL')
        engine.tick(None, robot.now)
        self.assertEqual(engine.pending_mode, 'GESTURE_DIRECT')
        self.echo_stop(session, robot)
        engine.tick(None, robot.now)
        self.assertEqual(engine.client_owner, 'GESTURE_DIRECT')
        self.assertIs(engine.client, session)

    def test_hand_direct_without_target_and_estop_blocks_automatic_restart(self):
        session, robot = self.make_session()
        session.start = lambda: None
        engine = DashboardEngine(load_config(),'.tmp',logger=False,client_factory=lambda config:session)
        engine.connect_robot(HOST)
        engine.hand = HandDrive(hold=.1)
        engine.select_control_mode('GESTURE_DIRECT')
        self.echo_stop(session,robot)
        engine.tick(None,robot.now)

        def observe():
            now = robot.now
            result = dict(frame=PerceptionFrame(now,640,480,()), hand_only=True, completed_at=now,
                          fps=30,latency_ms=10, vision=dict(hand_count=1, recognized_at=now,raw=[
                              dict(name='Open_Palm',confidence=.95,associated_target=None)]))
            engine.tick(result,now)
            session.tick()

        observe()
        self.assertTrue(session.armed)
        robot.now += .12
        observe()
        self.assertEqual(robot.last_packet['type'],'cmd_vel')
        self.assertEqual(robot.last_packet['vx'],2.5)
        self.assertFalse(engine.c.confirmed)
        engine.request('ESTOP')
        session.tick()
        self.assertEqual(robot.last_packet['type'],'stop')
        observe()
        self.assertFalse(session.armed)
        self.assertEqual(robot.last_packet['type'],'stop')

    def test_two_dashboard_plans_use_the_same_live_connection_without_reapproval(self):
        session, robot = self.make_session()
        session.start = lambda: None
        with tempfile.TemporaryDirectory() as folder:
            engine = DashboardEngine(load_config(),folder,logger=False,client_factory=lambda config:session)
            engine.connect_robot(HOST)
            engine.select_control_mode('LLM_COMMAND')
            self.echo_stop(session,robot)
            engine.tick(None,robot.now)
            initial = session.protocol.session_id
            try:
                for command in ('앞으로 15cm 가', '오른쪽으로 10cm 가'):
                    engine.language.plan = ValidatedPlan(parse_operator_command(command))
                    engine.language.phase = 'READY'
                    engine.start_natural_motion(HOST, False)
                    deadline = monotonic()+4
                    while engine.language.busy and monotonic()<deadline:
                        engine.tick(None,robot.now)
                        sleep(.005)
                    engine.tick(None,robot.now)
                    self.assertEqual(engine.language.phase,'DONE',engine.language.detail)
                    self.assertIs(engine.client,session)
                    self.assertEqual(session.protocol.session_id,initial)
                    self.assertFalse(session.rx.closed)
                    self.assertTrue(session.snapshot()['stop_verified'])
                self.assertEqual(robot.moves,2)
            finally:
                engine.close()

    def test_dashboard_operational_and_test_workspaces_use_central_connection(self):
        from python_controller.llm_cli import motion_timeout
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from python_controller.dashboard_runtime import PreviewStream
        from python_controller.visualization.dashboard import Dashboard

        app = QApplication.instance() or QApplication([])
        session, robot = self.make_session()
        session.start = lambda: None
        with tempfile.TemporaryDirectory() as folder:
            engine = DashboardEngine(load_config(),folder,client_factory=lambda config:session,logger=False)
            with patch('socket.socket',side_effect=AssertionError('No real UDP')):
                window = Dashboard(load_config(),engine=engine,stream=PreviewStream())
                window.timer.stop()
                window.host.setText(HOST)
                window.show()
                app.processEvents()
                try:
                    self.assertEqual([window.workspaces.tabText(i) for i in range(2)], ['중앙제어','테스트·진단'])
                    self.assertEqual([window.tabs.tabText(i) for i in range(4)], ['직접 제어','손동작 제어','사람 추종','자연어 제어'])
                    window.navigate(2)
                    window.connect_button.click()
                    window.poll()
                    self.assertTrue(window.test_buttons['FORWARD'].isEnabled())
                    QTest.mousePress(window.test_buttons['FORWARD'],Qt.LeftButton)
                    session.tick()
                    self.assertEqual(robot.last_packet['type'],'cmd_vel')
                    QTest.mouseRelease(window.test_buttons['FORWARD'],Qt.LeftButton)
                    session.tick()
                    self.assertEqual(robot.last_packet['type'],'stop')
                    window.poll()
                    # A tap during pending STOP must not turn into a delayed move.
                    QTest.mouseClick(window.test_buttons['RIGHT'],Qt.LeftButton)
                    self.assertIsNone(window.pending_jog)
                    self.assertFalse(session.active)
                    QTest.mousePress(window.test_buttons['RIGHT'],Qt.LeftButton)
                    self.assertIsNotNone(window.pending_jog)
                    self.echo_stop(session,robot)
                    window.poll()
                    session.tick()
                    self.assertEqual(robot.last_packet['type'],'cmd_vel')
                    QTest.mouseRelease(window.test_buttons['RIGHT'],Qt.LeftButton)
                    self.echo_stop(session,robot)
                    window.workspaces.setCurrentIndex(1)
                    window.diagnosis_pages.setCurrentIndex(4)
                    self.echo_stop(session,robot)
                    window.poll()
                    self.assertEqual(engine.client_owner,'MOTOR_TEST')
                    self.assertTrue(window.wheel_buttons['M1'].isEnabled())
                    QTest.mousePress(window.turn_trial,Qt.LeftButton)
                    window.poll()
                    QTest.mouseRelease(window.turn_trial,Qt.LeftButton)
                    session.tick()
                    self.assertEqual(robot.last_packet['type'],'cmd_vel')
                    self.assertEqual(session.trial['duration_s'],.5)
                    window.estop.click()
                    session.tick()
                    window.poll()
                    self.assertEqual(robot.last_packet['type'],'stop')
                    self.echo_stop(session,robot)
                    window.workspaces.setCurrentIndex(0)
                    window.tabs.setCurrentIndex(window.language_tab_index)
                    self.echo_stop(session,robot)
                    window.poll()
                    control = engine.language
                    control.plan = ValidatedPlan(parse_operator_command('앞으로 15cm 가'))
                    control.phase = 'READY'
                    window.language_panel.refresh()
                    self.assertTrue(window.language_panel.execute_button.isEnabled())
                    self.assertTrue(window.language_panel.prepared.isHidden())
                    window.language_panel.execute_button.click()
                    deadline = monotonic()+6
                    while control.busy and monotonic()<deadline:
                        window.poll()
                        app.processEvents()
                        sleep(.005)
                    window.poll()
                    self.assertEqual(control.phase,'DONE',(control.detail,robot.now,robot.last_packet,robot.moves))
                    self.assertIs(engine.client,session)
                finally:
                    window.close()
