"""One balanced 30-pair live batch; labels stay local. No model installation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import Budget,RecordingAPI,read_jsonl,write_json,append_jsonl,verifier_metrics,digest
from python_controller.knowledge.generation import RAGCalls
from python_controller.llm_cli import load_environment

OUT=Path('docs/verification/rag_v3_phase2')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--model');args=parser.parse_args()
    if not args.live:raise ValueError('No mock scores substituted: use --live for real verification')
    env=load_environment(); env={**env,'DIG_KNOWLEDGE_CLOUD':'1','DIG_KNOWLEDGE_SEMANTIC':'nemotron','DIG_KNOWLEDGE_MAX_TOKENS':'4096'}
    environment=json.loads((OUT/'environment.json').read_text())
    if not environment.get('repository_public'):raise ValueError('Public-source preflight required')
    if args.model:
        if args.model not in environment.get('available_model_ids',[]):raise ValueError('Model not returned by catalog')
        env['NVIDIA_MODEL']=args.model
    path=OUT/('independent_verifier_results.jsonl' if args.model else 'verifier_results.jsonl')
    if path.exists():raise ValueError('Results exist; no implicit rerun or extra charges')
    rows=read_jsonl(OUT/'semantic_cases.jsonl')
    if digest(rows)!=json.loads((OUT/'dataset_manifest.json').read_text())['semantic_sha256']:
        raise ValueError('FROZEN_SEMANTIC_HASH_MISMATCH')
    selected=[]
    for label in ('ENTAILMENT','CONTRADICTION','NEUTRAL'):
        candidates=[r for r in rows if r['gold']==label and r['dataset_split']=='development']
        selected.extend(candidates[:10])
    pairs=[{'text':r['claim'],'quotes':[r['evidence']],'context':[r['evidence']]} for r in selected]
    api=RecordingAPI(Budget(OUT/'api_budget.json'),env);api.purpose='semantic_verifier_balanced_batch'
    failure=None
    with api.installed():
        try:predictions=RAGCalls(env).verify(pairs)
        except Exception as exc:predictions=['UNAVAILABLE']*len(pairs);failure=type(exc).__name__
    for row,prediction in zip(selected,predictions):
        append_jsonl(path,{'pair_id':row['pair_id'],'gold':row['gold'],'prediction':prediction,
                          'dataset_split':row['dataset_split'],'model_id':env.get('NVIDIA_MODEL'),
                          'failure_code':failure,'batch_latency_ms':sum(r['latency_ms'] for r in api.records),
                          'latency_note':'One batched request; not per-pair latency','gold_status':'provisional_gold'})
    for request in api.records:append_jsonl(OUT/'api_requests.jsonl',request)
    write_json(OUT/'verifier_metrics.json',verifier_metrics(read_jsonl(path)))
    print(json.dumps(verifier_metrics(read_jsonl(path)),ensure_ascii=False))
if __name__=='__main__':main()
