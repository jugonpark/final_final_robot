"""Offline candidates are eligibility tests, never semantic truth assertions."""
import unittest
from unittest.mock import patch

from python_controller.knowledge import verifier

ROWS=[{'variant_id':'SOFTWARE_COMMON','evidence_status':'SOFTWARE_IMPLEMENTATION'}]


class CalibrationTests(unittest.TestCase):
    def test_target_is_not_automatically_a_design_goal(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        source='목표 손 제스처를 사용합니다. ESP32의 펌웨어를 재사용합니다.'
        claim='ESP32의 펌웨어를 재사용해요.'
        self.assertEqual(verifier.deterministic_reason(claim,[source],ROWS),'DESIGN_QUALIFIER_DROPPED')
        original=verifier.DESIGN
        self.assertIsNone(candidate_reason(claim,[source],ROWS))
        self.assertIs(verifier.DESIGN,original)

    def test_design_and_cad_counterexamples_remain_blocked(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        for source,rows in [('이동 거리 10cm는 설계 목표입니다.',ROWS),
                             ('이동 거리 10cm는 성능 목표입니다.',ROWS),
                             ('모터 전압은 5V입니다.',[{'variant_id':'PCB_V19_DESIGN','evidence_status':'CAD_DESIGN'}])]:
            with self.subTest(source=source):
                claim='이동 거리 10cm를 실측했어요.' if '10cm' in source else '모터 전압은 5V예요.'
                self.assertIsNotNone(candidate_reason(claim,[source],rows))

    def test_numeric_binding_accepts_only_exact_subject_value(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        source='ESP32 전원은 5V 모터 전원은 12V'
        self.assertEqual(verifier.deterministic_reason('ESP32 전원은 5V예요.',[source],ROWS),'QUANTITY_BINDING_MISMATCH')
        self.assertIsNone(candidate_reason('ESP32 전원은 5V예요.',[source],ROWS))
        for bad in ('ESP32 전원은 12V예요.','ESP32 전원은 5A예요.','ESP32 전원은 5V이므로 안전해요.'):
            self.assertIsNotNone(candidate_reason(bad,[source],ROWS))

    def test_numeric_conditions_negation_and_conflicting_values_fail_closed(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        for source in ('ESP32 전원은 5V 모터 전원은 12V이며 CLEAR일 때만 허용합니다.',
                       'ESP32 전원은 5V가 아닙니다. 모터 전원은 12V입니다.',
                       'ESP32 전원은 5V ESP32 전원은 12V'):
            self.assertIsNotNone(candidate_reason('ESP32 전원은 5V예요.',[source],ROWS))

    def test_polite_korean_negative_gap_is_not_a_final_semantic_verdict(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        source='ESP32 전원은 5V가 아닙니다.'
        self.assertIsNone(verifier.deterministic_reason('ESP32 전원은 5V예요.',[source],ROWS))
        self.assertEqual(candidate_reason('ESP32 전원은 5V예요.',[source],ROWS),'NEGATION_DROPPED')
        self.assertIsNone(candidate_reason('ESP32 전원은 5V가 아닙니다.',[source],ROWS))

    def test_unrelated_subject_negation_is_not_global(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        source='Qt는 PySide6로 구현됩니다. 모터의 실물 구동은 검증하지 않았습니다.'
        claim='Qt는 PySide6로 구현돼요.'
        self.assertEqual(verifier.deterministic_reason(claim,[source],ROWS),'NEGATION_DROPPED')
        self.assertIsNone(candidate_reason(claim,[source],ROWS))

    def test_relevant_and_implicit_conditions_cannot_be_dropped(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        for source in ('Qt는 Linux에서만 PySide6를 사용합니다.',
                       'Qt는 PySide6로 구현됩니다. 다만 이 기능은 Linux에서만 사용할 수 있습니다.',
                       'Qt는 PySide6를 사용하지 않습니다.'):
            self.assertIsNotNone(candidate_reason('Qt는 PySide6를 사용해요.',[source],ROWS))

    def test_wrong_entities_versions_and_multiple_facts_remain_blocked(self):
        from scripts.rag_phase2_5_candidates import candidate_reason
        for claim,source,rows in [('ESP32는 Bluetooth로 통신해요.','ESP32는 UDP로 통신합니다.',ROWS),
            ('PCB V19 설계의 모터 전원은 5V예요.','PCB V18 설계의 모터 전원은 5V입니다.',
             [{'variant_id':'PCB_V18_DESIGN','evidence_status':'CAD_DESIGN'}]),
            ('ESP32 전원은 5V이고 모터 전원은 5V예요.','ESP32 전원은 5V 모터 전원은 12V',ROWS)]:
            self.assertIsNotNone(candidate_reason(claim,[source],rows))

    def test_renderer_preserves_claim_words_without_new_causal_fact(self):
        from scripts.rag_phase2_5_candidates import render_candidate
        claims=[{'text':'관제는 UDP 명령을 보내요.'},{'text':'ESP32가 명령을 받아요.'}]
        reply=render_candidate(claims)
        self.assertEqual(reply,'관제는 UDP 명령을 보내요. ESP32가 명령을 받아요.')
        self.assertNotIn('따라서',reply)
        with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            self.assertEqual(render_candidate(claims),reply)

    def test_core20_has_atomic_provenance_and_twenty_reviewable_csv_cases(self):
        import csv
        import hashlib
        import json
        from pathlib import Path
        from scripts.build_rag_phase2_5_gold import read_source
        folder=Path(__file__).resolve().parents[1]/'docs/verification/rag_v3_phase2_5'
        cases=[json.loads(s) for s in (folder/'gold_core20.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(cases),20)
        self.assertEqual({c:sum(r['category']==c for r in cases) for c in {r['category'] for r in cases}},
                         {'simple_fact':4,'architecture':5,'numerical_entity':3,'version_comparison':2,'complex':2,'insufficient':2,'live_state':2})
        self.assertEqual(sum(r['split']=='development' for r in cases),10)
        self.assertEqual(sum(r['split']=='test_candidate' for r in cases),10)
        for row in cases:
            self.assertFalse(row['human_approved'])
            for fact in row['expected_facts']+row['abstention_rationale_sources']:
                raw,text=read_source(fact['source_path'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(),fact['source_sha256'])
                self.assertIn(fact['evidence_excerpt'],text)
                self.assertNotIn('\n',fact['statement'])
        with (folder/'gold_core20_review.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(len({r['case_id'] for r in csv.DictReader(stream)}),20)

    def test_recorded_replay_and_exhausted_budget_are_unchanged(self):
        import json
        from pathlib import Path
        from scripts.analyze_rag_phase2_5 import verify_preservation
        folder=Path(__file__).resolve().parents[1]/'docs/verification/rag_v3_phase2_5'
        summary=json.loads((folder/'replay_summary.json').read_text())
        self.assertEqual(summary['raw_claim_blocks'],11)
        self.assertEqual(sum(summary['deterministic_rejections'].values()),9)
        self.assertEqual(summary['historical_semantic_claims'],2)
        self.assertTrue(summary['all_cached_replays_match'])
        with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            self.assertEqual(verify_preservation(include_code=False)['new_external_requests'],0)
