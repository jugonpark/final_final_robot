import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'PySide6 not installed')
class RecoveryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
        from python_controller.visualization.dashboard import Dashboard
        self.no_socket = patch('socket.socket', side_effect=AssertionError('No real sockets'))
        self.no_socket.start()
        self.addCleanup(self.no_socket.stop)
        cfg = load_config()
        self.engine = DashboardEngine(cfg, '.tmp', 'SYNTHETIC_PREVIEW', logger=False)
        self.window = Dashboard(cfg, preview=True, engine=self.engine, stream=PreviewStream())
        self.window.timer.stop()
        self.window.poll()
        self.addCleanup(self.window.close)

    def fake_session(self, **updates):
        self.transport = dict(connected=True, ready=True, stop_verified=True, telemetry={'fault': 'NONE'},
                              recovery_state='NONE', recovery_generation=1, supported_test_profile=True,
                              validation_detail='OK', session_lost=False, reason='MODE_READY')
        self.transport.update(updates)
        session = SimpleNamespace(central_session=True, active=False, snapshot=lambda: self.transport)
        self.engine.client = session
        self.engine.client_owner = 'DIRECT_CONTROL'
        self.addCleanup(setattr, self.engine, 'client', None)
        return session

    def test_pi_address_save_reloads_and_preserves_esp32_address(self):
        from python_controller.config import load_config
        from python_controller.control.safety_supervisor import SafetySupervisor

        with tempfile.TemporaryDirectory() as folder:
            self.window.radar_file = Path(folder)/'radar.json'
            esp32 = self.window.host.text()
            self.window.radar_host.setText(' 192.0.2.106 ')
            self.window.save_radar_button.click()
            cfg = load_config(radar_path=self.window.radar_file)
            self.assertEqual(cfg['radar']['source_ip'], '192.0.2.106')
            self.assertEqual(self.window.host.text(), esp32)
            self.assertTrue(SafetySupervisor(cfg['radar']['source_ip']).snapshot(1.)['blocked'])
            self.fake_session()
            with self.assertRaises(ValueError):
                self.engine.save_radar_source(self.window.radar_file, '192.0.2.107')
            self.assertEqual(load_config(radar_path=self.window.radar_file)['radar']['source_ip'], '192.0.2.106')

    def test_fault_stop_proof_and_movement_readiness_are_independent(self):
        self.fake_session(ready=False, connected=False, validation_detail='ENCODER_JUMP',
                          telemetry={'fault': 'ENCODER_JUMP'}, recovery_state='RECOVERED_WAITING_NEW_INPUT')
        td = dict(fault='ENCODER_JUMP', encoder_policy='STRICT', fault_motor_mask=2,
                  fault_delta_count=[0, 65, 0], fault_delta_limit=30, fault_pwm_before=[0, 40, 0])
        self.window.update_recovery(self.transport, td)
        values = self.window.recovery_fields.values
        self.assertIn('출력 정지 확인됨', values['출력 정지 확인'].text())
        self.assertIn('이동 차단', values['구동 준비'].text())
        self.assertIn('ENCODER_JUMP', values['장치 Fault (telemetry.fault)'].text())
        self.assertIn('ENCODER_JUMP', values['수신 검증 상세 (validation_detail)'].text())
        self.assertEqual(values['fault_motor_mask'].text(), '2')
        self.assertIn('65', values['fault_delta_count'].text())

    def test_clear_radar_banner_shows_remaining_board_profile_gate(self):
        data = self.window.last_data.copy()
        data['transport'] = dict(data['transport'], connected=True, ready=False, stop_verified=True,
                                 fault='NONE', telemetry={'fault': 'NONE'},
                                 hardware_profile_status='HARDWARE_PROFILE_UNSELECTED',
                                 radar=dict(state='CLEAR', blocked=False, reason='RADAR_CLEAR', age_s=.016))
        self.window.update_display(data, None)
        banner = self.window.radar_status.text()
        self.assertIn('레이더 CLEAR · 이동 차단 · 보드 프로필 미선택', banner)
        self.assertIn('ESP32 정지 확인', banner)
        self.assertFalse(self.window.radar_rearm.isEnabled())

    def test_session_conflict_can_request_stop_resync_even_if_mode_change_is_pending(self):
        self.fake_session(ready=False, connected=False, session_lost=True, recovery_state='SESSION_CONFLICT')
        self.engine.pending_mode = 'LLM_COMMAND'
        self.window.update_recovery(self.transport, {'fault': 'NONE'})
        self.assertTrue(self.window.recover_button.isEnabled())
        self.assertFalse(self.window.encoder_policy_apply.isEnabled())

    def test_legacy_cannot_claim_test_profile_or_applied_timeout(self):
        self.fake_session(supported_test_profile=False, protocol_version=1, heartbeat_timeout_ms=600)
        self.window.update_recovery(self.transport, {'fault': 'NONE'})
        self.assertFalse(self.window.encoder_policy.isEnabled())
        self.assertFalse(self.window.encoder_policy_apply.isEnabled())
        values = self.window.recovery_fields.values
        for name in ('ESP32 적용 정책', 'ESP32 적용 heartbeat 만료 (ms)', 'fault_dt_us'):
            self.assertIn('미검증', values[name].text())
        self.assertNotIn('600', values['ESP32 적용 heartbeat 만료 (ms)'].text())

    def test_policy_requests_are_confirmed_only_by_device_telemetry(self):
        session = self.fake_session()
        self.engine.set_encoder_policy = Mock()
        self.engine.recover_robot = Mock()
        self.window.update_recovery(self.transport, {'fault': 'NONE', 'encoder_policy': 'STRICT'})
        self.window.encoder_policy.setCurrentIndex(self.window.encoder_policy.findData('FILTERED_TEST'))
        self.window.encoder_policy_apply.click()
        self.engine.set_encoder_policy.assert_called_once_with('FILTERED_TEST')
        self.assertIn('STRICT', self.window.recovery_fields.values['ESP32 적용 정책'].text())
        self.assertIn('응답을 확인', self.window.recovery_notice.text())
        self.window.recover_button.click()
        self.engine.recover_robot.assert_called_once_with()
        session.active = True
        self.window.update_recovery(self.transport, {})
        self.assertFalse(self.window.encoder_policy_apply.isEnabled())
        session.active = False
        self.engine.release_future = object()
        self.window.update_recovery(self.transport, {})
        self.assertFalse(self.window.encoder_policy_apply.isEnabled())
        self.engine.release_future = None
        with patch.object(type(self.engine.language), 'busy', new_callable=PropertyMock, return_value=True):
            self.window.update_recovery(self.transport, {})
            self.assertFalse(self.window.encoder_policy_apply.isEnabled())

    def test_invalid_session_cannot_present_neutral_hand_as_only_gate(self):
        data = self.window.last_data.copy()
        data['control_source'] = 'GESTURE_DIRECT'
        data['transport'] = dict(data['transport'], stop_verified=True, session_lost=True,
                                 recovery_state='SYNC_STOP', validation_detail='ESP32_REBOOT')
        data['hand_control'] = dict(reason='WAITING_FOR_NEUTRAL_HAND', label=None,
                                    confidence=0, elapsed=0, frames=0, direction='STOP')
        self.window.update_display(data, None)
        self.assertIn('SYNC_STOP', self.window.hand_summary.text())
        self.assertNotIn('손을 내리거나', self.window.hand_summary.text())

    def test_follow_and_language_refuse_stopped_but_lost_session(self):
        self.fake_session(session_lost=True, connected=False, ready=True, recovery_state='SYNC_STOP')
        self.engine.source = 'LIVE_CAMERA'
        self.engine.client_owner = 'GESTURE_FOLLOW'
        self.window.follow_panel.refresh(self.transport)
        self.assertFalse(self.window.follow_panel.connect_button.isEnabled())
        self.assertIn('출력 정지 확인', self.window.follow_panel.connection_status.text())
        self.assertIn('구동 준비 차단', self.window.follow_panel.connection_status.text())
        self.engine.client_owner = 'LLM_COMMAND'
        self.engine.language.phase = 'READY'
        self.window.language_panel.refresh()
        self.assertFalse(self.window.language_panel.execute_button.isEnabled())
        self.assertIn('SYNC_STOP', self.window.language_panel.gate.text())
        self.engine.language.phase = 'IDLE'

    def test_background_ui_poll_only_renders_supervisor_snapshot(self):
        self.window.background_control = True
        data = self.window.last_data
        with patch.object(self.engine, 'operator_alive', create=True) as alive, \
                patch.object(self.engine, 'render_state', create=True, return_value=(data, None)), \
                patch.object(self.engine, 'tick', side_effect=AssertionError('UI must not supervise')) as tick, \
                patch.object(self.window, 'update_display') as display:
            self.window.poll()
            alive.assert_called_once_with()
            tick.assert_not_called()
            self.assertIs(display.call_args.args[0], data)

    def test_supervisor_failure_is_displayed_and_latches_stop_without_ui_fallback(self):
        self.window.background_control = True
        self.engine.control_error = 'CONTROL_SUPERVISOR_FAILED: RuntimeError'
        with patch.object(self.engine,'tick',side_effect=AssertionError('No fallback supervision')):
            self.window.poll()
        self.assertIn('CONTROL_SUPERVISOR_FAILED',self.window.pipeline.text())
        self.assertTrue(self.engine.c.estop_latched)

    def test_new_recovery_generation_discards_ui_pending_input(self):
        data = self.window.last_data.copy()
        data['transport'] = dict(data['transport'], recovery_generation=1)
        self.window.update_display(data, None)
        self.window.held_direction = 'FORWARD'
        self.window.pending_direct = (object(), 999999999)
        data['transport']['recovery_generation'] = 2
        self.window.update_display(data, None)
        self.assertIsNone(self.window.held_direction)
        self.assertIsNone(self.window.pending_direct)
