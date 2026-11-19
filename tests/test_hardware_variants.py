from contextlib import closing
import csv
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from python_controller.knowledge.answer import answer_documents
from python_controller.knowledge.library import KnowledgeError, KnowledgeLibrary, SCHEMA
from python_controller.knowledge.variants import BREADBOARD, V18, V19


ROOT = Path(__file__).resolve().parents[1]


class HardwareKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.library = KnowledgeLibrary(Path(folder.name) / 'knowledge.sqlite3', ROOT)
        cls.library.vector = None

    def test_scoped_question_matrix(self):
        cases = (
            ('브레드보드에는 어떤 부품이 있어?', BREADBOARD, '9. 브레드보드'),
            ('빵판 BOM', BREADBOARD, 'TS0185'),
            ('breadboard BOM', BREADBOARD, 'MG90S'),
            ('브레드보드 TB6612 드라이버', BREADBOARD, 'SZH-MDBL-00'),
            ('브레드보드 서보는 몇 개?', BREADBOARD, 'MG90S 4개'),
            ('브레드보드 GPIO 번호 알려줘', BREADBOARD, 'GPIO 번호'),
            ('브레드보드 배선표', BREADBOARD, '실제 모터·서보 결선이 없습니다'),
            ('브레드보드 배터리 전압', BREADBOARD, '7.4V'),
            ('TERANTY 배터리 몇 V야?', BREADBOARD, '7.4V'),
            ('브레드보드 드라이버 교체 호환', BREADBOARD, '확인되지 않았습니다'),
            ('PCB V18 BOM', V18, '99.8×99.8mm'),
            ('V18의 모터 드라이버가 뭐야?', V18, 'HW166'),
            ('PCB 1.8 서보 연결', V18, 'PCA9685 확장 연결이 확인되지 않습니다'),
            ('V18 U1 STBY 핀 번호', V18, '패드 11'),
            ('V18 전원 입력이 뭐야?', V18, 'XT60PW-M'),
            ('PCB V18 제작 승인됐어?', V18, 'A08'),
            ('V18 DRC 검사', V18, 'ERC 제외 검사 4개'),
            ('V18 장착홀은 몇 개?', V18, '총 4개'),
            ('현재 PCB V18 장착홀은?', V18, 'H1, H2, H3, H4'),
            ('V18 TB6612 모듈 호환돼?', V18, '검증되지 않았습니다'),
            ('PCB V19 M2 엔코더 핀 번호', V19, '| M2 | 21 | 22 | 26 | 36 | 39 |'),
            ('V19 GPIO32는 무슨 핀이야?', V19, '/VBAT_ADC'),
            ('현행 PCB 설계의 BOM', V19, 'ESP32 DevKit 38핀'),
            ('PCB V19 제작 승인', V19, 'FINAL_PINMAP_APPROVED=false'),
        )
        for question, variant, expected in cases:
            with self.subTest(question=question), patch(
                'python_controller.knowledge.answer._nemotron_evidence', side_effect=AssertionError('local only')
            ):
                answer = answer_documents(self.library, question)
                self.assertIn(expected, answer['reply'])
                self.assertEqual({row['variant_id'] for row in answer['citations']}, {variant})
                row = answer['citations'][0]
                self.assertTrue(row['source_path'] and row['section'] and row['revision'] and row['evidence_status'])
                self.assertEqual(len(row['source_sha256']), 64)
                if variant in (V18, V19):
                    self.assertIn('FAB_APPROVED=false', answer['reply'])
                    self.assertNotIn('SZH-MDBL-00', answer['reply'])
                else:
                    self.assertNotIn('HW166', answer['reply'])

    def test_ambiguous_as_built_and_comparisons(self):
        for question in ('PCB에는 어떤 모터 드라이버가 있어?', '핀맵 알려줘', '서보 MG90S는 PCB 어디에 연결돼?',
                         'PCB 안전 검사 완료했어?', '브레드보드와 PCB는 뭐가 달라?'):
            with self.subTest(question=question):
                response = answer_documents(self.library, question)
                self.assertIn('V18', response['reply'])
                self.assertFalse(response['citations'])
                with self.assertRaises(KnowledgeError):
                    self.library.search_robot_documents(question)
        as_built = answer_documents(self.library, '현재 PCB 실물이 연결됐니?')
        self.assertIn('현장', as_built['reply'])
        self.assertFalse(as_built['citations'])
        comparison = answer_documents(self.library, '브레드보드와 PCB V19 핀맵 비교')
        self.assertEqual([row['variant_id'] for row in comparison['citations']], [BREADBOARD, V19])
        self.assertEqual(comparison['citations'][0]['source_kind'], 'FIRMWARE_SOURCE')
        self.assertIn('FINAL_PROJECT_V1.zip', comparison['citations'][1]['source_path'])

    def test_separate_original_evidence_and_review_namespace(self):
        with (ROOT / 'docs/hardware/breadboard/BOM.csv').open(encoding='utf-8-sig', newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([int(row['qty']) for row in rows], [1, 2, 1, 2, 1, 1, 4, 1, 1])
        docs = self.library.documents()
        variant_sources = [row for row in docs if row['trust'] == 'hardware_reference']
        self.assertEqual({row['variant_id'] for row in variant_sources}, {BREADBOARD, V18, V19})
        self.assertTrue(all(row['status'] == 'scoped' for row in variant_sources))
        self.assertFalse(any('/evidence/' in row['path'] or 'SOURCE_DRAFT' in row['path'] for row in docs))
        self.assertEqual([row['evidence_status'] for row in docs if row['trust'] == 'review_draft'], ['REVIEW_ONLY'])
        general = self.library.search_robot_documents('실행 레이더 시스템 구조 텔레메트리')
        self.assertTrue(general)
        self.assertFalse(any(row['trust'] in ('hardware_reference', 'review_draft') for row in general))

    def test_battery_correction_is_scoped(self):
        battery = answer_documents(self.library, '배터리는 몇 V야?')
        self.assertIn('7.4V', battery['reply'])
        self.assertNotIn('7.6V', battery['reply'])
        self.assertEqual({row['variant_id'] for row in battery['citations']}, {BREADBOARD})

    def test_retired_documents_and_old_citations_are_removed_on_existing_database(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'existing.sqlite3'
            old = KnowledgeLibrary(path, ROOT)
            survivor = old.register('my-note.txt', b'OPERATOR_NOTE remains available.')
            removed = old._ingest('docs/hardware/pcb-v19/FACTS.md', b'Prior hardware text.', 'V19',
                                  'hardware_reference', metadata=('PCB_V19_CANDIDATE', 'KICAD_AUDIT',
                                                                   'CAD_DESIGN', 'old archive'))
            with old._db() as db:
                chunk_id = db.execute('SELECT id FROM chunks WHERE document_id=?', (removed['id'],)).fetchone()['id']
            current = KnowledgeLibrary(path, ROOT)
            self.assertIsNone(current.get_document_chunk(chunk_id))
            self.assertIn(survivor['id'], {row['id'] for row in current.documents()})
            reply = answer_documents(current, 'PCB V19 BOM')
            self.assertIn('ESP32 DevKit 38핀', reply['reply'])
            self.assertEqual([row['variant_id'] for row in reply['citations']], [V19])

    def test_legacy_index_migrates_in_place(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'existing.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(SCHEMA)
                db.execute("INSERT INTO documents VALUES (1,'legacy.txt','sha','revision','user_reference','indexed',NULL)")
                db.execute("INSERT INTO chunks VALUES (1,1,'본문',NULL,1,'RELAYLEGACY evidence')")
                db.execute("INSERT INTO chunk_search VALUES ('legacy.txt','본문','RELAYLEGACY evidence')")
            library = KnowledgeLibrary(path, ROOT)
            rows = library.search_robot_documents('RELAYLEGACY')
            self.assertEqual(rows[0]['id'], 1)
            self.assertEqual(rows[0]['source_kind'], 'USER_UPLOAD')
            self.assertEqual(rows[0]['evidence_status'], 'UNVERIFIED')
