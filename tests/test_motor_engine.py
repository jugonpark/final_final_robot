import json
from pathlib import Path
import tempfile
import unittest

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.communication.motor_test import MotorTestClient
from test_motor_test import Socket
from test_protocol import telemetry


class MotorEngineTests(unittest.TestCase):
    def test_camera_free_manual_command_and_trial_log_are_finite_and_mock_labeled(self):
        now=[1.]
        def factory(config):
            c=MotorTestClient(config,clock=lambda:now[0],evidence_source='MOCK_UDP')
            c.tx,c.rx=Socket(),Socket();c.start=lambda:None
            return c
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            e=DashboardEngine(load_config(),folder,client_factory=factory)
            try:
                e.connect_motor_test('127.0.0.1',True);c=e.client;c.tick();now[0]+=.1
                c.rx.incoming.append((telemetry(session=c.protocol.session_id,seq=c.protocol.seq,encoder_count=[0,0,0],wheel_target=[0,0,0]),('127.0.0.1',8888)))
                c.tick();e.tick(None,now[0]);e.authorize_motor_test(True,True);e.begin_motor_test('LEFT',.4);c.tick()
                d=e.tick(None,now[0])
                self.assertIsNone(e.error)
                self.assertFalse(d['following']);self.assertEqual(d['control_source'],'MANUAL_MOTOR_TEST')
                self.assertEqual(d['safe']['vy_cm_s'],load_config()['motor_test']['linear_cm_s'])
                self.assertIsNone(d['transport']['last_tx_frame_at'])
            finally:e.close()
            records=[json.loads(line) for line in e.logger.path.read_text().splitlines()]
            trial_events=[r for r in records if r.get('event')=='MOTOR_TEST_COMPLETED']
            self.assertEqual(trial_events[-1]['trial']['evidence_source'],'MOCK_UDP')
            self.assertEqual(trial_events[-1]['trial']['physical_result'],'NOT TESTED')
