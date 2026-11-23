# D.I.G. Prototype A+B v1.1

## 사용법

프로젝트 루트에서 LLM 의존성을 설치하고 `.env.example`을 참고해 환경변수나 로컬 `.env`에 NVIDIA API 키와 `NVIDIA_MODEL`을 설정한다. CLI는 카메라·Qt 패키지 없이 실행할 수 있다. 현재 검증된 설정은 [C8 Nemotron 3 Ultra 검증](C8_NEMOTRON_API_VALIDATION.md)에 있다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-llm.txt
.\.venv\Scripts\python.exe -m python_controller.llm_cli --dry-run "앞으로 15cm 가고 오른쪽으로 10cm 이동해"
```

기본 모드는 dry-run이다. 검증기는 로봇 몸체 기준 방향, 명시적인 거리 및 cm/mm/m 단위, 순서가 분명한 이동과 마지막 정지를 지원한다. 모델과 원문의 방향·거리·순서가 일치해야 한다. 이동당 5–50cm, 총 100cm, 최대 4행동을 허용한다. 범위를 벗어나거나 지원 표현으로 해석되지 않는 요청은 API 호출 전에 거절한다.

명시적으로 승인된 실물 시험에서만 현재 로봇의 IPv4를 `ESP32_IP`에 설정하고 `--execute`를 사용한다. 이동 계획을 읽고 `EXECUTE`로 승인한 뒤, 다른 제어기 종료, 보드 NETWORK 모드와 방향 교정, 시험 공간과 물리 전원 차단 준비를 확인하고 `READY`를 입력한다. 실행 중 Ctrl+C는 API 호출 없이 STOP을 시도한다. 독립 CLI의 `--execute "정지"`는 모델 없이 STOP만 요청한다. UDP 8889를 다른 제어기가 점유 중이면 그 제어기에서 중단한다.

JSONL 기록은 `logs/llm/`에 생성된다. `GOAL_REACHED`는 ESP32의 엔코더 기반 오도메트리 보고다. 승인된 실물 시험의 줄자 측정값은 `run_id`와 `motion_id`를 붙여 `physical_distance_cm`으로 따로 기록한다. 현재 실행의 실측값은 비어 있다.

## 단계별 검증

| 단계 | 변경 파일 및 결과 | 남은 검증 |
| --- | --- | --- |
| C0 기존 기준 | 시스템 Python의 기존 unittest 81개 중 77개 통과, PySide6 미설치로 UI 4개 건너뜀. 프로젝트 `.venv`에서 기존 81개 전부 통과. 원본 해시 검사 포함 | 장비의 실제 펌웨어·핀·엔코더 상태 |
| C1 NVIDIA | `llm/nvidia_client.py`, `llm/prompt.py`, `requirements-llm.txt`, `.env.example`, `.gitignore`. OpenAI SDK 요청 경로·본문을 모의 HTTP에서 확인. 401/403/404/429/5xx·타임아웃·빈 응답·잘린 응답 거절 확인 | 키를 사용한 인증 호출과 지정 모델 접근. NVIDIA 공개 엔드포인트에는 Deprecated가 표시된다 |
| C2 계획 | `planning/schema.py`, `planning/validator.py`, `tests/test_llm_planning.py`. 정상 25·복합 25·모호 25·범위 초과 또는 미지원 25개 문장, 엄격한 JSON·Pydantic 정책 및 원문과의 불일치 거절 확인 | 실제 모델의 한국어 해석 성공률 |
| C3 거리 통신 | `communication/esp32_udp.py`, `execution/llm_telemetry.py`, `execution/control_lease.py`, `tests/test_llm_execution.py`. SLOW 5cm/s 4방향 패킷, 세션·부팅 ID·순서·목표 ID·fault·거리 상태 판정, Windows UDP 8889 상호 배타 바인드 확인 | 실제 ESP32 IP·포트·텔레메트리 호환 |
| C4 실행 | `execution/motion_executor.py`, `execution/run_log.py`, `llm_cli.py`, `tests/test_llm_cli.py`. 초기 STOP 및 heartbeat 확인, 동일 바이트 MOVE 1회 재전송, 순차 목표 및 중간 STOP, stale/fault/Ctrl+C/예외 중단을 모의 시험. Windows 루프백에서 목표 2개, STOP 확인 3회, heartbeat 간격 104.5–138.2ms 확인 | 실제 Wi-Fi 지연·heartbeat 간격·watchdog 정지 시간 |
| C5 후보 | `esp32_firmware/candidates/llm_ab_v1/mode_guard.patch`, `esp32_firmware/PATCH_NOTES_LLM_AB.md`. 참조본과 후보를 범용 ESP32 Dev Module 타깃으로 컴파일 | 실제 보드 타깃과 일치 여부 및 모드 분리 하드웨어 회귀 |
| C6 실물 | 보드 연결·업로드·모터 구동 미실시 | 사용자 승인 후 정지 연결, 띄운 바퀴에서 방향·watchdog 확인, 5cm 전진부터 바닥 실측 |

최종 명령 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`에서 총 100개가 통과했다. 여기에는 기존 대시보드·추종·제스처·MOTOR TEST 회귀시험이 포함된다. 검증 상태는 `SOFTWARE_VERIFIED`, `HARDWARE_VERIFIED` 미확인, `END_TO_END_VERIFIED` 미확인이다.

기존 펌웨어는 정상 STOP 뒤 최근 목표 ID를 보관할 수 있다. 실행기는 새 세션에서 보존된 ID를 피한다. 별도 UDP ACK가 없으므로 목표와 STOP 확인에는 명령 순서에 맞는 텔레메트리를 사용한다. 중단 시에는 STOP 송신 시도를 기록하며 정지 확인 여부는 미확인으로 남긴다.

같은 PC에서는 수신 포트가 제어권을 잠근다. 다른 LAN 호스트가 명령을 보내면 실행기는 세션 불일치나 텔레메트리 단절에서 중단한다. 시험 중에는 로봇 네트워크의 다른 송신기를 종료하고 전원 차단 수단을 준비한다.
