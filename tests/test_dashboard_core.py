import json
import math
from pathlib import Path
import tempfile
import unittest

from python_controller.config import load_config
from python_controller.models import GestureObservation, MotionCommand
from python_controller.control.state_machine import Controller
from python_controller.vision.gesture_recognizer import gesture_diagnostics
from python_controller.monitoring import motor_feedback, SessionLogger, TestBook
from test_control import frame


class DashboardCoreTests(unittest.TestCase):
    def test_raw_is_visible_when_multiple_people_block_control(self):
        d=gesture_diagnostics([('Thumb_Up',.92,2)],2,2,1,1)
        self.assertEqual(d['raw'][0]['name'],'Thumb_Up')
        self.assertEqual(d['rejection'],'MULTIPLE_PERSON_RESTRICTION')
        self.assertEqual(d['hand_count'],1)

    def test_gesture_rejections_distinct(self):
        self.assertEqual(gesture_diagnostics([],None,1,1,0)['rejection'],'NO_HAND_DETECTED')
        self.assertEqual(gesture_diagnostics([('Open_Palm',.9,None)],1,1,1,1)['rejection'],'HAND_TARGET_MISMATCH')
        self.assertEqual(gesture_diagnostics([('Open_Palm',.9,1)],None,1,1,1)['rejection'],'TARGET_NOT_SELECTED')

    def test_raw_confirmed_applied_and_unarmed_rejection(self):
        c=Controller(load_config());c.select_target(1);c.confirm_target()
        for i in range(8):
            t=1+i*.1
            c.step(frame(t,gesture=GestureObservation('Open_Palm',.99,1,t)),t)
        self.assertEqual(c.gesture_status['confirmed'],'Open_Palm')
        self.assertIsNone(c.gesture_status['applied_action'])
        self.assertEqual(c.gesture_status['rejection'],'CONTROLLER_NOT_ARMED')

    def test_progress_before_confirmation_and_real_applied_action(self):
        c=Controller(load_config());c.select_target(1);c.confirm_target()
        c.step(frame(1),1,'ARM')
        for i in range(4):
            t=1.1+i*.1;c.step(frame(t,gesture=GestureObservation('Open_Palm',.99,1,t)),t)
        self.assertGreater(c.gesture_status['progress'],0)
        self.assertIsNone(c.gesture_status['confirmed'])
        self.assertEqual(c.gesture_status['rejection'],'GESTURE_UNSTABLE')
        for i in range(4,8):
            t=1.1+i*.1;c.step(frame(t,gesture=GestureObservation('Open_Palm',.99,1,t)),t)
        self.assertEqual(c.last_applied_action,'RESUME')

    def test_request_is_visible_while_safety_output_is_zero(self):
        c=Controller(load_config());c.select_target(1);c.confirm_target()
        d=c.step(frame(),1)
        self.assertGreater(c.requested_command.vx_cm_s,0)
        self.assertEqual(d.command,MotionCommand())

    def test_emergency_state_persists_until_successful_arm(self):
        c=Controller(load_config());c.select_target(1);c.confirm_target()
        c.step(frame(),1,'ESTOP')
        self.assertEqual(c.step(frame(1.1,[]),1.1,'RESUME').reason,'EMERGENCY_STOP')
        self.assertFalse(c.armed)

    def test_missing_feedback_is_null_and_units_convert(self):
        self.assertIsNone(motor_feedback(None,2.9)['target_rpm'])
        d=motor_feedback(dict(wheel_target=[2*math.pi*2.9,0,0],wheel_speed=[0,0,0],wheel_pwm=[1,0,0],encoder_count=[5,0,0]),2.9)
        self.assertAlmostEqual(d['target_rpm'][0],60)
        self.assertEqual(d['encoder_count'],[5,0,0])
        self.assertIsNone(motor_feedback(dict(wheel_target=[float('nan'),0,0]),2.9)['target_rpm'])

    def test_testbook_requires_notes_and_never_defaults_to_pass(self):
        b=TestBook()
        self.assertTrue(all(r['result']=='NOT TESTED' for r in b.rows))
        with self.assertRaises(ValueError):b.record(0,'PASS','')
        b.record(0,'FAIL','No camera','SIMULATION')
        self.assertEqual(b.rows[0]['result'],'FAIL')

    def test_log_records_null_and_separates_test_results(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as tmp:
            log=SessionLogger(tmp,'SIMULATION','SYNTHETIC_PREVIEW')
            try:
                log.write({'telemetry':None,'safe':{'vx_cm_s':0},'program_mode':'SIMULATION','evidence_source':'SYNTHETIC_PREVIEW'},force=True)
            finally:log.close()
            records=[json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertIsNone(records[-1]['telemetry'])
            self.assertEqual(records[-1]['evidence_source'],'SYNTHETIC_PREVIEW')
