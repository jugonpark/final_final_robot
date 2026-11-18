import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from python_controller.diagnostics import safe_payload
from python_controller.monitoring import motor_feedback
from scripts.compare_turn_rpm import compare_turns


class TurnRPMTests(unittest.TestCase):
    def test_compare_excludes_startup_and_untrusted_telemetry(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'events.jsonl'
            rows = []
            for token, w, speed in ((1, .2, 9), (2, -.2, -6)):
                for i in range(7):
                    payload = dict(boot_id=1, session_id=2, execution_token=token,
                                   uptime_ms=token * 2000 + i * 200,
                                   mode='NETWORK', state='VELOCITY', fault='NONE',
                                   encoder_warning=False, control_style='FEEDFORWARD_PI',
                                   cmd_vx=0, cmd_vy=0, cmd_w=w,
                                   wheel_target_rpm=[7 if w > 0 else -7]*3,
                                   wheel_measured_rpm=[40 if i < 3 else speed]*3,
                                   wheel_ff_pwm=[20]*3, wheel_pi_trim_pwm=[3]*3, wheel_pwm=[23]*3)
                    rows.append(dict(event='UDP_RX', accepted=i != 6, json=safe_payload(payload)))
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
            result = compare_turns(path, .2)
        self.assertEqual([row['motor'] for row in result], ['M1', 'M2', 'M3'])
        self.assertEqual(result[0]['left']['samples'], 3)
        self.assertEqual(result[0]['left_minus_right_abs_measured_rpm'], 3)
        self.assertEqual(motor_feedback(rows[-1]['json'], 2.9)['measured_rpm'], [-6]*3)
