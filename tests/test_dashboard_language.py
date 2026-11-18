import json
import unittest
from unittest.mock import patch

from python_controller.planning.dashboard_command import normalize_dashboard_command, prepare_dashboard_plan
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.planning.validator import PlanError, parse_operator_command
from korean_command_cases import AMBIGUOUS, DENIED


class DashboardLanguageTests(unittest.TestCase):
    def test_conversational_request_keeps_explicit_directions_distances_and_order(self):
        command = '로봇아 먼저 앞으로 15cm만 가 주고 오른쪽으로 10cm 이동해 줘 부탁해'
        actions = parse_operator_command(normalize_dashboard_command(command))
        self.assertEqual([(a.direction,int(a.distance_cm)) for a in actions],[('forward',15),('right',10)])
        response = json.dumps(dict(needs_clarification=False,explanation='입력한 순서로 이동',
                                   actions=[dict(type='move',direction=a.direction,distance_cm=int(a.distance_cm)) for a in actions]))
        with patch('python_controller.llm.nvidia_client.NvidiaPlanner') as planner:
            planner.return_value.plan.return_value = response
            plan = prepare_dashboard_plan(command,dict(NVIDIA_API_KEY='mock-key',NVIDIA_MODEL='mock-model'))
            self.assertEqual(plan.actions,actions)
            planner.return_value.plan.assert_called_once_with(command)
            planner.return_value.plan.return_value = response.replace('"right"','"left"')
            with self.assertRaisesRegex(PlanError,'PLAN_DOES_NOT_MATCH_COMMAND'):
                prepare_dashboard_plan(command,dict(NVIDIA_API_KEY='mock-key',NVIDIA_MODEL='mock-model'))

    def test_original_ambiguous_and_denied_cases_remain_non_executable(self):
        for command in (*AMBIGUOUS,*DENIED):
            with self.subTest(command=command),self.assertRaises(PlanError):
                parse_operator_command(normalize_dashboard_command(command))

    def test_stop_uses_local_plan_and_never_calls_model(self):
        with patch('python_controller.llm.nvidia_client.NvidiaPlanner',side_effect=AssertionError('STOP must be local')):
            plan = prepare_dashboard_plan('멈춰줘',{})
        self.assertEqual(plan.actions[0].type,'stop')

    def test_opposite_moves_and_rotation_preview_keep_original_order(self):
        cases = [
            ('앞으로 5cm가고 뒤로 5cm가 그리고 좌로 한바퀴 회전해',
             [{'type':'move','direction':'forward','distance_cm':5},
              {'type':'move','direction':'backward','distance_cm':5},
              {'type':'rotate','direction':'left','angle_deg':360}]),
            ('왼쪽으로 10cm 이동한 다음 오른쪽으로 5cm 이동해',
             [{'type':'move','direction':'left','distance_cm':10},
              {'type':'move','direction':'right','distance_cm':5}]),
            ('앞으로 5cm 가고 뒤로 5cm 이동해',
             [{'type':'move','direction':'forward','distance_cm':5},
              {'type':'move','direction':'backward','distance_cm':5}]),
            ('한 바퀴 회전한 후 앞으로 5cm 이동해',
             [{'type':'rotate','direction':None,'angle_deg':360},
              {'type':'move','direction':'forward','distance_cm':5}]),
        ]
        for text, actions in cases:
            with self.subTest(text=text), patch('python_controller.llm.nvidia_client.NvidiaPlanner') as api:
                api.return_value.plan.return_value = json.dumps(
                    dict(needs_clarification=False, explanation='순서 유지', actions=actions))
                config = dict(NVIDIA_API_KEY='mock-key', NVIDIA_MODEL='mock-model')
                plan = prepare_dashboard_plan(text, config)
                self.assertEqual([a.model_dump() for a in plan.actions], actions)
                if plan.requires_angle_control:
                    executor = LLMMotionExecutor('127.0.0.1', lambda *a, **k: None,
                                                 socket_factory=lambda: self.fail('opened socket'))
                    with self.assertRaisesRegex(MotionError, 'ROTATION_CONTROL_NOT_VERIFIED'):
                        executor.run(plan, approved=True)
                api.return_value.plan.return_value = json.dumps(
                    dict(needs_clarification=False, explanation='순서 변경', actions=actions[::-1]))
                with self.assertRaisesRegex(PlanError, 'PLAN_DOES_NOT_MATCH_COMMAND'):
                    prepare_dashboard_plan(text, config)

    def test_compact_turns_and_natural_connectors_normalize_as_measurable_actions(self):
        command = '앞으로 50mm 하고 뒤로 5cm 이동한 후 우로 반바퀴 그다음 정지'
        plan = parse_operator_command(normalize_dashboard_command(command), allow_rotation_preview=True)
        self.assertEqual([a.type for a in plan], ['move','move','rotate','stop'])
        self.assertEqual((plan[0].distance_cm, plan[1].direction, plan[2].angle_deg, plan[2].direction),
                         (5, 'backward', 180, 'right'))
        for command, action_type in [('좌로 한바퀴','rotate'),('왼쪽으로 5cm','move')]:
            self.assertEqual(parse_operator_command(command, allow_rotation_preview=True)[0].type, action_type)
