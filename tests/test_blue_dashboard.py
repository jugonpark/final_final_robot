"""Blue shell regression tests: navigation must preserve control boundaries."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
from python_controller.visualization.dashboard import Dashboard, CameraView


class BlueDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        guard = patch('socket.socket', side_effect=AssertionError('UI must not open UDP'))
        guard.start()
        self.addCleanup(guard.stop)
        cfg = load_config()
        self.engine = DashboardEngine(cfg, folder.name, 'SYNTHETIC_PREVIEW', logger=False)
        self.window = Dashboard(cfg, preview=True, engine=self.engine, stream=PreviewStream())
        self.window.timer.stop()
        self.addCleanup(self.window.close)
        self.window.show()
        self.window.poll()
        self.app.processEvents()

    def test_ai_default_sidebar_and_persistent_safety_at_all_sizes(self):
        w = self.window
        self.assertEqual(w.tabs.currentIndex(), w.language_tab_index)
        self.assertEqual(self.engine.active_control_mode, 'SIMULATION')
        self.assertIsNone(self.engine.client)
        self.assertEqual(len(w.nav_buttons), 5)
        for width, height in ((940, 680), (1320, 920), (1600, 980)):
            w.resize(width, height)
            for page, button in enumerate(w.nav_buttons):
                button.click()
                w.radar_status.setText('레이더 UNKNOWN · 이동 차단 · ESP32 정지 응답 미확인 · '
                    '연결 복구 후 유효한 CLEAR와 정지 응답을 확인하고 새 이동을 허용하세요. '*2)
                self.app.processEvents()
                self.assertTrue(button.isChecked())
                self.assertEqual((w.width(),w.height()),(width,height))
                for widget in (w.estop, w.radar_status, w.camera_badge):
                    self.assertTrue(widget.isVisible())
                    bounds = widget.rect()
                    bounds.moveTopLeft(widget.mapTo(w, bounds.topLeft()))
                    self.assertTrue(w.rect().contains(bounds), (page, width, bounds))
                self.assertLessEqual(w.minimumSizeHint().width(), width)

    def test_navigation_ends_hold_and_settings_do_not_acquire_control(self):
        w = self.window
        for page in (3, 4):
            with patch.object(w, 'end_motor_hold') as stop, patch.object(self.engine, 'select_control_mode') as select:
                w.nav_buttons[page].click()
                stop.assert_called_once()
                select.assert_not_called()
        with patch.object(w, 'end_motor_hold') as stop:
            self.app.sendEvent(w, QEvent(QEvent.WindowDeactivate))
            stop.assert_called_once()

    def test_camera_segments_share_camera_and_show_distinct_configured_guides(self):
        w = self.window
        w.nav_buttons[1].click()
        self.assertEqual(w.tabs.currentIndex(), w.follow_tab_index)
        self.assertIn('전진', w.gesture_guide.text())
        w.hand_mapping['Open_Palm'].setCurrentIndex(w.hand_mapping['Open_Palm'].findData('RIGHT'))
        w.camera_mode_buttons[1].click()
        self.assertEqual(w.tabs.currentIndex(), w.people_tab_index)
        self.assertIn('재개', w.gesture_guide.text())
        self.assertNotIn('전진', w.gesture_guide.text())
        w.camera_mode_buttons[0].click()
        self.assertEqual(self.engine.hand.mapping['Open_Palm'], 'FORWARD')
        self.assertIn('전진', w.gesture_guide.text())
        self.assertIn('미적용', w.gesture_guide.text())
        w.hand_apply.click()
        w.sync_navigation()
        self.assertIn('우측', w.gesture_guide.text())
        self.assertNotIn('미적용', w.gesture_guide.text())
        with patch.object(self.engine, 'configure_hand') as configure:
            w.hand_enable.setEnabled(False)
            w.hand_apply.click()
            configure.assert_not_called()
        self.assertEqual(w.findChildren(CameraView), [w.camera])
        self.assertIs(w.camera_column.parent(), w.hand_split)

    def test_rejected_mode_does_not_present_requested_owner_as_active(self):
        w = self.window
        with patch.object(self.engine, 'select_control_mode', side_effect=ValueError('STOP_NOT_CONFIRMED')):
            w.nav_buttons[2].click()
        self.assertEqual(w.tabs.currentIndex(), w.motor_tab_index)
        self.assertEqual(self.engine.active_control_mode, 'SIMULATION')
        self.assertIn('STOP_NOT_CONFIRMED', w.statusBar().currentMessage())
        self.assertIn('시뮬레이션', w.mode.text())

    def test_estop_keys_from_each_page_and_svg_resources(self):
        w = self.window
        for button in w.nav_buttons:
            self.assertFalse(button.icon().isNull())
            button.click()
            w.setFocus()
            for key in (Qt.Key_E, Qt.Key_Space):
                with patch.object(w, 'action') as action:
                    QTest.keyClick(w, key)
                    action.assert_called_once_with('ESTOP')
        self.assertEqual(w.estop.property('variant'), 'danger')

    def test_rotation_cards_replan_and_input_edit_preserve_execute_gate(self):
        from PySide6.QtWidgets import QLabel, QFrame
        from python_controller.planning.validator import ValidatedPlan, parse_operator_command
        p = self.window.language_panel
        for command in ('앞으로 5cm 가', '좌로 한바퀴 회전해', '뒤로 10cm 가'):
            p.command.setText(command)
            p.control.plan = ValidatedPlan(parse_operator_command(command, allow_rotation_preview=True))
            p.control.phase = 'READY'
            p.refresh()
            self.assertEqual(len(p.plan_steps.findChildren(QFrame, 'planStep')), len(p.control.plan.actions))
            self.app.processEvents()
            self.assertFalse(p.execute_button.isEnabled())
            if p.control.plan.requires_angle_control:
                self.assertIn('360', p.preview.toPlainText())
                self.assertTrue(any('미검증' in label.text() for label in p.plan_steps.findChildren(QLabel)))
        p.command.setText('앞으로 15cm 가')
        self.assertIsNone(p.control.plan)
        self.assertFalse(p.prepared.isChecked())
        self.assertEqual(p.preview.toPlainText(), '')
        self.assertEqual(p.plan_steps.findChildren(QFrame, 'planStep'), [])

    def test_radar_and_fresh_stop_are_both_required_for_execution(self):
        from python_controller.planning.validator import ValidatedPlan, parse_operator_command
        w = self.window
        w.host.setText('127.0.0.1')
        p = w.language_panel
        self.engine.source = 'LIVE_CAMERA'
        status = dict(connected=True, ready=True, stop_verified=True, recovery_state='NONE')
        session = SimpleNamespace(central_session=True, active=False, snapshot=lambda:status)
        self.engine.client = session
        self.engine.client_owner = 'LLM_COMMAND'
        try:
            p.control.plan = ValidatedPlan(parse_operator_command('앞으로 5cm 가'))
            p.control.phase = 'READY'
            for state, blocked, stopped in (('UNKNOWN',True,True),('EVADE',True,True),('CLEAR',True,True),('CLEAR',False,False),('CLEAR',False,True)):
                status['radar'] = dict(state=state, blocked=blocked, reason='RADAR_'+state, age_s=0.)
                status['stop_verified'] = stopped
                p.refresh()
                self.assertEqual(p.execute_button.isEnabled(), state=='CLEAR' and not blocked and stopped)
            p.control.plan = ValidatedPlan(parse_operator_command('좌로 한바퀴 회전해', allow_rotation_preview=True))
            p.refresh()
            self.assertFalse(p.execute_button.isEnabled())
        finally:
            self.engine.client = None
            self.engine.client_owner = None


if __name__ == '__main__':
    unittest.main()
