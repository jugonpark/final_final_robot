"""Offline/live evaluation utilities. Never imported by robot execution code."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import statistics
from time import perf_counter


def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def append_jsonl(path, row):
    with Path(path).open('a',encoding='utf-8') as f:
        f.write(json.dumps(row,ensure_ascii=False)+'\n')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def sensitive_payload(value, depth=0):
    from python_controller.knowledge.library import SECRET
    if depth>12:
        return True
    if isinstance(value,str):
        if SECRET.search(value):return True
        if value.lstrip().startswith(('{','[')):
            try:return sensitive_payload(json.loads(value),depth+1)
            except (ValueError,TypeError):pass
    elif isinstance(value,list):
        return any(sensitive_payload(item,depth+1) for item in value)
    elif isinstance(value,dict):
        import re
        return any((bool(item) and re.search(r'PASSWORD|PASSWD|API_KEY|SECRET_KEY|ACCESS_TOKEN|WIFI_SSID',key,re.I)) or
                   sensitive_payload(item,depth+1) for key,item in value.items())
    return False


class Budget:
    """Persistent conservative cap: failed attempts count, no implicit retries."""
    def __init__(self,path,cap=12):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.cap=min(12,max(0,cap))

    def reserve(self,purpose):
        lock=self.path.with_suffix('.lock')
        with lock.open('x') as handle:
            try:
                state=json.loads(self.path.read_text()) if self.path.exists() else {'cap':self.cap,'requests':[]}
                state['cap']=min(state['cap'],self.cap)
                if len(state['requests'])>=state['cap']:
                    raise RuntimeError('EXPERIMENT_BUDGET_EXHAUSTED')
                state['requests'].append({'number':len(state['requests'])+1,'purpose':purpose})
                temp=self.path.with_suffix('.new')
                write_json(temp,state)
                temp.replace(self.path)
                return len(state['requests'])
            finally:
                handle.close()  # Windows cannot unlink an open file.
                lock.unlink(missing_ok=True)


class RecordingAPI:
    """Wrap SDK resources without changing NvidiaPlanner's validation/transport."""
    def __init__(self,budget,env):
        from openai import OpenAI
        self.factory=OpenAI; self.budget=budget; self.env=env
        self.records=[]; self.purpose='unassigned'

    def client(self,**kwargs):
        client=self.factory(**kwargs)
        create=client.chat.completions.create
        def recorded(**params):
            payload=json.dumps(params.get('messages',[]),ensure_ascii=False)
            if sensitive_payload(params.get('messages',[])):
                raise ValueError('SENSITIVE_EVALUATION_PAYLOAD')
            number=self.budget.reserve(self.purpose)
            row={'request_number':number,'purpose':self.purpose,'model_id':params.get('model'),
                 'max_tokens':params.get('max_tokens'),'temperature':params.get('temperature'),
                 'payload_sha256':hashlib.sha256(payload.encode()).hexdigest(),
                 'input_tokens':None,'output_tokens':None,'reasoning_tokens':None,'failure_code':None}
            start=perf_counter()
            try:
                result=create(**params)
                usage=getattr(result,'usage',None)
                row.update(input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None),
                           reasoning_tokens=getattr(getattr(usage,'completion_tokens_details',None),'reasoning_tokens',None))
                row['response_text']=getattr(result.choices[0].message,'content',None) if result.choices else None
                return result
            except Exception as exc:
                row['failure_code']=f'HTTP_{exc.status_code}' if hasattr(exc,'status_code') else type(exc).__name__
                raise
            finally:
                row['latency_ms']=1000*(perf_counter()-start)
                self.records.append(row)
        client.chat.completions.create=recorded
        return client

    @contextmanager
    def installed(self):
        from unittest.mock import patch
        with patch('openai.OpenAI',self.client),patch('python_controller.llm.nvidia_client.OpenAI',self.client):
            yield self


def verifier_metrics(rows):
    labels=('ENTAILMENT','CONTRADICTION','NEUTRAL')
    matrix={g:{p:0 for p in (*labels,'UNAVAILABLE')} for g in labels}
    for row in rows:
        matrix[row['gold']][row['prediction'] if row['prediction'] in labels else 'UNAVAILABLE']+=1
    count=len(rows); scores={}
    for label in labels:
        tp=matrix[label][label]; actual=sum(matrix[label].values()); pred=sum(matrix[g][label] for g in labels)
        precision=tp/pred if pred else 0.; recall=tp/actual if actual else 0.
        scores[label]={'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0.,'n':actual}
    negatives=[r for r in rows if r['gold']!='ENTAILMENT']; positives=[r for r in rows if r['gold']=='ENTAILMENT']
    return {'n':count,'accuracy':sum(r['gold']==r['prediction'] for r in rows)/count if count else None,
            'macro_f1':statistics.mean(s['f1'] for s in scores.values()) if count else None,
            'class_metrics':scores,'confusion_matrix':matrix,
            'false_acceptance_rate':sum(r['prediction']=='ENTAILMENT' for r in negatives)/len(negatives) if negatives else None,
            'false_rejection_rate':sum(r['prediction']!='ENTAILMENT' for r in positives)/len(positives) if positives else None,
            'unavailable_rate':sum(r['prediction']=='UNAVAILABLE' for r in rows)/count if count else None}


def response_metrics(row,case,annotation=None):
    """Only independent annotations of FINAL reply atoms can establish truth."""
    annotation=annotation or {}
    atoms=annotation.get('final_reply_atoms')
    complete=atoms is not None and all(type(a.get('supported')) is bool for a in atoms)
    supported=sum(a['supported'] for a in atoms) if complete else None
    required={f['fact_id'] for f in case['expected_facts'] if f['required']}
    covered={i for a in (atoms or []) if a.get('supported') is True for i in a.get('gold_ids',[])}
    def labelled_accuracy(key):
        values=[a[key] for a in (atoms or []) if type(a.get(key)) is bool]
        return statistics.mean(values) if values else None
    return {'faithfulness':supported/len(atoms) if atoms and complete else None,'supported_claims':supported,
            'factual_claims':len(atoms) if atoms is not None else None,
            'coverage':len(covered&required)/len(required) if required and complete else (0. if required and not row['reply'] else None),
            'naturalness':annotation.get('naturalness'),'relevance':annotation.get('relevance'),
            'numerical_accuracy':labelled_accuracy('numerical_correct'),'entity_accuracy':labelled_accuracy('entity_correct'),
            'version_consistency':labelled_accuracy('version_correct'),
            'abstention_accuracy':annotation.get('appropriate_abstention'),
            'citation_correctness':statistics.mean(a['citation_correct'] for a in atoms) if atoms and all(type(a.get('citation_correct')) is bool for a in atoms) else None,
            'citation_completeness':statistics.mean(a.get('has_appropriate_citation',False) for a in atoms) if atoms is not None and atoms else None,
            'answered':bool(atoms) if atoms is not None else None,
            'total_latency_ms':row.get('total_latency_ms')}


def latency(values):
    values=sorted(v for v in values if v is not None)
    return {'n':len(values),'p50':statistics.median(values) if values else None,
            **{f'p{p}':values[math.ceil(p/100*len(values))-1] if values else None for p in (95,99)}}
