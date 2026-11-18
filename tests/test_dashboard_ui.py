import importlib.util
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
QT_AVAILABLE=importlib.util.find_spec('PySide6') is not None


@unittest.skipUnless(QT_AVAILABLE,'PySide6 not installed')
class DashboardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_live_capture_preview_during_model_warmup_never_counts_as_perception(self):
        from time import monotonic
        import numpy as np
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
        from python_controller.visualization.dashboard import Dashboard

        class WarmingStream(PreviewStream):
            status = 'LOADING_YOLO'
            camera_status = 'CAPTURING'

            def latest(self):
                return None

            def latest_capture(self):
                return self.capture

        config = load_config()
        engine = DashboardEngine(config,'.tmp','SYNTHETIC_PREVIEW',logger=False)
        stream = WarmingStream()
        stream.capture = (np.zeros((480,640,3),dtype=np.uint8),monotonic(),12)
        with patch('socket.socket',side_effect=AssertionError('Preview must not open sockets')):
            window = Dashboard(config,preview=True,engine=engine,stream=stream)
            window.timer.stop()
            try:
                stream.capture = (stream.capture[0],monotonic(),12)
                window.poll()
                self.assertIsNotNone(window.camera.image)
                self.assertIn('원본 영상 수신 중',window.camera_status.text())
                self.assertFalse(window.last_data['perception_fresh'])
                self.assertEqual(window.camera.people,())
                stream.capture = (stream.capture[0],monotonic()-5,12)
                window.poll()
                self.assertIsNone(window.camera.image)
            finally:
                window.close()

    def test_preview_ui_buttons_stop_and_small_window_render(self):
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine,PreviewStream
        from python_controller.visualization.dashboard import Dashboard
        config=load_config()
        engine=DashboardEngine(config,'.tmp','SYNTHETIC_PREVIEW',logger=False)
        with patch('socket.socket',side_effect=AssertionError('Preview must not open sockets')):
            window=Dashboard(config,preview=True,engine=engine,stream=PreviewStream())
            window.timer.stop();window.resize(960,700);window.show();self.app.processEvents()
            try:
                window.tabs.setCurrentIndex(window.people_tab_index)
                window.poll()
                window.follow_panel.connect_button.click();window.poll()
                self.assertEqual(window.last_data['state'],'FOLLOWING')
                QTest.mouseClick(window.estop,Qt.LeftButton);window.poll()
                self.assertEqual(window.last_data['stop_reason'],'EMERGENCY_STOP')
                self.assertFalse(window.last_data['armed'])
                self.assertIn('SYNTHETIC',window.mode.text())
                window.workspaces.setCurrentIndex(window.diagnostic_tab_index)
                window.diagnosis_pages.setCurrentIndex(3);window.test_notes.setFocus();self.app.processEvents()
                QTest.keyClicks(window.test_notes,'camera pass')
                self.assertEqual(window.test_notes.text(),'camera pass')
                self.assertTrue(engine.c.estop_latched)
                for index in range(4):
                    window.tabs.setCurrentIndex(index);window.poll();self.app.processEvents()
                    self.assertFalse(window.grab().isNull())
                self.assertTrue(all(r['result']=='NOT TESTED' for r in engine.book.rows))
            finally:window.close()
