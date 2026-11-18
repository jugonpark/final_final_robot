import contextlib
import io
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import time


class RuntimeTests(unittest.TestCase):
    def test_camera_loop_selection_arm_resume_and_estop_without_udp(self):
        from python_controller.main import main
        from python_controller.models import PersonObservation
        with patch.dict('sys.modules',{'cv2':SimpleNamespace(waitKey=lambda n:next(keys))}), \
             patch('python_controller.vision.camera.Camera') as camera, \
             patch('python_controller.vision.person_detector.PersonDetector') as detector, \
             patch('python_controller.vision.gesture_recognizer.GestureRecognizer') as recognizer, \
             patch('python_controller.visualization.debug_view.DebugView') as view, \
             patch('socket.socket',side_effect=AssertionError('SIMULATION cannot open sockets')):
            keys=iter([13,ord('a'),ord('r'),ord('e'),ord('q')])
            camera.return_value.read.side_effect=lambda:(SimpleNamespace(shape=(480,640,3)),time.monotonic())
            detector.return_value.detect.side_effect=lambda image,t:(PersonObservation(1,(240,100,400,300),.9,t),)
            recognizer.return_value.recognize.return_value=(None,[])
            view.return_value.drain_events.return_value=[]
            view.return_value.closed.return_value=False
            self.assertEqual(main([]),0)
            decisions=[call.args[3] for call in view.return_value.draw.call_args_list]
            self.assertTrue(any(d.state=='FOLLOWING' and d.command.vx_cm_s>0 for d in decisions))
            self.assertEqual(decisions[-1].reason,'EMERGENCY_STOP')
            camera.return_value.close.assert_called_once()
            recognizer.return_value.close.assert_called_once()

    def test_demo_never_opens_sockets_and_prints_latched_stop(self):
        from python_controller.main import main
        output = io.StringIO()
        with patch('socket.socket',side_effect=AssertionError('SIMULATION must not open UDP')), contextlib.redirect_stdout(output):
            self.assertEqual(main(['--demo']),0)
        text = output.getvalue()
        self.assertIn('FOLLOWING',text)
        self.assertIn('TARGET_LOST',text)
        self.assertIn('OBSTACLE_STOP',text)
        self.assertIn('SIMULATION',text)

    def test_hardware_demo_is_rejected_before_socket_creation(self):
        from python_controller.main import main
        with patch('socket.socket',side_effect=AssertionError('No sockets allowed')), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['--demo','--mode','HARDWARE'])
