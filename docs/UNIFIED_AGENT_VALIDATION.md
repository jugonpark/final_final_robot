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
