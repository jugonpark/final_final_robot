import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream

VIEWER = 'a' * 32
OTHER_VIEWER = 'b' * 32


class WebDashboardTests(unittest.TestCase):
    def setUp(self):
        from python_controller.web_dashboard import WebDashboard

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('The engine must not open UDP')).start()
        config = load_config()
        self.engine = DashboardEngine(config, Path(tmp.name), 'SYNTHETIC_PREVIEW', logger=False)
        self.addCleanup(self.engine.close)
        self.web = WebDashboard(self.engine, PreviewStream)
        self.addCleanup(self.web.close_camera)

    def power(self, enabled, viewer=VIEWER):
        return self.web.command({'action': 'camera_power', 'enabled': enabled, 'viewer': viewer})

    def tick(self):
        stream = self.web.update_camera()
        if stream:
            stream.target_id = self.engine.c.target_id
        self.engine.tick(stream.latest() if stream else None)

    def test_state_and_stop_use_existing_engine(self):
        self.power(True)
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
        self.power(True)
        self.tick()
        camera = self.web.camera()
        self.assertEqual(camera['people'][0]['id'], 7)
        self.assertTrue(camera['image'].startswith('data:image/jpeg;base64,'))
        self.web.command({'action': 'select_target', 'id': 7, 'frame': camera['frame']})
        self.tick()
        self.assertEqual(self.web.state()['target'], 7)
        with self.assertRaises(ValueError):
            self.web.command({'action': 'select_target', 'id': 7, 'frame': camera['frame'] - 1})

    def test_camera_is_lazy_and_stops_follow_when_last_viewer_leaves(self):
        self.assertIsNone(self.web.stream)
        self.assertEqual(self.web.state()['camera_status'], 'CAMERA_OFF')
        self.assertIsNone(self.web.camera(VIEWER)['image'])
        self.power(True)
        self.power(True, OTHER_VIEWER)
        self.tick()
        self.web.command({'action': 'resume_follow'})
        self.tick()
        self.assertTrue(self.web.state()['following'])
        old_frame = self.web.camera(VIEWER)['frame']
        self.power(False)
        self.assertIsNotNone(self.web.stream)
        self.assertIsNone(self.web.camera(VIEWER)['image'])
        self.power(False, OTHER_VIEWER)
        state = self.web.state()
        self.assertIsNone(self.engine.last_result)
        self.assertFalse(state['following'])
        self.assertFalse(state['perception_fresh'])
        self.assertFalse(state['armed'])
        self.assertIsNone(state['target'])
        with self.assertRaises(ValueError):
            self.web.command({'action': 'resume_follow'})
        self.power(True)
        self.tick()
        self.assertFalse(self.web.state()['following'])
        with self.assertRaises(ValueError):
            self.web.command({'action': 'select_target', 'id': 7, 'frame': old_frame})

    def test_missing_browser_heartbeat_closes_camera_and_does_not_clear_estop(self):
        from python_controller.web_dashboard import CAMERA_VIEW_TIMEOUT_S

        now = monotonic()
        with patch('python_controller.web_dashboard.monotonic', return_value=now):
            self.power(True)
        self.web.command({'action': 'estop'})
        with patch('python_controller.web_dashboard.monotonic', return_value=now + CAMERA_VIEW_TIMEOUT_S + .1):
            self.assertFalse(self.web.camera(VIEWER)['lease_active'])
            self.tick()
        self.assertIsNone(self.web.stream)
        self.assertTrue(self.web.state()['estop_latched'])

    def test_reopen_waits_for_previous_stream_to_release(self):
        class DelayedRelease(PreviewStream):
            stopped = False

        first = DelayedRelease()
        self.web.stream_factory = iter((first, PreviewStream())).__next__
        self.power(True)
        self.power(False)
        self.power(True)
        self.assertIsNone(self.web.stream)
        self.assertEqual(self.web.state()['camera_status'], 'CLOSING_CAMERA')
        first.stopped = True
        self.tick()
        self.assertIsNot(self.web.stream, first)
        self.assertTrue(self.web.state()['perception_fresh'])

    def test_camera_can_be_turned_off_even_after_control_error(self):
        self.power(True)
        self.engine.control_error = 'TEST_CONTROL_FAILED'
        self.power(False, OTHER_VIEWER)
        self.assertIsNone(self.web.stream)
        self.assertFalse(self.web.camera_views)
        with self.assertRaises(ValueError):
            self.power(True)

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
