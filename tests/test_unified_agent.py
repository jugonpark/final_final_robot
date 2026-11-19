import unittest
import importlib.util
import os
from decimal import Decimal
from threading import Event
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

from python_controller.planning.schema import Move

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
PLAN = '{"intent":"create_plan","plan":{"actions":[{"type":"move","direction":"forward","distance_cm":10}]}}'
EDIT = '{"intent":"modify_plan","action_index":1,"replacement":{"type":"move","direction":"forward","distance_cm":5}}'


class Interpreter:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.contexts = []

    def interpret(self, text, *, history, plan):
        from python_controller.llm.agent_intent import parse_intent
        self.contexts.append((text, history, plan))
        value = next(self.responses)
        return parse_intent(value() if callable(value) else value)


class AgentIntentTests(unittest.TestCase):
    def test_schema_reuses_motion_types_and_refuses_permissions(self):
        from python_controller.llm.agent_intent import parse_intent
        intent = parse_intent('{"intent":"create_plan","plan":{"actions":[{"type":"move","direction":"forward","distance_cm":5.5}]}}')
        self.assertIsInstance(intent.plan.actions[0], Move)
        self.assertEqual(intent.plan.actions[0].distance_cm, Decimal('5.5'))
        for payload in ('{"intent":"stop","intent":"chat"}', '{"intent":"stop","authorization_ticket":"forged"}',
                        '{"intent":"switch_mode","mode":"MOTOR_TEST"}',
                        '{"intent":"create_plan","plan":{"actions":[{"type":"move","direction":"forward","distance_cm":true}]}}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_intent(payload)

    def test_model_does_not_invent_motion_or_replace_unrequested_fields(self):
        from python_controller.llm.agent_intent import parse_intent, revise_plan, new_plan
        initial = parse_intent('{"intent":"create_plan","plan":{"actions":[{"type":"move","direction":"forward","distance_cm":10},{"type":"move","direction":"backward","distance_cm":5}]}}')
        plan = new_plan('앞으로 10cm 가고 뒤로 5cm 가', initial)
        edit = parse_intent('{"intent":"modify_plan","action_index":1,"replacement":{"type":"move","direction":"forward","distance_cm":5}}')
        updated = revise_plan('아니 첫 번째 5cm로 수정', edit, plan)
        self.assertEqual(updated.actions[0].distance_cm, Decimal(5))
        self.assertEqual(updated.actions[1], plan.actions[1])
        for text in ('첫 번째 15cm로 수정', '두 번째 5cm로 수정', '그대로 둬',
                     '첫 번째 4.999999999999999999999999999999cm로 수정'):
            with self.assertRaises(ValueError):
                revise_plan(text, edit, plan)
        with self.assertRaises(ValueError):
            new_plan('어디로 갈까?', initial)

    def test_imperative_stop_never_matches_status_question_or_plan(self):
        from python_controller.llm.agent_intent import emergency_stop
        for text in ('멈춰', '로봇아, 지금 긴급정지해!', '즉시 정지하세요', '멈추세요', '멈춰라', '지금 당장 멈춰', 'emergency stop'):
            self.assertTrue(emergency_stop(text), text)
        for text in ('왜 멈췄어?', '앞으로 5cm 가고 정지', '정지 버튼 위치 알려줘'):
            self.assertFalse(emergency_stop(text), text)

    def test_nemotron_uses_intent_prompt_and_validates_response(self):
        from python_controller.llm.agent_intent import NvidiaIntentInterpreter
        with patch('python_controller.llm_cli.load_environment', return_value={'NVIDIA_API_KEY':'test-key','NVIDIA_MODEL':'test-model'}), \
                patch('python_controller.llm.nvidia_client.NvidiaPlanner.complete', return_value='{"intent":"query_status","target":"radar"}') as complete:
            intent = NvidiaIntentInterpreter().interpret('레이더 상태 알려줘', history=[], plan=None)
        self.assertEqual(intent.target, 'radar')
        messages, = complete.call_args.args
        self.assertIn('switch_mode', messages[0]['content'])
        self.assertNotIn('test-key', str(messages))


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'Install requirements-agent.txt')
class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        sockets = patch('socket.socket', side_effect=AssertionError('No real robot sockets'))
        sockets.start()
        self.addCleanup(sockets.stop)
        self.engine = DashboardEngine(load_config(), folder.name, logger=False)
        self.engine.central_ui = True
        self.addCleanup(self.engine.close)

    def agent(self, *intent):
        from python_controller.llm.unified_agent import UnifiedAgent
        self.interpreter = Interpreter(*intent)
        return UnifiedAgent(self.engine, self.interpreter)

    def drain(self, agent):
        deadline = monotonic()+4
        while agent.busy and monotonic()<deadline:
            agent.poll()
            sleep(.005)
        self.assertFalse(agent.busy)

    def test_graph_interrupt_revision_and_replay_do_not_dispatch_motion(self):
        agent = self.agent(PLAN, EDIT)
        agent.submit('앞으로 10cm 가')
        self.drain(agent)
        old = agent.view()['card']
        self.assertEqual(agent.graph.graph.get_state(agent.graph.config).next, ('review',))
        agent.submit('아니 첫 번째 5cm로 수정')
        self.drain(agent)
        card = agent.view()['card']
        self.assertEqual((card['id'], card['revision']), (old['id'], 2))
        self.assertEqual(card['steps'], ['앞으로 5 cm'])
        self.assertTrue(self.interpreter.contexts[1][1])
        with self.assertRaises(ValueError):
            agent.review(old['id'], 1, approve=True, operator_confirmed=True)
        with patch.object(self.engine, 'start_natural_motion') as start:
            self.assertEqual(agent.graph.review(card, 'approve'), 'approve')
            start.assert_not_called()
        self.assertFalse(agent.view()['can_approve'])

    def test_stop_preempts_stalled_model_and_discards_late_plan(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def stuck():
            entered.set()
            release.wait(4)
            return PLAN
        agent = self.agent(stuck)
        agent.submit('앞으로 10cm 가')
        self.assertTrue(entered.wait(2))
        agent.submit('멈추세요')
        self.assertTrue(self.engine.c.estop_latched)
        self.assertTrue(agent.busy)
        release.set()
        self.drain(agent)
        self.assertIsNone(agent.view()['card'])
        self.assertEqual(len(self.interpreter.contexts), 1)

    def test_untrusted_model_cannot_stop_on_a_status_question(self):
        agent = self.agent('{"intent":"stop"}')
        agent.submit('왜 멈췄어?')
        self.drain(agent)
        self.assertFalse(self.engine.c.estop_latched)

    def attach_session(self, *, radar=True):
        from python_controller.control.safety_supervisor import SafetySupervisor
        from test_radar_integration import PI, radar as radar_packet
        from test_v2_goal_transport import V2GoalTests

        robot, session, _ = V2GoalTests().setup_transport()
        self.engine.client, self.engine.client_owner = session, 'LLM_COMMAND'
        session.operator_alive()
        self.engine.seen_recovery_generation = session.recovery_generation
        gate = session.safety = SafetySupervisor(PI)
        if radar:
            seq = [0]
            tick = session.tick
            def step():
                seq[0] += 1
                gate.receive(radar_packet(seq[0]), PI, robot.now)
                session.operator_alive()
                tick()
            session.tick = step
            for _ in range(20):
                session.tick()
                if session.snapshot()['stop_verified'] and seq[0] >= 2:
                    break
            session.rearm_radar()
        return robot, session, gate

    def test_mode_waits_for_new_stop_and_keeps_latch_and_disarm(self):
        robot, session, _ = self.attach_session()
        self.engine.request('ESTOP')
        agent = self.agent('{"intent":"switch_mode","mode":"GESTURE_DIRECT"}')
        agent.submit('손동작으로 조종할래')
        self.drain(agent)
        self.assertEqual(self.engine.pending_mode, 'GESTURE_DIRECT')
        self.assertEqual(self.engine.client_owner, 'LLM_COMMAND')
        self.engine._finish_mode_change()
        self.assertEqual(self.engine.client_owner, 'LLM_COMMAND')
        for _ in range(20):
            session.tick()
            if session.snapshot()['stop_verified']:
                break
        self.engine._finish_mode_change()
        self.assertEqual(self.engine.client_owner, 'GESTURE_DIRECT')
        self.assertTrue(self.engine.c.estop_latched)
        self.assertTrue(self.engine.hand_armed_once)
        self.assertFalse(session.armed)
        self.assertEqual(robot.moves, 0)

    def test_live_one_use_review_dispatches_existing_executor_once(self):
        robot, session, _ = self.attach_session()
        agent = self.agent(PLAN, '{"intent":"query_status","target":"radar"}')
        agent.submit('앞으로 10cm 가')
        self.drain(agent)
        agent.submit('레이더 상태 알려줘')
        self.drain(agent)
        self.assertIn('레이더 CLEAR', agent.view()['messages'][-1]['content'])
        view = agent.view()
        self.assertTrue(view['can_approve'], view['status'])
        card = view['card']
        agent.review(card['id'], 1, approve=True, operator_confirmed=True)
        with self.assertRaises(ValueError):
            agent.review(card['id'], 1, approve=True, operator_confirmed=True)
        self.drain(agent)
        deadline = monotonic()+5
        while self.engine.language.busy and monotonic()<deadline:
            self.engine.language.poll()
            sleep(.005)
        self.assertEqual(self.engine.language.phase, 'DONE', self.engine.language.detail)
        self.assertIs(self.engine.client, session)
        self.assertEqual(robot.moves, 1)
        with self.assertRaises(ValueError):
            agent.review(card['id'], 1, approve=True, operator_confirmed=True)

    def test_hazard_after_ticket_issuance_invalidates_approval(self):
        from test_radar_integration import PI, radar
        robot, _, gate = self.attach_session()
        agent = self.agent(PLAN)
        agent.submit('앞으로 10cm 가')
        self.drain(agent)
        card = agent.view()['card']
        agent.review(card['id'], 1, approve=True, operator_confirmed=True)
        gate.receive(radar(100,'EVADE',epoch=1), PI, robot.now)
        self.drain(agent)
        self.assertEqual(robot.moves, 0)
        self.assertIsNone(agent.view()['card'])

    def test_pause_after_review_click_revokes_queued_execution(self):
        robot, _, _ = self.attach_session()
        agent = self.agent(PLAN)
        agent.submit('앞으로 10cm 가')
        self.drain(agent)
        card = agent.view()['card']
        agent.review(card['id'], 1, approve=True, operator_confirmed=True)
        self.engine.request('PAUSE')
        self.drain(agent)
        self.assertIsNone(agent.view()['card'])
        self.assertFalse(self.engine.language.busy)
        self.assertEqual(robot.moves, 0)

    def test_expiry_or_recovery_revokes_issued_approval(self):
        robot, session, _ = self.attach_session()
        agent = self.agent(PLAN, PLAN)
        for cause in ('expiry', 'recovery'):
            with self.subTest(cause=cause):
                agent.submit('앞으로 10cm 가')
                self.drain(agent)
                card = agent.view()['card']
                agent.review(card['id'], 1, approve=True, operator_confirmed=True)
                if cause == 'expiry':
                    with patch('python_controller.llm.unified_agent.monotonic', return_value=agent.ticket.expires_at+1):
                        self.drain(agent)
                else:
                    session.recovery_generation += 1
                    self.drain(agent)
                self.assertIsNone(agent.view()['card'])
                self.assertFalse(self.engine.language.busy)
                self.assertEqual(robot.moves, 0)

    def test_live_gate_rechecks_radar_at_the_executor_socket_handoff(self):
        from test_radar_integration import PI, radar
        robot, session, gate = self.attach_session()
        agent = self.agent(PLAN)
        agent.submit('앞으로 10cm 가')
        self.drain(agent)
        card = agent.view()['card']
        entered, release = Event(), Event()
        original = self.engine.language._execute

        def wait_for_trip(host, plan):
            entered.set()
            release.wait(4)
            return original(host, plan)

        with patch.object(self.engine.language, '_execute', side_effect=wait_for_trip), \
                patch.object(session, 'distance_socket', wraps=session.distance_socket) as socket_lease:
            try:
                agent.review(card['id'], 1, approve=True, operator_confirmed=True)
                self.drain(agent)
                self.assertTrue(entered.wait(2))
                gate.receive(radar(100,'UNKNOWN',epoch=1), PI, robot.now)
            finally:
                release.set()
            deadline = monotonic()+5
            while self.engine.language.busy and monotonic()<deadline:
                self.engine.language.poll()
                sleep(.005)
            self.assertEqual(self.engine.language.phase, 'FAILED')
            socket_lease.assert_not_called()
        self.assertEqual(robot.moves, 0)

    def test_radar_rearm_does_not_clear_estop_or_arm_hand_control(self):
        from python_controller.dashboard_runtime import PreviewStream
        from python_controller.models import PerceptionFrame
        from python_controller.vision.gesture_recognizer import gesture_diagnostics

        robot, session, _ = self.attach_session()
        agent = self.agent('{"intent":"switch_mode","mode":"GESTURE_DIRECT"}')
        agent.submit('손동작으로 조종할래')
        self.drain(agent)
        for _ in range(20):
            session.tick()
            if session.snapshot()['stop_verified']:
                break
        self.engine._finish_mode_change()
        self.assertTrue(self.engine.hand_requires_activation)
        self.engine.request('ESTOP')
        for _ in range(20):
            session.tick()
            if session.snapshot()['stop_verified']:
                break
        self.engine.rearm_radar()
        now = robot.now
        neutral = PreviewStream().latest()
        neutral.update(frame=PerceptionFrame(now,640,480,()), hand_only=True, completed_at=now,
                       vision=gesture_diagnostics([],None,0,now,0))
        with patch.object(session, 'arm', wraps=session.arm) as arm:
            self.engine.tick(neutral, now=now)
            arm.assert_not_called()
        self.assertTrue(self.engine.c.estop_latched)
        self.assertFalse(session.armed)
        agent.acknowledge_estop()
        self.assertFalse(self.engine.c.estop_latched)
        self.assertFalse(session.armed)

    @unittest.skipUnless(importlib.util.find_spec('PySide6'), 'PySide6 missing')
    def test_qt_navigation_is_display_only_and_status_uses_python(self):
        from PySide6.QtWidgets import QApplication
        from python_controller.dashboard_runtime import PreviewStream
        from python_controller.visualization.dashboard import Dashboard

        app = QApplication.instance() or QApplication([])
        interpreter = Interpreter('{"intent":"navigate","page":"camera"}', '{"intent":"query_status","target":"radar"}')
        window = Dashboard(self.engine.cfg, engine=self.engine, stream=PreviewStream(), agent_enabled=True, agent_interpreter=interpreter)
        window.timer.stop()
        self.addCleanup(window.close)
        window.show()
        with patch.object(self.engine, 'select_control_mode') as select, patch.object(self.engine, 'end_hold') as stop:
            panel = window.language_panel
            panel.command.setText('카메라 화면 보여줘')
            panel.send_button.click()
            self.drain(window.agent)
            window.poll()
            app.processEvents()
            self.assertEqual(window.tabs.currentIndex(), window.follow_tab_index)
            select.assert_not_called()
            stop.assert_not_called()
            window.nav_buttons[0].click()
            panel.command.setText('레이더 상태 알려줘')
            panel.send_button.click()
            self.drain(window.agent)
            window.poll()
            self.assertIn('레이더 UNKNOWN', panel.history.toPlainText())
            self.assertFalse(panel.approve.isEnabled())

    @unittest.skipUnless(importlib.util.find_spec('PySide6'), 'PySide6 missing')
    def test_qt_emergency_shortcut_and_typed_stop_clear_deferred_inputs(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from python_controller.dashboard_runtime import PreviewStream
        from python_controller.visualization.dashboard import Dashboard

        app = QApplication.instance() or QApplication([])
        robot, session, _ = self.attach_session()
        session.select_mode('DIRECT_CONTROL')
        self.engine.client_owner = 'DIRECT_CONTROL'
        window = Dashboard(self.engine.cfg, engine=self.engine, stream=PreviewStream(), agent_enabled=True,
                           agent_interpreter=Interpreter())
        window.timer.stop()
        self.addCleanup(window.close)
        window.show()
        window.poll()
        panel = window.language_panel
        panel.command.setFocus()
        app.processEvents()
        session.armed = True
        QTest.keyClick(panel.command, Qt.Key_Space)
        self.assertTrue(self.engine.c.estop_latched)
        for _ in range(20):
            session.tick()
            if session.snapshot()['stop_verified']:
                break
        window.agent.acknowledge_estop()
        window.poll()
        window.pending_jog = ('FORWARD', session, None)
        window.test_buttons['FORWARD'].setDown(True)
        panel.command.setText('멈추세요')
        panel.send()
        window.poll()
        self.assertIsNone(window.pending_jog)
        self.assertFalse(session.active)
        self.assertTrue(self.engine.c.estop_latched)
        self.assertFalse(any(packet['type'] in ('cmd_move','cmd_vel') for packet in robot.sent))
