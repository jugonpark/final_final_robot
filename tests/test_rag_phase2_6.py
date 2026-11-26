"""Offline candidate contracts. Mock entailment is never actual answer quality."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.recovery.retrieval import retrieve,units
from python_controller.knowledge.recovery.pipeline import EvidenceSet,Timings,validate,answer_candidate,relevance
from python_controller.knowledge.recovery.rules import candidate_reason
from python_controller.knowledge.recovery.client import ReusableCalls


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.lib=KnowledgeLibrary(self.root/'db.sqlite',self.root);self.lib.vector=None;self.lib.documents()
        self.doc('transport.md','# 통신\n\n| 구간 | 방식 |\n|---|---|\n| 노트북 PC → ESP32 명령 전송 | UDP 8888 |\n| ESP32 → PC 텔레메트리 수신 | UDP 8889 |')
        self.doc('ui.md','# 화면 기술\n\nQt 화면은 PySide6를 사용하여 구현합니다.\n\nlatest-only worker는 카메라 프레임을 처리합니다.')
        self.doc('radar.md','# 상태\n\n레이더 UNKNOWN은 센서 상태가 유효하지 않음을 나타냅니다.')

    def doc(self,name,text,trust='local_reference'):
        (self.root/name).write_bytes(text.encode('utf-8'))
        return self.lib._ingest(name,text.encode(),'r1',trust)

    def bundle(self,q='Qt 화면은 어떤 기술로 구현해?'):
        ev,_,_=retrieve(self.lib,q);return EvidenceSet(ev)

    def raw(self,bundle,text='Qt 화면은 PySide6로 구현해요.',key=None):
        key=key or next(k for k,(r,i) in bundle.lookup.items() if 'PySide6' in r['quote_candidates'][i])
        return {'claims':[{'claim_id':'model','text':text,'evidence_ids':[key],'role':'core'}],'unanswered_aspects':[]}

    def test_table_columns_units_preserved(self):
        ev,_,_=retrieve(self.lib,'PC에서 ESP32로 어떤 통신을 사용해?')
        self.assertTrue(any('UDP 8888' in r['body'] and '| 구간 | 방식 |' in r['body'] for r in ev))
        self.assertTrue(all(len(r['quote_candidates'])==1 for r in ev))

    def test_relation_graph_is_data_and_executable_fences_are_excluded(self):
        diagram='flowchart TD\nPC --> TX["ESP32 명령 UDP 8888"]\nTX --> RX["PC 텔레메트리 UDP 8889"]'
        good='# 통신 흐름\n\n```mermaid\n'+diagram+'\n```'
        self.assertTrue(any(kind=='relation_graph' and quote==diagram for _,quote,kind in units(good)))
        for bad in ('```python\nprint("ESP32 UDP")\n```','```mermaid\n'+diagram+'\nclick TX "https://example.invalid"\n```'):
            self.assertFalse(list(units('# 위험\n'+bad)))

    def test_mock_end_to_end_preserves_speech_and_source_open_identifier(self):
        root=self.root/'project';root.mkdir()
        body='# 화면 기술\n\nQt 화면은 PySide6로 구현합니다.'
        (root/'guide.md').write_bytes(body.encode())
        lib=KnowledgeLibrary(root/'project.db',root);lib.vector=None;lib.documents()
        lib._ingest('guide.md',body.encode(),'r1','project_doc')
        original_id=lib.search_robot_documents('Qt 화면')[0]['id']
        def generate(system,payload):
            key=next(e['id'] for e in payload['evidence'] if 'PySide6' in e['text'])
            return {'claims':[{'claim_id':'x','text':'Qt 화면은 PySide6로 구현해요.','evidence_ids':[key],'role':'core'}],'unanswered_aspects':[]}
        answer=answer_candidate(lib,'Qt 화면 기술',env={'DIG_KNOWLEDGE_CLOUD':'1'},generate=generate,semantic=lambda p:['ENTAILMENT']*len(p))
        self.assertEqual(answer['reply'],'Qt 화면은 PySide6로 구현해요.')
        self.assertEqual(answer['speech_text'],answer['reply'])
        self.assertEqual(answer['citations'][0]['id'],original_id)
        self.assertEqual(answer['claims'][0]['evidence_ids'],[original_id])
        for name in ('retrieval_ms','evidence_selection_ms','generation_wrapper_ms','deterministic_validation_ms','semantic_validation_ms','answer_assembly_ms','total_ms'):
            self.assertGreaterEqual(answer['timings_ms'][name],0)

    def test_query_facets_paraphrase(self):
        for question in ('대시보드 UI는 어떤 라이브러리로 구현했어?','Qt 화면은 어떤 기술로 만든 거야?'):
            with self.subTest(question=question):self.assertTrue(any('PySide6' in r['body'] for r in retrieve(self.lib,question)[0]))

    def test_unknown_ids_cross_request_and_stale(self):
        a=self.bundle();b=self.bundle();self.assertTrue(set(a.lookup).isdisjoint(b.lookup))
        for raw in (self.raw(a,key='E999'),self.raw(a,key=next(iter(b.lookup)))):
            accepted,rejected=validate(raw,a,self.lib,'Qt 화면 기술',lambda p:['ENTAILMENT']*len(p),Timings())
            self.assertFalse(accepted);self.assertEqual(rejected[0]['reason'],'INVALID_EVIDENCE_ID')
        with self.lib._db() as db:db.execute("UPDATE documents SET status='outdated'")
        accepted,rejected=validate(self.raw(a),a,self.lib,'Qt 화면 기술',lambda p:['ENTAILMENT']*len(p),Timings())
        self.assertFalse(accepted);self.assertEqual(rejected[0]['reason'],'STALE_EVIDENCE')

    def test_valid_id_still_needs_semantic_and_no_rewrite(self):
        b=self.bundle()
        for relation,count in [('ENTAILMENT',1),('NEUTRAL',0),('CONTRADICTION',0),('UNAVAILABLE',0)]:
            with self.subTest(relation=relation):
                accepted,_=validate(self.raw(b),b,self.lib,'Qt 화면 기술',lambda p:[relation]*len(p),Timings())
                self.assertEqual(len(accepted),count)
        result=answer_candidate(self.lib,'Qt 화면 기술',env={'DIG_KNOWLEDGE_CLOUD':'1'},generate=lambda *a:self.fail('private scope must block'))
        self.assertEqual(result['answer_mode'],'abstained')
        self.assertEqual(result['speech_text'],result['reply'])

    def test_mutated_evidence_rejected(self):
        b=self.bundle();r=next(r for r in b.rows if 'PySide6' in r['body']);r['quote_candidates'][0]='Qt 화면은 PyQt6로 구현합니다.'
        accepted,rejected=validate(self.raw(b,text='Qt 화면은 PyQt6로 구현해요.',key=next(k for k,(row,_) in b.lookup.items() if row is r)),b,self.lib,'Qt 화면 기술',lambda p:['ENTAILMENT']*len(p),Timings())
        self.assertFalse(accepted);self.assertEqual(rejected[0]['reason'],'STALE_EVIDENCE')

    def test_incomplete_source_is_restored_only_exact_revision(self):
        full='# 상태\n\n레이더 UNKNOWN은 센서 상태가 유효하지 않음을 나타냅니다. UNKNOWN을 정상으로 단정하지 않습니다.'
        self.doc('long.md',full)
        with self.lib._db() as db:db.execute("UPDATE chunks SET body=?,index_version='legacy-tokenizer-unavailable' WHERE document_id=(SELECT id FROM documents WHERE path='long.md')",(full.split('UNKNOWN을')[0]+'UNKNOWN을',))
        rows=retrieve(self.lib,'레이더 UNKNOWN 의미')[0]
        self.assertTrue(any('UNKNOWN을 정상으로 단정하지 않습니다.' in r['body'] for r in rows))
        (self.root/'long.md').write_text(full+' changed',encoding='utf-8')
        rows=retrieve(self.lib,'레이더 UNKNOWN 의미')[0]
        self.assertFalse(any(r['path']=='long.md' for r in rows))

    def test_irrelevant_role_core_and_compound_are_rejected(self):
        self.assertFalse(relevance('노트북 로봇 통신 방식','카메라에서 GUI로 최신 프레임을 전달해요.'))
        b=self.bundle();raw=self.raw(b,text='Qt 화면은 PySide6로 구현해요. 그리고 화면에는 버튼이 있어요.')
        accepted,rejected=validate(raw,b,self.lib,'Qt 화면 기술',lambda p:self.fail('compound must not reach judge'),Timings())
        self.assertFalse(accepted);self.assertEqual(rejected[0]['reason'],'COMPOUND_CLAIM')

    def test_rules_normal_and_counterexamples(self):
        rows=[{'variant_id':'SOFTWARE_COMMON','evidence_status':'SOFTWARE_IMPLEMENTATION'}]
        for support,text,ok in [
            ('ESP32는 목표 손 제스처를 처리합니다.','ESP32는 손 제스처를 처리해요.',True),
            ('ESP32 목표 속도는 10cm/s입니다.','ESP32 목표 속도는 10cm/s예요.',True),
            ('ESP32 향후 설계 목표는 10cm/s입니다.','ESP32 속도는 10cm/s로 실측됐어요.',False),
            ('Qt 화면은 PySide6를 사용합니다. 모터는 미검증입니다.','Qt 화면은 PySide6를 사용해요.',True),
            ('Qt 화면은 PySide6를 사용합니다. 다만 Qt는 특정 경우에만 사용합니다.','Qt 화면은 PySide6를 사용해요.',False),
            ('ESP32 전원은 5V입니다. 모터 전원은 12V입니다.','ESP32 전원은 5V예요.',True),
            ('ESP32 전원은 5V입니다. 모터 전원은 12V입니다.','ESP32 전원은 12V예요.',False),
            ('모터 전원은 12V입니다.','모터 전원은 12A예요.',False),
        ]:
            with self.subTest(text=text,support=support):self.assertEqual(candidate_reason(text,[support],rows) is None,ok)
        for negative in ('아닙니다','아닌 상태입니다','아님','아닐 수 있습니다','미검증입니다','미지원입니다','제외합니다','불가능합니다','사용하지 않음'):
            with self.subTest(negative=negative):self.assertIsNotNone(candidate_reason('ESP32는 정상입니다.',['ESP32는 '+negative],rows))

    def test_client_reuse_budget_close_exception(self):
        requests=[];created=[];closed=[]
        def factory():
            c=NS(chat=NS(completions=NS(create=lambda **kw:NS(choices=[NS(finish_reason='stop',message=NS(content='{}',tool_calls=None))]))),close=lambda:closed.append(1));created.append(c);return c
        calls=ReusableCalls({'NVIDIA_API_KEY':'offline-test','NVIDIA_MODEL':'test-model'},reserve=lambda:requests.append(1),client_factory=factory)
        calls.diagnostic(lambda generate,verify:(generate('s',{}),generate('s',{})))
        calls.diagnostic(lambda generate,verify:generate('s',{}))
        self.assertEqual(len(created),1);self.assertEqual(len(requests),3)
        calls.close();calls.close();self.assertEqual(len(closed),1)
        with self.assertRaises(ValueError):calls.diagnostic(lambda *a:None)
        denied=ReusableCalls({'NVIDIA_API_KEY':'offline-test','NVIDIA_MODEL':'test-model'},reserve=lambda:(_ for _ in ()).throw(ValueError('budget')),client_factory=lambda:self.fail('budget denied'))
        with self.assertRaises(ValueError):denied.diagnostic(lambda g,v:g('s',{}))

    def test_source_profiles_and_user_privacy(self):
        self.doc('private.md','# Qt 기술\n\nQt 화면은 내부 비공개 기술입니다.','user_reference')
        hit=[]
        result=answer_candidate(self.lib,'Qt 화면 기술',env={'DIG_KNOWLEDGE_CLOUD':'1'},generate=lambda *a:hit.append(1))
        self.assertFalse(hit);self.assertIn({'reason':'CLOUD_POLICY'},result['verification_failures'])
        self.assertFalse(retrieve(self.lib,'V18 V19 중 현재 연결된 것은?')[0])

    def test_retirement_during_judge_and_revision_change(self):
        b=self.bundle()
        def retire(pairs):
            with self.lib._db() as db:db.execute("UPDATE documents SET status='outdated'")
            return ['ENTAILMENT']*len(pairs)
        accepted,rejected=validate(self.raw(b),b,self.lib,'Qt 화면 기술',retire,Timings())
        self.assertFalse(accepted);self.assertIn({'reason':'STALE_EVIDENCE'},rejected)

    def test_planned_and_historical_sections_need_qualifiers(self):
        for heading,reason in [('개편 설계','DESIGN_SECTION_QUALIFIER_DROPPED'),('변경 전 전체 구조','HISTORICAL_SCOPE_DROPPED')]:
            with self.subTest(heading=heading):
                b=self.bundle()
                for row in b.rows:row['heading']=heading
                accepted,rejected=validate(self.raw(b),b,self.lib,'Qt 화면 기술',lambda p:self.fail('scope dropped'),Timings())
                self.assertFalse(accepted);self.assertIn({'reason':reason},rejected)
        with self.lib._db() as db:db.execute("UPDATE documents SET status='indexed',version='new-revision'")
        accepted,rejected=validate(self.raw(b),b,self.lib,'Qt 화면 기술',lambda p:self.fail('revision changed'),Timings())
        self.assertFalse(accepted);self.assertIn({'reason':'STALE_EVIDENCE'},rejected)

    def test_profile_mixing_and_relevant_conditions_stay_blocked(self):
        rows=[{'variant_id':'PCB_V18_DESIGN','evidence_status':'CAD_DESIGN'},{'variant_id':'PCB_V19_DESIGN','evidence_status':'CAD_DESIGN'}]
        self.assertEqual(candidate_reason('ESP32는 UDP로 통신해요.',['ESP32는 UDP로 통신합니다.'],rows),'MIXED_VARIANT')
        rows=[{'variant_id':'SOFTWARE_COMMON','evidence_status':'SOFTWARE_IMPLEMENTATION'}]
        for source in ('ESP32는 CLEAR일 때만 UDP를 사용합니다.','ESP32는 UDP를 사용합니다. 다만 해당 기능은 미검증입니다.'):
            self.assertIsNotNone(candidate_reason('ESP32는 UDP를 사용해요.',[source],rows))

    def test_sdk_error_discards_client_without_retry_and_config_is_snapshotted(self):
        env={'NVIDIA_API_KEY':'offline-test','NVIDIA_MODEL':'first-model'};reserved=[];closed=[];options=[]
        def failing(**kw):options.append(kw);raise OSError('mock transport error')
        client=NS(chat=NS(completions=NS(create=failing)),close=lambda:closed.append(1))
        calls=ReusableCalls(env,reserve=lambda:reserved.append(1),client_factory=lambda:client)
        env['NVIDIA_MODEL']='second-model'
        with self.assertRaises(OSError):calls.diagnostic(lambda g,v:g('s',{}))
        self.assertEqual(len(reserved),1);self.assertEqual(len(closed),1);self.assertIsNone(calls.client)
        self.assertEqual(options[0]['model'],'first-model');calls.close()

    def test_client_serializes_whole_diagnostics_and_rejects_third_call(self):
        reserved=[];closed=[]
        client=NS(chat=NS(completions=NS(create=lambda **kw:NS(choices=[NS(finish_reason='stop',message=NS(content='{}',tool_calls=None))]))),close=lambda:closed.append(1))
        calls=ReusableCalls({'NVIDIA_API_KEY':'offline-test','NVIDIA_MODEL':'test-model'},reserve=lambda:reserved.append(1),client_factory=lambda:client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(calls.diagnostic,lambda g,v:(g('s',{}),g('s',{}))) for _ in range(2)]
            for future in futures:future.result(timeout=15)
        self.assertEqual(len(reserved),4)
        with self.assertRaises(TimeoutError):calls.diagnostic(lambda g,v:(g('s',{}),g('s',{}),g('s',{})))
        self.assertEqual(len(reserved),6);calls.close();self.assertEqual(len(closed),1)


if __name__=='__main__':unittest.main()
