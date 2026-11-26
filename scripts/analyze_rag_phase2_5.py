"""Zero-network forensic replay. Historical verdicts are not new semantic judgments."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.knowledge import verifier
from python_controller.knowledge.library import KnowledgeLibrary
from scripts.rag_phase2_5_candidates import candidate_reason,PROMPT_CANDIDATE,render_candidate

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'docs/verification/rag_v3_phase2'
OUT=ROOT/'docs/verification/rag_v3_phase2_5'


def jsonl(path):return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def cell(value):return str(value).replace('|','&#124;').replace('\n','<br>')


def verify_preservation(*,include_code=True):
    frozen=json.loads((OUT/'preservation_manifest.json').read_text())['files']
    # Later implementation may evolve; immutable experiment evidence may not.
    if not include_code:
        frozen={name:digest for name,digest in frozen.items() if name.startswith('docs/')}
    changed=[name for name,sha in frozen.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha]
    if changed:raise ValueError('Existing evidence/source changed: '+str(changed))
    budget=json.loads((OLD/'api_budget.json').read_text())
    assert len(budget['requests'])==budget['cap']==12
    return {'checked_files':len(frozen),'changed_files':changed,'budget_requests':12,'new_external_requests':0}


def restore(evidence,folder):
    library=KnowledgeLibrary(Path(folder)/'evidence.db',Path(folder));library.vector=None;library.documents()
    with library._db() as db:
        for r in evidence:
            db.execute('INSERT OR IGNORE INTO documents(id,path,sha,version,trust,status,variant_id,source_digest) VALUES (?,?,?,?,?,?,?,?)',
                       (r['document_id'],r['path'],r['sha'],r['version'],r['trust'],'indexed',r['variant_id'],r['source_sha256']))
            db.execute('INSERT INTO chunks(id,document_id,heading,page,line,body,active) VALUES (?,?,?,?,?,?,1)',
                       (r['id'],r['document_id'],r['heading'],r['page'],r['line'],r['body']))
    return library


INTENTS={
 'RAG-KO-061':'PC와 ESP32 사이의 통신 방식과 명령/텔레메트리 방향. 카메라 파이프라인 상세는 질문의 핵심이 아님.',
 'RAG-KO-062':'사용한 Qt Python 바인딩(PySide6)과 UI 기술. 설치 절차·파일 경로·일반 대화 SSE는 부가 정보.',
 'RAG-KO-063':'레이더 UNKNOWN의 의미. 모든 UNKNOWN 원인을 하나로 단정하지 말고 수신 사유와 유효 상태를 구분.'}
MISSING={
 'RAG-KO-061':'최종 답변에 UDP 방식 및 ESP32 명령/PC 텔레메트리 방향이 빠짐. 남은 카메라/GUI 설명은 핵심 질문을 대체하지 못함.',
 'RAG-KO-062':'PySide6라는 실제 바인딩과 질문 결론 전체가 빠짐. 가져온 6개 근거의 body/후보 어느 쪽에도 PySide6가 없음.',
 'RAG-KO-063':'유효하지 않은 센서 상태와 수신 부재의 구분이 빠짐. 미연결·시뮬레이션 사례만 남음.'}
ASSESSMENTS={
 ('RAG-KO-061',0):'정상 차단: 전역 문서에서 사실일 수 있지만 선택한 인용은 UDP/ESP32 통신 전체를 뒷받침하지 않음. 복수 사실과 인용 연결 오류.',
 ('RAG-KO-061',1):'False Rejection: 목표 손 제스처의 목표를 설계 목표로 오인. 원문에 기존 ESP32 기능 재사용과 Python 상위 제어가 직접 명시됨.',
 ('RAG-KO-061',2):'기록된 ENTAILMENT로 통과했으나 통신 질문에 대한 직접성/필수 사실은 부족. 한 블록에 여러 작업 사실이 묶임.',
 ('RAG-KO-062',0):'정상 차단: 선택 인용에 PyQt/PySide 바인딩 근거가 없고 4탭 정보는 다른 인용 후보에 있음. 실제 PySide6 근거는 별도 문서 절에 존재.',
 ('RAG-KO-062',1):'규칙 오탐: 인용의 latest-only worker에 only가 있어 전역 조건 누락으로 처리. 역할/경로 두 사실을 한 블록에 묶었으며 파일명은 음성 답변에 불필요.',
 ('RAG-KO-062',2):'부분 정상 사실의 과잉 차단 후보: 대화 문맥에서 문서 발췌를 제외한다는 다른 절을 조건으로 적용. 다만 일반 대화 범위가 생략된 전체 LLM 스트리밍 일반화는 의미 재검토가 필요.',
 ('RAG-KO-062',3):'정상 스타일 차단: requirements.txt가 본문에 포함됨. 설치/실행 경로는 질문 핵심도 아니며 복수 사실 블록으로 같이 소실.',
 ('RAG-KO-063',0):'정상 차단: 범위 밖 quote_index. 문장의 사실 여부와 인용 계약 오류를 분리해야 함.',
 ('RAG-KO-063',1):'정상 차단: 범위 밖 quote_index. CLEAR 조건은 원문에서 찾을 수 있어도 임의 번호를 서버가 자동 승인해서는 안 됨.',
 ('RAG-KO-063',2):'기록된 ENTAILMENT로 통과. 원문 전체와 맞지만 실제 전달된 legacy 청크가 UNKNOWN을 뒤에서 끝나 근거 완결성이 불충분.',
 ('RAG-KO-063',3):'정상 차단: 범위 밖 quote_index. RADAR_TIMEOUT 설명은 관련 원문이 있으나 번호 연결은 잘못됨.'}

OVERLAP={
 1:('related_not_same_core',['RAG-KO-037','RAG-KO-038'],'영상/손동작 주제는 같지만 Thumb 조정 방향은 새 명시적 질문'),
 2:('related_not_same_core',['RAG-KO-078'],'제어 역할과 관련; 동시 계획 편집과 단일 이동 제어권을 명시적으로 구분'),
 3:('partial_topic_overlap',['RAG-KO-041','RAG-KO-047'],'드라이버 주제는 중복하나 브레드보드 BOM namespace를 분리'),
 4:('strong_scope_overlap',['RAG-KO-080'],'제작 승인 주제 재사용. 이번에는 V19 문서 값과 실물 승인을 분리'),
 5:('confirmed_same_intent',['RAG-KO-061'],'실제 API 평가와 같은 질문'),
 6:('confirmed_same_intent',['RAG-KO-021','RAG-KO-022','RAG-KO-062'],'Qt 바인딩 질문의 패러프레이즈 및 실제 평가 질문'),
 7:('confirmed_same_intent',['RAG-KO-005','RAG-KO-006','RAG-KO-063'],'UNKNOWN 의미/원인 질문 재사용'),
 8:('partial_fact_overlap',['RAG-KO-019','RAG-KO-020','RAG-KO-084'],'worker와 GUI 작업 분리를 구체화'),
 9:('strong_intent_overlap',['RAG-KO-065','RAG-KO-061'],'ESP32 역할 질문 재사용, 기존 061 생성문에도 역할 사실 노출'),
 10:('confirmed_same_fact',['RAG-KO-075'],'배터리 전압 질문을 브레드보드 TERANTY로 한정'),
 11:('subset_of_prior_comparison',['RAG-KO-079'],'기존 핀 비교의 V19 M2 부분집합'),
 12:('related_not_same_core',['RAG-KO-033','RAG-KO-073','RAG-KO-076'],'cmd_move/계획 질문 관련, 행동 수·개별/총 거리 세 슬롯을 새로 명시'),
 13:('strong_topic_overlap',['RAG-KO-041','RAG-KO-059','RAG-KO-060','RAG-KO-068'],'버전별 부품 차이의 모터 드라이버/실물 호환성 하위 질문'),
 14:('confirmed_same_intent',['RAG-KO-045','RAG-KO-079'],'브레드보드/V19 엔코더 비교를 M2로 구체화'),
 15:('strong_scope_overlap',['RAG-KO-023','RAG-KO-066'],'CLEAR 충분성 및 STOP 조건 질문; 063 실제 생성에도 1초 조건 노출'),
 16:('partial_topic_overlap',['RAG-KO-035','RAG-KO-070'],'통신·워치독 관련, GUI 프레임 timestamp와 물리 보장 구분은 별도 핵심'),
 17:('known_negative_family',[],'기존 semantic_cases의 근거 없는 1.234ms 정밀 지연 NEUTRAL 계열과 중복'),
 18:('related_safety_scope',['RAG-KO-069','RAG-KO-080'],'안전/실물 승인 과장 계열 관련. 2A 연속 부하 자체는 새 세부 질문'),
 19:('confirmed_same_intent',['RAG-KO-072','RAG-KO-106','RAG-KO-107','RAG-KO-108','RAG-KO-109','RAG-KO-110'],'문서만으로 현재 속도를 묻는 기존 질문 재사용'),
 20:('related_not_same_core',['RAG-KO-051','RAG-KO-068','RAG-KO-080'],'현재 연결/버전/실물 범위 관련. 실제 PCB 리비전 단정은 새 명시적 요청')}


def overlap_audit(core):
    old=jsonl(OLD/'gold_cases.jsonl');grouped={}
    for row in old:grouped.setdefault(' '.join(row['question'].split()),[]).append(row)
    duplicates=[{'question':q,'case_ids':[r['case_id'] for r in rows],
                 'splits':sorted({r['dataset_split'] for r in rows}),
                 'different_gold_fact_sets':len({json.dumps(r['expected_facts'],sort_keys=True,ensure_ascii=False) for r in rows})}
                for q,rows in grouped.items() if len(rows)>1]
    reviewed=[]
    md=['# 기존 평가와 core20 의미 중복 검토','',
        '에이전트가 기존 120개 질문을 읽고 의도/필수 사실의 중복을 구분한 검토입니다. 인간 독립 검토와 미사용 holdout 인증은 아닙니다.',
        f'기존 120개 중 정확히 같은 질문 문자열의 고유 수: {len(grouped)}. 반복 그룹 수: {len(duplicates)}. 원본은 삭제/병합하지 않았습니다.','',
        '| core20 | 분할 | 판단 | 기존 사례 | 근거와 제한 |','|---|---|---|---|---|']
    for i,row in enumerate(core,1):
        label,ids,reason=OVERLAP[i]
        reviewed.append({'case_id':row['case_id'],'split':row['split'],'agent_semantic_overlap':label,
                         'prior_cases':ids,'reason':reason,'human_review_pending':True,
                         'pristine_holdout_claimed':False})
        md.append(f'| {row["case_id"]} | {row["split"]} | {label} | {", ".join(ids) or "semantic latency family"} | {reason} |')
    md.extend(['','## 기존 정확 중복 그룹','',
               '| 기존 ID | 분할 | 서로 다른 gold fact 집합 수 | 질문 |','|---|---|---:|---|'])
    for d in duplicates:md.append(f'| {", ".join(d["case_ids"])} | {", ".join(d["splits"])} | {d["different_gold_fact_sets"]} | {d["question"]} |')
    md.extend(['','같은 질문에 다른 required-fact 후보가 달린 그룹은 채점 일관성을 추가 검토해야 합니다. 서로 다른 사실 집합이 있다는 것만으로 문서 사실 자체가 모순된다고 결론 내리지는 않습니다.',
               '시험 후보 10개는 개발용과 파일상 분리했지만, 기존 질문/근거/출력 노출이 있는 항목을 독립적인 새 성능 입증 자료로 쓰지 않습니다. 인간 검토 후 같은 필수 사실의 다른 말하기를 한쪽 분할로 묶고, 최종 입증에는 별도 미노출 질문을 고정하세요.'])
    save('overlap_review.json',{'old_questions':120,'unique_exact_questions':len(grouped),'exact_duplicate_groups':duplicates,'core20_agent_review':reviewed})
    (OUT/'SEMANTIC_OVERLAP_REVIEW.md').write_text('\n'.join(md)+'\n',encoding='utf-8')


def main():
    before=verify_preservation()
    records=jsonl(OLD/'grounded_rag_v3.jsonl')
    requests=json.loads((OLD/'request_audit.json').read_text())['requests']
    core=jsonl(OUT/'gold_core20.jsonl')
    overlap_audit(core)
    coremap={f'RAG-KO-0{i}':core[n] for i,n in ((61,4),(62,5),(63,6))}
    traces=[];timing=[];all_reasons=[]
    md=['# 기존 061·062·063 상세 실패 분석표','',
        '기존 실제 응답/근거/의미 판정을 그대로 읽었습니다. 이 단계의 새 외부 호출은 0회입니다.',
        '아래 False Rejection 판정은 원문 대조에 의한 에이전트 분석이며 인간 독립 검토 완료가 아닙니다.','']
    with patch.object(socket,'socket',side_effect=AssertionError('Phase 2.5 forbids network')):
        for row in records:
            cid=row['case_id'];evidence=row['evidence_snapshot'];lookup={r['id']:r for r in evidence}
            historical=next((r for r in requests if r['purpose']==cid+':C_judge'),None)
            verdicts=json.loads(historical['response_text'])['verdicts'] if historical else []
            per_claim=[];pending=0
            for claim in row['raw_generated_claims']['claims']:
                support_rows=[];quotes=[];invalid=[]
                for s in claim['supports']:
                    r=lookup.get(s['evidence_id']);index=s['quote_index']
                    if r is None or type(index) is not int or not 0<=index<len(r['quote_candidates']):
                        invalid.append({'support':s,'available_indices':list(range(len(r['quote_candidates']))) if r else []})
                    else:support_rows.append(r);quotes.append(r['quote_candidates'][index])
                reason='INVALID_CITATION' if invalid else verifier.deterministic_reason(claim['text'],quotes,support_rows)
                candidate='INVALID_CITATION' if invalid else candidate_reason(claim['text'],quotes,support_rows)
                semantic=verdicts[pending]['relation'] if reason is None else 'NOT_CALLED'
                if reason is None:pending+=1
                if reason:all_reasons.append(reason)
                detail={'claim_id':claim['claim_id'],'text':claim['text'],'supports':claim['supports'],'actual_quotes':quotes,
                        'invalid_supports':invalid,'deterministic_reason':reason,'historical_semantic':semantic,
                        'candidate_eligibility_reason':candidate,'candidate_is_verified':False,
                        'qualifier_hits':{name:[{'token':m.group(),'context':q[max(0,m.start()-30):m.end()+40]} for q in quotes for m in pattern.finditer(q)]
                                          for name,pattern in [('design',verifier.DESIGN),('negative',verifier.NEGATIVE),('conditional',verifier.CONDITIONAL)]},
                        'analyst_assessment':ASSESSMENTS[(cid,len(per_claim))]}
                per_claim.append(detail)
            assert pending==len(verdicts),'Historical batch mapping mismatch'
            with tempfile.TemporaryDirectory() as folder:
                library=restore(evidence,folder)
                def cached(pairs):
                    assert len(pairs)==len(verdicts)
                    return [v['relation'] for v in verdicts]
                accepted,rejected=verifier.verify_claims(row['raw_generated_claims'],evidence,library,semantic=cached)
            assert [c['text'] for c in accepted]==[c['text'] for c in row['claims']]
            assert rejected==row['verification_failures']
            result={'case_id':cid,'question':row['question'],'intent':INTENTS[cid],'grounded_answer_facts':coremap[cid]['expected_facts'],
                    'retrieved_evidence':evidence,'claim_trace':per_claim,'final_reply':row['reply'],'speech_text':row.get('speech_text'),
                    'unanswered_aspects':row.get('unanswered_aspects'),'cached_replay_matches_record':True,
                    'historical_semantic_verdicts':verdicts,'analyst_review':'human_review_pending'}
            result['missing_core_facts']=MISSING[cid]
            traces.append(result)
            md.extend([f'## {cid}', '', '| 항목 | 내용 |','|---|---|',f'| 원래 질문 | {cell(row["question"])} |',
                       f'| 핵심 의도 | {cell(INTENTS[cid])} |',
                       f'| 문서로 확인 가능한 핵심 정답 | {cell(" / ".join(f["statement"] for f in coremap[cid]["expected_facts"] if f["required"]))} |',
                       f'| 최종 답변 | {cell(row["reply"])} |',
                       f'| 누락된 핵심 사실 | {cell(MISSING[cid])} |',
                       f'| 역사적 의미 검증 | {cell(verdicts) if verdicts else "호출 안 됨: 전단에서 모두 제외"} |',
                       '| 평가 문제와 실제 답변 문제 | 기존 gold는 수정하지 않음. core20은 별도 원자적 초안. 실제 인용/규칙 실패는 기존 gold와 독립적으로 재현됨 |','',
                       '| 근거 ID | 문서 / 절 | 후보 번호 | index version |','|---|---|---|---|'])
            for r in evidence:md.append(f'| {r["id"]} | {cell(r["path"]+" / "+r["heading"])} | {list(range(len(r["quote_candidates"])))} | {r.get("index_version")} |')
            md.extend(['','| 생성 claim | 생성된 전체 문장 | 연결 근거 | 결정론적 결과 | 기록된 의미 판정 | 원인 구분 |','|---|---|---|---|---|---|'])
            for c in per_claim:md.append(f'| {c["claim_id"]} | {cell(c["text"])} | {cell(c["supports"])} | {c["deterministic_reason"] or "통과"} | {c["historical_semantic"]} | {cell(c["analyst_assessment"])} |')
            md.extend(['','### 선택된 실제 인용과 규칙 일치 지점',''])
            for c in per_claim:
                md.extend([f'**{c["claim_id"]}**',f'- 규칙이 일치한 부분: `{cell(c["qualifier_hits"])}`'])
                for q in c['actual_quotes']:md.extend(['','```text',q,'```'])
                if c['invalid_supports']:md.append('범위 오류: `'+str(c['invalid_supports'])+'`')
            sem=row.get('semantic_verification_latency_ms');gen=row.get('generation_latency_ms');retr=row.get('retrieval_latency_ms')
            sdk_gen=next((r['latency_ms'] for r in requests if r['purpose']==cid+':C_generator'),None)
            sdk_sem=next((r['latency_ms'] for r in requests if r['purpose']==cid+':C_judge'),None)
            timing.append({'case_id':cid,'retrieval_ms':retr,'evidence_selection_ms':None,'generation_wrapper_ms':gen,
                           'deterministic_validation_ms':None,'semantic_wrapper_ms':sem,'semantic_called':historical is not None,
                           'rendering_ms':None,'total_ms':row['total_latency_ms'],
                           'unseparated_remainder_ms':row['total_latency_ms']-sum(x or 0 for x in (retr,gen,sem)),
                           'sdk_create_generation_ms':sdk_gen,'sdk_create_semantic_ms':sdk_sem,
                           'generation_wrapper_minus_create_ms':gen-sdk_gen if gen is not None and sdk_gen is not None else None,
                           'semantic_wrapper_minus_create_ms':sem-sdk_sem if sem is not None and sdk_sem is not None else None})
    save('claim_traces.json',traces)
    save('latency_decomposition.json',timing)
    save('replay_summary.json',{'cases':3,'raw_claim_blocks':sum(len(r['claim_trace']) for r in traces),
                               'deterministic_rejections':dict(Counter(all_reasons)),
                               'historical_semantic_claims':sum(len(r['historical_semantic_verdicts']) for r in traces),
                               'new_semantic_judgments':0,'all_cached_replays_match':True,
                               'candidate_results_are_eligibility_only':True})
    (OUT/'FAILURE_CASES_061_062_063.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    (OUT/'PROMPT_AND_NATURALNESS_CANDIDATE.md').write_text(
        '# 한국어 답변 개선 후보 (운영 미적용)\n\n실제 새 모델 출력/개선 성능은 미측정입니다. 기존 JSON 계약에 추가할 제약 초안입니다.\n\n```text\n'+PROMPT_CANDIDATE+
        '\n```\n\n검증 후 재서술 모델은 쓰지 않습니다. 후보 조립기는 승인된 claim 문자열을 공백 하나로 연결하는 것만 허용합니다. 따라서/그래서/그 결과 같은 인과 연결은 임의 삽입하지 않습니다. '
        '결론 우선 순서는 최초 생성에서 얻어야 하며, 근거 없는 결론 문장을 renderer가 보충하지 않습니다.\n\n'
        '음성 검토표: 핵심 결론 우선 / 긴 복수 사실 분리 / 같은 주어 반복 / 파일명·출처 번호 제외 / 약어 이해 가능 / 조건·부정 보존을 각각 검토하세요. '
        '점수는 인간 검토 전 공란입니다. 운영 speech_text는 변경하지 않았습니다.\n',encoding='utf-8')
    save('preservation_check.json',verify_preservation())
    print('Zero-network replay: 3 cases match; 11 raw claim blocks traced; all old hashes unchanged')
if __name__=='__main__':main()
