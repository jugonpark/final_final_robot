# 한국어 UI 및 ESP32 진단 구현 결과

2026-11-01, Windows / 프로젝트 `.venv`. 이번 변경에서 실제 모터 명령은 자동 실행하지 않았다. 실제 ESP32·모터 검증은 **미검증 (NOT TESTED)** 이다.

## 현재 모터 미동작에 관한 결론

16:39:47 KST 재점검에서 선택된 노트북 인터페이스는 Wi-Fi `10.103.239.213/24`, 저장된 주소는 `10.232.69.103`이었다. 서로 직접 연결 대역이 다르고 저장 주소의 ICMP 응답은 없었다. UDP 8889 바인딩 검사는 성공했고 기존 수신 점유는 없었다. 연결 범주는 공용이며 Domain/Private/Public 방화벽은 활성화, 기본 동작 값은 NotConfigured였다. 이것만으로 유효 방화벽 정책이나 UDP 차단을 확정하지 않는다. 열거된 시리얼 포트는 없었다.

이 재점검은 라우트 선택·포트 검사·ICMP·Windows 상태 조회만 수행했다. 실제 UDP 명령 송신·ESP32 텔레메트리 수신은 수행/확인하지 않았다. 현재 보드 IP·mode·fault·전원·배선·모터 회전은 알 수 없다. 과거 주소 오류나 전원 고장을 확정할 근거는 없다. 기존 15:57 실행 로그는 16:04까지 시뮬레이션이었으며 실제 송신·유효 수신 기록이 없었다.

당초 16:15 이전의 `172.31.58.226/23` 관측값은 네트워크 변경 전 값으로 현재 판단에 사용하지 않는다.

현재 원문 증거: [진단 JSON](connection_diagnostics/diagnosis_20261101_163952_747921.json), [진단 Markdown](connection_diagnostics/diagnosis_20261101_163952_747921.md). 네트워크 상태는 시간이 지나면 다시 점검해야 한다.

## 확인하고 수정한 코드 문제

1. 비정상 텔레메트리가 모두 INVALID_TELEMETRY로 묶여 NETWORK 모드 미진입·세션·순서·형식·재부팅 사유를 구분하기 어려웠다. 구체적인 거절 코드와 필터링한 수신 원문을 보존하되 제어 유효성 검사는 유지했다.
2. 연결 초기 소켓/포트 실패가 시뮬레이션 상태에서 세션 로그에 남지 않았다. 연결 시도 단계·Windows 오류 코드·시각을 강제로 기록한다.
3. 외부 송신자·소켓 송수신 실패·실제 송수신 주소를 추적하기 어려웠다. UDP_TX/UDP_RX, 거절 사유 집계, 감시 스레드 오류 코드, STOP 시도와 송신 간격을 기록한다. 송신 완료는 ACK로 표시하지 않는다.
4. 화면에 영어 문구가 많았으며 복합 상태 문구는 일부만 번역될 수 있었다. 표시 전용 한국어 변환과 Qt 기본 대화상자 번역을 적용하고 내부 식별자·콤보박스 저장 코드를 유지했다. 작은 화면에서 확인·설정·관찰 줄이 가로로 넘치지 않게 배치했다.
5. 모터 시험 종료 요약에는 모터별 PWM 요약이 없었다. 모터별 최대 절댓값 PWM을 보존해 다른 모터의 PWM을 해당 모터의 근거로 오인하지 않게 했다. 회전하지 않음·반대 부호는 엔코더 근거와 육안 관찰을 분리한다.

6. 실제 GUI 실행 중 강제 UDP 사건 기록이 상태 요약 기록의 주기 제한 시각을 계속 갱신하는 문제가 추가로 관측됐다. `monitoring.py`에서 강제 사건과 주기 상태 기록의 시각 제한을 분리하고 회귀 검사를 추가했다.

실제 실행은 시뮬레이션으로 시작했고 웹캠 추론 프레임을 기록했다. 실행 중 창에서 UDP 연결이 시작된 뒤 저장 주소 `10.232.69.103:8888`로 STOP 송신이 기록됐으나 UDP_RX와 유효 텔레메트리는 없었다. `cmd_vel` 송신은 없었다. 이는 보드의 수신/정지 성공 증거가 아니며 모터 시험 활성화 조건도 충족하지 못했다. 원문 집계는 [실제 실행 점검](korean_live_check.json)을 참조한다.

## 프로토콜 비교

`network_protocol.cpp`, `.ino`, `robot_config.h`, `safety_telemetry.cpp`와 Python 구현을 비교했다. 소스 계약상 명령 형식 불일치는 발견하지 못했다. 실제 업로드된 보드의 펌웨어 일치는 미검증이다.

| 항목 | 소스 비교 결과 |
|---|---|
| 주소 | 펌웨어 WiFi.begin, 고정 WiFi.config 없음 → DHCP 사용 |
| 포트 | Python → ESP32:8888, ESP32:8888 → 마지막 컨트롤러 IP:8889 |
| 명령 | type, nonzero uint32 session_id, 증가 seq; cmd_vel은 status=SLOW, vx/vy cm/s, w rad/s |
| 새 세션 | 이동 중 새 세션이면 정지. Python은 새 STOP 이후 최신 정지 응답을 더 엄격하게 요구 |
| heartbeat | 펌웨어는 오래된 이동을 지우지 않음. Python은 검증된 정지에서만 사용 |
| 주기·제한 | 이동 워치독 300ms, 텔레메트리 200ms, SLOW 5cm/s·0.5rad/s; 시험 기본 2.5cm/s·0.2rad/s·0.8초·10Hz |
| 명령 처리 | 별도 ACK 없음. 세션·seq·상태·희망 속도 일치와 실제 엔코더 관측을 구분 |
| mode/fault | MANUAL_TEST 수신은 제어에 사용하지 않음. 사용자 STOP→AUTO→STATUS 확인; 자동 fault reset 없음 |

이번 작업에서 ESP32 펌웨어·핀·CPR·PID·방향·속도 상한·워치독은 변경하지 않았다.

## 수정 파일

- `python_controller/diagnostics.py`: 무구동 네트워크 검사, 여섯 단계 진단, 근거별 가설, JSON/Markdown 보고서.
- `python_controller/visualization/korean.py`: 한국어 표시 사전, 상태/손동작 설명, 내부 코드 유지.
- `python_controller/visualization/dashboard.py`: 한국어 화면·경고·진단 영역·송수신 원문·실물 점검·보고서 저장.
- `python_controller/communication/esp32_udp.py`: 구체적 수신 검증 사유, 송수신 주소·시각·간격·소켓 오류·STOP 추적.
- `python_controller/communication/motor_test.py`: 시험 요약의 모터별 PWM 관측값 추가.
- `python_controller/monitoring.py`: 강제 송수신 기록으로 인한 상태 요약 기록 누락 방지.
- `python_controller/dashboard_runtime.py`: 시뮬레이션에서도 연결 실패와 실제 UDP 사건 기록.
- `tests/test_connection_diagnostics.py`, `tests/test_korean_diagnostics_ui.py`: 신규 17개 검사. 기존 64개 검사 파일은 유지.
- `README.md`, `docs/DASHBOARD_GUIDE.md`, `docs/MOTOR_TEST_GUIDE.md`, 이 문서와 `docs/KOREAN_DIAGNOSTICS_GUIDE.md`: 최신 화면·진단·절차 및 검증 범위.

## 검증 결과와 한계

- 자동 검사 **81개 통과**: 기존 64개 회귀 + 신규 17개. 상세: [자동 검사 출력](korean_diagnostics_unit_tests.txt).
- 잘못된 주소·포트 충돌·무송신 검사·수신/송신 소켓 오류·세션/seq 거절·MANUAL 모드·한국어/내부 코드 유지·보고서 저장 및 기존 STOP/fault/timeout/긴급 정지/재승인/종료 경로를 검사했다. 통신 성공 사례는 Mock이며 실물 UDP 성공을 의미하지 않는다.
- Qt 합성 화면의 다섯 탭을 1320×920·960×700에서 렌더링했다. 한글 글꼴과 주요 안내를 육안 확인했고 모터 시험의 가로 스크롤 범위는 두 크기 모두 0이었다. 스크롤 아래 조작·관찰 영역도 확인했다. [진단 화면](korean_preview/full_4.png), [작은 화면의 시험 영역](korean_preview/small_motor_controls.png).
- 실제 보드 업로드·현재 보드 프로토콜·모터 전압·STBY·GND·엔코더 방향·실제 회전·지상 이동·구동 중 통신 단절·물리 정지·ESP32 워치독 실물 시험은 **미검증**이다.

## 다음 현장 시험

시리얼 115200bps의 현재 STATUS/IP·mode·fault를 확보하고 통신 가능한 네트워크를 맞춘다. 연결 진단에서 STOP·정지 텔레메트리·heartbeat를 먼저 확인하고 보고서를 저장한다. 그 뒤 바퀴 공중 고정·전원·물리 차단 수단 확인 및 사용자 1회 승인 후 방향 하나만 저속 시험한다. PWM·엔코더·육안 방향과 STOP 결과를 기록한다. 수신이 없으면 먼저 네트워크를 확인하며, 수신만으로 모터·전원 문제를 확정하지 않는다.

실행·버튼별 상세: [한국어 사용 안내](../KOREAN_DIAGNOSTICS_GUIDE.md).
