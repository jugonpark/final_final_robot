"""Audit provisional labels; never silently repair frozen gold after seeing replies."""
from collections import Counter,defaultdict
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge.experiments import read_jsonl,write_json

OUT=Path('docs/verification/rag_v3_phase2')
def main():
    cases=read_jsonl(OUT/'gold_cases.jsonl'); issues=[]; groups=defaultdict(set);verified=0
    for row in cases:
        groups[row['semantic_group']].add(row['dataset_split'])
        for fact in row['expected_facts']:
            text=subprocess.check_output(['git','show',fact['source_revision']+':'+fact['source_path']]).decode('utf-8-sig')
            if fact['evidence_excerpt'] not in text:issues.append({'case_id':row['case_id'],'issue':'EXCERPT_INTEGRITY_REVIEW'})
            else:verified+=1
            if len(re.split(r'(?<=[.!?])\s+|\n',fact['statement']))>1:
                issues.append({'case_id':row['case_id'],'issue':'MULTI_FACT_PARAGRAPH_NEEDS_ATOMIC_GOLD_REVIEW'})
    # These were discovered by source inspection. Do not change the original dataset/hash.
    issues.append({'case_id':'RAG-KO-061','issue':'GOLD_RELEVANCE_ERROR',
                   'reason':'Requested UDP table needle was excluded by prose-card filtering; builder silently fell back to unrelated provenance paragraph.',
                   'resolution':'Exclude preregistered coverage scoring; revise in a separately versioned dataset before further confirmatory experiments.'})
    issues.append({'case_id':'RAG-KO-062','issue':'GOLD_COMPLETENESS_PENDING',
                   'reason':'Qt installation excerpt does not specify actual PySide6 binding; needed fact is in ARCHITECTURE.md.'})
    write_json(OUT/'gold_audit.json',{'case_count':len(cases),'excerpt_integrity_checked':verified,
               'category_counts':dict(Counter(c['category'] for c in cases)),
               'cross_split_document_family_groups':[g for g,v in groups.items() if len(v)>1],
               'cross_document_paraphrase_equivalence_review':'human_review_pending',
               'issues':issues,'human_reviewed':False,'confirmatory_quality_metrics_ready':False,
               'source_integrity_does_not_establish_gold_relevance_or_completeness':True})
    print('Gold audit: integrity checked; atomicity/relevance/human review remain pending')
if __name__=='__main__':main()
