"""Append-only mission evidence. SQLite I/O runs outside the control thread."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import SimpleQueue
import re
import sqlite3
from threading import Lock, Thread


SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS mission_runs (
    run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
    actions TEXT NOT NULL, started_at TEXT NOT NULL, provenance TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_events (
    run_id TEXT NOT NULL REFERENCES mission_runs(run_id), sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL, source TEXT NOT NULL, step_index INTEGER, received_at TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL, reason TEXT, evidence TEXT NOT NULL, PRIMARY KEY(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS mission_runs_by_time ON mission_runs(started_at DESC);
'''
RUNS = '''SELECT r.*, COALESCE(e.event_type, 'UNCONFIRMED') AS state, e.reason
          FROM mission_runs r LEFT JOIN mission_events e ON e.run_id=r.run_id AND e.sequence=(
              SELECT MAX(sequence) FROM mission_events WHERE run_id=r.run_id AND event_type IN ('COMPLETED','ABORTED')
          )'''
EVIDENCE_FIELDS = ('session_id', 'boot_id', 'motion_id', 'last_seq', 'move_seq', 'stop_barrier',
                   'controller_generation', 'radar_generation')
EVENTS = frozenset({'RUN_STARTED', 'HANDSHAKE_STOP_CONFIRMED', 'MOVE_ACCEPTED', 'GOAL_REACHED',
                    'STOP_CONFIRMED', 'STOP_REQUESTED', 'ABORT_STOP_RESULT', 'COMPLETED', 'ABORTED'})


class HistoryUnavailable(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _reason(value):
    if not value:
        return None
    return value if isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', value) else 'UNCLASSIFIED_FAILURE'


class MissionHistory:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.degraded = False
        self._jobs = SimpleQueue()
        self._lock = Lock()
        self._writer = None
        self._closed = False

    def start(self, run_id, plan_id, revision, actions, *, provenance='ESP32_EXECUTOR'):
        start = utc_now()
        self._enqueue((run_id, plan_id, revision, json.dumps(actions), start, provenance),
                      (run_id, 1, 'RUN_STARTED', 'supervisor', None, start, 0, None, '{}'))

    def record(self, run_id, sequence, event, *, source, elapsed_ms, step=None, reason=None, fields=None):
        if event not in EVENTS or source not in ('executor', 'supervisor', 'operator'):
            raise ValueError('INVALID_HISTORY_EVENT')
        fields = fields or {}
        sample = fields.get('telemetry') or {}
        evidence = {key: value for key in EVIDENCE_FIELDS
                    if type(value := fields.get(key, sample.get(key))) is int and 0 <= value < 2**64}
        if type(fields.get('stop_verified')) is bool:
            evidence['stop_verified'] = fields['stop_verified']
        self._enqueue(None, (run_id, sequence, event, source, step, utc_now(), elapsed_ms, _reason(reason),
                             json.dumps(evidence, sort_keys=True)))

    def _enqueue(self, run, event):
        with self._lock:
            if self._closed:
                self.degraded = True
                return
            if self._writer is None:
                thread = Thread(target=self._write, name='mission-history-writer', daemon=True)
                try:
                    thread.start()
                except RuntimeError:
                    self.degraded = True
                    return
                self._writer = thread
            self._jobs.put((run, event))

    def _write(self):
        db = None
        try:
            while (job := self._jobs.get()) is not None:
                try:
                    if db is None:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        db = sqlite3.connect(self.path, timeout=.75)
                        db.execute('PRAGMA foreign_keys=ON')
                        db.executescript(SCHEMA)
                    self._append(db, *job)
                except (OSError, sqlite3.Error, ValueError):
                    self.degraded = True
                    if db is not None:
                        db.close()
                        db = None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _append(db, run, event):
        with db:
            db.execute('BEGIN IMMEDIATE')
            if run is not None:
                previous = db.execute('SELECT * FROM mission_runs WHERE run_id=?', (run[0],)).fetchone()
                if previous is None:
                    db.execute('INSERT INTO mission_runs VALUES (?,?,?,?,?,?)', run)
                elif previous[:4] != run[:4] or previous[5] != run[5]:
                    raise ValueError('HISTORY_RUN_CONFLICT')
            run_id, sequence = event[:2]
            old = db.execute('SELECT * FROM mission_events WHERE run_id=? AND sequence=?', (run_id, sequence)).fetchone()
            if old is not None:
                if old[:5] != event[:5] or old[6:] != event[6:]:
                    raise ValueError('HISTORY_EVENT_CONFLICT')
                return
            expected, = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM mission_events WHERE run_id=?', (run_id,)).fetchone()
            if sequence != expected:
                raise ValueError('HISTORY_EVENT_GAP')
            db.execute('INSERT INTO mission_events VALUES (?,?,?,?,?,?,?,?,?)', event)

    def _read(self, query, args):
        try:
            if not self.path.is_file():
                return []
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=.3)) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(query, args)]
        except (OSError, sqlite3.Error) as exc:
            raise HistoryUnavailable('HISTORY_UNAVAILABLE') from exc

    @staticmethod
    def _run(row):
        return {**row, 'actions': json.loads(row['actions'])}

    def recent(self, *, failures=False, limit=25):
        filter_ = " WHERE e.event_type='ABORTED'" if failures else ''
        return [self._run(row) for row in self._read(RUNS + filter_ + ' ORDER BY r.started_at DESC LIMIT ?',
                                                    (1 if failures else limit,))]

    def detail(self, run_id):
        runs = self._read(RUNS + ' WHERE r.run_id=?', (run_id,))
        if not runs:
            return None
        events = self._read('SELECT * FROM mission_events WHERE run_id=? ORDER BY sequence', (run_id,))
        terminal = next((event for event in reversed(events) if event['event_type'] in ('COMPLETED', 'ABORTED')), None)
        return {**self._run(runs[0]), 'state': terminal['event_type'] if terminal else 'UNCONFIRMED',
                'reason': (terminal or {}).get('reason'),
                'events': [{**event, 'evidence': json.loads(event['evidence'])} for event in events]}

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._jobs.put(None)
        if self._writer:
            self._writer.join(timeout=1)
