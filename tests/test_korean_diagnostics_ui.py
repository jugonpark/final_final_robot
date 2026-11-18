import importlib.util
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')


@unittest.skipUnless(importlib.util.find_spec('PySide6'),'PySide6 not installed')
class KoreanDiagnosticUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def window(self):
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine,PreviewStream
        from python_controller.visualization.dashboard import Dashboard
        cfg=load_config();engine=DashboardEngine(cfg,'.tmp','SYNTHETIC_PREVIEW',logger=False)
        window=Dashboard(cfg,preview=True,engine=engine,stream=PreviewStream())
        window.timer.stop();window.poll()
        return window

    def test_korean_labels_and_choices_preserve_internal_codes(self):
        from python_controller.visualization.korean import ko
        from PySide6.QtWidgets import QMessageBox
        with patch('socket.socket',side_effect=AssertionError('Simulation opened sockets')):
            w=self.window()
            try:
                self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                    ['직접 제어','손동작 제어','사람 추종','자연어 제어'])
                self.assertEqual([w.workspaces.tabText(i) for i in range(w.workspaces.count())],
                    ['중앙제어','테스트·진단'])
                self.assertIn('긴급 정지',w.estop.text())
                self.assertIn('ESP32 주소',w.pipeline.text())
                self.assertIn('처리 지연',w.camera_status.text());self.assertNotIn('Processing',w.camera_status.text())
                self.assertEqual(w.test_result.itemData(0),'NOT TESTED')
                self.assertIn('미검증',w.test_result.itemText(0))
                self.assertEqual(w.direction_observations[0].itemData(1),'MATCHES EXPECTED')
                for value in ('Open_Palm','Closed_Fist','Thumb_Up','Thumb_Down','EMERGENCY_STOP','NO_TELEMETRY'):
                    self.assertRegex(ko(value),'[가-힣]');self.assertEqual(ko(ko(value)),ko(value))
                with patch.object(QMessageBox,'warning') as warning:
                    w.warning('IP not saved','Use the numeric unicast IPv4 address of the ESP32')
                    self.assertIn('IP 저장 실패',warning.call_args.args[1])
                    self.assertRegex(warning.call_args.args[2],'[가-힣]')
            finally:w.close()

    def test_report_export_keeps_simulation_and_rejects_stale_target_network(self):
        with patch('socket.socket',side_effect=AssertionError('Simulation opened sockets')):
            w=self.window()
            try:
                w.host.setText('127.0.0.1')
                w.network_probe.result={'target':'10.0.0.5','selected_local_ip':'10.0.0.2'}
                w.poll()
                self.assertIsNone(w.last_diagnostic_report['network'].get('target'))
                self.assertEqual(w.diagnostic_steps.item(1,1).text(),'미검증')
                w.hardware_checks[0].setChecked(True)
                with patch('python_controller.visualization.dashboard.save_report',return_value='diagnosis.json') as save:
                    w.export_diagnosis()
                    report=save.call_args.args[1]
                self.assertEqual(report['program_mode'],'SIMULATION')
                self.assertEqual(report['evidence_source'],'SYNTHETIC_PREVIEW')
                self.assertEqual(report['physical_motor_result'],'NOT TESTED')
                self.assertIn('OPERATOR_CHECKED',report['operator_hardware_checks'].values())
                self.assertFalse(any(b.isEnabled() for b in w.test_buttons.values()))
            finally:w.close()
