from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from python_controller.mission.draft_store import DraftConflict, DraftOperation, DraftStore
from python_controller.planning.validator import parse_operator_command


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'draft.sqlite3'
        self.store = DraftStore(self.path)

    def op(self, kind, draft=None, **data):
        return DraftOperation(kind=kind, request_id=uuid4().hex, mission_id=draft['id'] if draft else None,
                              expected_revision=draft['revision'] if draft else 0, **data)

    def append(self, text, draft=None):
        return self.op('append', draft, actions=list(parse_operator_command(text, allow_rotation_preview=True)))

    def test_restart_and_reorder_keep_mission_identity(self):
        first_request = self.append('앞으로 5cm 가')
        first_result = self.store.apply(first_request)
        _, first = first_result
        _, second = self.store.apply(self.append('오른쪽으로 5cm 가고 뒤로 10cm 가', first))
        original = [row['block_id'] for row in second['blocks']]
        _, reordered = self.store.apply(self.op('reorder', second, target_block_id=original[1], before_block_id=original[0]))
        self.assertEqual(reordered['id'], first['id'])
        self.assertEqual(reordered['revision'], 3)
        self.assertEqual([row['block_id'] for row in reordered['blocks']], [original[1], original[0], original[2]])
        self.assertEqual(reordered['before'], second['steps'])
        restored = DraftStore(self.path)
        self.assertEqual(restored.apply(first_request), first_result)
        self.assertEqual(restored.current()[1], reordered)
        with self.assertRaises(DraftConflict):
            restored.apply(first_request.model_copy(update={'kind': 'clear'}))

    def test_concurrent_edit_and_policy_failure_are_atomic(self):
        _, card = self.store.apply(self.append('앞으로 50cm 가고 오른쪽으로 25cm 가'))
        ops = [self.append('왼쪽으로 20cm 가', card), self.append('뒤로 20cm 가', card)]

        def apply(op):
            try:
                return DraftStore(self.path).apply(op)
            except DraftConflict:
                return None

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(result is not None for result in pool.map(apply, ops)), 1)
        _, saved = self.store.current()
        with self.assertRaisesRegex(ValueError, 'TOTAL_DISTANCE_LIMIT'):
            self.store.apply(self.append('앞으로 10cm 가', saved))
        _, saved = self.store.apply(self.append('정지', saved))
        with self.assertRaisesRegex(ValueError, 'ACTION_COUNT'):
            self.store.apply(self.append('앞으로 5cm 가', saved))
        self.assertEqual(self.store.current()[1], saved)

    def test_claim_uses_exact_revision_and_cannot_overwrite_future_draft(self):
        _, old = self.store.apply(self.append('앞으로 5cm 가'))
        _, edited = self.store.apply(self.append('앞으로 5cm 가', old))
        with self.assertRaises(DraftConflict):
            self.store.claim(old)
        serial = self.store.claim(edited)
        _, future = self.store.apply(self.append('뒤로 5cm 가'))
        self.store.restore(serial, edited)
        self.assertNotEqual(future['id'], old['id'])
        self.assertEqual(self.store.current()[1], future)
