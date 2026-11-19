import importlib.util
import json
import os
import unittest
from decimal import Decimal
from threading import Event
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

from python_controller.planning.schema import Move

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
PLAN = '{"intent":"create_plan","plan":{"actions":[{"type":"move","direction":"forward","distance_cm":10}]}}'
EDIT = '{"intent":"modify_plan","action_index":1,"replacement":{"type":"move","direction":"forward","distance_cm":5}}'
COMPOSITE = '{"intent":"mode_then_plan","mode":"LLM_COMMAND","motion_text":"앞으로 5cm 가"}'


def move(direction, distance):
    return {'type': 'move', 'direction': direction, 'distance_cm': distance}


def sequence(*actions):
    return json.dumps({'intent': 'create_sequence', 'actions': actions})


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
    def test_explicit_composite_and_sequence_do_not_invent_movement(self):
        from python_controller.llm.agent_intent import explicit_composite, new_plan, parse_intent, requested_plan

        self.assertEqual(explicit_composite('LLM 제어로 바꾸고 앞으로 5cm 가', parse_intent(COMPOSITE)), '앞으로 5cm 가')
        for text in ('수동 제어로 바꿔줘 그리고 앞으로 5cm 가', 'LLM 제어로 바꿔줘 그리고 앞으로 15cm 가'):
            with self.assertRaisesRegex(ValueError, 'COMPOSITE_DOES_NOT_MATCH_COMMAND'):
                explicit_composite(text, parse_intent(COMPOSITE))
        guarded = '앞으로 10cm 갔다가 오른쪽으로 5cm, 뒤로 10cm 가는 계획 만들어줘.'
        plan = new_plan(guarded, parse_intent(sequence(move('forward', 10), move('right', 5), move('backward', 10))))
        self.assertEqual(len(plan.actions), 3)
        with self.assertRaisesRegex(ValueError, 'PLAN_DOES_NOT_MATCH_COMMAND'):
            new_plan(guarded, parse_intent(sequence(move('forward', 10), move('left', 5), move('backward', 10))))
        with self.assertRaisesRegex(ValueError, 'ACTION_COUNT'):
            requested_plan(' 그리고 '.join(['앞으로 5cm 가'] * 5))
        self.assertTrue(requested_plan('앞으로 5cm 가고 좌회전 90도').requires_angle_control)

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

    def test_edits_reuse_card_validate_all_steps_and_preserve_previous_on_stale_edit(self):
        from python_controller.llm.agent_intent import parse_intent

        def edit(kind, revision, index=None, replacement=None):
            return {'kind': kind, 'expected_revision': revision, 'index': index, 'replacement': replacement}

        updates = iter((edit('delete', 1, 2), edit('append', 2, replacement=move('right', 5)),
                        edit('insert', 3, 2, move('backward', 5)), edit('replace', 4, 1, move('forward', 25)),
                        edit('delete', 4, 2), edit('replace', 5, 1, move('forward', 50)),
                        edit('replace', 5, 1, move('forward', 50)),
                        edit('append', 6, replacement={'type': 'stop'}),
                        edit('append', 6, replacement=move('right', 5)),
                        edit('replace', 6, 3, move('backward', 50))))

        class Editor:
            def interpret(self, text, *, history, plan):
                if not plan:
                    return parse_intent(sequence(move('forward', 50), move('left', 5), move('backward', 40)))
                return parse_intent(json.dumps({'intent': 'edit_plan', 'plan_id': plan['id'], 'operation': next(updates)}))

        from python_controller.llm.unified_agent import UnifiedAgent
        agent = UnifiedAgent(self.engine, Editor())
        agent.submit('앞으로 50cm 가고 왼쪽으로 5cm 가고 뒤로 40cm 가')
        self.drain(agent)
        plan_id = agent.view()['card']['id']
        cases = [('두 번째 동작 삭제해', 2, 2), ('마지막에 오른쪽으로 5cm 추가해', 3, 3),
                 ('두 번째 단계 앞에 뒤로 5cm 추가해', 4, 4), ('첫 번째 동작 앞으로 25cm로 바꿔', 5, 4),
                 ('두 번째 동작 삭제해', 5, 4), ('첫 번째 동작 앞으로 30cm로 바꿔', 5, 4),
                 ('첫 번째 동작 앞으로 50cm로 바꿔', 6, 4), ('마지막에 정지 추가해', 6, 4),
                 ('마지막에 오른쪽으로 5cm 추가해', 6, 4), ('세 번째 동작 뒤로 50cm로 바꿔', 6, 4)]
        for text, revision, count in cases:
            with self.subTest(text=text, revision=revision):
                agent.ticket = object()
                agent.submit(text)
                self.assertIsNone(agent.ticket)
                self.drain(agent)
                card = agent.view()['card']
                self.assertEqual((card['id'], card['revision'], len(card['steps'])), (plan_id, revision, count))
                self.assertEqual(agent.graph.graph.get_state(agent.graph.config).next, ('review',))
        with self.assertRaises(ValueError):
            agent.review(plan_id, 5, approve=True, operator_confirmed=True)

    def test_live_vision_queries_omit_stale_or_synthetic_observations(self):
        from python_controller.dashboard_runtime import PreviewStream

        stream = PreviewStream()
        stream.target_id = 7
        output = stream.latest()
        output['vision']['raw'] = [{'name': 'Open_Palm', 'confidence': .92}]
        self.engine.tick(output)
        agent = self.agent('{"intent":"query_vision","target":"all"}', '{"intent":"query_vision","target":"people"}',
                           '{"intent":"query_vision","target":"hands"}', '{"intent":"explain_blocked","target":"all"}',
                           '{"intent":"query_vision","target":"mapping"}')
        agent.submit('사람과 손동작 보여?')
        self.drain(agent)
        self.assertIn('검출 인원 1명', agent.view()['messages'][-1]['content'])
        self.assertIn('92%', agent.view()['messages'][-1]['content'])
        with patch('python_controller.llm.unified_agent.monotonic', return_value=output['frame'].timestamp+30):
            agent.submit('지금 사람 보여?')
            self.drain(agent)
            view = agent.view()
            self.assertIn('최근 유효한 카메라 인식 결과가 없어', view['messages'][-1]['content'])
            self.assertIsNone(view['vision_facts']['people'])
        self.engine.source = 'SYNTHETIC_PREVIEW'
        agent.submit('손동작 보여?')
        self.drain(agent)
        self.assertIn('합성 영상 미리보기', agent.view()['messages'][-1]['content'])
        self.assertEqual(agent.view()['vision_facts']['hands'], [])
        agent.submit('왜 안 움직여?')
        self.drain(agent)
        reply = agent.view()['messages'][-1]['content']
        for evidence in ('레이더 UNKNOWN', 'ESP32 중앙 제어 통신 미확인', '장치 STOP 응답 미확인'):
            self.assertIn(evidence, reply)
        self.engine.client_owner = 'GESTURE_DIRECT'
        agent.submit('손바닥 펼치면 뭐해?')
        self.drain(agent)
        view = agent.view()
        self.assertIn('손바닥 펼침 (Open_Palm) → 전진 (FORWARD)', view['messages'][-1]['content'])
        self.assertEqual(view['vision_facts']['mapping'], self.engine.hand.mapping)

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

    def test_composite_waits_for_fresh_mode_stop_then_only_previews(self):
        robot, session, _ = self.attach_session()
        session.select_mode('DIRECT_CONTROL')
        self.engine.client_owner = 'DIRECT_CONTROL'
        agent = self.agent(COMPOSITE)
        agent.submit('LLM 제어로 바꿔줘 그리고 앞으로 5cm 가')
        self.drain(agent)
        self.assertEqual(self.engine.pending_mode, 'LLM_COMMAND')
        self.assertFalse(session.snapshot()['stop_verified'])
        self.assertTrue(agent.view()['waiting_mode_plan'])
        self.assertIsNone(agent.view()['card'])
        self.assertIsNone(agent.ticket)
        agent.submit('진행 상태 알려줘')
        self.assertTrue(agent.view()['waiting_mode_plan'])
        self.assertEqual(len(self.interpreter.contexts), 1)
        for _ in range(25):
            session.tick()
            self.engine._finish_mode_change()
            if self.engine.pending_mode is None:
                break
        view = agent.view()
        self.assertFalse(view['waiting_mode_plan'])
        self.assertEqual(view['card']['steps'], ['앞으로 5 cm'])
        self.assertEqual(agent.graph.graph.get_state(agent.graph.config).next, ('review',))
        self.assertTrue(view['can_approve'])
        self.assertIsNone(agent.ticket)
        self.assertEqual(robot.moves, 0)

    def test_composite_cancels_on_radar_trip_or_estop(self):
        from test_radar_integration import PI, radar

        robot, session, gate = self.attach_session()
        session.select_mode('DIRECT_CONTROL')
        self.engine.client_owner = 'DIRECT_CONTROL'
        agent = self.agent(COMPOSITE, COMPOSITE)
        command = 'LLM 제어로 바꿔줘 그리고 앞으로 5cm 가'
        agent.submit(command)
        self.drain(agent)
        self.assertTrue(agent.view()['waiting_mode_plan'])
        agent.emergency()
        self.assertFalse(agent.view()['waiting_mode_plan'])
        self.assertTrue(self.engine.c.estop_latched)
        for _ in range(20):
            session.tick()
            if session.snapshot()['stop_verified']:
                break
        agent.acknowledge_estop()
        agent.submit(command)
        self.drain(agent)
        self.assertTrue(agent.view()['waiting_mode_plan'])
        gate.receive(radar(100, 'EVADE', epoch=1), PI, robot.now)
        view = agent.view()
        self.assertFalse(view['waiting_mode_plan'])
        self.assertIsNone(view['card'])
        self.assertEqual(robot.moves, 0)

    def test_explicit_mode_correction_replaces_waiting_composite(self):
        robot, session, _ = self.attach_session()
        session.select_mode('DIRECT_CONTROL')
        self.engine.client_owner = 'DIRECT_CONTROL'
        agent = self.agent(COMPOSITE)
        agent.submit('LLM 제어로 바꿔줘 그리고 앞으로 5cm 가')
        self.drain(agent)
        self.assertEqual(self.engine.pending_mode, 'LLM_COMMAND')
        agent.submit('아니다 손동작으로')
        self.assertEqual(self.engine.pending_mode, 'GESTURE_DIRECT')
        self.assertFalse(agent.view()['waiting_mode_plan'])
        self.assertIsNone(agent.view()['card'])
        self.assertEqual(len(self.interpreter.contexts), 1)
        self.assertEqual(robot.moves, 0)

    def test_four_steps_use_one_executor_and_report_only_stop_verified_completion(self):
        from python_controller.execution.motion_executor import LLMMotionExecutor

        robot, _, _ = self.attach_session()
        actions = (move('forward', 5), move('right', 5), move('backward', 5), move('left', 5))
        agent = self.agent(sequence(*actions))
        agent.submit('앞으로 5cm 가고 오른쪽으로 5cm 가고 뒤로 5cm 가고 왼쪽으로 5cm 가')
        self.drain(agent)
        card = agent.view()['card']
        with patch.object(LLMMotionExecutor, 'run', autospec=True, side_effect=LLMMotionExecutor.run) as run:
            agent.review(card['id'], 1, approve=True, operator_confirmed=True)
            self.drain(agent)
            deadline = monotonic()+5
            while self.engine.language.busy and monotonic()<deadline:
                self.engine.language.poll()
                sleep(.005)
            self.engine.language.poll()
            run.assert_called_once()
        progress = agent.view()['progress']
        self.assertEqual(robot.moves, 4)
        self.assertEqual((progress['plan_id'], progress['revision']), (card['id'], 1))
        self.assertEqual(progress['verified_count'], 4)
        self.assertEqual(progress['steps'], ['STOP_VERIFIED'] * 4)
        self.assertEqual(progress['state'], 'COMPLETED')
        if importlib.util.find_spec('PySide6'):
            from PySide6.QtWidgets import QApplication
            from python_controller.visualization.agent_panel import AgentPanel

            app = QApplication.instance() or QApplication([])
            panel = AgentPanel(agent, lambda _: None)
            self.addCleanup(panel.close)
            app.processEvents()
            summary = panel.execution_progress.text()
            self.assertIn('단계 STOP 확인 4/4', summary)
            self.assertIn('4. 왼쪽으로 5 cm · STOP 확인', summary)

    def test_move_without_stop_ack_does_not_complete_or_start_next(self):
        from python_controller.execution.motion_executor import LLMMotionExecutor

        robot, _, _ = self.attach_session()
        agent = self.agent(sequence(move('forward', 5), move('right', 5)))
        agent.submit('앞으로 5cm 가고 오른쪽으로 5cm 가')
        self.drain(agent)
        card = agent.view()['card']
        waiting, proceed = Event(), Event()
        confirm = LLMMotionExecutor._confirm_stop
        sendto = robot.sendto

        def omit_stationary_confirmation(raw, destination):
            goal = robot.goal
            sent = sendto(raw, destination)
            if goal and json.loads(raw)['type'] == 'stop':
                robot.goal = goal
            return sent

        def observe_before_stop(executor):
            if robot.moves:
                waiting.set()
                proceed.wait(4)
                robot.sendto = omit_stationary_confirmation
            return confirm(executor)

        with patch.object(LLMMotionExecutor, '_confirm_stop', observe_before_stop):
            try:
                agent.review(card['id'], 1, approve=True, operator_confirmed=True)
                self.drain(agent)
                self.assertTrue(waiting.wait(2))
                self.engine.language.poll()
                state = agent.view()['progress']
                self.assertEqual(state['steps'], ['GOAL_REPORTED', 'WAITING'])
                self.assertEqual(state['verified_count'], 0)
                agent.submit('진행 상태 알려줘')
                self.assertIn('STOP 확인 중', agent.view()['messages'][-1]['content'])
            finally:
                proceed.set()
            deadline = monotonic()+5
            while self.engine.language.busy and monotonic()<deadline:
                self.engine.language.poll()
                sleep(.005)
            self.engine.language.poll()
        robot.sendto = sendto
        progress = agent.view()['progress']
        self.assertEqual(robot.moves, 1)
        self.assertEqual((progress['state'], progress['verified_count']), ('ABORTED', 0))
        self.assertEqual(progress['steps'], ['BLOCKED', 'BLOCKED'])

    def test_radar_trip_during_sequence_blocks_remaining_steps_and_no_resume(self):
        from python_controller.execution.motion_executor import LLMMotionExecutor
        from test_radar_integration import PI, radar

        robot, _, gate = self.attach_session()
        agent = self.agent(sequence(move('forward', 5), move('right', 5)),
                           '{"intent":"chat","reply":"새 계획과 운영자 승인이 필요합니다."}')
        agent.submit('앞으로 5cm 가고 오른쪽으로 5cm 가')
        self.drain(agent)
        card = agent.view()['card']
        confirm = LLMMotionExecutor._confirm_stop

        def trip_before_stop(executor):
            if robot.moves:
                gate.receive(radar(100, 'EVADE', epoch=1), PI, robot.now)
            return confirm(executor)

        with patch.object(LLMMotionExecutor, '_confirm_stop', trip_before_stop):
            agent.review(card['id'], 1, approve=True, operator_confirmed=True)
            self.drain(agent)
            deadline = monotonic()+5
            while self.engine.language.busy and monotonic()<deadline:
                self.engine.language.poll()
                sleep(.005)
            self.engine.language.poll()
        progress = agent.view()['progress']
        self.assertEqual(progress['state'], 'ABORTED')
        self.assertEqual(progress['steps'], ['BLOCKED', 'BLOCKED'])
        agent.submit('계속해')
        self.drain(agent)
        self.assertEqual(robot.moves, 1)
        self.assertIsNone(agent.view()['card'])
        self.assertFalse(agent.view()['can_approve'])

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
