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

    def test_inference_delay_keeps_preview_stable_without_stale_target_selection(self):
        from time import monotonic
        from dataclasses import replace
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
        from python_controller.visualization.dashboard import Dashboard

        config = load_config()
        timeout = config['safety']['perception_timeout_s']
        engine = DashboardEngine(config, '.tmp', logger=False)
        stream = PreviewStream()
        with patch('socket.socket', side_effect=AssertionError('Simulation must not open sockets')):
            window = Dashboard(config, engine=engine, stream=stream)
            window.timer.stop()
            self.addCleanup(window.close)
            window.navigate_camera(1)
            window.show()
            now = monotonic()
            result = stream.latest()
            result.update(frame=replace(result['frame'], timestamp=now), completed_at=now)

            def render(result, at, *, error=None, camera_status=None):
                capture = (result['image'], at, 0)
                data = engine.tick(result, now=at, pipeline_error=error, pipeline_health={'camera':camera_status})
                with patch('python_controller.visualization.dashboard.time.monotonic', return_value=at):
                    window.update_display(data, result, capture)
                self.app.processEvents()
                return data

            render(result, now+.01)
            badge = window.camera_badge.text()
            size = window.camera.size()
            self.assertTrue(window.camera.people)
            displayed = window.camera.image.cacheKey()
            stale = render(result, now+timeout+.05)
            self.assertFalse(stale['perception_fresh'])
            self.assertEqual(window.camera.image.cacheKey(), displayed)
            self.assertIn('지연', window.camera_status.text())
            self.assertEqual(window.camera.size(), size)
            self.assertEqual(window.camera_badge.text(), badge)
            # A newly completed inference can also arrive already too old to use.
            late = dict(result, frame=replace(result['frame'], timestamp=now+.01),
                        completed_at=now+timeout+.07)
            render(late, now+timeout+.08)
            self.assertEqual(window.camera.image.cacheKey(), displayed)
            with patch.object(engine, 'request') as request:
                QTest.mouseClick(window.camera, Qt.LeftButton, pos=window.camera.rect().center())
                request.assert_not_called()

            at = now+timeout+.12
            updated = dict(result, frame=replace(result['frame'], timestamp=at), completed_at=at)
            render(updated, at+.01)
            self.assertTrue(window.camera.people)
            self.assertNotEqual(window.camera.image.cacheKey(), displayed)
            self.assertEqual(window.camera.size(), size)

            stream.error='VISION: failed'
            render(updated, at+timeout+.05, error=stream.error)
            self.assertIn('원본 영상 수신 중', window.camera_status.text())
            self.assertFalse(window.camera.people)
            render(updated, at+2, error='CAMERA: failed', camera_status='CAMERA_ERROR')
            self.assertIsNone(window.camera.image)
            self.assertIn('오류', window.camera_badge.text())
