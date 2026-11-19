import time
import unittest
from threading import Event
from unittest.mock import patch

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine,LatestSlot,VisionStream
from python_controller.models import PerceptionFrame,PersonObservation
from python_controller.vision.gesture_recognizer import gesture_diagnostics


def result(now):
    return dict(frame=PerceptionFrame(now,640,480,(PersonObservation(1,(240,100,400,300),.9,now),)),
                vision=gesture_diagnostics([],1,1,now,0),fps=20,latency_ms=50,completed_at=now+.05)


class DashboardRuntimeTests(unittest.TestCase):
    def engine(self,source='LIVE_CAMERA'):
        return DashboardEngine(load_config(),'.tmp',source,logger=False)

    def test_latest_slot_never_queues_old_frames(self):
        slot=LatestSlot()
        for i in range(1000):slot.put(i)
        self.assertEqual(slot.get(),(1000,999))

    def test_yolo_startup_resamples_latest_camera_frame(self):
        from types import SimpleNamespace

        stream = VisionStream(load_config())
        image = SimpleNamespace(shape=(480, 640, 3))
        stream.capture.put((image, time.monotonic()-2, 10))
        newest = []

        def construct_detector(config):
            newest.append(time.monotonic())
            stream.capture.put((image, newest[0], 10))
            def detect(frame, timestamp):
                self.assertEqual(timestamp, newest[0])
                stream.done.set()
                return ()
            return SimpleNamespace(detect=detect)

        recognizer = SimpleNamespace(recognize=lambda *args: (None, []),
                                     diagnostics={}, close=lambda: None)
        with patch('python_controller.vision.person_detector.PersonDetector', side_effect=construct_detector), \
             patch('python_controller.vision.gesture_recognizer.GestureRecognizer', return_value=recognizer):
            stream._vision()
        self.assertIsNone(stream.error)
        self.assertEqual(stream.latest()['frame'].timestamp, newest[0])
        self.assertEqual(stream.latest()['metrics']['skipped_capture_frames'], 1)

    def test_stopping_live_stream_releases_camera_in_capture_worker(self):
        from types import SimpleNamespace

        stream = VisionStream(load_config())
        reading, released = Event(), Event()

        def capture():
            reading.set()
            stream.done.wait(2)
            return object(), time.monotonic()

        camera = SimpleNamespace(read=capture, close=released.set)
        with patch('python_controller.vision.camera.Camera', return_value=camera), patch.object(stream, '_vision'):
            stream.start()
            try:
                self.assertTrue(reading.wait(2))
                stream.stop()
                self.assertTrue(released.wait(2))
            finally:
                stream.close()
        self.assertTrue(stream.stopped)

    def test_simulation_cannot_open_sockets_and_stale_frame_stops(self):
        e=self.engine()
        with patch('socket.socket',side_effect=AssertionError('No socket in SIMULATION')):
            e.tick(result(1),1.05);e.request('CONFIRM');e.tick(result(1.1),1.15)
            e.request('ARM');e.tick(result(1.2),1.25)
            e.request('RESUME');d=e.tick(result(1.3),1.35)
            self.assertTrue(d['following'])
            self.assertIsNone(d['transport']['last_tx'])
            self.assertIsNone(d['motors']['measured_rpm'])
            d=e.tick(None,2)
            self.assertEqual(d['safe']['vx_cm_s'],0)
            self.assertFalse(d['perception_fresh'])
            self.assertEqual(d['state'],'FAULT')

    def test_hardware_transition_gates_before_client_creation(self):
        e=self.engine()
        with patch('socket.socket',side_effect=AssertionError('Must refuse before socket')):
            with self.assertRaises(ValueError):e.connect_hardware('127.0.0.1',False)
            with self.assertRaises(ValueError):e.connect_hardware('127.0.0.1',True)
        e=self.engine('SYNTHETIC_PREVIEW');e.cfg['communication']['signs_verified']=True
        with self.assertRaises(ValueError):e.connect_hardware('127.0.0.1',True)

    def test_authorized_mode_change_still_does_not_arm(self):
        class FakeClient:
            def __init__(self,cfg):self.started=False
            def start(self):self.started=True
            def close(self):pass
        e=self.engine();e.cfg['communication']['signs_verified']=True;e.client_factory=FakeClient
        e.connect_hardware('127.0.0.1',True)
        self.assertEqual(e.mode,'HARDWARE')
        self.assertTrue(e.client.started)
        self.assertFalse(e.c.armed)
        self.assertEqual(e.c.state,'PAUSED')
        e.disconnect();self.assertEqual(e.mode,'SIMULATION')

    def test_estop_event_cannot_be_overwritten_by_queued_resume(self):
        e=self.engine();e.request('ESTOP');e.request('RESUME')
        self.assertEqual(e.tick(result(1),1.05)['stop_reason'],'EMERGENCY_STOP')
        self.assertEqual(e.tick(result(1.1),1.15)['stop_reason'],'EMERGENCY_STOP')
