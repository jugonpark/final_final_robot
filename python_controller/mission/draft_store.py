"""Editable mission data. This store has no execution or approval authority."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter

from python_controller.planning.schema import Action, Move, StrictModel, describe_action
from python_controller.planning.validator import ValidatedPlan

Identifier = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
ACTION = TypeAdapter(Action)


def load_action(row):
    return ACTION.validate_python({key: Decimal(value) if key in ('distance_cm', 'angle_deg') else value
                                   for key, value in row.items()})


def load_plan(rows):
    return ValidatedPlan(tuple(load_action(row) for row in rows))


class MissionBlock(StrictModel):
    block_id: Identifier
    action: Action
    source_turn_id: Identifier | None = None


class DraftMission(StrictModel):
    mission_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    blocks: tuple[MissionBlock, ...]
    updated_at: str


class DraftOperation(StrictModel):
    kind: Literal['append', 'insert', 'delete', 'replace', 'reorder', 'clear', 'request_execution']
    mission_id: Identifier | None
    expected_revision: Annotated[int, Field(ge=0)]
    request_id: Identifier
    target_block_id: Identifier | None = None
    before_block_id: Identifier | None = None
    actions: Annotated[list[Action], Field(min_length=1, max_length=4)] | None = None


class DraftConflict(ValueError):
    pass


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class DraftStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS active (slot INTEGER PRIMARY KEY CHECK(slot=1), serial INTEGER, draft TEXT);
                INSERT OR IGNORE INTO active VALUES(1, 0, NULL);
                CREATE TABLE IF NOT EXISTS draft_edits (request_id TEXT PRIMARY KEY, proposal TEXT NOT NULL,
                    before TEXT, after TEXT, serial INTEGER NOT NULL);
            ''')

    @contextmanager
    def _db(self, *, write=False):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _read(db):
        serial, draft = db.execute('SELECT serial, draft FROM active WHERE slot=1').fetchone()
        return serial, json.loads(draft) if draft else None

    def current(self):
        with self._db() as db:
            return self._read(db)

    @staticmethod
    def _check(draft, mission_id, revision):
        ref = (draft['id'], draft['revision']) if draft else (None, 0)
        if ref != (mission_id, revision):
            raise DraftConflict('STALE_REVISION')

    def _change(self, request_id, proposal, mission_id, revision, update):
        key = _dump(proposal)
        with self._db(write=True) as db:
            logged = db.execute('SELECT proposal, after, serial FROM draft_edits WHERE request_id=?', (request_id,)).fetchone()
            if logged:
                if logged[0] != key:
                    raise DraftConflict('REQUEST_ID_CONFLICT')
                return logged[2], json.loads(logged[1]) if logged[1] else None
            serial, draft = self._read(db)
            self._check(draft, mission_id, revision)
            next_draft = update(draft)
            after = _dump(next_draft) if next_draft else None
            db.execute('UPDATE active SET serial=?, draft=? WHERE slot=1', (serial+1, after))
            db.execute('INSERT INTO draft_edits VALUES(?,?,?,?,?)',
                       (request_id, key, _dump(draft) if draft else None, after, serial+1))
            return serial+1, next_draft

    def apply(self, op, *, source_turn_id=None):
        if op.kind == 'request_execution':
            raise ValueError('REVIEW_IS_NOT_A_DRAFT_EDIT')

        def update(draft):
            blocks = list(draft['blocks']) if draft else []
            ids = [block['block_id'] for block in blocks]
            actions_required = op.kind in ('append', 'insert', 'replace')
            target_required = op.kind in ('delete', 'replace', 'reorder')
            if (bool(op.actions) != actions_required or (op.target_block_id is not None) != target_required or
                    (op.before_block_id is not None and op.kind not in ('insert', 'reorder'))):
                raise ValueError('INVALID_DRAFT_OPERATION')
            if (target_required and op.target_block_id not in ids or
                    op.before_block_id is not None and op.before_block_id not in ids):
                raise DraftConflict('BLOCK_NOT_FOUND')
            if op.kind == 'clear':
                return None
            additions = [MissionBlock(block_id=uuid4().hex, action=action, source_turn_id=source_turn_id).model_dump(mode='json')
                         for action in op.actions or []]
            if op.kind == 'append':
                blocks.extend(additions)
            elif op.kind == 'insert':
                index = ids.index(op.before_block_id) if op.before_block_id else len(blocks)
                blocks[index:index] = additions
            elif op.kind == 'delete':
                del blocks[ids.index(op.target_block_id)]
            elif op.kind == 'replace':
                if len(additions) != 1:
                    raise ValueError('SINGLE_ACTION_REVISION_REQUIRED')
                index = ids.index(op.target_block_id)
                blocks[index] = {**additions[0], 'block_id': op.target_block_id}
            elif op.before_block_id != op.target_block_id:
                block = blocks.pop(ids.index(op.target_block_id))
                index = next((i for i, row in enumerate(blocks) if row['block_id'] == op.before_block_id), len(blocks))
                blocks.insert(index, block)
            if not blocks:
                return None
            actions = [block['action'] for block in blocks]
            plan = load_plan(actions)
            mission = DraftMission(mission_id=draft['id'] if draft else uuid4().hex,
                                   revision=op.expected_revision+1,
                                   blocks=tuple(MissionBlock(**{**block, 'action': load_action(block['action'])}) for block in blocks),
                                   updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            return {'id': mission.mission_id, 'revision': mission.revision, 'updated_at': mission.updated_at,
                    'blocks': blocks, 'actions': actions, 'rotation': plan.requires_angle_control,
                    'steps': [describe_action(action) for action in plan.actions],
                    'distance_cm': str(sum(action.distance_cm for action in plan.actions if isinstance(action, Move))),
                    'before': draft['steps'] if draft else None}

        return self._change(op.request_id, op.model_dump(mode='json'), op.mission_id, op.expected_revision, update)

    def claim(self, card):
        """Archive a frozen revision before the host attempts the one-use dispatch."""
        serial, _ = self._change(uuid4().hex, {'kind': 'claim'}, card['id'], card['revision'], lambda draft: None)
        return serial

    def restore(self, serial, card):
        """Keep a denied draft unless another workspace has already replaced it."""
        with self._db(write=True) as db:
            if self._read(db) != (serial, None):
                return
            after = _dump(card)
            db.execute('UPDATE active SET serial=?, draft=? WHERE slot=1', (serial+1, after))
            db.execute('INSERT INTO draft_edits VALUES(?,?,?,?,?)', (uuid4().hex, '{"kind":"restore"}', None, after, serial+1))
