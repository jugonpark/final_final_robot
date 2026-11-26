"""Twenty source-checked draft cases, atomic facts, no automatic human approval."""
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/verification/rag_v3_phase2_5'
REV='8861848'
ARCH='docs/ARCHITECTURE.md';SCHEMA='docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md'
BB='docs/hardware/breadboard/FACTS.md';PIN='docs/hardware/breadboard/PIN_PROFILE.md'
V18='docs/hardware/pcb-v18/FACTS.md';V19='docs/hardware/pcb-v19/FACTS.md'
RADAR='docs/RADAR_OPERATION_GUIDE.md';DASH='docs/DASHBOARD_GUIDE.md'


@lru_cache(maxsize=None)
def read_source(path):
    raw=subprocess.check_output(['git','show',f'{REV}:{path}'],cwd=ROOT)
    return raw,raw.decode('utf-8-sig')


def fact(statement,path,needle,required=True,scope='documented_software',key=None):
    raw,text=read_source(path)
    if text.count(needle)!=1:raise ValueError(f'Need one exact source match: {path}: {needle}')
    offset=text.index(needle);start=text.rfind('\n',0,offset)+1;end=text.find('\n',offset)
    quote=text[start:end if end>=0 else len(text)]
    return {'statement':statement,'required':required,'source_path':path,'source_revision':REV,
            'source_sha256':hashlib.sha256(raw).hexdigest(),'line':text.count('\n',0,start)+1,
            'evidence_excerpt':quote,'scope':scope,'fact_key':key or statement,
            'annotation_status':'agent_source_checked_human_review_pending','human_approved':False}


def cases():
    f=fact
    return [
    ('simple_fact','development','손의 Thumb Up과 Thumb Down은 추종 거리 설정을 어떻게 바꾸나요?',[
        f('문서에서 Thumb Up은 목표 상대 높이를 증가시킨다.',ARCH,'Thumb Up은 0.08 증가',key='thumb_up_increase'),
        f('문서에서 Thumb Down은 목표 상대 높이를 감소시킨다.',ARCH,'Thumb Down은 감소',key='thumb_down_decrease'),
        f('이 설정은 박스 높이를 이용한 상대 거리 대용값이며 실제 거리값이 아니다.',ARCH,'실제 거리값은 만들지 않는다',key='relative_proxy_not_metric')]),
    ('simple_fact','test_candidate','손동작 화면을 쓰면서 AI 계획도 만들 수 있나요? 이동 제어권도 동시에 두 개가 되나요?',[
        f('손동작이 실제 제어권을 가진 동안에도 AI 계획 생성과 편집을 사용할 수 있다.','docs/WEB_PYTHON_COMPARISON.md','손동작이 실제 이동 제어권을 가진 동안에도',key='plans_available_during_hand'),
        f('물리 이동 제어권은 동시에 하나다.','docs/WEB_PYTHON_COMPARISON.md','물리 이동 제어권은 동시에 하나만 가진다',key='one_motion_owner')]),
    ('simple_fact','test_candidate','브레드보드 제공 BOM에는 어떤 모터 드라이버 모듈이 적혀 있나요?',[
        f('제공 BOM의 모터 드라이버는 TB6612FNG 모듈 SZH-MDBL-00이다.',BB,'제공 BOM에는 TB6612FNG 모듈 SZH-MDBL-00 2개',scope='BREADBOARD_BOM',key='bb_driver_model'),
        f('이 모듈은 제공 BOM에 2개로 기재돼 있다.',BB,'제공 BOM에는 TB6612FNG 모듈 SZH-MDBL-00 2개',scope='BREADBOARD_BOM',key='bb_driver_count'),
        f('실제 모듈 장착과 배선은 확인이 필요하다.',BB,'실제 모듈 장착과 배선은 확인이 필요합니다',scope='BREADBOARD_BOM',key='bb_driver_not_asbuilt')]),
    ('simple_fact','test_candidate','PCB V19의 문서상 제작 승인 상태를 알려줘. 실물 승인과도 같은 뜻인가요?',[
        f('V19 문서의 FAB_APPROVED는 false다.',V19,'`FAB_APPROVED=false`',scope='PCB_V19_CAD',key='v19_fab_false'),
        f('V19 문서의 FINAL_PINMAP_APPROVED는 false다.',V19,'`FINAL_PINMAP_APPROVED=false`',scope='PCB_V19_CAD',key='v19_pin_approval_false'),
        f('실물 구동과 제조·실장 승인 확인이 필요하다.',V19,'모터 실물 구동, 제조·실장 승인 확인이 필요',scope='PCB_V19_CAD',key='v19_approval_not_physical')]),
    ('architecture','development','우리 프로그램은 노트북이랑 로봇이 어떻게 통신해?',[
        f('PC 통신은 UDP를 사용한다.','docs/CONTROL_SIMPLE_V2_PLAN.md','UDP 8888/8889, heartbeat',key='communication_udp'),
        f('문서의 제어 흐름에는 ESP32로 명령을 전달하는 경로가 있다.','docs/CENTRAL_CONTROL_WORKFLOW.md','ESP32 명령 UDP 8888',key='pc_to_esp_command'),
        f('문서의 제어 흐름에는 PC로 텔레메트리를 받는 경로가 있다.','docs/CENTRAL_CONTROL_WORKFLOW.md','PC 텔레메트리 UDP 8889',key='esp_to_pc_telemetry'),
        f('문서의 ESP32 명령 포트는 UDP 8888이다.','docs/CENTRAL_CONTROL_WORKFLOW.md','ESP32 명령 UDP 8888',required=False,key='command_port_optional'),
        f('문서의 PC 텔레메트리 포트는 UDP 8889다.','docs/CENTRAL_CONTROL_WORKFLOW.md','PC 텔레메트리 UDP 8889',required=False,key='telemetry_port_optional'),
        f('소켓 소유 모듈은 communication/esp32_udp.py다.',ARCH,'`communication/esp32_udp.py`만 소켓을 소유',required=False,key='socket_module_optional')]),
    ('architecture','development','Qt 화면은 어떤 기술로 만든 거야?',[
        f('기본 Qt UI에 사용하는 Python 바인딩은 PySide6다.',ARCH,'배치 실행의 기본 UI는 PySide6',key='qt_binding_pyside6'),
        f('Qt 화면 구현은 visualization/dashboard.py에 있다.',ARCH,'`visualization/dashboard.py`가 Qt 탭',required=False,key='qt_view_path_optional')]),
    ('architecture','development','레이더가 UNKNOWN이면 무슨 뜻이야?',[
        f('UNKNOWN/null은 값을 받지 못했거나 검증할 수 없다는 표시다.',DASH,'`UNKNOWN`/null은 값을 받지 못했거나 검증할 수 없다는 뜻',key='unknown_meaning'),
        f('RADAR_NO_DATA는 설정한 Pi에서 아직 유효한 상태 패킷을 받지 못했다는 사유다.',RADAR,'| `RADAR_NO_DATA` |',required=False,key='radar_no_data_optional'),
        f('RADAR_SENSOR_UNKNOWN은 Pi 패킷은 받았으나 센서 상태가 유효하지 않다는 사유다.',RADAR,'| `RADAR_SENSOR_UNKNOWN` |',required=False,key='radar_sensor_unknown_optional')]),
    ('architecture','development','영상 처리와 GUI 제어 작업은 어떻게 분리되어 있나요?',[
        f('문서의 처리 순서는 Camera worker, Vision worker, GUI 상태 머신, 안전 명령이다.',ARCH,'Camera worker → 최신 슬롯',key='camera_vision_gui_order'),
        f('GUI 스레드에서 카메라 read를 실행하지 않는다.',ARCH,'GUI 스레드에서 카메라 read',key='gui_no_camera_io'),
        f('GUI 스레드에서 AI 추론을 실행하지 않는다.',ARCH,'GUI 스레드에서 카메라 read',key='gui_no_ai_inference'),
        f('GUI 스레드에서 소켓 recv를 실행하지 않는다.',ARCH,'GUI 스레드에서 카메라 read',key='gui_no_socket_recv')]),
    ('architecture','test_candidate','ESP32와 PC의 Python 코드는 각각 어떤 역할을 하나요?',[
        f('기존 ESP32의 운동학 기능을 재사용한다.',ARCH,'ESP32의 운동학·PID·엔코더·워치독을 재사용하며',key='esp_kinematics_reused'),
        f('기존 ESP32의 PID 기능을 재사용한다.',ARCH,'ESP32의 운동학·PID·엔코더·워치독을 재사용하며',key='esp_pid_reused'),
        f('상위 제어는 Python으로 추가했다.',ARCH,'상위 제어만 Python으로 추가했다',key='python_high_level')]),
    ('numerical_entity','development','브레드보드 TERANTY 배터리의 기준 전압은 몇 V인가요?',[
        f('사용자가 확인한 TERANTY 배터리 기준 전압은 7.4V다.',BB,'TERANTY 배터리의 기준 전압은 사용자가 확인한 7.4V',scope='BREADBOARD_BOM',key='battery_voltage_7_4')]),
    ('numerical_entity','test_candidate','PCB V19 CAD에서 M2 엔코더 A와 B는 각각 어느 GPIO인가요?',[
        f('V19 CAD M2의 ENC A는 GPIO36이다.',V19,'| M2 | 21 | 22 | 26 | 36 | 39 |',scope='PCB_V19_CAD',key='v19_m2_enc_a'),
        f('V19 CAD M2의 ENC B는 GPIO39이다.',V19,'| M2 | 21 | 22 | 26 | 36 | 39 |',scope='PCB_V19_CAD',key='v19_m2_enc_b'),
        f('실물 엔코더 출력과 외부 풀업은 별도 확인이 필요하다.',V19,'엔코더 출력과 외부 풀업은 실물 확인이 필요',scope='PCB_V19_CAD',key='v19_enc_not_physical')]),
    ('numerical_entity','development','AI 이동 초안의 행동 수, 이동당 거리와 총 거리 제한은 어떻게 되나요?',[
        f('초안의 최대 동작 수는 4블록이다.',SCHEMA,'초안은 최대 4블록',key='plan_max_4'),
        f('개별 이동 거리는 5~50cm다.',SCHEMA,'블록당 5~50cm',key='move_5_50_cm'),
        f('총 이동 거리 합계는 100cm 이하다.',SCHEMA,'합계 100cm 이하',key='plan_total_100_cm')]),
    ('version_comparison','test_candidate','V18과 V19의 모터 드라이버 자료를 보면 실물 모듈 교체가 검증됐다고 볼 수 있나요?',[
        f('V18은 TB6612FNG HW166 형상 모듈 소켓 설계다.',V18,'V18의 MD1과 MD2는 TB6612FNG HW166',scope='PCB_V18_CAD',key='v18_driver_socket'),
        f('V19에는 TB6612FNG 모듈 소켓이 설계돼 있다.',V19,'MD1/MD2 TB6612FNG 모듈 소켓',scope='PCB_V19_CAD',key='v19_driver_socket'),
        f('V19 문서는 모듈 간 대체 호환성을 확인하지 못했다고 명시한다.',V19,'모듈 간 대체 호환성은 확인되지 않았습니다',scope='PCB_V19_CAD',key='v19_compat_unverified')]),
    ('version_comparison','test_candidate','브레드보드 펌웨어와 V19 CAD의 M2 엔코더 핀을 따로 비교해줘.',[
        f('브레드보드 펌웨어 M2 ENC A는 GPIO16이다.',PIN,'| M2 | 21 | 22 | 26 | 16 | 17 |',scope='BREADBOARD_FIRMWARE',key='bb_m2_enc_a'),
        f('브레드보드 펌웨어 M2 ENC B는 GPIO17이다.',PIN,'| M2 | 21 | 22 | 26 | 16 | 17 |',scope='BREADBOARD_FIRMWARE',key='bb_m2_enc_b'),
        f('V19 CAD M2 ENC A는 GPIO36이다.',V19,'| M2 | 21 | 22 | 26 | 36 | 39 |',scope='PCB_V19_CAD',key='compare_v19_a'),
        f('V19 CAD M2 ENC B는 GPIO39이다.',V19,'| M2 | 21 | 22 | 26 | 36 | 39 |',scope='PCB_V19_CAD',key='compare_v19_b'),
        f('브레드보드 실제 배선은 별도 확인이 필요하다.',PIN,'제공 BOM에는 실제 모터·서보 결선이 없습니다',scope='BREADBOARD_FIRMWARE',key='bb_pin_not_asbuilt')]),
    ('complex','development','레이더가 CLEAR면 바로 움직여도 되나요? 차단 해제 조건과 이전 미션 재개 여부를 설명해줘.',[
        f('자동 해제 조건에는 최신 CLEAR의 연속 1초 유지가 필요하다.',RADAR,'최신 `CLEAR`가 연속 1초 유지되며',key='clear_one_second'),
        f('해제 조건에는 새 ESP32 STOP 확인이 필요하다.',RADAR,'최신 `CLEAR`가 연속 1초 유지되며',key='fresh_stop_required'),
        f('해제 조건에는 정상 세션 확인이 필요하다.',RADAR,'최신 `CLEAR`가 연속 1초 유지되며',key='normal_session_required'),
        f('해제 조건에는 활성 운영자 화면이 필요하다.',RADAR,'운영자 화면이 활성 상태이고',key='operator_active_required'),
        f('차단 해제 후 이전 수동 입력·추종·미션을 자동 재개하지 않는다.',RADAR,'이전 수동 입력·추종·미션은 재개하지 않습니다',key='no_old_command_replay')]),
    ('complex','test_candidate','GUI가 멈춰도 프레임 나이를 감시하나요? 이 구조가 모든 물리적 정지를 보장하나요?',[
        f('GUI가 멈춰도 UDP worker가 원래 프레임 timestamp를 감시한다.',DASH,'GUI가 멈추거나 새 인식이 오지 않아도 UDP worker',key='udp_watches_timestamp'),
        f('실제 OS·전원·UDP 장애에 대한 정지 보장은 없다.',DASH,'실제 OS·전원·UDP 장애에 대한 정지 보장은 없으며',key='no_universal_stop_guarantee'),
        f('기존 ESP32 워치독과 물리 차단 수단도 함께 사용해야 한다.',DASH,'기존 ESP32 워치독과 물리 차단 수단을 함께 사용',key='physical_cutoff_needed')]),
    ('insufficient','development','우리 로봇 UDP 통신의 실측 지연은 항상 1ms 이내인가요?',[]),
    ('insufficient','test_candidate','V19 실물에서 2A 연속 부하의 안전 운전이 검증됐나요?',[
        f('V19 문서는 부하 전류·발열·전원 보호를 실물 확인 대상으로 남긴다.',V19,'부하 전류, 발열과 전원 보호는 실물 확인',required=False,scope='PCB_V19_CAD',key='load_verification_missing')]),
    ('live_state','development','문서만 보고 지금 로봇의 실제 이동 속도를 알려줘.',[]),
    ('live_state','test_candidate','지금 연결된 실물 PCB가 V18인지 V19인지 문서로 정해줘.',[]),
    ]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    prior=[json.loads(x) for x in (ROOT/'docs/verification/rag_v3_phase2/gold_cases.jsonl').read_text(encoding='utf-8').splitlines()]
    entries=[]
    for index,(category,split,question,facts) in enumerate(cases(),1):
        for n,entry in enumerate(facts,1):entry['fact_id']=f'G25-{index:03}-F{n:02}'
        words=set(re.findall(r'[A-Za-z0-9]+|[가-힣]{2,}',question.lower()))
        ranked=sorted(prior,key=lambda r:len(words&set(re.findall(r'[A-Za-z0-9]+|[가-힣]{2,}',r['question'].lower()))),reverse=True)[:3]
        known={5:'RAG-KO-061',6:'RAG-KO-062',7:'RAG-KO-063'}.get(index)
        rationale=[]
        if index==17:
            rationale=[fact('프로토콜 표는 PC 통신의 UDP 8888/8889를 명시하며 실측 1ms 보장의 근거로 쓰지 않는다.',
                            'docs/CONTROL_SIMPLE_V2_PLAN.md','UDP 8888/8889, heartbeat',False,scope='bounded_negative_review')]
        elif index==19:
            rationale=[fact('실시간 상태는 문서 대신 런타임 스냅숏에서 조회하는 경로다.',SCHEMA,'상태는 실제 런타임 스냅숏에서',False,scope='live_state_route')]
        elif index==20:
            rationale=[fact('현재 연결된 PCB 리비전은 문서만으로 확인할 수 없다는 안내를 제공한다.',
                            'python_controller/knowledge/variants.py','현재 연결된 PCB 리비전은 문서로 확인할 수 없습니다.',False,scope='as_built_unknown')]
        entries.append({'case_id':f'G25-{index:03}','category':category,'split':split,'question':question,
                        'expected_facts':facts,'answerable_from_docs':category not in ('insufficient','live_state'),
                        'should_abstain_on_requested_assertion':category in ('insufficient','live_state'),
                        'required_response_behavior':('확인된 문서 범위만 설명하고 현재 상태·미검증 성능 단정을 보류' if category in ('insufficient','live_state') else '필수 atomic fact를 정확한 범위와 연결해 설명'),
                        'prior_semantic_overlap':{'status':'confirmed_seen_diagnostic' if known else 'review_required_not_certified_unseen',
                                                  'confirmed_case':known,'lexical_review_candidates':[r['case_id'] for r in ranked],
                                                  'note':'Lexical neighbors are review aids, not semantic equivalence decisions.'},
                        'human_review_status':'pending','human_approved':False,
                        'independent_review':None,'voice_readability_score':None,'abstention_rationale_sources':rationale})
    output=OUT/'gold_core20.jsonl'
    data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in entries)
    if output.exists() and output.read_text(encoding='utf-8')!=data:
        if (OUT/'core20_frozen.json').exists():raise ValueError('Frozen core20 differs: create a new dataset version')
        previous=[json.loads(s) for s in output.read_text(encoding='utf-8').splitlines()]
        if any(r.get('human_approved') or r.get('human_review_status')!='pending' or r.get('independent_review') is not None for r in previous):
            raise ValueError('Do not overwrite human review')
        for name in ('gold_core20.jsonl','GOLD_CORE20_REVIEW.md','gold_core20_review.csv','core20_manifest.json'):
            old=OUT/name;backup=OUT/(name+'.draft0')
            if old.exists() and not backup.exists():backup.write_bytes(old.read_bytes())
    output.write_text(data,encoding='utf-8')
    md=['# 핵심 20문항 Gold 검토표','',
        '모든 사실은 에이전트가 고정 리비전 원문과 대조한 **검토 초안**입니다. 인간 검토 완료가 아닙니다.',
        '개발 10 / 시험 후보 10. 기존 120개와 주제·의미가 겹칠 수 있어 시험 후보는 깨끗한 신규 holdout으로 인증하지 않습니다.',
        '필수/선택 사실을 나눴으며 미확인 상태·성능을 사실로 채우지 않습니다. 최종 정답 승인 전에는 확정 품질 점수를 산출하지 마세요.','',
        '| ID | 유형 | 분할 | 질문 | 필수 사실 수 | 인간 승인 |','|---|---|---|---|---:|---|']
    csvrows=[]
    for row in entries:
        md.append(f'| {row["case_id"]} | {row["category"]} | {row["split"]} | {row["question"]} | {sum(f["required"] for f in row["expected_facts"])} | 미검토 |')
    for row in entries:
        md.extend(['',f'## {row["case_id"]} · {row["question"]}',
                   f'분할: {row["split"]} · 기존 의미 중복: {row["prior_semantic_overlap"]["status"]} · 검색 보조 후보: '+', '.join(row['prior_semantic_overlap']['lexical_review_candidates']),
                   '',f'답변 행동: {row["required_response_behavior"]}', '',
                   '| Fact ID | 필수 | 독립 사실 | 출처·리비전·행 | 정확한 근거 | 검토 |','|---|---|---|---|---|---|'])
        for entry in row['expected_facts']:
            excerpt=entry['evidence_excerpt'].replace('|','&#124;')
            md.append(f'| {entry["fact_id"]} | {"필수" if entry["required"] else "선택"} | {entry["statement"]} | {entry["source_path"]} @ {REV}:{entry["line"]} | {excerpt} | □ 승인 □ 수정 □ 보류 |')
            csvrows.append({'case_id':row['case_id'],'category':row['category'],'split':row['split'],'question':row['question'],**entry,'human_review':''})
        if not row['expected_facts']:
            md.append('| — | 단정 보류 | 문서만으로 요청한 실측/현재 상태를 확정하지 않음 | 아래 범위 검토 필요 | 임의 정답 수치 없음 | □ 승인 □ 수정 □ 보류 |')
            csvrows.append({'case_id':row['case_id'],'category':row['category'],'split':row['split'],'question':row['question'],
                            'fact_id':'','required':False,'statement':row['required_response_behavior'],
                            'source_revision':REV,'scope':'bounded_negative_or_live_query','human_approved':False,
                            'annotation_status':'human_review_pending','human_review':''})
        if row['category'] in ('insufficient','live_state'):
            md.extend(['','부재/현재 상태 검토 범위: ARCHITECTURE.md, DASHBOARD_GUIDE.md, CONTROL_SIMPLE_V2_PLAN.md, V19 FACTS 및 variants.py. 문서 검색 범위에서 정밀 실측/현재 장치 상태의 직접 근거를 확보하지 못함을 뜻하며 전 세계적 부재 증명이 아닙니다.'])
            for rationale in row['abstention_rationale_sources']:
                md.append(f'- 보류/경로 근거: {rationale["statement"]} — {rationale["source_path"]} @ {REV}:{rationale["line"]} / `{rationale["evidence_excerpt"]}`')
                for csvrow in csvrows:
                    if csvrow['case_id']==row['case_id'] and not csvrow.get('fact_id'):
                        csvrow.update({k:rationale[k] for k in ('source_path','source_revision','line','evidence_excerpt','source_sha256','scope')})
    (OUT/'GOLD_CORE20_REVIEW.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    keys=['case_id','category','split','question','fact_id','required','statement','source_path','source_revision','line','evidence_excerpt','scope','source_sha256','fact_key','annotation_status','human_approved','human_review']
    with (OUT/'gold_core20_review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(csvrows)
    manifest={'case_count':20,'atomic_fact_count':sum(len(r['expected_facts']) for r in entries),
              'categories':{c:sum(r['category']==c for r in entries) for c in sorted({r['category'] for r in entries})},
              'splits':{s:sum(r['split']==s for r in entries) for s in ('development','test_candidate')},
              'source_revision':REV,'sha256':hashlib.sha256(data.encode()).hexdigest(),
              'human_approved':False,'unseen_holdout_certified':False}
    (OUT/'core20_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':main()
