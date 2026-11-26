"""Phase 2 annotation interface. Verification self-reports are never ground-truth labels."""
from difflib import SequenceMatcher
import math
import statistics
import re


def prose_copy_length(text, bodies):
    def normalize(value):
        # A long exact component identifier is not a copied explanatory paragraph.
        value=re.sub(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_/-]{14,}(?![A-Za-z0-9])','<TERM>',value)
        return re.sub(r'\s+','',value)
    return max((SequenceMatcher(None,normalize(text),normalize(body),autojunk=False).find_longest_match().size
                for body in bodies),default=0)


def score_answer(answer, evidence_texts, annotation=None, *, direct_quote=False):
    """Annotation claim IDs must cover every factual claim to score faithfulness.

    annotation = {claims: {c1: {supported: bool, citation_correct: bool,
      numerical_correct: bool, entity_correct: bool, version_correct: bool,
      gold_ids: [str]}}, expected_fact_ids: [str], should_abstain: bool,
      relevance: float, naturalness: float}. Labels require human/independent review.
    """
    annotation = annotation or {}
    claims = answer.get('claims',[])
    labels = annotation.get('claims',{})
    complete = bool(claims) and all(c['claim_id'] in labels for c in claims)
    def ratio(key):
        values = [labels[c['claim_id']].get(key) for c in claims] if complete else []
        return sum(values)/len(values) if values and all(type(v) is bool for v in values) else None
    expected = set(annotation.get('expected_fact_ids',[]))
    covered = {i for c in claims for i in labels.get(c['claim_id'],{}).get('gold_ids',[]) if labels.get(c['claim_id'],{}).get('supported') is True}
    reply = answer.get('reply','')
    longest = prose_copy_length(reply,evidence_texts)
    # Short technical names/quantities are excluded; direct quotes are an explicit separate mode.
    raw_copy = None if direct_quote else (longest/len(re.sub(r'\s+','',reply)) if len(reply)>=45 and longest>=45 else 0.)
    return dict(answer_faithfulness=ratio('supported'), answer_relevance=annotation.get('relevance'),
                citation_correctness=ratio('citation_correct'),
                citation_completeness=(sum(bool(c.get('evidence_ids')) for c in claims)/len(claims) if claims else None),
                numerical_accuracy=ratio('numerical_correct'),entity_accuracy=ratio('entity_correct'),
                version_consistency=ratio('version_correct'), answer_naturalness=annotation.get('naturalness'),
                raw_copy_rate=raw_copy, longest_copy_chars=longest,
                abstention_accuracy=(int((not bool(claims)) == annotation['should_abstain']) if type(annotation.get('should_abstain')) is bool else None),
                answer_coverage=len(expected & covered)/len(expected) if expected else None,
                latency_ms=answer.get('latency_ms'), token_usage=answer.get('token_usage'),
                verification_failure_rate=(len(answer.get('verification_failures',[]))/(len(claims)+len(answer.get('verification_failures',[])))
                                           if claims or answer.get('verification_failures') else None))


def summarize_answers(records):
    result = {}
    keys = ('answer_faithfulness','answer_relevance','citation_correctness','citation_completeness',
            'numerical_accuracy','entity_accuracy','version_consistency','answer_naturalness',
            'raw_copy_rate','abstention_accuracy','answer_coverage','verification_failure_rate')
    for key in keys:
        measured = [r[key] for r in records if r.get(key) is not None]
        result[key] = statistics.mean(measured) if measured else None
        result[key+'_measured_count'] = len(measured)
    times = sorted(r['latency_ms'] for r in records if r.get('latency_ms') is not None)
    result['latency_p50_ms'] = statistics.median(times) if times else None
    result['latency_p95_ms'] = times[math.ceil(.95*len(times))-1] if times else None
    result['token_usage'] = None  # No token totals are inferred from character counts.
    return result
