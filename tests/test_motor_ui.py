import importlib.util
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')


@unittest.skipUnless(importlib.util.find_spec('PySide6'),'PySide6 not installed')
class MotorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_one_activation_hold_release_and_never_arm_from_follow(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from python_controller.config import load_config
        from python_controller.dashboard_runtime import DashboardEngine
        from python_controller.visualization.dashboard import Dashboard
        from python_controller.communication.robot_session import RobotSession
        from test_motor_test import Socket
        from test_protocol import telemetry
        class Stream:
            error=None;status='MOCK_INPUT';target_id=None
            def start(self):pass
            def latest(self):return None
            def close(self):pass
        now=[time.monotonic()]
        def factory(config):
            client=RobotSession(config,clock=lambda:now[0],evidence_source='MOCK_UDP')
            client.tx,client.rx=Socket(),Socket();client.start=lambda:None
            return client
        config=load_config();engine=DashboardEngine(config,'.tmp',client_factory=factory,logger=False)
        with patch('socket.socket',side_effect=AssertionError('Only fake sockets may be used')):
            window=Dashboard(config,stream=Stream(),engine=engine);window.timer.stop();window.show();self.app.processEvents()
            try:
                window.poll();self.assertTrue(all(not b.isEnabled() for b in window.test_buttons.values()))
                engine.connect_robot('127.0.0.1')
                client=engine.client;client.tick();now[0]+=.1
                client.rx.incoming.append((telemetry(session=client.protocol.session_id,seq=client.protocol.seq,encoder_count=[0,0,0],wheel_target=[0,0,0]),('127.0.0.1',8888)))
                client.tick();window.poll()
                QTest.keyClick(window,Qt.Key_A);QTest.keyClick(window,Qt.Key_R);window.poll()
                self.assertFalse(client.armed);self.assertFalse(engine.c.armed)
                self.assertTrue(all(p['type']!='cmd_vel' for p in client.tx.sent))
                self.assertTrue(window.test_buttons['FORWARD'].isEnabled())
                QTest.mousePress(window.test_buttons['FORWARD'],Qt.LeftButton);client.tick();window.poll()
                self.assertEqual(client.tx.sent[-1]['type'],'cmd_vel')
                self.assertTrue(window.test_buttons['FORWARD'].isEnabled())
                self.assertFalse(window.last_data['following'])
                self.assertIn('MOCK_UDP',window.bench_state.values['Source'].text())
                QTest.mouseRelease(window.test_buttons['FORWARD'],Qt.LeftButton);client.tick();window.poll()
                self.assertEqual(client.tx.sent[-1]['type'],'stop')
                self.assertFalse(window.wheel_buttons['M2'].isEnabled())
                now[0]+=.1
                client.rx.incoming.append((telemetry(session=client.protocol.session_id,seq=client.protocol.seq,uptime=1200,
                    encoder_count=[0,0,0],wheel_target=[0,0,0]),('127.0.0.1',8888)))
                client.tick();window.poll()
                window.workspaces.setCurrentIndex(1);window.diagnosis_pages.setCurrentIndex(4);client.tick()
                now[0]+=.1
                client.rx.incoming.append((telemetry(session=client.protocol.session_id,seq=client.protocol.seq,uptime=1300,
                    encoder_count=[0,0,0],wheel_target=[0,0,0]),('127.0.0.1',8888)))
                client.tick();window.poll()
                self.assertTrue(window.wheel_buttons['M2'].isEnabled())
                QTest.mousePress(window.wheel_buttons['M2'],Qt.LeftButton);client.tick();window.poll()
                self.assertEqual(client.tx.sent[-1]['type'],'cmd_vel')
                window.workspaces.setCurrentIndex(0);window.tabs.setCurrentIndex(window.follow_tab_index);client.tick()
                self.assertEqual(client.tx.sent[-1]['type'],'stop')
                QTest.mouseClick(window.estop,Qt.LeftButton);client.tick();window.poll()
                self.assertEqual(client.tx.sent[-1]['type'],'stop')
                self.assertTrue(all(not b.isEnabled() for b in window.test_buttons.values()))
            finally:window.close()
