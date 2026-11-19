import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream


class WebDashboardTests(unittest.TestCase):
    def setUp(self):
        from python_controller.web_dashboard import WebDashboard

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('The engine must not open UDP')).start()
        stream = self.stream = PreviewStream()
        config = load_config()
        self.engine = DashboardEngine(config, Path(tmp.name), 'SYNTHETIC_PREVIEW', logger=False)
        self.addCleanup(self.engine.close)
        self.web = WebDashboard(self.engine, stream)

    def tick(self):
        self.stream.target_id = self.engine.c.target_id
        self.engine.tick(self.stream.latest())

    def test_state_and_stop_use_existing_engine(self):
        self.tick()
        before = self.web.state()
        self.assertEqual(before['owner'], 'SIMULATION')
        self.assertFalse(before['stop_verified'])
        self.assertEqual(before['radar']['state'], 'UNKNOWN')
        self.web.command({'action': 'resume_follow'})
        self.tick()
        self.assertTrue(self.web.state()['following'])
        self.web.command({'action': 'estop'})
        self.tick()
        self.assertEqual(self.web.state()['stop_code'], 'EMERGENCY_STOP')
        self.assertTrue(self.web.state()['estop_latched'])
        self.assertIsNone(self.engine.client)

    def test_camera_matches_metadata_and_stale_frame_cannot_select(self):
        self.tick()
        camera = self.web.camera()
        self.assertEqual(camera['people'][0]['id'], 7)
        self.assertTrue(camera['image'].startswith('data:image/jpeg;base64,'))
        self.web.command({'action': 'select_target', 'id': 7, 'frame': camera['frame']})
        self.tick()
        self.assertEqual(self.web.state()['target'], 7)
        with self.assertRaises(ValueError):
            self.web.command({'action': 'select_target', 'id': 7, 'frame': camera['frame'] - 1})

    def test_local_plan_never_grants_motion_and_stop_clears_plan(self):
        self.web.command({'action': 'plan', 'text': '앞으로 5cm 가고 뒤로 10cm 가'})
        plan = self.web.state()['plan']
        self.assertEqual(len(plan['steps']), 2)
        self.assertEqual(plan['distance_cm'], '15')
        self.web.command({'action': 'plan', 'text': '좌로 한바퀴 회전해'})
        self.assertTrue(self.web.state()['plan']['rotation'])
        with self.assertRaises(ValueError):
            self.web.command({'action': 'execute'})
        self.web.command({'action': 'estop'})
        self.assertIsNone(self.web.state()['plan'])

    def test_hardware_and_arbitrary_actions_are_rejected(self):
        for action in ('connect', 'forward', 'arm', 'set_policy', 'rearm_radar'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.web.command({'action': action})
        self.assertIsNone(self.engine.client)


if __name__ == '__main__':
    unittest.main()
