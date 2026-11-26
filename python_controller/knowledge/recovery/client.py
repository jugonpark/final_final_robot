"""Explicit, budget-gated SDK reuse candidate. Never created by production code."""
import json
from threading import RLock
from time import monotonic

from python_controller.knowledge.generation import JUDGE_SYSTEM
from python_controller.knowledge.recovery.pipeline import Timings
from python_controller.planning.validator import strict_json_object


class ReusableCalls:
    """One immutable configuration, serialized client, explicit close lifecycle.

    reserve must durably authorize/count EVERY SDK create before network dispatch.
    No ledger is supplied or created here. No retries, models lookup or streaming.
    """
    def __init__(self,env,*,reserve,client_factory=None,timings=None):
        if not callable(reserve):raise ValueError('Explicit budget reservation required')
        from python_controller.llm.nvidia_client import NvidiaPlanner,DEFAULT_BASE_URL
        self.planner=NvidiaPlanner(api_key=env.get('NVIDIA_API_KEY'),model=env.get('NVIDIA_MODEL'),base_url=env.get('NVIDIA_BASE_URL',DEFAULT_BASE_URL))
        self.reserve=reserve;self.factory=client_factory;self.timings=timings or Timings()
        self.lock=RLock();self.client=None;self.closed=False
        self.request_timings=[]
        self.timeout=max(5.,min(180.,float(env.get('DIG_KNOWLEDGE_TIMEOUT_S',90))))
        self.limit=max(256,min(2048,int(env.get('DIG_KNOWLEDGE_MAX_TOKENS',1024))))

    def complete(self,system,payload):
        with self.lock:
            if self.closed:raise ValueError('CLIENT_CLOSED')
            # A request scope's shared deadline is owned by diagnostic().
            if not hasattr(self,'deadline'):raise ValueError('Explicit diagnostic scope required')
            remaining=self.deadline-monotonic()
            if self.calls>=2 or remaining<1:raise TimeoutError('RAG_BUDGET_EXHAUSTED')
            with self.timings.stage('request_construction_ms'):
                messages=[{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
                options=self.planner._request_options();options['max_tokens']=self.limit
                options['timeout']=min(remaining,self.timeout*.6) if self.calls==0 else remaining
            # Reservation failure causes no SDK initialization or request.
            self.reserve();self.calls+=1
            before=dict(self.timings.values);started=monotonic()
            try:
                if self.client is None:
                    with self.timings.stage('sdk_initialization_ms'):
                        if self.factory is None:
                            from openai import OpenAI
                            self.client=OpenAI(api_key=self.planner.api_key,base_url=self.planner.base_url,timeout=self.timeout,max_retries=0)
                        else:self.client=self.factory()
                remaining=self.deadline-monotonic()
                if remaining<1:raise TimeoutError('RAG_DEADLINE')
                options['timeout']=min(options['timeout'],remaining)
                with self.timings.stage('sdk_create_ms'):
                    result=self.client.chat.completions.create(messages=messages,**options)
                with self.timings.stage('response_parsing_ms'):
                    from python_controller.llm.nvidia_client import _completion_content
                    content=_completion_content(result)
                    if self.planner.api_key in content:raise ValueError('UNSAFE_RESPONSE')
                    return strict_json_object(content)
            except Exception:
                # Broken transport is discarded; no automatic retry.
                self._close_client();raise
            finally:
                self.request_timings.append({'call_in_diagnostic':self.calls,
                    'sdk_initialization_ms':self.timings.values.get('sdk_initialization_ms',0)-before.get('sdk_initialization_ms',0),
                    'sdk_create_ms':self.timings.values.get('sdk_create_ms',0)-before.get('sdk_create_ms',0),
                    'response_parsing_ms':self.timings.values.get('response_parsing_ms',0)-before.get('response_parsing_ms',0),
                    'client_close_ms':self.timings.values.get('client_close_ms',0)-before.get('client_close_ms',0),
                    'request_after_reservation_ms':(monotonic()-started)*1000})

    def verify(self,pairs):
        raw=self.complete(JUDGE_SYSTEM,{'pairs':[{'claim_id':f'c{i+1}','claim':p['text'],'supports':p['quotes'],'scope_context':p['context']} for i,p in enumerate(pairs)]})
        rows=raw.get('verdicts')
        if set(raw)!={'verdicts'} or not isinstance(rows,list) or len(rows)!=len(pairs):return ['UNAVAILABLE']*len(pairs)
        if any(not isinstance(r,dict) or set(r)!={'claim_id','relation'} or r['claim_id']!=f'c{i+1}' or r['relation'] not in ('ENTAILMENT','NEUTRAL','CONTRADICTION','UNAVAILABLE') for i,r in enumerate(rows)):
            return ['UNAVAILABLE']*len(pairs)
        return [r['relation'] for r in rows]

    def diagnostic(self,callback):
        # Lock across generation+judge so another request cannot steal its budget.
        with self.lock:
            if self.closed:raise ValueError('CLIENT_CLOSED')
            self.deadline=monotonic()+self.timeout;self.calls=0
            try:return callback(self.complete,self.verify)
            finally:del self.deadline

    def _close_client(self):
        if self.client is not None:
            client,self.client=self.client,None
            with self.timings.stage('client_close_ms'):client.close()

    def close(self):
        with self.lock:
            self.closed=True;self._close_client()
