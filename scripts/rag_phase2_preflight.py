"""Explicit metadata-only NVIDIA preflight, consumes one of the 12 allowed calls."""
import json
import os
from pathlib import Path
import platform
import sys
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import Budget,write_json
from python_controller.llm_cli import load_environment

def main():
    env=load_environment(); output=Path('docs/verification/rag_v3_phase2'); output.mkdir(parents=True,exist_ok=True)
    report={'configured_model':env.get('NVIDIA_MODEL'),'base_url':env.get('NVIDIA_BASE_URL'),
            'key_present':bool(env.get('NVIDIA_API_KEY')),'python':platform.python_version(),
            'processor':platform.processor(),'logical_cpus':os.cpu_count(),
            'quota':None,'pricing':None,'metadata_only':True}
    try:
        import psutil
        report['ram_bytes']=psutil.virtual_memory().total
    except ImportError:
        report['ram_bytes']=None
    with urlopen(Request('https://api.github.com/repos/jugonpark/final_final_robot',headers={'User-Agent':'DIG-RAG-evaluation'}),timeout=20) as response:
        report['repository_public']=json.load(response).get('private') is False
    if env.get('NVIDIA_BASE_URL','https://integrate.api.nvidia.com/v1').rstrip('/')!='https://integrate.api.nvidia.com/v1':
        raise ValueError('EXPERIMENT_REQUIRES_OFFICIAL_NVIDIA_ENDPOINT')
    if report['key_present']:
        from openai import OpenAI
        Budget(output/'api_budget.json',12).reserve('metadata_models_preflight')
        try:
            with OpenAI(api_key=env['NVIDIA_API_KEY'],base_url='https://integrate.api.nvidia.com/v1',timeout=20,max_retries=0) as client:
                raw=client.models.with_raw_response.list()
                report['available_model_ids']=sorted(row.id for row in raw.parse().data)
                report['rate_limit_headers']={k:v for k,v in raw.headers.items() if k.lower().startswith('x-ratelimit')}
        except Exception as exc:
            report['failure_code']=f'HTTP_{exc.status_code}' if hasattr(exc,'status_code') else type(exc).__name__
    write_json(output/'environment.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='available_model_ids'},ensure_ascii=False))
if __name__=='__main__':
    main()
