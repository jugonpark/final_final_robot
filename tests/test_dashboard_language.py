import json
import unittest
from unittest.mock import patch

from python_controller.planning.dashboard_command import normalize_dashboard_command, prepare_dashboard_plan
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
