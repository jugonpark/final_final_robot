"""Analyze actual runs; annotation-free quality cells stay null, never inferred."""
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import read_jsonl,write_json,append_jsonl,response_metrics,latency
from python_controller.knowledge.answer_metrics import prose_copy_length

OUT=Path('docs/verification/rag_v3_phase2')
def main():
    cases={r['case_id']:r for r in read_jsonl(OUT/'gold_cases.jsonl')}
    annotations={r['blind_response_id']:r for r in read_jsonl(OUT/'response_annotations.jsonl')} if (OUT/'response_annotations.jsonl').exists() else {}
    names={'A':'baseline_v2','B':'generative_rag','C':'grounded_rag_v3'}
    contextual_rows=read_jsonl(OUT/'grounded_rag_v3.jsonl') if (OUT/'grounded_rag_v3.jsonl').exists() else []
    context={row['case_id']:[r['body'] for r in row.get('evidence_snapshot',[])] for row in contextual_rows}
    metrics={};blind=[];errors=[];allrows=[]
    for arm,name in names.items():
        path=OUT/(name+'.jsonl'); rows=read_jsonl(path) if path.exists() else []
        scored=[]
        for row in rows:
            case=cases[row['case_id']]
            bid=__import__('hashlib').sha256((row['case_id']+arm).encode()).hexdigest()[:12]
            score=response_metrics(row,case,annotations.get(bid));scored.append(score)
            evidence=context.get(row['case_id'],[])
            longest=prose_copy_length(row['reply'],evidence)
            score['raw_copy_rate']=min(1,longest/max(1,len(''.join(row['reply'].split())))) if longest>=45 else 0.
            blind.append({'blind_response_id':bid,'question':row['question'],'reply':row['reply'],
                          'citation_metadata':row.get('citations',[]),'reference_facts':case['expected_facts'],
                          'annotation_status':'human_review_pending','required_fields':['final_reply_atoms','naturalness','relevance']})
            failure=row.get('failure_code')
            if failure or row.get('verification_failures') or row.get('answer_mode') in ('abstained','error','partial','unavailable'):
                errors.append({'case_id':row['case_id'],'experiment_id':arm,'question':row['question'],
                               'expected_facts':case['expected_facts'],'retrieved_evidence_ids':row['retrieved_evidence_ids'],
                               'generated_claims':row.get('raw_generated_claims'),'verification_failures':row.get('verification_failures',[]),
                               'final_reply':row['reply'],'stage':'model_api' if failure else 'validation_or_abstention',
                               'error_code':failure or ('OVER_ABSTENTION_CANDIDATE' if case['answerable'] else 'EXPECTED_ABSTENTION'),
                               'root_cause_review':'pending independent assessment; do not assume every abstention is an error',
                               'proposal':'Inspect evidence/claim/validator stage on development set only'})
        count=len(rows)
        def average(key):
            values=[r[key] for r in scored if r.get(key) is not None];return statistics.mean(values) if values else None
        annotated=[r for r in scored if r.get('factual_claims') is not None]
        atom_count=sum(r['factual_claims'] for r in annotated)
        supported_count=sum(r['supported_claims'] for r in annotated if r.get('supported_claims') is not None)
        metrics[arm]={'n':count,**{k:average(k) for k in ('faithfulness','coverage','naturalness','relevance','citation_correctness','citation_completeness','raw_copy_rate','numerical_accuracy','entity_accuracy','version_consistency','abstention_accuracy')},
                      'faithfulness_micro':supported_count/atom_count if atom_count and all(r.get('supported_claims') is not None for r in annotated) else None,
                      'substantive_answer_rate':None,'over_abstention_rate':None,'under_abstention_rate':None,
                      'decision_rejection_rate':sum(r.get('rejected_claim_count',0) for r in rows)/sum(r.get('rejected_claim_count',0)+r.get('passed_claim_count',0) for r in rows) if any(r.get('rejected_claim_count',0)+r.get('passed_claim_count',0) for r in rows) else None,
                      'latency_all_ms':latency([r['total_latency_ms'] for r in rows]),
                      'latency_success_ms':latency([r['total_latency_ms'] for r in rows if not r.get('failure_code') and r.get('answer_mode') not in ('error','unavailable')]),
                      'api_call_count_attributed':sum(r['api_call_count'] for r in rows),
                      'input_tokens':sum(r['input_tokens'] for r in rows) if rows and all(r['input_tokens'] is not None for r in rows) else None,
                      'output_tokens':sum(r['output_tokens'] for r in rows) if rows and all(r['output_tokens'] is not None for r in rows) else None,
                      'failure_rate':sum(bool(r.get('failure_code')) for r in rows)/count if count else None,
                      'fully_verified_answers':sum(r.get('verification_status')=='verified' for r in rows),
                      'quality_annotation_count':sum(s.get('factual_claims') is not None for s in scored),
                      'category_counts':{category:sum(cases[r['case_id']]['category']==category for r in rows) for category in sorted({c['category'] for c in cases.values()})},
                      'note':'B shares generation calls with C; attributed counts are not additional physical calls. Copy measured against shared actual retrieved evidence, not gold paragraphs.'}
        allrows.extend(rows)
    write_json(OUT/'metrics.json',metrics)
    import random
    random.Random(20261126).shuffle(blind)
    write_json(OUT/'blind_review.json',blind)
    # Fresh analysis files replace only generated files, never immutable raw requests/results.
    (OUT/'error_analysis.jsonl').write_text('',encoding='utf-8')
    for row in errors:append_jsonl(OUT/'error_analysis.jsonl',row)
    write_json(OUT/'tradeoff_summary.json',{'pareto_frontier':None,'selected_configuration':'unchanged Phase1 defaults',
              'reason':'Only 3 real questions and provisional/unreviewed gold; quality criteria cannot establish Pareto dominance.',
              'independent_model':'Not selected without Korean calibration/license/cost comparison',
              'risk_coverage_curve':None,'threshold_reason':'Categorical verdicts provide no calibrated confidence threshold',
              'production_optimization_applied':False,'privacy_relaxation':False})
    requests={}
    for filename in ('request_summary.json','api_requests.jsonl'):
        path=OUT/filename
        entries=(json.loads(path.read_text()) if path.suffix=='.json' else read_jsonl(path)) if path.exists() else []
        for entry in entries:requests[entry.get('request_number',(entry['purpose'],entry['payload_sha256']))]=entry
    budget=json.loads((OUT/'api_budget.json').read_text())
    write_json(OUT/'request_audit.json',{'reserved_attempts':len(budget['requests']),'cap':budget['cap'],
               'inference_requests_observed':len(requests),'metadata_requests_observed':1,
               'reservation_failed_before_http':1,'actual_http_total':len(requests)+1,
               'requests':list(requests.values()),'quota_and_pricing':None})
    figures=OUT/'figures';figures.mkdir(exist_ok=True)
    panels=[('faithfulness','Faithfulness'),('coverage','Coverage'),('naturalness','Korean naturalness'),('raw_copy_rate','Long prose copy rate'),
            ('latency','Latency P50/P95 ms'),('cost','Attributed calls / tokens'),('confusion','Semantic confusion matrix'),
            ('false_rates','False acceptance / rejection'),('faith_coverage','Faithfulness vs coverage'),('faith_latency','Faithfulness vs latency'),
            ('natural_copy','Naturalness vs copy'),('errors','Observed pipeline error counts'),('risk_coverage','Risk / coverage'),('pareto','Pareto frontier')]
    # SVG artifacts are actual data labels, not fabricated zero-valued graphs.
    import html
    vm=json.loads((OUT/'verifier_metrics.json').read_text()) if (OUT/'verifier_metrics.json').exists() else {}
    for key,title in panels:
        if key=='latency':data={a:m['latency_all_ms'] for a,m in metrics.items()}
        elif key=='cost':data={a:{k:m[k] for k in ('api_call_count_attributed','input_tokens','output_tokens')} for a,m in metrics.items()}
        elif key=='confusion':data=vm.get('confusion_matrix')
        elif key=='false_rates':data={k:vm.get(k) for k in ('false_acceptance_rate','false_rejection_rate')}
        elif key=='errors':data={a:sum(e['experiment_id']==a for e in errors) for a in names}
        elif key in ('faithfulness','coverage','naturalness','raw_copy_rate'):data={a:m[key] for a,m in metrics.items()}
        else:data=None
        lines=json.dumps(data,ensure_ascii=False,indent=2).splitlines() if data is not None else ['Not measured / no defensible frontier or calibrated thresholds.']
        height=max(160,80+18*len(lines)); content=''.join(f'<text x="20" y="{75+i*18}" fill="#cbd5e1" font-family="monospace" font-size="13">{html.escape(line)}</text>' for i,line in enumerate(lines))
        (figures/(key+'.svg')).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="980" height="{height}"><rect width="100%" height="100%" fill="#0b1727"/><text x="20" y="32" fill="white" font-family="sans-serif" font-size="22">{title}</text>{content}</svg>',encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
