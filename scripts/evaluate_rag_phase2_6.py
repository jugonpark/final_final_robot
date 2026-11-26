"""Offline-only recovery diagnostics. Network blocked, no API approval option."""
from hashlib import sha256
import json
from io import BytesIO
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import tarfile
from time import perf_counter
from unittest.mock import patch
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.evidence import select_evidence
from python_controller.knowledge.recovery.retrieval import retrieve,_git_bytes
from scripts.build_rag_phase2_dataset import FILES,REV
from scripts.analyze_rag_phase2_5 import restore
from python_controller.knowledge.verifier import verify_claims
from python_controller.knowledge.recovery.rules import candidate_reason
from python_controller.knowledge.recovery.pipeline import answer_candidate,Timings
from python_controller.knowledge.recovery.client import ReusableCalls

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/verification/rag_v3_phase2_6'
CASES=[
 ('061','우리 프로그램은 노트북이랑 로봇이 어떻게 통신해?',('UDP','명령','텔레메트리')),
 ('062','Qt 화면은 어떤 기술로 만든 거야?',('PySide6',)),
 ('063','레이더가 UNKNOWN이면 무슨 뜻이야?',('UNKNOWN','유효')),
 ('new_transport','PC에서 ESP32로 제어 메시지를 어떤 프로토콜로 전송해?',('UDP',)),
 ('new_ui','대시보드 GUI는 어떤 라이브러리로 구현했어?',('PySide6',)),
 ('new_state','UNKNOWN 상태는 센서 값이 유효하다는 뜻인가요?',('UNKNOWN','유효')),
 ('new_table','PC 통신 포트 설정을 알려줘',('8888','8889')),
]


def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def preservation():
    frozen=json.loads((OUT/'preservation_manifest.json').read_text(encoding='utf-8'))
    changed=[p for p,digest in frozen['files'].items() if sha256((ROOT/p).read_bytes()).hexdigest()!=digest]
    if changed:raise ValueError('Existing files changed: '+repr(changed))
    budget=json.loads((ROOT/'docs/verification/rag_v3_phase2/api_budget.json').read_text())
    assert len(budget['requests'])==budget['cap']==12
    return {'checked_files':len(frozen['files']),'changed_files':changed,'old_budget':12,'new_external_requests':0}


def replay():
    old=ROOT/'docs/verification/rag_v3_phase2'
    rows=[json.loads(line) for line in (old/'grounded_rag_v3.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    requests=json.loads((old/'request_audit.json').read_text())['requests'];results=[]
    for row in rows:
        historical=next((r for r in requests if r['purpose']==row['case_id']+':C_judge'),None)
        verdicts=json.loads(historical['response_text'])['verdicts'] if historical else []
        with tempfile.TemporaryDirectory() as folder:
            lib=restore(row['evidence_snapshot'],folder)
            accepted,rejected=verify_claims(row['raw_generated_claims'],row['evidence_snapshot'],lib,
                                           semantic=lambda pairs:[v['relation'] for v in verdicts])
        assert [c['text'] for c in accepted]==[c['text'] for c in row['claims']]
        assert rejected==row['verification_failures']
        candidate=[];lookup={r['id']:r for r in row['evidence_snapshot']}
        for c in row['raw_generated_claims']['claims']:
            supports=c['supports'];valid=all(s['evidence_id'] in lookup and 0<=s['quote_index']<len(lookup[s['evidence_id']]['quote_candidates']) for s in supports)
            reason=candidate_reason(c['text'],[lookup[s['evidence_id']]['quote_candidates'][s['quote_index']] for s in supports],
                                    [lookup[s['evidence_id']] for s in supports]) if valid else 'INVALID_CITATION'
            candidate.append({'text':c['text'],'rule_reason':reason,'new_semantic_judgment':None})
        results.append({'case_id':row['case_id'],'baseline_replay_matches':True,'baseline_reply':row['reply'],
                        'candidate_rules_on_original_claims':candidate,'new_candidate_answer':None,
                        'note':'Historical verdicts only reproduce original baseline; never reused to approve new claims or evidence.'})
    save('cached_replay.json',results)


def mock_diagnostic(lib):
    """Fixed mock content, not a new actual model answer or quality metric."""
    counters={'mock_reservations':0,'mock_initializations':0,'mock_closes':0}
    def reserve():counters['mock_reservations']+=1
    def create(**kwargs):
        payload=json.loads(kwargs['messages'][1]['content'])
        if 'pairs' in payload:
            content={'verdicts':[{'claim_id':p['claim_id'],'relation':'ENTAILMENT'} for p in payload['pairs']]}
        else:
            key=next(e['id'] for e in payload['evidence'] if 'PySide6' in e['text'])
            content={'claims':[{'claim_id':'mock','text':'Qt 화면은 PySide6로 구현해요.','evidence_ids':[key],'role':'core'}],'unanswered_aspects':[]}
        return NS(choices=[NS(finish_reason='stop',message=NS(content=json.dumps(content,ensure_ascii=False),tool_calls=None))])
    def factory():
        counters['mock_initializations']+=1
        return NS(chat=NS(completions=NS(create=create)),close=lambda:counters.__setitem__('mock_closes',counters['mock_closes']+1))
    timings=Timings();env={'NVIDIA_API_KEY':'offline-placeholder','NVIDIA_MODEL':'offline-mock','DIG_KNOWLEDGE_CLOUD':'1'}
    calls=ReusableCalls(env,reserve=reserve,client_factory=factory,timings=timings)
    result=calls.diagnostic(lambda generate,semantic:answer_candidate(lib,'Qt 화면은 어떤 기술로 만든 거야?',env=env,generate=generate,semantic=semantic,timings=timings))
    calls.close()
    assert result['reply']=='Qt 화면은 PySide6로 구현해요.' and result['speech_text']==result['reply']
    assert counters=={'mock_reservations':2,'mock_initializations':1,'mock_closes':1}
    save('mock_timing_sample.json',{'evidence_type':'MOCK_ONLY_NOT_API_PERFORMANCE','counters':counters,'result':result,
                                   'stage_totals_including_explicit_close_ms':timings.values,'sdk_request_timings':calls.request_timings})


def main():
    preservation();results=[]
    with patch.object(socket,'socket',side_effect=AssertionError('NETWORK_FORBIDDEN')),tempfile.TemporaryDirectory() as temp:
        # Same immutable corpus for every baseline and candidate arm.
        lib=KnowledgeLibrary(Path(temp)/'db.sqlite',ROOT);lib.vector=None
        # Disable automatic seeds: same nine explicit documents for ALL arms.
        # Workflow supplies explicit direction/port table; old raw runs unchanged.
        with patch.object(lib,'_seed',return_value=None):lib.documents()
        lib.cache=Path(temp)/'no-tokenizer'
        corpus=[]
        paths=FILES+['docs/CENTRAL_CONTROL_WORKFLOW.md']
        archive=subprocess.check_output(['git','-C',str(ROOT),'archive',REV,*paths],timeout=45)
        lib.recovery_source_bytes={}
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            snapshot={path:tar.extractfile(path).read() for path in paths}
        for path in paths:
            raw=snapshot[path]
            lib.recovery_source_bytes[(REV,path,sha256(raw).hexdigest())]=raw
            metadata=None;trust='project_doc'
            if '/hardware/' in path:
                variant={'breadboard':'BREADBOARD_PROTO','pcb-v18':'PCB_V18_DESIGN','pcb-v19':'PCB_V19_DESIGN'}[Path(path).parts[2]]
                metadata=(variant,'GIT_DOCUMENT','CAD_DESIGN' if 'PCB' in variant else 'USER_BOM_LIST',path);trust='hardware_reference'
            lib._ingest(path,raw,REV,trust,metadata=metadata)
            corpus.append({'path':path,'revision':REV,'sha256':sha256(raw).hexdigest()})
        for case_id,q,needles in CASES:
            for arm in ('fts','hybrid_unavailable','dedup_only','structure','structure_diverse'):
                start=perf_counter()
                if arm in ('fts','hybrid_unavailable','dedup_only'):
                    rows=lib.search_robot_documents(q,mode='hybrid' if arm=='hybrid_unavailable' else 'fts',limit=6)
                    if arm=='dedup_only':
                        seen=set();rows=[r for r in rows if not (r['body'] in seen or seen.add(r['body']))]
                    ev=select_evidence(rows,q)
                else:ev,_,_=retrieve(lib,q,diverse=arm=='structure_diverse')
                quotes=[v for r in ev for v in r['quote_candidates']]
                combined='\n'.join(quotes)
                results.append({'case_id':case_id,'question':q,'arm':arm,'core_marker_presence':{n:n in combined for n in needles},
                                'all_markers_present':all(n in combined for n in needles),'evidence_count':len(ev),
                                'unique_paths':len({r['path'] for r in ev}),'local_retrieval_selection_ms':(perf_counter()-start)*1000,
                                'evidence':[{'source_id':r.get('source_id',r['id']),'path':r['path'],'heading':r['heading'],'revision':r['revision'],
                                             'sha':r['sha'],'quotes':r['quote_candidates']} for r in ev]})
        save('retrieval_comparison.json',{'corpus':corpus,'same_input_all_arms':True,
             'hybrid_note':'No local dense model configured in this isolated run. Hybrid arm is FTS fallback, NOT a dense comparison.',
             'marker_note':'Evaluation-only lexical markers, not human answer success or faithfulness. Not used by candidate inference.',
             'results':results})
        # Provisional gold is evaluation-only and never supplied to inference.
        core=[json.loads(l) for l in (ROOT/'docs/verification/rag_v3_phase2_5/gold_core20.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        core_results=[]
        for case in core:
            q=case['question'];ev,_,plan=retrieve(lib,q)
            facts=[]
            for fact in case['expected_facts']:
                facts.append({'fact_id':fact['fact_id'],'required':fact['required'],'source_in_corpus':fact['source_path'] in {r['path'] for r in corpus},
                              'exact_excerpt_in_input':any(r['path']==fact['source_path'] and fact['evidence_excerpt'] in r['body'] for r in ev)})
            core_results.append({'case_id':case['case_id'],'question':q,'live_guard':plan['live'],'facts':facts})
        save('core20_provisional_retrieval.json',{'human_approved':False,'metric':'source/exact-excerpt presence only, not answer Coverage','cases':core_results})
        mock_diagnostic(lib)
        replay()
    save('preservation_check.json',preservation())
    print(json.dumps([{ 'case_id':r['case_id'],'arm':r['arm'],'present':r['all_markers_present']} for r in results],ensure_ascii=False))


if __name__=='__main__':main()
