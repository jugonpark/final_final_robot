"""Scoped hybrid retrieval. No control tools or passage embedding work."""
import logging
import os
import re

logger = logging.getLogger(__name__)


def combine(library, db, query, lexical, *, variant=None, limit=6, mode=None, candidate_limit=20):
    from python_controller.knowledge.library import SOURCES
    mode = mode or ('hybrid' if library.vector else 'fts')
    if mode not in ('fts', 'hybrid', 'rerank'):
        raise ValueError('Unknown retrieval mode')
    ranked = []
    if mode != 'fts' and library.vector:
        try:
            ranked = library.vector.rank(db, query, candidate_limit, variant=variant)
        except Exception as exc:
            logger.warning('Dense search unavailable: %s', type(exc).__name__)
    scores = {row['id']: 1/(60+rank+1) for rank,row in enumerate(lexical)}
    for rank, identifier in enumerate(ranked):
        scores[identifier] = scores.get(identifier, 0)+1/(60+rank+1)
    ids = sorted(scores, key=scores.get, reverse=True)
    if not ids:
        return []
    rows = db.execute(SOURCES+' WHERE c.id IN ('+','.join('?' for _ in ids)+')', ids).fetchall()
    lookup = {row['id']:dict(row) for row in rows}
    # Identifier matches survive semantic paraphrase scores.
    identifiers = re.findall(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b|\b(?:TB6612FNG|IWR6843AOP|RobotSession)\b',query,re.I)
    rows = [dict(lookup[i], retrieval_score=scores[i], retrieval_mode=mode) for i in ids if i in lookup]
    intro = bool(re.search(r'우리\s*(?:프로그램|로봇|시스템)|시스템.{0,12}(?:구조|동작)',query))
    rows.sort(key=lambda row: (sum(term.lower() in row['body'].lower() for term in identifiers), int(intro and any(word in row['heading'] for word in ('데이터 흐름','구성','역할','대시보드 확장'))),row['retrieval_score']),reverse=True)
    from python_controller.knowledge.settings import option
    if mode == 'rerank' or option('DIG_KNOWLEDGE_RERANK','0') == '1':
        from python_controller.knowledge.reranker import rerank
        rows = rerank(query, rows[:40], library.cache)
        if identifiers:
            rows.sort(key=lambda row:sum(term.lower() in row['body'].lower() for term in identifiers),reverse=True)
    return [{k:v for k,v in row.items() if k not in ('retrieval_score','retrieval_mode')} for row in rows[:limit]]
