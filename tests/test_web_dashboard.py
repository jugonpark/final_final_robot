import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import Mock, patch

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
        self.web = WebDashboard(self.engine, PreviewStream, settings_dir=Path(tmp.name))
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

    def test_addresses_are_validated_persisted_and_blocked_while_connected(self):
        from python_controller.communication.motor_test import load_connection

        self.web.command({'action': 'save_esp32_ip', 'address': '192.0.2.136'})
        self.web.command({'action': 'save_radar_ip', 'address': '192.0.2.106'})
        self.assertEqual(load_connection(self.web.settings_dir / 'connection.json'), '192.0.2.136')
        self.assertEqual(self.web.state()['settings']['radar'], '192.0.2.106')
        with self.assertRaises(ValueError):
            self.web.command({'action': 'save_esp32_ip', 'address': 12345})
        with patch.object(self.engine, 'client', object()):
            for action in ('save_esp32_ip', 'save_radar_ip'):
                with self.assertRaises(ValueError):
                    self.web.command({'action': action, 'address': '192.0.2.1'})

    def test_shared_connection_and_agent_approval_use_existing_engine(self):
        from python_controller.web_dashboard import WebDashboard

        agent = Mock()
        shared = WebDashboard(self.engine, None, agent, shared_stream=PreviewStream(), settings_dir=self.web.settings_dir)
        shared.command({'action': 'save_esp32_ip', 'address': '192.0.2.136'})
        with patch.object(self.engine, 'connect_robot') as connect:
            with self.assertRaises(ValueError):
                shared.command({'action': 'connect_robot', 'confirmed': 'true'})
            shared.command({'action': 'connect_robot', 'confirmed': True})
            connect.assert_called_once_with('192.0.2.136')
        with self.assertRaises(ValueError):
            self.web.command({'action': 'agent_approve', 'confirmed': True})
        shared.command({'action': 'agent_approve', 'plan_id': 'draft', 'revision': 3, 'confirmed': True})
        agent.review.assert_called_once_with('draft', 3, approve=True, operator_confirmed=True)

    def test_shared_camera_view_never_stops_qt_stream(self):
        from python_controller.web_dashboard import WebDashboard

        stream = Mock()
        shared = WebDashboard(self.engine, None, shared_stream=stream)
        shared.command({'action': 'camera_power', 'viewer': VIEWER, 'enabled': True})
        shared.command({'action': 'camera_power', 'viewer': VIEWER, 'enabled': False})
        shared.close_camera()
        self.assertIs(shared.stream, stream)
        stream.stop.assert_not_called()
        stream.close.assert_not_called()

    def test_shared_wheel_test_requires_confirmation_and_keeps_one_hold(self):
        from python_controller.web_dashboard import WebDashboard

        shared = WebDashboard(self.engine, None, shared_stream=PreviewStream())
        self.engine.client_owner = 'MOTOR_TEST'
        session = Mock(central_session=True)
        with patch.object(self.engine, 'client', session), patch.object(self.engine, 'begin_hold') as hold:
            for action in ('manual_prepare', 'test_turn'):
                with self.assertRaises(ValueError):
                    shared.command({'action': action, 'direction': 'M1'})
            result = shared.command({'action': 'manual_prepare', 'direction': 'M1', 'confirmed': True})
            hold.assert_not_called()
            shared.command({'action': 'manual_hold', **result})
            hold.assert_called_once_with('M1', remote=True)
            shared.command({'action': 'manual_hold', **result})
            session.renew_web_hold.assert_called_once()
            shared.command({'action': 'manual_release', **result})
            self.assertEqual(session.release_web_hold.call_count, 2)

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
