"""Development-only target/design qualifier experiment; no production mutation."""
import json
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import Budget,RecordingAPI,read_jsonl,write_json,append_jsonl
from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.generation import RAGCalls
from python_controller.knowledge.verifier import verify_claims
from python_controller.llm_cli import load_environment

OUT=Path('docs/verification/rag_v3_phase2')
def main():
    output=OUT/'adaptive_candidate.json'
    if output.exists():raise ValueError('No implicit paid rerun')
    row=next(r for r in read_jsonl(OUT/'grounded_rag_v3.jsonl') if r['case_id']=='RAG-KO-061')
    if row['dataset_split']!='development':raise ValueError('Candidate tuning restricted to development')
    evidence=row['evidence_snapshot']
    if any(r['trust']!='project_doc' for r in evidence):raise ValueError('No private evidence')
    candidate=re.compile(r'설계|(?:개발|성능|연구|제작)\s*목표|목표(?:치|입니다|이다)|예정|계획|CAD|SOURCE_CODE_ONLY|BOM|소스 기준',re.I)
    env={**load_environment(),'DIG_KNOWLEDGE_CLOUD':'1','DIG_KNOWLEDGE_SEMANTIC':'nemotron'}
    api=RecordingAPI(Budget(OUT/'api_budget.json'),env);api.purpose='development_target_design_candidate_judge'
    with tempfile.TemporaryDirectory() as tmp:
        library=KnowledgeLibrary(Path(tmp)/'snapshot.db',Path(tmp));library.vector=None;library.documents()
        with library._db() as db:
            for r in evidence:
                db.execute('INSERT OR IGNORE INTO documents(id,path,sha,version,trust,status,variant_id,source_digest) VALUES (?,?,?,?,?,?,?,?)',
                           (r['document_id'],r['path'],r['sha'],r['version'],r['trust'],'indexed',r['variant_id'],r['source_sha256']))
                db.execute('INSERT INTO chunks(id,document_id,heading,page,line,body,active) VALUES (?,?,?,?,?,?,1)',
                           (r['id'],r['document_id'],r['heading'],r['page'],r['line'],r['body']))
        with api.installed(),patch('python_controller.knowledge.verifier.DESIGN',candidate):
            claims,rejected=verify_claims(row['raw_generated_claims'],evidence,library,semantic=RAGCalls(env).verify)
    write_json(output,{'case_id':row['case_id'],'split':'development','policy':'contextual target/design qualifier experiment',
                      'before_claims':row['claims'],'before_rejections':row['verification_failures'],
                      'after_claims':claims,'after_rejections':rejected,'requests':api.records,
                      'production_default_changed':False,'held_out_improvement_proven':False})
    for request in api.records:append_jsonl(OUT/'api_requests.jsonl',request)
    print('Development candidate:',len(row['claims']),'->',len(claims),'accepted; not deployed')
if __name__=='__main__':main()
