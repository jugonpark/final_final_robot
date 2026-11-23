import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.compare_distance_trials import compare


class DistanceTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'trials.json'
        self.manifest = dict(evidence='EXAMPLE', condition='Test floor',
                             profiles={'factory': {'kp': 1.3, 'ki': 1.5}, 'candidate': {'kp': 1, 'ki': 1.5}},
                             baseline='factory', trials=[
                                 dict(id='a', profile='factory', direction='forward', target_cm=10, measured_cm=8),
                                 dict(id='b', profile='factory', direction='forward', target_cm=10, measured_cm=10),
                                 dict(id='c', profile='candidate', direction='forward', target_cm=10, measured_cm=9.5),
                                 dict(id='d', profile='candidate', direction='right', target_cm=10, measured_cm=10),
                             ])

    def report(self):
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        return compare(self.path)

    def write_run(self, *, stop=True, direction='forward', aborted=False):
        frame = dict(boot_id=1, session_id=2, motion_id=3)
        rows = [
            dict(event='APPROVED', source='DASHBOARD', actions=[dict(type='move', direction=direction, distance_cm='10')]),
            dict(event='STOP_CONFIRMED', telemetry=dict(frame, state='STOPPED')),
            dict(event='TELEMETRY', telemetry=dict(frame, state='DISTANCE_ACTIVE', fault='NONE',
                                                   wheel_target=[0, -2, 2], wheel_speed=[0, -1.75, 1.5])),
            dict(event='GOAL_REACHED', telemetry=dict(frame, state='GOAL_REACHED', goal_target_cm=10, goal_progress_cm=9.7))
        ]
        if stop:
            rows.append(dict(event='STOP_CONFIRMED', telemetry=dict(frame, state='STOPPED')))
        rows.append(dict(event='ABORTED' if aborted else 'COMPLETED'))
        (self.path.parent / 'run.jsonl').write_text(
            '\n'.join(json.dumps(dict(row, run_id='example')) for row in rows), encoding='utf-8')
        self.manifest['trials'][0]['log'] = 'run.jsonl'

    def test_compares_same_direction_and_target(self):
        candidate, baseline, other_direction = self.report()['comparison']
        self.assertEqual(candidate['mae_change_vs_baseline_cm'], -0.5)
        self.assertEqual(baseline['mean_error_cm'], -1)
        self.assertEqual(baseline['trials'], 2)
        self.assertIsNone(other_direction['mae_change_vs_baseline_cm'])

    def test_encoder_progress_is_separate_from_physical_measurement(self):
        self.write_run()
        trial = self.report()['trials'][0]
        self.assertEqual(trial['encoder_goal_cm'], 9.7)
        self.assertEqual(trial['measured_cm'], 8)
        self.assertEqual(trial['measured_minus_encoder_cm'], -1.7)
        self.assertEqual(trial['wheel_speed_tracking'][2],
                         dict(motor='M3', samples=1, mean_absolute_error_cm_s=0.5))

    def test_rejects_incomplete_or_mismatched_run(self):
        for options in ({'stop': False}, {'direction': 'left'}, {'aborted': True}):
            with self.subTest(options=options):
                self.write_run(**options)
                with self.assertRaises(ValueError):
                    self.report()
