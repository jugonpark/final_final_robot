import tempfile
import unittest
from pathlib import Path


class Phase2Tests(unittest.TestCase):
    def test_budget_reserves_before_failure_and_cannot_resume_past_cap(self):
        from python_controller.knowledge.experiments import Budget
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'budget.json'
            budget=Budget(path,2)
            budget.reserve('first')
            Budget(path,2).reserve('second')
            with self.assertRaises(RuntimeError):
                Budget(path,2).reserve('third')

    def test_confusion_counts_unavailable_as_failure_not_success(self):
        from python_controller.knowledge.experiments import verifier_metrics
        rows=[{'gold':'ENTAILMENT','prediction':'UNAVAILABLE'},
              {'gold':'CONTRADICTION','prediction':'ENTAILMENT'},
              {'gold':'NEUTRAL','prediction':'NEUTRAL'}]
        metrics=verifier_metrics(rows)
        self.assertAlmostEqual(metrics['accuracy'],1/3)
        self.assertEqual(metrics['false_acceptance_rate'],.5)
        self.assertEqual(metrics['false_rejection_rate'],1.)

    def test_unannotated_final_reply_is_not_scored_from_claim_self_reports(self):
        from python_controller.knowledge.experiments import response_metrics
        row={'reply':'검증 목록 밖에 100cm 안전이라는 문장이 있습니다.', 'claims':[],
             'verification_status':'verified','total_latency_ms':12}
        metrics=response_metrics(row,{'answerable':True,'expected_facts':[{'fact_id':'F1','required':True}]})
        self.assertIsNone(metrics['faithfulness'])
        self.assertIsNone(metrics['coverage'])
        self.assertIsNone(metrics['naturalness'])

    def test_final_output_atoms_override_a_model_claim_list_and_preserve_empty_faithfulness(self):
        from python_controller.knowledge.experiments import response_metrics
        case={'answerable':True,'expected_facts':[{'fact_id':'F1','required':True}]}
        row={'reply':'정확한 사실과 잘못된 100cm 주장이 함께 있음','claims':[{'text':'정확한 사실'}]}
        labels={'final_reply_atoms':[{'supported':True,'gold_ids':['F1'],'citation_correct':True},
                                     {'supported':False,'gold_ids':[],'citation_correct':False}], 'naturalness':3}
        metrics=response_metrics(row,case,labels)
        self.assertEqual(metrics['faithfulness'],.5)
        self.assertEqual(metrics['coverage'],1.)
        self.assertIsNone(response_metrics({'reply':'보류'},case,{'final_reply_atoms':[]})['faithfulness'])
        self.assertIsNone(response_metrics(row,case,{'final_reply_atoms':[{'supported':None}]})['faithfulness'])

    def test_B_is_experimental_citation_only_and_rejects_invalid_indices(self):
        import importlib.util
        import sys
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        sys.path.insert(0,str(scripts));self.addCleanup(lambda:sys.path.remove(str(scripts)))
        spec=importlib.util.spec_from_file_location('evaluation_only_B',scripts/'evaluate_rag_v3.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        raw={'claims':[{'claim_id':'x','text':'모델의 임의 주장입니다.','supports':[{'evidence_id':1,'quote_index':99}]}],'unanswered_aspects':[]}
        result=module.citation_only(raw,[{'id':1,'quote_candidates':['근거 문장']}],None)
        self.assertEqual(result['claims'],[])
        self.assertEqual(result['verification_status'],'citation_existence_only')

    def test_frozen_dataset_integrity_groups_and_gold_never_enter_generation_payload(self):
        import json
        from unittest.mock import patch
        from python_controller.knowledge.experiments import digest,read_jsonl
        from python_controller.knowledge.generation import RAGCalls
        folder=Path(__file__).resolve().parents[1]/'docs/verification/rag_v3_phase2'
        cases=read_jsonl(folder/'gold_cases.jsonl');pairs=read_jsonl(folder/'semantic_cases.jsonl')
        manifest=json.loads((folder/'dataset_manifest.json').read_text())
        self.assertEqual(len(cases),120);self.assertEqual(len(pairs),180)
        self.assertEqual(digest(cases),manifest['gold_sha256'])
        self.assertEqual(digest(pairs),manifest['semantic_sha256'])
        for group in {c['semantic_group'] for c in cases}:
            self.assertEqual(len({c['dataset_split'] for c in cases if c['semantic_group']==group}),1)
        evidence=[{'id':1,'heading':'통신','quote_candidates':['ESP32 uses UDP.'],'expected_facts':['must not send'],'gold':'ENTAILMENT'}]
        calls=RAGCalls({})
        with patch.object(calls,'complete',return_value={'claims':[],'unanswered_aspects':[]}) as complete:
            calls.generate('공개 질문',evidence)
        payload=complete.call_args.args[1]
        self.assertNotIn('expected_facts',str(payload));self.assertNotIn('gold',str(payload))

    def test_sensitive_input_is_blocked_before_budget_or_network(self):
        from types import SimpleNamespace
        from python_controller.knowledge.experiments import RecordingAPI,Budget
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            api=RecordingAPI(Budget(Path(tmp)/'ledger.json',12),{})
            create=Mock()
            api.factory=Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
            client=api.client()
            with self.assertRaises(ValueError):
                client.chat.completions.create(messages=[{'role':'user','content':'WIFI_PASSWORD=not-to-be-sent'}])
            create.assert_not_called()
            self.assertFalse((Path(tmp)/'ledger.json').exists())
            import json
            with self.assertRaises(ValueError):
                client.chat.completions.create(messages=[{'role':'user','content':json.dumps({'source':{'body':'WIFI_PASSWORD=not-to-be-sent'}})}])
            create.assert_not_called()
