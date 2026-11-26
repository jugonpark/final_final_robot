"""Grounded document explanation, isolated from control and approval capabilities."""
import logging
import re
import sqlite3
from time import perf_counter

from python_controller.knowledge.library import KnowledgeError, SECRET
from python_controller.knowledge.variants import clarification, hardware_scope
from python_controller.knowledge.evidence import (CITATION_FIELDS, UNTRUSTED_COMMAND, cloud_allowed,
                                                  current, safe_body, select_evidence)
from python_controller.knowledge.generation import RAGCalls, SYSTEM
from python_controller.knowledge.facts import structured_facts
from python_controller.knowledge.verifier import verify_claims
from python_controller.knowledge.rendering import render_reply, response
from python_controller.knowledge.program import local_program,scope as program_scope

logger = logging.getLogger(__name__)


def document_question(text, library=None):
    text = text.strip()
    if program_scope(text) is not None or (re.search(r'이동해도|주행해도',text) and re.search(r'무조건|항상|안전.*보장',text)):
        return True
    if re.match(r'^(?:문서\s*[:：]|문서(?:에서|\s*검색)|(?:매뉴얼|설명서)(?:에서|에\s*따르면|\s*[:：]))', text):
        return True
    if re.search(r'(?:우리\s*(?:로봇|프로그램|시스템)|D\.?I\.?G\.?|Qt|PySide6|웹\s*관제|중앙관제).{0,45}(?:동작|구조|설명|뭐야|차이|역할|기능)|레이더.{0,25}(?:UNKNOWN|왜|원인)|(?:RobotSession|STOP_CONFIRMED|TB6612FNG|IWR6843AOP).{0,35}(?:뭐|설명|역할|의미)',text,re.I):
        return True
    match = re.fullmatch(r'([가-힣A-Za-z0-9_][가-힣A-Za-z0-9_ ]{0,59}?)(?:이|가|은|는)?\s*누구(?:야|예요|인가요)\s*[?？]?', text)
    if not match:
        return False
    entity = match[1].strip()
    if entity in ('너', '넌', '당신', '니', '네'):
        return False
    if library is None:
        return True  # Cheap dispatch only; authoritative document lookup runs in the graph worker.
    try:
        return any(row['trust'] == 'user_reference' and row['path'].lower().endswith('.md') and
                   row['heading'].strip() == entity for row in library.search_robot_documents(entity))
    except (OSError, sqlite3.Error, KnowledgeError):
        return False


# Kept as a compatibility seam for callers/tests; it now returns untrusted atomic claims.
def _nemotron_evidence(question, rows, *, calls=None):
    from python_controller.llm_cli import load_environment
    env = calls.env if calls else load_environment()
    if not cloud_allowed(question, rows, env):
        return None
    evidence = rows if all('quote_candidates' in row for row in rows) else select_evidence(rows, question)
    return (calls or RAGCalls(env)).generate(question, evidence)


def _evidence_body(body):
    return safe_body(body)


def answer_documents(library, question, *, use_nemotron=True):
    start = perf_counter()
    result = _answer(library, question, use_nemotron=use_nemotron)
    result['latency_ms'] = round((perf_counter()-start)*1000, 2)
    # No prompts, source contents, user names or model responses in operational logs.
    logger.info('rag_v3 mode=%s status=%s ms=%s reasons=%s outcome=%s',result['answer_mode'],
                result['verification_status'],result['latency_ms'],
                sorted({r['reason'] for r in result['verification_failures']}),result.get('reason_code'))
    return result


def _answer(library, question, *, use_nemotron):
    if SECRET.search(question):
        return response('민감한 정보가 포함된 질문은 문서 검색에 사용할 수 없습니다.',mode='error')
    scope = hardware_scope(question)
    if scope == ():
        return response(clarification(question),mode='clarification')
    # Documents cannot establish a live device state; normal Agent status path remains authoritative.
    if re.search(r'(?:현재|지금).{0,30}(?:연결|움직|주행|정지|상태|장착)',question) and not scope:
        return response('문서로 현재 로봇 상태를 확인할 수 없어요. 관제의 실시간 상태를 확인해 주세요.',mode='clarification')
    local,evidence,incomplete=local_program(library,question)
    if local:
        return response(render_reply(local,partial=incomplete),evidence,mode='partial' if incomplete else 'template',
                        status='partial' if incomplete else 'structured',claims=local,partial=incomplete)
    try:
        rows = library.search_robot_documents(question, limit=6)
    except (OSError, sqlite3.Error, KnowledgeError):
        return response('문서 색인을 지금 읽을 수 없습니다.',mode='error',status='unavailable')
    evidence = select_evidence(rows,question)
    if not evidence:
        return response('색인된 문서에서 이 질문의 근거를 찾지 못했어요. 관련 문서를 등록하거나 확인할 항목을 알려 주세요.',reason='RETRIEVAL_MISS')
    if re.search(r'원문.{0,12}(?:보여|인용|그대로|확인)|직접\s*인용',question):
        live = [row for row in evidence if current(library,row)]
        text = '요청하신 문서의 원문이에요. 문서가 현재 장치 상태를 증명하지는 않아요.\n\n' + '\n\n'.join(
            f'{row["path"]} · {row["heading"]}\n{row["body"]}' for row in live[:3])
        return response(text,live,mode='template',status='citation_only')
    templates, conflict = structured_facts(evidence,question,library)
    if conflict:
        return response('같은 항목에 서로 다른 값이 있어 하나로 확정할 수 없어요. 출처의 리비전과 적용 조건을 확인해 주세요.', evidence,
                        mode='clarification',status='conflicting')
    # Simple typed lookups do not pay for large model generation or verification.
    if templates:
        used_ids = {i for claim in templates for i in claim['evidence_ids']}
        covered = {row['variant_id'] for row in evidence if row['id'] in used_ids}
        partial = bool(scope and set(scope)-covered)
        return response(render_reply(templates,partial=partial),evidence,
                        mode='partial' if partial else 'template',status='partial' if partial else 'structured',
                        claims=templates,partial=partial,
                        rejected=[{'reason':'INCOMPLETE_SCOPE'}] if partial else [])
    rejected = []
    if use_nemotron:
        from python_controller.llm_cli import load_environment
        env = load_environment()
        if env.get('DIG_KNOWLEDGE_CLOUD')!='1':
            return response('관련 자료는 찾았지만, 현재 설정에서는 외부 AI를 통한 문서 해석을 사용하지 않아요. 확인된 로컬 지식 범위에서는 답할 수 있으며, 이 질문의 추가 해석은 보류했어요.',evidence,
                            status='policy_disabled',reason='POLICY_DISABLED')
        if not cloud_allowed(question,rows,env):
            return response('관련 자료에 외부 전송이 제한된 문서가 포함돼 있어 AI 해석을 보류했어요. 등록된 소개처럼 로컬에서 확인할 수 있는 정보는 계속 조회할 수 있어요.',evidence,
                            status='restricted',reason='PRIVATE_SOURCE_RESTRICTED')
        if cloud_allowed(question, rows, env):
            if env.get('DIG_KNOWLEDGE_SEMANTIC','nemotron') != 'nemotron':
                return response('설명 검증기가 비활성화되어 문서 해석을 보류했어요. 관련 근거를 열어 확인할 수 있어요.',evidence,
                                status='unavailable',rejected=[{'reason':'UNAVAILABLE'}],reason='VERIFIER_UNAVAILABLE')
            calls = RAGCalls(env)
            try:
                raw = _nemotron_evidence(question,evidence,calls=calls)
                claims,rejected = verify_claims(raw,evidence,library,semantic=calls.verify)
                # Recheck after slow judge; retired rows must not justify a new answer.
                valid_ids = {row['id'] for row in evidence if current(library,row)}
                fresh = [c for c in claims if set(c['evidence_ids']).issubset(valid_ids)]
                rejected.extend({'reason':'STALE_EVIDENCE'} for c in claims if c not in fresh)
                if fresh:
                    used_ids = {i for claim in fresh for i in claim['evidence_ids']}
                    covered = {row['variant_id'] for row in evidence if row['id'] in used_ids}
                    if scope and set(scope)-covered:
                        rejected.append({'reason':'INCOMPLETE_SCOPE'})
                    partial = bool(rejected or raw.get('unanswered_aspects'))
                    return response(render_reply(fresh,partial=partial),evidence,
                                    mode='partial' if partial else 'grounded',
                                    status='partial' if partial else 'verified',claims=fresh,
                                    rejected=rejected,partial=partial)
            except (ValueError,OSError,ImportError,TypeError,KeyError):
                rejected = [{'reason':'GENERATION_UNAVAILABLE'}]
    unavailable = any(r['reason'] in ('UNAVAILABLE','GENERATION_UNAVAILABLE') for r in rejected)
    codes={r['reason'] for r in rejected}
    reason=('GENERATION_FAILED' if 'GENERATION_UNAVAILABLE' in codes else 'VERIFIER_UNAVAILABLE' if 'UNAVAILABLE' in codes else
            'INVALID_CITATION' if codes & {'INVALID_CITATION','INVALID_QUOTE','STALE_EVIDENCE'} else
            'SEMANTIC_REJECTION' if codes & {'NEUTRAL','CONTRADICTION'} else 'DETERMINISTIC_REJECTION' if codes else 'INSUFFICIENT_EVIDENCE')
    return response('관련 자료는 찾았지만, 질문의 핵심에 답할 설명을 확인하지 못했어요. 확인할 문서나 항목을 좁히면 다시 살펴볼 수 있어요.', evidence,
                    mode='abstained',status='unavailable' if unavailable else ('failed' if rejected else 'not_verified'),
                    rejected=rejected,reason=reason)
