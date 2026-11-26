# 기존 061·062·063 상세 실패 분석표

기존 실제 응답/근거/의미 판정을 그대로 읽었습니다. 이 단계의 새 외부 호출은 0회입니다.
아래 False Rejection 판정은 원문 대조에 의한 에이전트 분석이며 인간 독립 검토 완료가 아닙니다.

## RAG-KO-061

| 항목 | 내용 |
|---|---|
| 원래 질문 | 우리 프로그램은 노트북이랑 로봇이 어떻게 통신해? |
| 핵심 의도 | PC와 ESP32 사이의 통신 방식과 명령/텔레메트리 방향. 카메라 파이프라인 상세는 질문의 핵심이 아님. |
| 문서로 확인 가능한 핵심 정답 | PC 통신은 UDP를 사용한다. / 문서의 제어 흐름에는 ESP32로 명령을 전달하는 경로가 있다. / 문서의 제어 흐름에는 PC로 텔레메트리를 받는 경로가 있다. |
| 최종 답변 | 데이터 흐름은 카메라 워커 → 비전 워커 → GUI 상태 머신(50ms QTimer) → 안전 명령 순서로 처리되며, GUI 스레드에서 직접 카메라 읽기, AI 추론, 소켓 수신, 로그 플러시를 수행하지 않습니다.<br><br>나머지 내용은 근거 검증을 통과하지 못해 설명을 보류했어요. |
| 누락된 핵심 사실 | 최종 답변에 UDP 방식 및 ESP32 명령/PC 텔레메트리 방향이 빠짐. 남은 카메라/GUI 설명은 핵심 질문을 대체하지 못함. |
| 역사적 의미 검증 | [{'claim_id': 'c1', 'relation': 'ENTAILMENT'}] |
| 평가 문제와 실제 답변 문제 | 기존 gold는 수정하지 않음. core20은 별도 원자적 초안. 실제 인용/규칙 실패는 기존 gold와 독립적으로 재현됨 |

| 근거 ID | 문서 / 절 | 후보 번호 | index version |
|---|---|---|---|
| 2 | docs/ARCHITECTURE.md / 현재 대시보드 확장 | [0, 1, 2] | legacy-tokenizer-unavailable |
| 3 | docs/ARCHITECTURE.md / 현재 대시보드 확장 | [0, 1] | legacy-tokenizer-unavailable |
| 5 | docs/ARCHITECTURE.md / 데이터 흐름과 파일 책임 | [0, 1, 2] | legacy-tokenizer-unavailable |
| 6 | docs/ARCHITECTURE.md / 데이터 흐름과 파일 책임 | [0] | legacy-tokenizer-unavailable |
| 29 | docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md / 대화 예시 | [0, 1] | legacy-tokenizer-unavailable |
| 9 | docs/ARCHITECTURE.md / 2차 범위 | [0] | legacy-tokenizer-unavailable |

| 생성 claim | 생성된 전체 문장 | 연결 근거 | 결정론적 결과 | 기록된 의미 판정 | 원인 구분 |
|---|---|---|---|---|---|
| c1 | 노트북(PC)에서 실행되는 Python 대시보드 프로그램이 UDP 소켓을 통해 ESP32 기반 로봇과 통신합니다. `communication/esp32_udp.py` 모듈이 소켓을 전담하여 소유합니다. | [{'evidence_id': 5, 'quote_index': 0}] | ENTITY_MISMATCH | NOT_CALLED | 정상 차단: 전역 문서에서 사실일 수 있지만 선택한 인용은 UDP/ESP32 통신 전체를 뒷받침하지 않음. 복수 사실과 인용 연결 오류. |
| c2 | 기본 실행 모드에서는 UDP 소켓을 사용하지 않고 화면 시뮬레이션으로 동작하며, 실제 로봇 제어 시 ESP32의 운동학·PID·엔코더·워치독 펌웨어를 재사용하고 상위 제어 로직만 Python으로 추가합니다. | [{'evidence_id': 3, 'quote_index': 0}] | DESIGN_QUALIFIER_DROPPED | NOT_CALLED | False Rejection: 목표 손 제스처의 목표를 설계 목표로 오인. 원문에 기존 ESP32 기능 재사용과 Python 상위 제어가 직접 명시됨. |
| c3 | 데이터 흐름은 카메라 워커 → 비전 워커 → GUI 상태 머신(50ms QTimer) → 안전 명령 순서로 처리되며, GUI 스레드에서 직접 카메라 읽기, AI 추론, 소켓 수신, 로그 플러시를 수행하지 않습니다. | [{'evidence_id': 2, 'quote_index': 0}] | 통과 | ENTAILMENT | 기록된 ENTAILMENT로 통과했으나 통신 질문에 대한 직접성/필수 사실은 부족. 한 블록에 여러 작업 사실이 묶임. |

### 선택된 실제 인용과 규칙 일치 지점

**c1**
- 규칙이 일치한 부분: `{'design': [], 'negative': [{'token': '없', 'context': '한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communi'}, {'token': 'False', 'context': 'dar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTAC'}, {'token': '없', 'context': 'CLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을'}], 'conditional': []}`

```text
`control/state_machine.py`가 활성화·정지·재개를 소유하고, `follow_controller.py`가 계산과 제한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communication/esp32_udp.py`만 소켓을 소유한다. `radar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTACLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을
```
**c2**
- 규칙이 일치한 부분: `{'design': [{'token': '목표', 'context': '청 범위는 Windows 웹캠 → 사람 선택·추적 → 목표 손 제스처 → 상대 크기 추종 명령 → 화면 시뮬레이션이다. 기본 실행'}], 'negative': [{'token': '없', 'context': '→ 화면 시뮬레이션이다. 기본 실행에는 UDP 소켓이 없다. ESP32의 운동학·PID·엔코더·워치독을 재사용하며 상위 제어만 '}], 'conditional': []}`

```text
요청 범위는 Windows 웹캠 → 사람 선택·추적 → 목표 손 제스처 → 상대 크기 추종 명령 → 화면 시뮬레이션이다. 기본 실행에는 UDP 소켓이 없다. ESP32의 운동학·PID·엔코더·워치독을 재사용하며 상위 제어만 Python으로 추가했다. 원본 ZIP과 기존 로봇의 동작 설정은 보존한다.
```
**c3**
- 규칙이 일치한 부분: `{'design': [], 'negative': [{'token': '않', 'context': 'I 추론, 소켓 recv, 로그 flush를 실행하지 않는다. 느린 처리 중 오래된 프레임 큐를 쌓지 않는다. GUI가 멈추어도'}, {'token': '않', 'context': '하지 않는다. 느린 처리 중 오래된 프레임 큐를 쌓지 않는다. GUI가 멈추어도 UDP worker가 마지막으로 받은 perce'}, {'token': '않', 'context': ' 전환한다. 오래된 결과로 lease 시간을 갱신하지 않는다.'}], 'conditional': []}`

```text
Camera worker → 최신 슬롯(1개) → Vision worker → 최신 결과 슬롯(1개) → GUI 50ms QTimer의 상태 머신 → 안전 명령 순서다. GUI 스레드에서 카메라 read, AI 추론, 소켓 recv, 로그 flush를 실행하지 않는다. 느린 처리 중 오래된 프레임 큐를 쌓지 않는다. GUI가 멈추어도 UDP worker가 마지막으로 받은 perception lease의 실제 나이를 확인하며 STOP으로 전환한다. 오래된 결과로 lease 시간을 갱신하지 않는다.
```
## RAG-KO-062

| 항목 | 내용 |
|---|---|
| 원래 질문 | Qt 화면은 어떤 기술로 만든 거야? |
| 핵심 의도 | 사용한 Qt Python 바인딩(PySide6)과 UI 기술. 설치 절차·파일 경로·일반 대화 SSE는 부가 정보. |
| 문서로 확인 가능한 핵심 정답 | 기본 Qt UI에 사용하는 Python 바인딩은 PySide6다. |
| 최종 답변 | 관련 문서는 있지만 질문에 답할 직접 근거와 설명을 검증하지 못했어요. 확인할 항목이나 문서 범위를 더 구체적으로 알려 주세요. |
| 누락된 핵심 사실 | PySide6라는 실제 바인딩과 질문 결론 전체가 빠짐. 가져온 6개 근거의 body/후보 어느 쪽에도 PySide6가 없음. |
| 역사적 의미 검증 | 호출 안 됨: 전단에서 모두 제외 |
| 평가 문제와 실제 답변 문제 | 기존 gold는 수정하지 않음. core20은 별도 원자적 초안. 실제 인용/규칙 실패는 기존 gold와 독립적으로 재현됨 |

| 근거 ID | 문서 / 절 | 후보 번호 | index version |
|---|---|---|---|
| 29 | docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md / 대화 예시 | [0, 1] | legacy-tokenizer-unavailable |
| 17 | docs/DASHBOARD_GUIDE.md / 파일 변경 | [0, 1, 2] | legacy-tokenizer-unavailable |
| 28 | docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md / 현재 LLM 동작 | [0, 1] | legacy-tokenizer-unavailable |
| 3 | docs/ARCHITECTURE.md / 현재 대시보드 확장 | [0, 1] | legacy-tokenizer-unavailable |
| 27 | docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md / 스키마 구현 현황 | [0, 1] | legacy-tokenizer-unavailable |
| 11 | docs/DASHBOARD_GUIDE.md / 실행 | [0, 1, 2] | legacy-tokenizer-unavailable |

| 생성 claim | 생성된 전체 문장 | 연결 근거 | 결정론적 결과 | 기록된 의미 판정 | 원인 구분 |
|---|---|---|---|---|---|
| c1 | Qt 화면은 Python의 Qt 프레임워크(PyQt/PySide)를 사용하여 구현된 4탭 대시보드로, 입력·영상·그래프 등을 실행 가능한 형태로 제공합니다. | [{'evidence_id': 17, 'quote_index': 1}] | ENTITY_MISMATCH | NOT_CALLED | 정상 차단: 선택 인용에 PyQt/PySide 바인딩 근거가 없고 4탭 정보는 다른 인용 후보에 있음. 실제 PySide6 근거는 별도 문서 절에 존재. |
| c2 | 대시보드 실행 파일은 `python_controller/visualization/dashboard.py`에 있으며, 런타임 조립과 모드 전환은 `python_controller/dashboard_runtime.py`에서 담당합니다. | [{'evidence_id': 17, 'quote_index': 0}] | CONDITION_DROPPED | NOT_CALLED | 규칙 오탐: 인용의 latest-only worker에 only가 있어 전역 조건 누락으로 처리. 역할/경로 두 사실을 한 블록에 묶었으며 파일명은 음성 답변에 불필요. |
| c3 | LLM 응답 스트림(NVIDIA 모델)은 웹의 SSE 연결로 문장 중간부터 텍스트 조각을 전달하며, Qt는 화면 갱신 주기에 맞춰 현재까지 받은 텍스트를 표시합니다. | [{'evidence_id': 28, 'quote_index': 0}] | CONDITION_DROPPED | NOT_CALLED | 부분 정상 사실의 과잉 차단 후보: 대화 문맥에서 문서 발췌를 제외한다는 다른 절을 조건으로 적용. 다만 일반 대화 범위가 생략된 전체 LLM 스트리밍 일반화는 의미 재검토가 필요. |
| c4 | 기본 실행은 `main.py` 또는 `run_simulation.bat`로 Qt 대시보드를 열며, Qt 설치는 `requirements.txt`의 의존성으로 `pip install`로 준비됩니다. | [{'evidence_id': 11, 'quote_index': 0}] | SOURCE_TEXT_IN_REPLY | NOT_CALLED | 정상 스타일 차단: requirements.txt가 본문에 포함됨. 설치/실행 경로는 질문 핵심도 아니며 복수 사실 블록으로 같이 소실. |

### 선택된 실제 인용과 규칙 일치 지점

**c1**
- 규칙이 일치한 부분: `{'design': [{'token': '설계', 'context': 's.txt`, `.gitignore`, README, 설계·검증 문서.'}], 'negative': [], 'conditional': []}`

```text
- `main.py`, `run_simulation.bat`: UI 선택 및 새 기본 배치 실행.
- `control/follow_controller.py`, `control/state_machine.py`: 요청 명령·제스처 진단·긴급 정지 latch.
- `vision/gesture_recognizer.py`: 차단 이전 Raw 진단과 단계 시간.
- `communication/esp32_udp.py`: TX/ACK/STOP 이력 및 추가 배열 검증.
- `config/default.json`, `config/__init__.py`: RPM 환산 반지름과 검증.
- `requirements.txt`, `.gitignore`, README, 설계·검증 문서.
```
**c2**
- 규칙이 일치한 부분: `{'design': [], 'negative': [], 'conditional': [{'token': 'only', 'context': 'dashboard_runtime.py`: latest-only worker·제어 조립·모드 전환.\n- `python_controlle'}]}`

```text
- `python_controller/dashboard_runtime.py`: latest-only worker·제어 조립·모드 전환.
- `python_controller/visualization/dashboard.py`: 실행 가능한 Qt 4탭·입력·영상·그래프.
- `python_controller/monitoring.py`: 피드백 환산·결과 기록·비동기 logger.
- `tests/test_dashboard_core.py`, `test_dashboard_runtime.py`, `test_dashboard_telemetry.py`, `test_dashboard_ui.py`.
- 이 안내, [대시보드 검증 기록](verification/DASHBOARD_VALIDATION.md), [합성 화면 5종](verification/dashboard_preview).
```
**c3**
- 규칙이 일치한 부분: `{'design': [], 'negative': [], 'conditional': [{'token': '제외', 'context': ' 실행 영수증과 검색 문서 발췌는 대화 생성 문맥에서 제외한다. 최근 답변을 반복하면 한 번 새 답을 요청한다.\n4. 상태 질문은'}]}`

```text
2. **의도 분류**는 현재 메시지와 미실행 초안의 동작 목록으로 JSON 의도 객체 하나를 반환한다. Pydantic이 필드를 검사하며, 이동·편집 제안은 현재 사용자 원문과 대조해 방향, 수치, 단계와 버전을 확인한다.
3. **대화 생성**은 일반 대화와 확인 질문에서 별도로 NVIDIA 응답 스트림을 연다. 모델이 보내는 텍스트 조각은 문장 중간부터 웹의 SSE 연결로 전달된다. Qt는 현재까지 받은 텍스트를 화면 갱신 주기에 표시한다. 완료된 답변을 대화 기록에 저장한다. 최근 사용자 입력과 읽기 전용 답변을 합쳐 최대 10개 메시지를 문맥으로 사용한다. 장치 응답, 실행 영수증과 검색 문서 발췌는 대화 생성 문맥에서 제외한다. 최근 답변을 반복하면 한 번 새 답을 요청한다.
4. 상태 질문은 런타임 스냅숏을 조회하고, 기술·매뉴얼 질문은 로컬 SQLite FTS5 근거를 읽어 출처를 붙인다. 로컬 벡터 검색과 승인된 Git 문서의 클라우드 인용 선택은 별도 옵션이다. 알려진 문서 지시문 패턴을 발췌에서 걸러내고 클라우드 인용을 실제 원문에 대조한다. 문서 답변은 실행 경로와 분리된다.
5. LangGraph는 분류, 읽기 전용 답변, 문서 검색과 버전이 고정된 검토를 처리한다. Qt 운영자 승인과 실제 이동은 서버의 기존 실행 경로를 거친다. 실행 중 새 이동은 별도 미래 초안에 저장할 수 있다.
```
**c4**
- 규칙이 일치한 부분: `{'design': [], 'negative': [{'token': '않', 'context': '설치를 완료했다. 새 PC 전체 부트스트랩은 검증하지 않았다.'}], 'conditional': []}`

```text
`run_simulation.bat`도 같은 대시보드를 연다. Python 진입의 기본 OpenCV UI와 `--demo`는 기존 테스트와 사용법의 호환성을 위해 유지했다. Qt 설치는 `python -m pip install -r requirements.txt`로 준비하며, 현재 PC의 가상환경에는 설치를 완료했다. 새 PC 전체 부트스트랩은 검증하지 않았다.
```
## RAG-KO-063

| 항목 | 내용 |
|---|---|
| 원래 질문 | 레이더가 UNKNOWN이면 무슨 뜻이야? |
| 핵심 의도 | 레이더 UNKNOWN의 의미. 모든 UNKNOWN 원인을 하나로 단정하지 말고 수신 사유와 유효 상태를 구분. |
| 문서로 확인 가능한 핵심 정답 | UNKNOWN/null은 값을 받지 못했거나 검증할 수 없다는 표시다. |
| 최종 답변 | 레이더가 미연결 상태이거나 관측 데이터가 없으면 시스템은 이를 UNKNOWN으로 처리하며, 1차 시뮬레이션에서는 레이더 관측 없이 실행하고 화면에 UNKNOWN을 표시합니다.<br><br>나머지 내용은 근거 검증을 통과하지 못해 설명을 보류했어요. |
| 누락된 핵심 사실 | 유효하지 않은 센서 상태와 수신 부재의 구분이 빠짐. 미연결·시뮬레이션 사례만 남음. |
| 역사적 의미 검증 | [{'claim_id': 'c1', 'relation': 'ENTAILMENT'}] |
| 평가 문제와 실제 답변 문제 | 기존 gold는 수정하지 않음. core20은 별도 원자적 초안. 실제 인용/규칙 실패는 기존 gold와 독립적으로 재현됨 |

| 근거 ID | 문서 / 절 | 후보 번호 | index version |
|---|---|---|---|
| 29 | docs/DIG_AGENT_SCHEMA_AND_DIALOGUE.md / 대화 예시 | [0, 1] | legacy-tokenizer-unavailable |
| 23 | docs/RADAR_OPERATION_GUIDE.md / 3. PC 대시보드에서 수신 확인 | [0, 1] | legacy-tokenizer-unavailable |
| 5 | docs/ARCHITECTURE.md / 데이터 흐름과 파일 책임 | [0, 1, 2] | legacy-tokenizer-unavailable |
| 19 | docs/RADAR_OPERATION_GUIDE.md / 레이더 연결 및 실행 방법 | [0] | legacy-tokenizer-unavailable |
| 25 | docs/RADAR_OPERATION_GUIDE.md / 레이더를 연결하지 않은 바퀴 시험 | [0, 1] | legacy-tokenizer-unavailable |
| 9 | docs/ARCHITECTURE.md / 2차 범위 | [0] | legacy-tokenizer-unavailable |

| 생성 claim | 생성된 전체 문장 | 연결 근거 | 결정론적 결과 | 기록된 의미 판정 | 원인 구분 |
|---|---|---|---|---|---|
| c1 | 레이더 상태가 UNKNOWN이면 레이더 센서로부터 유효한 상태(CLEAR 또는 EVADE)를 수신하지 못했거나, 수신된 패킷이 잘못되어 상태를 판단할 수 없는 상황을 의미합니다. | [{'evidence_id': 23, 'quote_index': 2}, {'evidence_id': 5, 'quote_index': 0}] | INVALID_CITATION | NOT_CALLED | 정상 차단: 범위 밖 quote_index. 문장의 사실 여부와 인용 계약 오류를 분리해야 함. |
| c2 | UNKNOWN 상태는 안전 차단 해제 조건인 '최신 CLEAR가 연속 1초 유지'를 충족하지 않으므로, 이미 차단된 상태를 유지하거나 새로운 차단을 유발합니다. | [{'evidence_id': 23, 'quote_index': 2}] | INVALID_CITATION | NOT_CALLED | 정상 차단: 범위 밖 quote_index. CLEAR 조건은 원문에서 찾을 수 있어도 임의 번호를 서버가 자동 승인해서는 안 됨. |
| c3 | 레이더가 미연결 상태이거나 관측 데이터가 없으면 시스템은 이를 UNKNOWN으로 처리하며, 1차 시뮬레이션에서는 레이더 관측 없이 실행하고 화면에 UNKNOWN을 표시합니다. | [{'evidence_id': 5, 'quote_index': 0}] | 통과 | ENTAILMENT | 기록된 ENTAILMENT로 통과. 원문 전체와 맞지만 실제 전달된 legacy 청크가 UNKNOWN을 뒤에서 끝나 근거 완결성이 불충분. |
| c4 | RADAR_TIMEOUT은 수신 지연 경고로, 마지막 레이더 상태와 경과 시간을 유지하며 단순 지연만으로 새 차단을 만들지 않지만, 이미 EVADE나 UNKNOWN 등으로 차단된 상태는 유지됩니다. | [{'evidence_id': 23, 'quote_index': 3}] | INVALID_CITATION | NOT_CALLED | 정상 차단: 범위 밖 quote_index. RADAR_TIMEOUT 설명은 관련 원문이 있으나 번호 연결은 잘못됨. |

### 선택된 실제 인용과 규칙 일치 지점

**c1**
- 규칙이 일치한 부분: `{'design': [], 'negative': [{'token': '없', 'context': '한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communi'}, {'token': 'False', 'context': 'dar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTAC'}, {'token': '없', 'context': 'CLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을'}], 'conditional': []}`

```text
`control/state_machine.py`가 활성화·정지·재개를 소유하고, `follow_controller.py`가 계산과 제한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communication/esp32_udp.py`만 소켓을 소유한다. `radar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTACLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을
```
범위 오류: `[{'support': {'evidence_id': 23, 'quote_index': 2}, 'available_indices': [0, 1]}]`
**c2**
- 규칙이 일치한 부분: `{'design': [], 'negative': [], 'conditional': []}`
범위 오류: `[{'support': {'evidence_id': 23, 'quote_index': 2}, 'available_indices': [0, 1]}]`
**c3**
- 규칙이 일치한 부분: `{'design': [], 'negative': [{'token': '없', 'context': '한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communi'}, {'token': 'False', 'context': 'dar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTAC'}, {'token': '없', 'context': 'CLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을'}], 'conditional': []}`

```text
`control/state_machine.py`가 활성화·정지·재개를 소유하고, `follow_controller.py`가 계산과 제한만 담당한다. 별도의 추상 SafetyManager 없이 상태 머신에 안전 검사를 모아 작은 구조를 유지했다. `communication/esp32_udp.py`만 소켓을 소유한다. `radar_interface.py`는 미연결을 `None/False`로 반환한다. 미연결 관측을 실제 안전 입력으로 연결하면 `OBSTACLE_STOP`이며, 1차 시뮬레이션은 레이더 관측 없이 실행하고 화면에는 UNKNOWN을
```
**c4**
- 규칙이 일치한 부분: `{'design': [], 'negative': [], 'conditional': []}`
범위 오류: `[{'support': {'evidence_id': 23, 'quote_index': 3}, 'available_indices': [0, 1]}]`
