# D.I.G. Unified Control Agent 통합 및 검증

통합 기준은 로컬 `0fb0a6f`와 그 위에 작업 중이던 D.I.G. Blue 및 웹 관제다. ZIP의 README, 아키텍처, 스키마·도구, GitHub 연결표, 작업 지시를 검토했다. ZIP은 테스트와 설계 참고로 사용했고, 로봇 제어에는 기존 Python 구현을 연결했다.

| 단계 | 이번 변경 파일 | 확인한 동작 |
| --- | --- | --- |
| 1. Intent 및 Nemotron | `python_controller/llm/agent_intent.py`, `nvidia_client.py` | 기존 `Move / Rotate / Stop` 재사용. 중복 JSON 키, 승인 필드, 지원하지 않는 모드, 잘못된 수치 및 입력에 없는 이동·수정을 거부한다. |
| 2. 관제 연결 | `python_controller/llm/unified_agent.py`, `python_controller/dashboard_runtime.py` | `get_status`는 현재 Python 상태를 읽는다. `navigate`는 화면에만 반영된다. 실제 모드 변경은 기존 STOP 검증을 기다리며 Agent 요청만으로 활성화되지 않는다. |
| 3. 대화·계획 검토 | `python_controller/llm/agent_graph.py`, `requirements-agent.txt` | 실제 LangGraph 1.2.22에서 다중 턴 문맥, 계획 검토 중 상태 질의, 수정 버전, 승인 인터럽트를 확인했다. 각도 제어가 검증되지 않은 계획은 미리보기만 표시한다. |
| 4. 운영자 승인·실행 | `python_controller/llm/unified_agent.py`, `central_control.py`, `python_controller/dashboard_runtime.py` | 표시한 계획과 버전, 세션·부팅·복구·레이더 세대, STOP 요청을 확인하고 승인권을 한 번만 소비한다. 동일 RobotSession에서 기존 실행기가 한 번만 움직이는 것을 모의 ESP32로 확인했다. |
| 5. 화면·실행 진입점 | `python_controller/visualization/agent_panel.py`, `dashboard.py`, `python_controller/web_dashboard.py`, `python_controller/web/{index.html,app.js,style.css}`, `python_controller/main.py`, `run_simulation.bat`, `README.md` | Qt 대화·승인·실행 상태와 웹 시뮬레이션을 연결했다. 웹에서 계획 생성, 수정, 긴급 정지, 계획 폐기를 확인했다. |
| 6. 회귀 검증 | `tests/test_unified_agent.py` | 승인 재사용·만료·복구 변경, PAUSE, 모델 호출 중 정지, 레이더 EVADE/UNKNOWN, 손동작 자동 재활성화 방지, Qt Space와 지연 입력 취소를 추가 검증했다. |

## 권한 경계

- `--agent-enabled`는 명시적으로 켜는 옵션이다. Agent의 대화 기록과 계획 검토는 현재 프로세스 메모리에 있다. 체크포인트에는 승인권이나 전송 권한을 넣지 않는다.
- 정형화된 긴급 정지 입력과 상시 정지 버튼은 Python의 기존 `ESTOP` 요청을 직접 호출한다. 모델이 정지 Intent를 분류했다는 이유만으로 정지나 계획 취소를 수행하지 않는다.
- 승인권은 Qt에서 표시 중인 버전을 확인하고 운영자가 체크한 뒤 승인 버튼을 눌렀을 때 발급된다. 대기 중 상태 변화는 승인을 폐기한다. 실행기가 공유 거리 제어 채널을 얻는 순간에 동일 세션·안전 조건을 다시 확인한다.
- 긴급 정지 확인은 별도 운영자 동작이다. 실물 연결 시 새 STOP, 정상 통신, 레이더 CLEAR 및 차단 해제가 필요하다. 확인 뒤에도 새 제어 입력이 필요하다.
- 웹 Agent는 기본 합성 또는 선택적 실카메라 시뮬레이션이다. 현장 승인과 구동은 Qt 경로로 연결했다.
- 기존 `RobotSession`, `LLMMotionExecutor`, `control_simple_v2.ino`에 변경이 없다.

## 테스트 결과

| 구분 | 결과 |
| --- | --- |
| 신규 Agent 회귀 | 16개 통과 |
| 전체 프로젝트 | 206개 중 205 통과. 기존 로컬 `secrets.h` 파일 존재 검사 1건 실패 |
| ZIP 참조 구현 | 격리 실행 51개 통과. LangGraph 테스트 포함 |
| Qt 합성 실행 | `--dashboard-preview --agent-enabled --smoke-seconds 2` 종료 코드 0 |
| 실 Nemotron API | 고정 한국어 4개에서 화면 이동·상태 조회·계획 생성·첫 단계 수정 분류 확인. 각각 3.84초, 1.04초, 1.42초, 2.07초 |
| 브라우저 합성 세션 | 실 Nemotron 계획 생성·수정과 로컬 `멈춰` 경로, 계획 폐기, 실행 비활성 확인 |

실 ESP32·모터·레이더 센서의 물리 동작과 정지 전달은 이번에 검증하지 않았다. 모의 ESP32와 실 모델 응답은 별도의 증거다.

## Robot OS 미션 컨트롤

이 절은 `d8f2d1c`를 기준으로 적용한 제품형 웹 관제와 실행 이력 검증을 기록한다. 위 표는 초기 Agent 통합 결과다.

- 계획 편집은 마지막 단계 또는 순번을 해석하고, 변경 내용을 원문과 기존 계획에 대조한다. 서버에서 현재 계획 ID와 버전을 확인하고 전체 계획을 다시 검증한 뒤 수정 전후를 표시한다. 실물 실행에는 변경된 버전의 Qt 승인이 필요하다.
- Qt에서 시작한 실행의 계획 버전과 실행기 이벤트는 `logs/llm/mission-history.sqlite3`에 기록된다. SQLite 쓰기는 별도 스레드가 맡는다. 웹 미션 화면과 작업 이력 화면은 기록을 읽기 전용으로 표시한다. 종료 이벤트가 없는 실행은 종료 미확인으로 표시한다.
- 웹 2D 3WD 화면의 점선은 계획 경로이고 청록색 선은 명령 기반 가상 재생이다. STOP 확인은 실행기 이벤트에서 가져오며, 2D 화면은 실측 위치나 엔코더 추정을 표시하지 않는다.

| 검증 범위 | 결과 |
| --- | --- |
| 계획 편집 | 한국어 표현 42개를 고정 분류 결과로 검증했다. 단계 변경, 애매한 입력 거부, 수정 전후, 이전 버전 승인 거부를 확인했다. |
| 모의 ESP32와 SQLite | 4단계 실행의 STOP 이벤트 순서와 저장소 재조회, STOP 미확인 시 중단, 레이더 차단, 중복·누락 기록 처리 및 저장 지연과 STOP 경로 분리를 확인했다. |
| 실제 Chrome | 1440px, 1000px, 390px에서 합성 영상·고정 모델 응답·시뮬레이션 이력으로 계획 편집, 가상 재생, 이력 조회, 카메라 해제, 긴급 정지를 확인했다. 가상 재생 중 제어 요청은 0건이었다. |
| 전체 자동 테스트 | 격리한 소스에서 226개를 실행해 225개 통과, 1개 건너뜀을 기록했다. 건너뛴 검사는 비공개 원본 펌웨어 아카이브가 필요하다. |
| 실물과 외부 서비스 | 실 ESP32·레이더·카메라, 실물 구동·실측 위치, 실 Nemotron 분류는 이번 작업에서 미검증이다. |
