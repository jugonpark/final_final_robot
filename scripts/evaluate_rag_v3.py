"""Isolated Phase 2 A/B/C evaluation; --live is explicit and globally capped at 12."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import Budget,RecordingAPI,read_jsonl,write_json,append_jsonl,digest
from python_controller.knowledge import answer as v3
from python_controller.knowledge.evidence import select_evidence,current,citations
from python_controller.knowledge.verifier import Claims
from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.llm_cli import load_environment
from build_rag_phase2_dataset import FILES,REV,OUT,ROOT


def citation_only(raw,evidence,library):
    parsed=Claims.model_validate(raw)
    lookup={row['id']:row for row in evidence}; claims=[]
    for claim in parsed.claims:
        if all(s.evidence_id in lookup and s.quote_index<len(lookup[s.evidence_id]['quote_candidates']) and
               current(library,lookup[s.evidence_id]) for s in claim.supports):
            claims.append({'claim_id':f'c{len(claims)+1}','text':claim.text,
                           'evidence_ids':list(dict.fromkeys(s.evidence_id for s in claim.supports)),
                           'supports':[s.model_dump() for s in claim.supports]})
    return {'reply':'\n\n'.join(c['text'] for c in claims),'claims':claims,'citations':citations(evidence,claims),
            'answer_mode':'experimental_unverified','verification_status':'citation_existence_only'}


def record(case,arm,result,elapsed,stages,requests,rows):
    usage={key:(sum(r[key] for r in requests) if requests and all(r.get(key) is not None for r in requests) else (0 if not requests else None))
           for key in ('input_tokens','output_tokens','reasoning_tokens')}
    return {'case_id':case['case_id'],'experiment_id':arm,'dataset_split':case['dataset_split'],
            'question':case['question'],'model_id':requests[0]['model_id'] if requests else None,
            'model_configuration':{'max_tokens':2048,'thinking':False,'retries':0},'retrieval_mode':'fts',
            **{key:stages.get(key) for key in ('retrieval_latency_ms','generation_latency_ms','deterministic_validation_latency_ms','semantic_verification_latency_ms','rendering_latency_ms')},
            'total_latency_ms':elapsed,'first_verified_answer_ms':elapsed if result.get('verification_status')=='verified' else None,
            **usage,'api_call_count':len(requests),'retrieved_evidence_ids':[r['id'] for r in rows],
            'passed_claim_count':len(result.get('claims',[])),'rejected_claim_count':len(result.get('verification_failures',[])),
            'failure_code':next((r['failure_code'] for r in requests if r.get('failure_code')),None),
            **result}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    env=load_environment(); env={**env,'DIG_KNOWLEDGE_CLOUD':'1' if args.live else '0','DIG_KNOWLEDGE_VECTOR':'0',
                                'DIG_KNOWLEDGE_RERANK':'0','DIG_KNOWLEDGE_MAX_TOKENS':'2048','DIG_KNOWLEDGE_SEMANTIC':'nemotron'}
    if args.live:
        environment=json.loads((OUT/'environment.json').read_text(encoding='utf-8'))
        if not environment.get('repository_public') or not environment.get('key_present'):
            raise ValueError('PUBLIC_REPOSITORY_AND_API_PREFLIGHT_REQUIRED')
    api=RecordingAPI(Budget(OUT/'api_budget.json'),env)
    cases=read_jsonl(OUT/'gold_cases.jsonl')
    if digest(cases)!=json.loads((OUT/'dataset_manifest.json').read_text())['gold_sha256']:
        raise ValueError('FROZEN_GOLD_HASH_MISMATCH')
    selected=[c for c in cases if c['case_id'] in ('RAG-KO-061','RAG-KO-062','RAG-KO-063')] if args.live else cases
    version=Path('.tmp/rag-phase2-baseline/v2_answer.py')
    version.write_bytes(subprocess.check_output(['git','show',REV+':python_controller/knowledge/answer.py']))
    spec=importlib.util.spec_from_file_location('phase2_v2',version);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    manifest={'baseline_commit':REV,'branch':subprocess.check_output(['git','branch','--show-current']).decode().strip(),
              'phase1_snapshot_sha256':digest(json.loads(Path('.tmp/rag-phase2-baseline/files.json').read_text())),
              'dataset':json.loads((OUT/'dataset_manifest.json').read_text()),'live_selected_cases':['RAG-KO-061','RAG-KO-062','RAG-KO-063'],
              'api_cap_including_metadata_and_failed_reservations':12,'prompt_sha256':digest(v3.SYSTEM),
              'metrics_frozen_before_outputs':True,'gold_excluded_from_model_payload':True,
              'B_protocol':'same retrieved evidence and exact generated claims captured from live C; citation-only offline counterfactual, never an operational mode',
              'review_status':'provisional_gold/human_review_pending','operational_defaults_changed':False}
    if not (OUT/'experiment_manifest.json').exists():write_json(OUT/'experiment_manifest.json',manifest)
    with tempfile.TemporaryDirectory() as temp:
        library=KnowledgeLibrary(Path(temp)/'isolated.db',Path(temp));library.vector=None;library.documents()
        for path in FILES:
            raw=subprocess.check_output(['git','show',REV+':'+path])
            if '/hardware/' in path:
                variant={'breadboard':'BREADBOARD_PROTO','pcb-v18':'PCB_V18_DESIGN','pcb-v19':'PCB_V19_DESIGN'}[Path(path).parts[2]]
                library._ingest(path,raw,REV,'hardware_reference',metadata=(variant,'GIT_DOCUMENT','CAD_DESIGN' if 'PCB' in variant else 'USER_BOM_LIST',path))
            else:library._ingest(path,raw,REV,'project_doc')
        prefix='' if args.live else 'offline_'
        for case in selected:
            if args.live and case['privacy_policy']!='project_doc_only':continue
            cpath=OUT/(prefix+'grounded_rag_v3.jsonl')
            if cpath.exists() and any(r['case_id']==case['case_id'] for r in read_jsonl(cpath)):continue
            q=case.get('resolved_question',case['question']);rows=[];stages={};capture={}
            search=library.search_robot_documents
            def measured(*a,**kw):
                start=perf_counter(); found=search(*a,**kw)
                rows[:]=found; stages['retrieval_latency_ms']=1000*(perf_counter()-start);return found
            with api.installed(),patch('python_controller.llm_cli.load_environment',return_value=env),patch.object(library,'search_robot_documents',side_effect=measured):
                before=len(api.records);api.purpose=case['case_id']+':A'
                start=perf_counter()
                try: a=old.answer_documents(library,q,use_nemotron=args.live)
                except Exception as exc:a={'reply':'','claims':[],'citations':[],'answer_mode':'error','verification_status':'unavailable','error_type':type(exc).__name__}
                ar=record(case,'A',a,1000*(perf_counter()-start),stages,api.records[before:],rows)
                append_jsonl(OUT/(prefix+'baseline_v2.jsonl'),ar)
                stages={};before=len(api.records)
                from python_controller.knowledge.generation import RAGCalls
                generate=RAGCalls.generate;verify=RAGCalls.verify;validate=v3.verify_claims;render=v3.render_reply
                def validation(*a,**kw):
                    t=perf_counter()
                    try:return validate(*a,**kw)
                    finally:stages['deterministic_validation_latency_ms']=max(0,1000*(perf_counter()-t)-stages.get('semantic_verification_latency_ms',0))
                def rendering(*a,**kw):
                    t=perf_counter()
                    try:return render(*a,**kw)
                    finally:stages['rendering_latency_ms']=1000*(perf_counter()-t)
                def gen(self,*a,**kw):
                    api.purpose=case['case_id']+':C_generator';t=perf_counter()
                    try:
                        raw=generate(self,*a,**kw);capture['raw']=raw;capture['evidence']=a[1];return raw
                    finally:stages['generation_latency_ms']=1000*(perf_counter()-t)
                def judge(self,*a,**kw):
                    api.purpose=case['case_id']+':C_judge';t=perf_counter()
                    try:return verify(self,*a,**kw)
                    finally:stages['semantic_verification_latency_ms']=1000*(perf_counter()-t)
                start=perf_counter()
                with patch.object(RAGCalls,'generate',gen),patch.object(RAGCalls,'verify',judge),patch.object(v3,'verify_claims',validation),patch.object(v3,'render_reply',rendering):
                    c=v3.answer_documents(library,q,use_nemotron=args.live)
                elapsed=1000*(perf_counter()-start)
                cr=record(case,'C',c,elapsed,stages,api.records[before:],rows)
                cr['raw_generated_claims']=capture.get('raw');cr['evidence_snapshot']=select_evidence(rows,q)
                append_jsonl(cpath,cr)
                if 'raw' in capture:
                    t=perf_counter()
                    try:b=citation_only(capture['raw'],capture['evidence'],library)
                    except Exception:b={'reply':'','claims':[],'citations':[],'answer_mode':'error','verification_status':'invalid_model_output'}
                    validation=1000*(perf_counter()-t)
                    gen_requests=[r for r in api.records[before:] if r['purpose'].endswith('generator')]
                    br=record(case,'B',b,(stages.get('retrieval_latency_ms') or 0)+(stages.get('generation_latency_ms') or 0)+validation,
                              {**stages,'semantic_verification_latency_ms':0,'deterministic_validation_latency_ms':validation},gen_requests,rows)
                else:
                    br=record(case,'B',{'reply':'','claims':[],'citations':[],'answer_mode':'unavailable','verification_status':'generation_not_available'},
                              elapsed,stages,api.records[before:before+1],rows)
                br['paired_generation_replay']=True
                append_jsonl(OUT/(prefix+'generative_rag.jsonl'),br)
                for request in api.records[before:]:append_jsonl(OUT/'api_requests.jsonl',request)
                print(case['case_id'],c['answer_mode'],cr['api_call_count'],round(elapsed),flush=True)
    # A calls are also retained, including failed/timeout attempts.
    summary=OUT/(prefix+'request_summary.json')
    previous=json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else []
    write_json(summary,previous+api.records)  # A resume with no new calls must not erase prior usage.
if __name__=='__main__':main()
