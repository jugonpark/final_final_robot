"""End-to-end v2 goal wire simulation using the production transport and executor."""
import json
import unittest
from unittest.mock import patch

from python_controller.communication.esp32_udp import Protocol
from python_controller.communication.robot_session import RobotSession
from python_controller.config import load_config
from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
from python_controller.planning.validator import ValidatedPlan, parse_operator_command


class V2Robot:
    def __init__(self, bad_completion_token=False):
        self.now = 0.
        self.next_rx = 0.
        self.last = self.goal = None
        self.received = self.heartbeat_at = self.started = 0.
        self.tokens = []
        self.moves = 0
        self.completed_at = None
        self.bad_completion_token = bad_completion_token
        self.sent = []

    def sendto(self, raw, target):
        data = json.loads(raw)
        self.sent.append(data)
        self.last,self.received = data,self.now
        if data['type']=='stop':
            self.goal=None
        elif data['type']=='cmd_move':
            if self.goal is None or data['motion_id'] != self.goal['motion_id']:
                self.goal,self.started = data,self.now
                self.moves+=1
                self.tokens.append(data['execution_token'])
                self.heartbeat_at=self.now
        elif data['type']=='heartbeat':
            self.heartbeat_at=self.now
        return len(raw)

    def recvfrom(self, size):
        self.now+=.05
        if self.last is None or self.now<self.next_rx:
            raise BlockingIOError()
        self.next_rx=self.now+.2
        goal=self.goal
        reached=bool(goal and (self.now-self.started)*5>=goal['target_distance_cm'])
        if reached:self.completed_at=self.now
        state='GOAL_REACHED' if reached else 'DISTANCE_ACTIVE' if goal else 'STOPPED'
        progress=min(goal['target_distance_cm'],(self.now-self.started)*5) if goal else None
        token=goal['execution_token'] if goal else self.last.get('execution_token',0)
        if reached and self.bad_completion_token:token+=1
        data=dict(type='telemetry',session_id=self.last['session_id'],boot_id=99,
                  last_seq=self.last['seq'],uptime_ms=int(self.now*1000),command_age_ms=int((self.now-self.received)*1000),
                  state=state,mode='NETWORK',fault='NONE',protocol_version=2,
                  heartbeat_period_ms=100,heartbeat_timeout_ms=600,velocity_ttl_ms=300,
                  heartbeat_age_ms=int((self.now-self.heartbeat_at)*1000),velocity_age_ms=None,
                  controller_alive=True,motion_permit=bool(goal and not reached),execution_token=token,
                  encoder_policy='FILTERED_TEST',cmd_vx=0,cmd_vy=0,cmd_w=0,
                  wheel_pwm=[0,0,0],wheel_speed=[0,0,0],wheel_target=[0,0,0],encoder_count=[0,0,0],
                  motion_id=goal['motion_id'] if goal else None,goal_active=bool(goal and not reached),
                  goal_reached=reached,goal_target_cm=goal['target_distance_cm'] if goal else None,
                  goal_progress_cm=progress,remaining_cm=goal['target_distance_cm']-progress if goal else None,
                  lateral_error_cm=0,overshoot_cm=0)
        return json.dumps(data).encode(),('127.0.0.1',8888)

    def close(self):
        pass


class V2GoalTests(unittest.TestCase):
    def setup_transport(self, bad=False):
        cfg=load_config();cfg['communication']['host']='127.0.0.1'
        robot=V2Robot(bad)
        session=RobotSession(cfg,clock=lambda:robot.now)
        session.tx=session.rx=robot
        session.configure_owner('DIRECT_CONTROL')
        tick=session.tick
        def step():
            session.controller_alive()
            tick()
        session.tick=step
        for _ in range(30):
            session.tick()
            if session.snapshot()['ready'] and session.snapshot()['stop_verified']:break
        self.assertTrue(session.snapshot()['ready'])
        self.assertIsNotNone(session.v2_barrier)
        session.select_mode('LLM_COMMAND')
        executor=LLMMotionExecutor('127.0.0.1',lambda *a,**kw:None,clock=session.clock,
                                   protocol=Protocol(session.protocol.session_id),socket_factory=session.distance_socket)
        executor.sent.update({p['seq']:(p['type'],p['host_sent_at']) for p in session.tx_history})
        return robot,session,executor

    def test_thirty_cm_goal_runs_six_seconds_with_independent_heartbeat_and_new_token_per_action(self):
        robot,session,executor=self.setup_transport()
        executor.run(ValidatedPlan(parse_operator_command('앞으로 30cm 가고 오른쪽으로 10cm 이동해')),approved=True,
                     stop_requested=lambda:session.distance_cancelled)
        self.assertEqual(robot.moves,2)
        self.assertGreaterEqual(robot.now,8.)
        self.assertEqual(len(set(robot.tokens)),2)
        heartbeats=[p for p in robot.sent if p['type']=='heartbeat' and p.get('motion_permit')]
        self.assertGreater(len(heartbeats),30)
        self.assertTrue(all(p.get('controller_alive') for p in heartbeats))
        self.assertEqual([p['seq'] for p in robot.sent],list(range(1,len(robot.sent)+1)))
        self.assertTrue(executor.release['stop_verified'])
        self.assertFalse(session.distance_leased)

    def test_completion_from_other_execution_token_aborts_before_second_action(self):
        robot,session,executor=self.setup_transport(True)
        with self.assertRaisesRegex(MotionError,'EXECUTION_TOKEN_MISMATCH'):
            executor.run(ValidatedPlan(parse_operator_command('앞으로 5cm 가고 오른쪽으로 5cm 이동해')),approved=True)
        self.assertEqual(robot.moves,1)
        self.assertTrue(any(p['type']=='stop' for p in robot.sent))
        self.assertTrue(robot.last['type']=='stop' or
                        robot.last['type']=='heartbeat' and robot.last['motion_permit'] is False)
        self.assertIsNone(robot.goal)

    def test_default_cli_transport_uses_independent_session_without_real_sockets(self):
        robot,session,_=self.setup_transport()
        session.start=lambda:None
        session.controller_alive()
        executor=LLMMotionExecutor('127.0.0.1',lambda *a,**kw:None,clock=session.clock)
        with patch('python_controller.communication.robot_session.RobotSession',return_value=session),patch(
            'socket.socket',side_effect=AssertionError('CLI must use the mocked transport')):
            executor.run(ValidatedPlan(parse_operator_command('앞으로 5cm 가')),approved=True)
        self.assertIs(executor.owned_session,session)
        self.assertTrue(executor.independent_transport)
        self.assertEqual(robot.moves,1)
        self.assertTrue(session.done.is_set())
