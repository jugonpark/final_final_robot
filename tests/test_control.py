import math
import unittest

from python_controller.config import load_config
from python_controller.models import PersonObservation, GestureObservation, PerceptionFrame, RadarObservation
from python_controller.control.state_machine import Controller
from python_controller.control.follow_controller import FollowController


def frame(t=1., people=None, gesture=None, ok=True):
    return PerceptionFrame(t, 640, 480, tuple(people if people is not None else [PersonObservation(1, (240, 100, 400, 300), .9, t)]), gesture, ok)


class ControlTests(unittest.TestCase):
    def test_turn_direction_acceleration_and_deadband(self):
        cfg=load_config()['follow']
        f=FollowController(cfg)
        right=PersonObservation(1,(400,100,560,300),.9,1)
        command,error,size=f.compute(right,640,480,1)
        self.assertGreater(error,0)
        self.assertLess(command.w_rad_s,0)  # Proposed sign, not a physical calibration claim.
        self.assertLessEqual(abs(command.w_rad_s),cfg['angular_accel_rad_s2']*.05)
        left=PersonObservation(1,(80,100,240,300),.9,2)
        f.reset()
        self.assertGreater(f.compute(left,640,480,2)[0].w_rad_s,0)

    def test_pause_has_priority_over_simultaneous_resume(self):
        self.start()
        for i in range(8):
            t=1.1+i*.1
            d=self.c.step(frame(t,gesture=GestureObservation('Closed_Fist',.99,1,t)),t,'RESUME')
        self.assertEqual(d.state,'PAUSED')

    def test_low_confidence_and_single_frame_do_not_pause(self):
        self.start()
        for i in range(9):
            t=1.1+i*.1
            d=self.c.step(frame(t,gesture=GestureObservation('Closed_Fist',.2,1,t)),t)
            self.assertEqual(d.state,'FOLLOWING')
        self.assertEqual(self.c.step(frame(2.1,gesture=GestureObservation('Closed_Fist',.99,1,2.1)),2.1).state,'FOLLOWING')

    def test_thumb_changes_setpoint_once_per_held_gesture(self):
        self.start()
        initial=self.c.follow.desired
        for i in range(20):
            t=1.1+i*.1
            self.c.step(frame(t,gesture=GestureObservation('Thumb_Up',.99,1,t)),t)
        self.assertAlmostEqual(self.c.follow.desired,initial+.08)

    def test_invalid_radar_and_future_frames_stop(self):
        self.start()
        d=self.c.step(frame(1.1),1.1,radar=RadarObservation(None,None,1.1,False))
        self.assertEqual(d.state,'OBSTACLE_STOP')
        self.assertEqual(self.c.step(frame(5),2).state,'FAULT')

    def test_overlapping_people_stop_even_with_same_tracking_id(self):
        self.start()
        people=list(frame(1.1).people)+[PersonObservation(2,(250,110,420,320),.9,1.1)]
        self.assertEqual(self.c.step(frame(1.1,people),1.1).state,'TARGET_LOST')

    def setUp(self):
        self.c = Controller(load_config())
        self.c.select_target(1)
        self.c.confirm_target()

    def start(self, t=1.):
        self.c.step(frame(t), t, 'ARM')
        return self.c.step(frame(t + .05), t + .05, 'RESUME')

    def test_no_motion_before_arm_and_resume(self):
        self.assertEqual(self.c.step(frame(), 1, 'RESUME').command.vx_cm_s, 0)
        self.assertEqual(self.c.step(frame(), 1, 'ARM').command.vx_cm_s, 0)
        self.assertGreater(self.c.step(frame(1.05), 1.05, 'RESUME').command.vx_cm_s, 0)

    def test_loss_latches_and_never_switches_person(self):
        self.start()
        d = self.c.step(frame(1.1, [PersonObservation(2, (240,100,400,300), .9, 1.1)]), 1.1)
        self.assertEqual(d.state, 'TARGET_LOST')
        self.assertEqual(d.target_id, 1)
        self.assertEqual(self.c.step(frame(1.2), 1.2).command.vx_cm_s, 0)
        self.assertGreater(self.c.step(frame(1.25), 1.25, 'RESUME').command.vx_cm_s, 0)

    def test_stale_camera_communication_fault_and_estop_stop(self):
        for kwargs in [dict(now=2.), dict(now=1.1, communication_ok=False), dict(now=1.1, esp32_fault='MOTOR_STALL')]:
            self.setUp(); self.start()
            d = self.c.step(frame(1.1), **kwargs)
            self.assertEqual(d.command.vx_cm_s, 0)
            self.assertNotEqual(d.state, 'FOLLOWING')
        self.setUp(); self.start()
        self.assertEqual(self.c.step(frame(1.1), 1.1, 'ESTOP').state, 'PAUSED')
        self.assertEqual(self.c.step(frame(1.2), 1.2, 'RESUME').command.vx_cm_s, 0)

    def test_obstacle_requires_explicit_resume(self):
        self.start()
        radar = RadarObservation(True, .4, 1.1, True)
        self.assertEqual(self.c.step(frame(1.1), 1.1, radar=radar).state, 'OBSTACLE_STOP')
        self.assertEqual(self.c.step(frame(1.2), 1.2).command.vx_cm_s, 0)

    def test_held_resume_gesture_across_obstacle_cannot_restart(self):
        self.start()
        self.c.step(frame(1.1),1.1,radar=RadarObservation(True,.3,1.1,True))
        for i in range(15):
            t=1.2+i*.1
            d=self.c.step(frame(t,gesture=GestureObservation('Open_Palm',.99,1,t)),t)
            self.assertEqual(d.command.vx_cm_s,0)

    def test_gestures_temporal_target_only_stop_and_dedup(self):
        self.start()
        for i in range(9):
            t = 1.1 + i * .1
            g = GestureObservation('Closed_Fist', .99, 2, t)
            self.assertEqual(self.c.step(frame(t, gesture=g), t).state, 'FOLLOWING')
        for i in range(9):
            t = 2.1 + i * .1
            g = GestureObservation('Closed_Fist', .99, 1, t)
            d = self.c.step(frame(t, gesture=g), t)
        self.assertEqual(d.state, 'PAUSED')
        self.assertEqual(d.command.vx_cm_s, 0)
        self.assertEqual(self.c.step(frame(3.1), 3.1).state, 'PAUSED')

    def test_limits_turn_deadband_invalid_and_hardware_reverse(self):
        self.start()
        for i in range(30):
            t = 1.1 + i * .05
            d = self.c.step(frame(t), t)
            self.assertLessEqual(abs(d.command.vx_cm_s), 5)
            self.assertLessEqual(abs(d.command.w_rad_s), .5)
            self.assertEqual(d.command.vy_cm_s, 0)
            self.assertEqual(d.command.w_rad_s, 0)
        bad = PersonObservation(1, (math.nan,0,100,200), .9, 3)
        self.assertEqual(self.c.step(frame(3, [bad]), 3).command.vx_cm_s, 0)
        self.c = Controller(load_config(), hardware=True)
        self.c.select_target(1); self.c.confirm_target(); self.start()
        close = PersonObservation(1, (200,0,440,440), .9, 1.1)
        d = self.c.step(frame(1.1, [close]), 1.1)
        self.assertGreaterEqual(d.command.vx_cm_s, 0)


if __name__ == '__main__':
    unittest.main()
