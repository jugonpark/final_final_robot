import io
import json
import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
from python_controller.web_dashboard import OPERATOR_TIMEOUT_S, WebDashboard, handler, run_web_dashboard


class BrowserControlTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('No real transport allowed')).start()
        self.engine = DashboardEngine(load_config(), Path(tmp.name), 'LIVE_CAMERA', logger=False)
        self.addCleanup(self.engine.close)
        self.stream = Mock(stopped=True)
        self.web = WebDashboard(self.engine, lambda: self.stream, control_enabled=True,
                                settings_dir=Path(tmp.name))
        self.addCleanup(self.web.close_camera)

    def heartbeat(self, now=10):
        with patch('python_controller.web_dashboard.monotonic', return_value=now):
            return self.web.command({'action': 'operator_heartbeat'})

    def test_capability_is_separate_from_camera_ownership_and_requires_operator(self):
        state = self.web.state()
        self.assertTrue(state['control_available'])
        self.assertEqual(state['runtime_kind'], 'browser_control')
        self.assertFalse(state['shared_control'])
        self.assertFalse(state['operator_ready'])
        self.assertIsNone(self.web.stream)
        with patch.object(self.engine, 'connect_robot') as connect:
            with self.assertRaises(ValueError):
                self.web.command({'action': 'connect_robot', 'confirmed': True})
            with patch('python_controller.web_dashboard.monotonic', return_value=10):
                self.heartbeat()
                self.web.command({'action': 'connect_robot', 'confirmed': True})
            connect.assert_called_once_with(self.engine.cfg['communication']['host'])
        self.assertIsNone(self.web.stream)

    def test_simulation_cannot_opt_into_control_or_heartbeat(self):
        self.engine.source = 'SYNTHETIC_PREVIEW'
        with self.assertRaises(ValueError):
            WebDashboard(self.engine, PreviewStream, control_enabled=True)
        simulation = WebDashboard(self.engine, PreviewStream)
        for action in ('connect_robot', 'operator_heartbeat', 'agent_approve', 'manual_prepare'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                simulation.command({'action': action, 'confirmed': True})
        self.assertFalse(simulation.state()['control_available'])
        self.assertIsNone(self.engine.client)

    def test_state_separates_current_owner_from_pending_stop_handshake(self):
        with patch.object(self.engine, 'client', Mock(central_session=True)):
            self.engine.client_owner = 'GESTURE_DIRECT'
            self.engine.pending_mode = 'LLM_COMMAND'
            state = self.web.state()
            self.assertEqual(state['owner'], 'GESTURE_DIRECT')
            self.assertEqual(state['pending_mode'], 'LLM_COMMAND')
            self.engine.pending_mode = None
            self.assertIsNone(self.web.state()['pending_mode'])
        with patch('python_controller.web_dashboard.monotonic', return_value=10), \
                patch.object(self.engine, 'select_control_mode') as switch:
            self.heartbeat()
            self.web.command({'action': 'select_mode', 'mode': 'GESTURE_DIRECT'})
            switch.assert_called_once_with('GESTURE_DIRECT', auto_arm=False)

    def test_live_connection_keeps_lazy_camera_lifecycle(self):
        session = Mock(central_session=True)
        viewer = 'a' * 32
        with patch.object(self.engine, 'client', session):
            self.web.command({'action': 'camera_power', 'viewer': viewer, 'enabled': True})
            self.assertIs(self.web.stream, self.stream)
            self.stream.start.assert_called_once()
            self.web.command({'action': 'camera_power', 'viewer': viewer, 'enabled': False})
            self.stream.stop.assert_called_once()
            self.assertIsNone(self.web.stream)

    def test_expired_operator_stops_manual_and_llm_and_cannot_auto_resume(self):
        session = Mock(central_session=True)
        with patch.object(self.engine, 'client', session), patch.object(self.engine.language, 'cancel') as cancel:
            self.heartbeat()
            self.web.manual_hold = ('old', session)
            self.engine.web_vision = ('viewer', session, 'GESTURE_FOLLOW', 20)
            self.engine.pending_mode = 'LLM_COMMAND'
            with patch('python_controller.web_dashboard.monotonic', return_value=10 + OPERATOR_TIMEOUT_S + .1):
                self.web.update_camera()
                self.assertFalse(self.web.state()['operator_ready'])
            session.stop.assert_called_once_with('EMERGENCY_STOP')
            cancel.assert_called_once_with('EMERGENCY_STOP')
            self.assertIsNone(self.web.manual_hold)
            self.assertIsNone(self.engine.web_vision)
            self.assertIsNone(self.engine.pending_mode)
            self.assertTrue(self.engine.c.estop_latched)
            self.assertGreater(self.engine.control_generation, 0)
            self.heartbeat(20)
            self.assertTrue(self.engine.c.estop_latched)
            self.assertIsNone(self.web.manual_hold)
            self.assertIsNone(self.engine.web_vision)
            self.assertEqual(cancel.call_count, 1)

    def test_post_heartbeat_keeps_real_session_manual_hold_alive_and_loss_stops_it(self):
        from python_controller.communication.robot_session import RobotSession
        from test_llm_execution import HOST, MockESP32
        from test_robot_session import SessionWire

        self.engine.cfg['communication']['host'] = HOST
        robot = MockESP32()
        session = RobotSession(self.engine.cfg, clock=lambda: robot.now)
        session.tx, session.rx = SessionWire(robot), SessionWire(robot)
        session.configure_owner('DIRECT_CONTROL')
        session.controller_alive()
        session.tick()
        robot.now = .21
        session.controller_alive()
        session.tick()
        self.assertTrue(session.snapshot()['stop_verified'])
        started = robot.now
        with patch.object(self.engine, 'client', session), \
                patch.object(self.engine, 'client_owner', 'DIRECT_CONTROL'), \
                patch('python_controller.web_dashboard.monotonic', side_effect=lambda: robot.now):
            self.web.command({'action': 'operator_heartbeat'})
            token = self.web.command({'action': 'manual_prepare', 'direction': 'FORWARD'})
            for elapsed in (0, .1, .2, .3, .4, .5):
                robot.now = started + elapsed
                self.web.command({'action': 'operator_heartbeat'})
                self.web.command({'action': 'manual_hold', **token})
                session.controller_alive()
                session.tick()
                self.assertTrue(session.active)
            robot.now += .26
            session.controller_alive()
            session.tick()
            self.assertFalse(session.active)
            self.assertEqual(robot.last_packet['type'], 'stop')
            self.web.command({'action': 'operator_heartbeat'})
            self.assertFalse(session.active)

    def test_late_heartbeat_cannot_skip_timeout_when_supervisor_has_not_run(self):
        self.heartbeat()
        self.heartbeat(10 + OPERATOR_TIMEOUT_S + .1)
        self.assertTrue(self.engine.c.estop_latched)

    def test_live_agent_approval_preserves_revision_and_operator_confirmation(self):
        self.web.agent = Mock()
        with patch('python_controller.web_dashboard.monotonic', return_value=10):
            self.heartbeat()
            self.web.command({'action': 'agent_approve', 'plan_id': 'draft', 'revision': 7, 'confirmed': True})
        self.web.agent.review.assert_called_once_with('draft', 7, approve=True, operator_confirmed=True)

    def test_get_state_does_not_renew_operator_lease(self):
        self.heartbeat()
        with patch.object(self.engine, 'operator_alive') as alive:
            with patch('python_controller.web_dashboard.monotonic', return_value=10.5):
                self.web.state()
            self.assertEqual(self.web.operator_until, 10 + OPERATOR_TIMEOUT_S)
            alive.assert_not_called()

    def test_heartbeat_http_requires_local_origin_json_and_valid_body(self):
        cls = handler(self.web, 8766)
        for origin, content_type, expected in (
                ('http://evil.example', 'application/json', 403),
                ('http://127.0.0.1:8766', 'text/plain', 403),
                ('http://127.0.0.1:8766', 'application/json', 200)):
            request = object.__new__(cls)
            request.path = '/api/control'
            request.headers = Message()
            payload = json.dumps({'action': 'operator_heartbeat'}).encode()
            for name, value in {'Host': '127.0.0.1:8766', 'Origin': origin,
                                'Content-Type': content_type, 'Content-Length': str(len(payload))}.items():
                request.headers[name] = value
            request.rfile = io.BytesIO(payload)
            request.connection = Mock()
            request.send_json = Mock()
            with patch('python_controller.web_dashboard.monotonic', return_value=10):
                request.do_POST()
            args = request.send_json.call_args.args
            self.assertEqual(args[1] if len(args) > 1 else 200, expected)
            self.assertEqual(self.web.operator_until is not None, expected == 200)


class BrowserRuntimeTests(unittest.TestCase):
    def test_cli_dispatches_standalone_without_loading_qt(self):
        from python_controller.main import main
        with patch('python_controller.web_dashboard.run_web_dashboard', return_value=0) as run:
            self.assertEqual(main(['--ui', 'web', '--web-control', '--open-web', '--agent-enabled']), 0)
        self.assertEqual(run.call_args.kwargs, dict(camera=False, port=8765, agent_enabled=True,
                                                   control=True, open_web=True))

    def test_invalid_control_combinations_fail_before_resources(self):
        from python_controller.main import main
        for args in (['--web-control'], ['--ui', 'web', '--web-control', '--demo'],
                     ['--ui', 'web', '--web-control', '--share-web'],
                     ['--ui', 'web', '--web-control', '--mode', 'HARDWARE'],
                     ['--ui', 'web', '--web-control', '--radarless-bench']):
            with self.subTest(args=args), patch('socket.socket', side_effect=AssertionError('No socket')):
                with self.assertRaises(SystemExit):
                    main(args)

    def test_standalone_opens_bound_url_lazily_and_cleans_up_after_interrupt(self):
        engine = Mock(source='LIVE_CAMERA', control_lock=RLock())
        server = Mock(server_port=8779)
        server.serve_forever.side_effect = KeyboardInterrupt
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        with patch('python_controller.web_dashboard.DashboardEngine', return_value=engine), \
                patch('python_controller.web_dashboard.ThreadingHTTPServer', return_value=server), \
                patch('python_controller.web_dashboard.VisionStream') as camera, \
                patch('webbrowser.open', side_effect=OSError('No browser')) as browser:
            self.assertEqual(run_web_dashboard(load_config(), control=True, open_web=True), 0)
        browser.assert_called_once_with('http://127.0.0.1:8779/')
        camera.assert_not_called()
        engine.request.assert_called_once_with('ESTOP')
        engine.close.assert_called_once()
        server.serve_forever.assert_called_once()

    def test_agent_initialization_failure_still_closes_engine(self):
        engine = Mock(source='LIVE_CAMERA', control_lock=RLock())
        module = SimpleNamespace(UnifiedAgent=Mock(side_effect=ValueError('Agent unavailable')))
        with patch('python_controller.web_dashboard.DashboardEngine', return_value=engine), \
                patch.dict('sys.modules', {'python_controller.llm.unified_agent': module}), \
                patch('python_controller.web_dashboard.ThreadingHTTPServer') as server:
            with self.assertRaises(ValueError):
                run_web_dashboard(load_config(), control=True, agent_enabled=True)
        engine.request.assert_called_once_with('ESTOP')
        engine.close.assert_called_once()
        server.assert_not_called()

    def test_supervisor_start_failure_closes_engine_and_agent(self):
        engine = Mock(source='LIVE_CAMERA', control_lock=RLock())
        engine.start_control_loop.side_effect = RuntimeError('Supervisor unavailable')
        agent = Mock()
        module = SimpleNamespace(UnifiedAgent=Mock(return_value=agent))
        server = Mock(server_port=8766)
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        with patch('python_controller.web_dashboard.DashboardEngine', return_value=engine), \
                patch.dict('sys.modules', {'python_controller.llm.unified_agent': module}), \
                patch('python_controller.web_dashboard.ThreadingHTTPServer', return_value=server):
            with self.assertRaises(RuntimeError):
                run_web_dashboard(load_config(), control=True, agent_enabled=True)
        engine.request.assert_called_once_with('ESTOP')
        engine.close.assert_called_once()
        agent.close.assert_called_once()
        server.serve_forever.assert_not_called()

    def test_bind_failure_closes_existing_engine_without_starting_camera(self):
        engine = Mock(source='LIVE_CAMERA', control_lock=RLock())
        with patch('python_controller.web_dashboard.DashboardEngine', return_value=engine), \
                patch('python_controller.web_dashboard.ThreadingHTTPServer', side_effect=OSError('busy')), \
                patch('python_controller.web_dashboard.VisionStream') as camera:
            with self.assertRaises(OSError):
                run_web_dashboard(load_config(), control=True)
        engine.request.assert_called_once_with('ESTOP')
        engine.close.assert_called_once()
        camera.assert_not_called()


if __name__ == '__main__':
    unittest.main()
