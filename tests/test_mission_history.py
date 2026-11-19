import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from python_controller.execution.history_store import HistoryUnavailable, MissionHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'missions.sqlite3'
        self.store = MissionHistory(self.path)
        self.addCleanup(self.store.close)
        self.actions = [{'type': 'move', 'direction': 'forward', 'distance_cm': '10'}]
        self.run_id = 'a' * 32

    def test_reopened_store_only_reports_backed_terminal_state(self):
        self.assertEqual(self.store.recent(), [])
        self.assertFalse(self.path.exists())
        self.store.start(self.run_id, 'plan-a', 2, self.actions)
        self.store.record(self.run_id, 2, 'MOVE_ACCEPTED', source='executor', step=1, elapsed_ms=20,
                          fields={'telemetry': {'boot_id': 42, 'secret': 'do not persist', 'motion_id': 9}})
        self.store.close()
        reader = MissionHistory(self.path)
        last, = reader.recent()
        self.assertEqual(last['state'], 'UNCONFIRMED')
        self.assertEqual(reader.recent(failures=True), [])
        saved = reader.detail(self.run_id)
        self.assertEqual(saved['actions'], self.actions)
        self.assertEqual(saved['events'][-1]['evidence'], {'boot_id': 42, 'motion_id': 9})

    def test_duplicate_is_idempotent_and_conflict_is_reported(self):
        self.store.start(self.run_id, 'plan-a', 2, self.actions)
        self.store.start(self.run_id, 'plan-a', 2, self.actions)
        for event in ('STOP_REQUESTED', 'STOP_REQUESTED', 'COMPLETED'):
            self.store.record(self.run_id, 2, event, source='operator', reason='EMERGENCY_STOP', elapsed_ms=3)
        self.store.close()
        self.assertTrue(self.store.degraded)
        self.assertEqual([event['event_type'] for event in self.store.detail(self.run_id)['events']],
                         ['RUN_STARTED', 'STOP_REQUESTED'])

    def test_gap_cannot_become_completed_and_unavailable_read_is_explicit(self):
        self.store.start(self.run_id, 'plan-a', 2, self.actions)
        self.store.record(self.run_id, 3, 'COMPLETED', source='supervisor', elapsed_ms=15)
        self.store.close()
        self.assertTrue(self.store.degraded)
        self.assertEqual(self.store.detail(self.run_id)['state'], 'UNCONFIRMED')
        bad = self.path.with_name('invalid.sqlite3')
        bad.write_bytes(b'not a database')
        with self.assertRaises(HistoryUnavailable):
            MissionHistory(bad).recent()

    def test_slow_or_failed_writer_does_not_block_submitting_stop(self):
        from python_controller.llm.central_control import ExecutionProgress, NaturalControl
        from python_controller.planning.validator import ValidatedPlan, parse_operator_command

        control = NaturalControl(self.path.parent, 1)
        self.addCleanup(control.close)
        entered, release = Event(), Event()

        def fail(*_):
            entered.set()
            release.wait(4)
            raise sqlite3.OperationalError('test unavailable storage')

        with patch.object(control.history, '_append', side_effect=fail):
            try:
                plan = ValidatedPlan(parse_operator_command('앞으로 10cm 가'))
                control.phase = 'EXECUTING'
                control.progress = ExecutionProgress(plan, ('plan-a', 2))
                control.history.start(control.progress.run_id, 'plan-a', 2, self.actions)
                self.assertTrue(entered.wait(2))
                control.cancel('EMERGENCY_STOP')
                self.assertTrue(control._stop.is_set())
                self.assertFalse(release.is_set())
            finally:
                release.set()
                control.history.close()
        self.assertTrue(control.history.degraded)
