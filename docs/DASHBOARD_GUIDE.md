# 실시간 검증 대시보드 사용 및 구현 안내

최신 진단과 현재 한국어 화면의 전체 안내는 [한국어 진단 사용 안내](KOREAN_DIAGNOSTICS_GUIDE.md)를 참조하세요. 아래 검증 수치가 있으면 이전 단계의 기록입니다.

현재 하드웨어 정책은 [MOTOR_TEST_GUIDE.md](MOTOR_TEST_GUIDE.md)가 우선한다. 모터 동작 시험 탭이 추가되었고, 무구동 연결과 이동 승인을 분리했다. 아래의 하드웨어 A/R·자동 추종 연결 및 ACK 용어는 이전 단계 설명이다. 현재 하드웨어 자동 추종은 차단되며 별도 ACK 패킷도 없다. SIMULATION 인식·추종과 기본 4탭 설명은 유지된다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard
```

`run_simulation.bat`도 같은 대시보드를 연다. Python 진입의 기본 OpenCV UI와 `--demo`는 기존 테스트와 사용법의 호환성을 위해 유지했다. Qt 설치는 `python -m pip install -r requirements.txt`로 준비하며, 현재 PC의 가상환경에는 설치를 완료했다. 새 PC 전체 부트스트랩은 검증하지 않았다.

카메라 없는 명확한 합성 미리보기:

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard --dashboard-preview
```

SYNTHETIC_PREVIEW에서는 사람 박스와 손바닥 펼침 (Open Palm) 분류가 합성 입력이다. 성능 증거로 사용하지 않는다. 하드웨어 연결은 코드에서 거부한다. 정상 모드의 미수신 모터 값을 채우는 기능은 없다. `--smoke-seconds 30`은 지정 시간 뒤 정지·종료하는 검사용 옵션이다.

## 화면과 조작

목표를 클릭하거나 단일 인물 후보를 확인한 뒤 Enter로 확정하고 A → R 순서로 진행한다. 버튼도 동일하다. 손바닥 펼침 (Open Palm)은 승인 후 재개, 주먹 (Closed Fist)는 일시 정지, 엄지 올림 (Thumb Up)/Down은 상대 크기 목표값을 한 단계 변경한다. 가림·ID 변경·다인·손 소유권 제한은 기존과 같다. 목표 거리 설정은 실거리 cm가 아니라 사람 박스 높이 비율이며 교정된 값이 아니다.

상단에는 SIMULATION/HARDWARE, 상태, ARMED/DISARMED, 항상 접근 가능한 긴급 정지 버튼이 있다. Space는 이 창의 어디에서나 긴급 정지한다. 메모 입력 중 Space도 정지시키지만 입력 자체는 보존한다. A/R/P/C/E/Q/Enter는 텍스트·선택 입력에 초점이 있을 때 실행되지 않는다. 일반 화면에서 E는 긴급 정지, Q/Esc는 종료다. Ctrl+C/V 등의 편집 키는 제어 명령으로 처리하지 않는다. 긴급 정지 latch는 성공한 명시 ARM 전까지 유지된다.

- 종합 현황: 큰 영상, 박스/ID/목표 중심, 손 연결선, target 상태, Raw/Confirmed/Applied 카드, 요청·안전 명령과 방향 화살표.
- 영상 인식: 현재 선택·관측·제스처·단계 시간·명령의 상세 JSON. Raw 결과가 있어도 승인·적용이 없을 수 있다.
- 이동 및 모터: TX/RX·session/seq·매칭 ACK·fault·STOP 검증, 모터별 목표/측정 cm/s·RPM·PWM·엔코더, 최근 30초 유효 텔레메트리 그래프.
- 시험 및 기록: 하드웨어 연결의 명시 승인, 세션 경로, 7단계 수동 기록. 작은 창은 분할과 스크롤로 확인한다.

`UNKNOWN`/null은 값을 받지 못했거나 검증할 수 없다는 뜻이다. stale 텔레메트리는 마지막 값이 남을 수 있으므로 RX 유효성·나이를 함께 확인한다. 그래프는 유효 수신에만 새 점을 추가하며 과거 곡선이 남았다고 현재 정상이라는 뜻은 아니다. FPS는 완료된 추론 사이의 간격으로 계산한다. 최초 모델 초기화에는 오래 걸릴 수 있고 단계가 UI와 로그에 표시된다.

## 제스처 3단계

Raw에는 MediaPipe top category, confidence, 연결된 사람 ID, 손 수와 관측 시각을 남긴다. 사람이 여럿이면 Raw는 보여도 제어 입력은 차단한다. 기본 안정화 기준은 confidence 0.75, 연속 0.6초, 최소 3개 서로 다른 프레임, 간격 0.25초 이하다. 진행률은 시간과 프레임 양쪽의 작은 비율이다.

Confirmed는 이 조건을 충족한 현재 제스처다. Applied this update는 실제 상태/설정에 반영한 사건이고, Last gesture action은 마지막 적용의 이력과 경과 시간이다. 승인됐더라도 unarmed, 미확정 목표, safety stop, 중복 유지 상태이면 적용하지 않는다. UI는 원인을 각각 보여 준다. 예: NO_HAND_DETECTED, TARGET_NOT_SELECTED, HAND_TARGET_MISMATCH, MULTIPLE_PERSON_RESTRICTION, GESTURE_LOW_CONFIDENCE, GESTURE_UNSTABLE, GESTURE_NOT_MAPPED, CONTROLLER_NOT_ARMED, SAFETY_STOP_ACTIVE, RELEASE_RESUME_GESTURE.

## 명령·텔레메트리의 의미

1. Requested는 방향·상대 크기 제어의 제한 전 요구다. 상태가 PAUSED여도 진단용 요청이 보일 수 있다.
2. Safe는 상태·속도·가속도·후진 제한을 적용한 실제 상위 출력이다. 항상 최대 5cm/s·0.5rad/s이며 vy=0이다.
3. Last successful UDP send는 sendto가 반환한 실제 wire 패킷이다. 모터 수신·동작 성공의 증거가 아니다.
4. Matched ack command는 유효 텔레메트리의 last_seq에 대응하는 송신 이력이다. `host_sent_at`/`perception_frame_at`는 호스트 부가 정보이며 wire에 넣지 않는다.
5. ESP32 reported desired cmd는 `cmd_vx/cmd_vy/cmd_w`다. 펌웨어 내부의 desired 값이므로 가속도 제한 이후 본체 적용 속도로 부르지 않는다. 해당 본체 속도 필드는 NOT REPORTED로 표시한다.
6. wheel_target/wheel_speed는 cm/s, wheel_pwm은 PWM, encoder_count는 누적 count다. RPM은 `cm/s * 60 / (2*pi*wheel_radius_cm)`으로 환산한다. 설정 기본 반지름 2.9cm는 기존 firmware config에서 확인했다.

UDP 8888/8889, 텔레메트리 200ms, SLOW와 300ms 워치독을 펌웨어에서 재확인했다. source IP:8888, session, seq, boot, uptime, 숫자/배열을 검증한다. STOP 검증은 STOP 송신 이후에 해당 seq 이상을 보고한 fresh STOPPED·fault NONE·0 명령/PWM·낮은 wheel_speed가 있어야 한다. 이것도 물리 전원 차단이나 본체 정지 거리 검증은 아니다.

## 하드웨어 모드

SIMULATION에서는 ESP32 소켓을 만들지 않는다. 시험 및 기록에서 주소·명시 승인 후 Connect hardware를 누르거나 기존 CLI 승인을 사용한다. 검증된 설정 파일의 `communication.signs_verified=true`도 필요하다. 연결은 STOP만 송신하며 연결 자체로 ARM/RESUME하지 않는다. Fresh matching STOPPED, fault 없음, 실제 perception 갱신이 있어야 A와 새 재개를 진행할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard --mode HARDWARE --esp32-host ESP32_IP --config verified_hardware.json --acknowledge-hardware-risk
```

프로젝트의 false를 단순히 true로 바꾸는 행위는 물리 교정의 증거가 아니다. 이번 개발 중에는 실제 ESP32 연결·모터 구동을 하지 않았다. 자동 후진은 차단 상태로 유지한다. STOP + return to SIMULATION은 먼저 전송부를 정지·닫고 모드를 전환한다. 다시 하드웨어에 연결해도 unarmed다.

GUI가 멈추거나 새 인식이 오지 않아도 UDP worker가 원래 프레임 timestamp를 감시한다. 250ms lease 만료/700ms 텔레메트리 지연/고장에서는 STOP과 해제가 필요하다. heartbeat는 사용하지 않는다. 실제 OS·전원·UDP 장애에 대한 정지 보장은 없으며 기존 ESP32 워치독과 물리 차단 수단을 함께 사용한다.

## 테스트 기록과 로그

각 실행은 `logs/YYYYMMDD_HHMMSS_microseconds/`에 events.jsonl과 test_results.json을 남긴다. 모든 수동 결과는 NOT TESTED로 시작한다. PASS/FAIL에는 수행 확인과 메모가 필요하며, SIMULATION/합성 모드에서는 하드웨어 5~7단계 PASS를 저장하지 못한다. 기록은 관찰자의 수동 진술임을 명시하고 자동 검증 결과와 섞지 않는다. 이전 결과 변경 이력도 JSONL 사건으로 남는다.

1 카메라 → 2 추적 → 3 네 제스처 → 4 추종 명령 → 5 실제 ESP32 통신 → 6 띄운 바퀴 → 7 별도 승인된 통제 공간 주행 순서다. 기록 버튼은 모터 시험을 실행하지 않는다. 후방 센서·전방 충돌 회피는 준비되지 않았으므로 일반 장애물 환경에서 자동 주행하지 않는다.

JSONL에는 로컬 timezone 포함 timestamp, monotonic_s, mode/source, 상태/목표/confidence, Raw/Confirmed/Applied/거부 이유, 수평 오차/상대 크기, requested/safe, transmitted packet·TX 시각/연결된 frame 시각, ESP32 reported state/fault, motor values, STOP 이유를 기록한다. 캡처 읽기 시작·제스처 추론 시작/완료·승인·적용·송신·수신의 호스트 단조 시간을 구분한다. source의 frame timestamp는 센서 촬영시각은 아니다.

평상시 상태는 최대 10Hz로 샘플링하므로 모든 20Hz wire 패킷을 개별 보존하는 트레이스가 아니다. 사용자 제어·상태 변화·제스처 적용 사건은 강제로 기록한다. 매칭 seq와 프레임 부가 정보 없이 서로 다른 행의 시간을 단순히 빼서 정확한 제스처→모터 지연으로 주장하지 않는다. 이미지나 Wi-Fi 설정을 기록하지 않는다. queue/disk 오류는 긴급 정지를 요청한다.

## 파일 변경

추가:

- `python_controller/dashboard_runtime.py`: latest-only worker·제어 조립·모드 전환.
- `python_controller/visualization/dashboard.py`: 실행 가능한 Qt 4탭·입력·영상·그래프.
- `python_controller/monitoring.py`: 피드백 환산·결과 기록·비동기 logger.
- `tests/test_dashboard_core.py`, `test_dashboard_runtime.py`, `test_dashboard_telemetry.py`, `test_dashboard_ui.py`.
- 이 안내, [대시보드 검증 기록](verification/DASHBOARD_VALIDATION.md), [합성 화면 5종](verification/dashboard_preview).

수정:

- `main.py`, `run_simulation.bat`: UI 선택 및 새 기본 배치 실행.
- `control/follow_controller.py`, `control/state_machine.py`: 요청 명령·제스처 진단·긴급 정지 latch.
- `vision/gesture_recognizer.py`: 차단 이전 Raw 진단과 단계 시간.
- `communication/esp32_udp.py`: TX/ACK/STOP 이력 및 추가 배열 검증.
- `config/default.json`, `config/__init__.py`: RPM 환산 반지름과 검증.
- `requirements.txt`, `.gitignore`, README, 설계·검증 문서.

이번 변경에서는 펌웨어·원본 ZIP·모델 파일을 수정하지 않았다. 기존 30개 테스트 파일의 검증 목적을 유지했다.

## 남은 실물 검증

실제 4종 제스처를 반복 제시하며 Raw confidence, 승인 시간, 거부 사유, 설정 변화와 pause/resume을 기록해야 한다. 그 뒤 다인·가림·ID 변화를 검증한다. ESP32 연결 이후에는 STOP handshake·단절·fault·띄운 바퀴 엔코더·방향·RPM·정지 반응을 검사한다. 본체 방향·거리·제동은 지상에서 별도 승인 후 확인한다. 후방 레이더 실제 통합·Pi 카메라/성능은 이번 범위 밖이다.
