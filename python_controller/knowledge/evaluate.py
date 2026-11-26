"""Reproducible retrieval comparison; no device access or implicit download."""
import argparse
import json
import math
from pathlib import Path
import statistics
from time import perf_counter, process_time
from python_controller.knowledge.library import KnowledgeLibrary, KnowledgeError
from python_controller.knowledge.vector import VectorRanker
from python_controller.knowledge.answer_metrics import score_answer, summarize_answers


def evaluate(library, cases, mode, candidates=20):
    from python_controller.knowledge.answer import answer_documents
    records=[]
    answer_records=[]
    citation_checked=citation_valid=abstain_checked=abstain_valid=0
    cpu=process_time()
    for case in cases:
        start=perf_counter()
        try:
            rows=library.search_robot_documents(case['question'],limit=20,mode=mode,candidate_limit=candidates)
            error=None
        except KnowledgeError as exc:
            rows=[]
            error=str(exc)
        answer=answer_documents(library,case['question'],use_nemotron=False)
        answer_records.append(score_answer(answer,[r['body'] for r in rows],case.get('answer_annotation'),
                                           direct_quote=case.get('direct_quote',False)))
        for citation in answer.get('citations',[]):
            citation_checked+=1
            source=library.get_document_chunk(citation['id'])
            citation_valid+=int(bool(source and source['sha']==citation['sha'] and source['path']==citation['path']))
        if case.get('kind')=='abstain':
            abstain_checked+=1
            abstain_valid+=int(answer.get('insufficient_evidence',not answer.get('citations')))
        names=[Path(row['path']).name for row in rows]
        expected=set(case['expected_documents'])
        seen=set()
        relevance=[]
        for name in names:
            relevance.append(int(name in expected and name not in seen))
            seen.add(name)
        first=next((i+1 for i,r in enumerate(relevance[:10]) if r),None)
        dcg=sum(r/math.log2(i+2) for i,r in enumerate(relevance[:5]))
        ideal=sum(1/math.log2(i+2) for i in range(min(5,len(expected))))
        records.append(dict(case,documents=names,error=error,ms=1000*(perf_counter()-start),
            recall5=len(expected.intersection(names[:5]))/len(expected) if expected else None,
            recall20=len(expected.intersection(names[:20]))/len(expected) if expected else None,
            reciprocal_rank=1/first if first else 0.,ndcg5=dcg/ideal if ideal else 0.,
            rerank_status=sorted({r.get('rerank_status','disabled') for r in rows})))
    eligible=[r for r in records if r['kind']=='retrieval']
    latency=sorted(r['ms'] for r in records)
    def mean(key):
        return statistics.mean(r[key] for r in eligible) if eligible else None
    try:
        import psutil
        rss=psutil.Process().memory_info().rss
    except ImportError:
        rss=None
    return dict(mode=mode,candidates=candidates,count=len(records),retrieval_count=len(eligible),
        recall_at_5=mean('recall5'),recall_at_20=mean('recall20'),mrr_at_10=mean('reciprocal_rank'),
        ndcg_at_5=mean('ndcg5'),mean_ms=statistics.mean(latency),p50_ms=statistics.median(latency),
        p95_ms=latency[math.ceil(len(latency)*.95)-1],cpu_seconds=process_time()-cpu,rss_bytes=rss,
        citation_accuracy=citation_valid/citation_checked if citation_checked else None,citations_checked=citation_checked,
        abstention_accuracy=abstain_valid/abstain_checked if abstain_checked else None,answer_faithfulness=None,
        answer_metrics=summarize_answers(answer_records),
        notes='Citation accuracy verifies source ID/path/hash, not semantic entailment. Semantic metrics require answer_annotation; unmeasured metrics remain null. Reranker measurements are separate.',results=records)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('.tmp/rag-v2-evaluation.sqlite3'))
    parser.add_argument('--cache',type=Path,default=Path('logs/knowledge-models'))
    parser.add_argument('--vector',action='store_true')
    parser.add_argument('--reranker',action='store_true')
    parser.add_argument('--candidates',type=int,choices=(10,20,40),default=20)
    parser.add_argument('--cases',type=Path,default=Path(__file__).with_name('evaluation_cases.json'))
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    library=KnowledgeLibrary(args.database,Path(__file__).resolve().parents[2])
    library.cache=args.cache
    library.vector=None
    library.documents()
    cases=json.loads(args.cases.read_text(encoding='utf-8'))
    reports=[evaluate(library,cases,'fts',args.candidates)]
    if args.vector or args.reranker:
        library.vector=VectorRanker(args.cache)
        start=perf_counter()
        prepared=library.prepare_embeddings(wait=True)
        index_seconds=perf_counter()-start
        report=evaluate(library,cases,'hybrid',args.candidates)
        report.update(index_seconds=index_seconds,index_jobs=prepared)
        reports.append(report)
    if args.reranker:
        reports.append(evaluate(library,cases,'rerank',args.candidates))
    text=json.dumps(reports,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,encoding='utf-8')
    print(text)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
