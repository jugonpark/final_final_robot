# ESP32 통신 및 수동 모터 시험

최신 진단과 현재 한국어 화면의 전체 안내는 [한국어 진단 사용 안내](KOREAN_DIAGNOSTICS_GUIDE.md)를 참조하세요. 아래 검증 수치가 있으면 이전 단계의 기록입니다.

이 단계는 통신과 **바퀴를 띄운 벤치 시험**만 제공한다. 사람 추종·손 제스처의 하드웨어 출력은 차단했다. 기본 실행은 SIMULATION이며 자동 모터 시험은 없다. 기존 ESP32 펌웨어와 제어 알고리즘은 수정하지 않았다.

## 실행과 IP 설정

`run_simulation.bat`를 실행하거나 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard
```

**모터 동작 시험** 탭을 연다. ESP32의 현재 숫자 IPv4를 입력하고 IP 저장를 누르면 `settings/connection.json`에 IP만 저장한다. 저장은 접속이나 모터 활성화를 수행하지 않는다. 다음 실행에서도 기본 모드는 SIMULATION이다. IP는 설정의 `communication.host` 또는 `--esp32-host`로도 지정할 수 있다. 과거 다른 프로젝트의 IP를 현재 주소로 가정하지 않는다.

`UDP 통신 연결만 승인합니다`를 확인하고 UDP 연결를 누른다. 이 승인은 STOP/정지 후 heartbeat 통신에만 해당한다. **cmd_vel을 보내는 승인이 아니다.** 정상 수신 전에는 연결 성공으로 표시하지 않는다. 주소 저장·연결에 Wi-Fi 비밀번호는 필요하지 않으며 로그에도 기록하지 않는다.

연결만 CLI에서 명시적으로 승인하는 경우:

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard --mode HARDWARE --esp32-host 192.168.1.25 --acknowledge-hardware-risk
```

위 IP는 예시이며 실제 보드 주소로 바꾼다. CLI 실행도 모터 시험은 비활성화 상태로 시작한다. 기존 OpenCV 하드웨어 진입은 차단했다. SIMULATION의 OpenCV 인식 화면과 headless demo는 유지한다.

## 무구동 통신 확인

1. 같은 Wi-Fi/서브넷의 ESP32 IP, 보드의 NETWORK 모드, 노트북 UDP 수신 8889 방화벽 허용을 확인한다. 여러 제어 프로그램이 동시에 같은 보드를 제어하지 않게 한다.
2. Python은 8888로 typed STOP을 먼저 보낸다. 새로운 난수 session_id와 증가 seq를 사용한다.
3. ESP32 IP:8888에서 노트북의 고정 8889로 들어오는 텔레메트리를 검사한다. type, session, seq 범위, boot_id, uptime 증가, NETWORK mode, fault, 숫자/배열을 검증한다.
4. 새 STOP의 seq 이상을 보고하는 fresh STOPPED, cmd_v*=0, PWM=0, 낮은 wheel_speed를 확인한다. 이전 STOPPED로 새 정지 요청을 확인하지 않는다.
5. 확인 후 정지 상태에서만 약 10Hz heartbeat를 보낸다. 모터 동작 시험의 정지 상태 생존 통신 확인로 명시적으로 다시 확인할 수도 있다. `last_seq`, `state`, `fault`, 마지막 TX와 RX 나이를 확인한다.

UDP 송신 반환은 로컬 전송 결과다. 이 펌웨어는 별도 ACK 패킷이 없다. UI의 `Telemetry seq match (not ACK)`는 실제 수신한 텔레메트리 seq에 대응하는 송신 이력일 뿐이며, 모터 회전 성공을 뜻하지 않는다. 모터 시험의 `Command processing evidence`는 더 엄격하게 session/seq, VELOCITY 상태, fault NONE, cmd_vx/cmd_vy/cmd_w echo 일치를 요구한다.

## 수동 시험 순서

1. 모터 전원 인가 전 배선·공통 GND·전압·STBY·엔코더 풀업과 전원 차단 수단을 확인한다.
2. **세 바퀴를 모두 바닥에서 띄우고 플랫폼을 고정한다.** 사람·물체·케이블이 바퀴에 닿지 않게 한다.
3. STOP 검증, fresh 텔레메트리와 wheel_target/wheel_speed/wheel_pwm/encoder_count 3개 배열이 모두 있어야 한다.
4. `All wheels are lifted...`와 `짧은 모터 시험 한 번을 승인합니다`를 확인하고 **모터 시험 1회 활성화**를 누른다. 이 버튼 자체는 이동 명령을 보내지 않는다.
5. 시간을 확인한 뒤 방향 버튼 **하나**를 클릭한다. SLOW cmd_vel을 약 10Hz로 보내고 시간이 지나면 독립 전송 스레드가 STOP으로 전환한다. 시험당 한 번의 승인만 사용하며 종료 후 재승인이 필요하다.
6. STOP/P, 상단 긴급 정지, Space/E, 창 닫기/Q/Esc/Ctrl+C로 정지를 요청할 수 있다. 고장·지연·UI 생존 lease 만료 후 자동 재활성화하지 않는다.
7. 정지 응답을 기다린 뒤 완료된 trial을 선택해 M1/M2/M3의 육안 결과와 관찰 메모를 기록한다. 초깃값은 모두 NOT TESTED다.

하드웨어 연결 동안 A/R과 자동 추종 버튼은 모터를 활성화할 수 없다. 인식 결과는 보여도 제스처를 하드웨어 이동으로 적용하지 않는다. 모터 동작 시험의 명시적 승인 경로만 허용한다. 카메라는 벤치 시험의 안전 입력으로 사용하지 않으며, UI 생존 시간과 통신·deadline을 별도로 검사한다.

## 기본 설정

`python_controller/config/default.json`의 motor_test 섹션이며 JSON override를 사용할 수 있다.

| 항목 | 기본값 |
|---|---|
| linear_cm_s / angular_rad_s | 2.5cm/s / 0.2rad/s |
| duration_s / max_duration_s | 0.8s / 2.0s |
| send_period_s | 0.1s, 약 10Hz |
| ui_lease_s | 0.25s |
| telemetry_timeout_s | 0.5s |
| stop_feedback_timeout_s | 2.0s |
| body_radius_cm / wheel_radius_cm | firmware 기준 9.0 / 2.9 |
| min_encoder_delta | 관측 분류용 2 count |

SLOW의 최대 선속도 5cm/s·각속도 0.5rad/s와 기존 300ms ESP32 워치독은 유지한다. Python은 설정 오류·NaN·허용 범위를 벗어나는 시간을 거부한다. 패킷 전송 스레드의 OS 스케줄 지연이나 전원·Wi-Fi 장애에서 물리 정지를 보장하는 값은 아니다.

이전 `communication.signs_verified`는 자동 추종 교정 gate로 남겨 두었다. 무구동 연결과 바퀴 띄운 수동 검증을 하려고 이를 임의로 true로 바꿀 필요가 없다. 이번 단계는 자동 추종 하드웨어 출력을 제공하지 않는다.

## 방향과 세 모터의 기대값

화면 방향은 제안된 body 축이다. Forward/Backward는 ±vx, Left/Right는 ±vy, 좌/우 회전은 ±w다. 실제 물리 방향은 검증 전 확정하지 않는다. 펌웨어의 진단용 기대 수식은 다음과 같다.

```text
M1 = vy + R*w
M2 = -sqrt(3)/2*vx - 0.5*vy + R*w
M3 = +sqrt(3)/2*vx - 0.5*vy + R*w
```

기본 전진 2.5cm/s의 기대값은 약 `[0, -2.165, +2.165]`cm/s다. **전진 시험에서 M1이 0인 것은 이 운동학에서 정상**일 수 있다. 기본 좌회전 w=0.2의 기대값은 세 바퀴 모두 +1.8cm/s다. 세 모터 반응은 회전/좌우 시험도 포함해 확인한다. 실제 wheel_target은 펌웨어 가속도·휠 제한 이후 값이므로 첫 패킷부터 최종 기대값과 같을 필요는 없다. PID·PWM·피드포워드는 변경하지 않았다.

## 피드백과 기록 해석

이동 및 모터 및 모터 동작 시험에서 수신한 목표/측정 cm/s, 측정 RPM 환산값, PWM, encoder_count를 확인한다. `* RPM`은 수신 wheel_speed를 firmware 반지름으로 환산한 값이며 임의 생성한 측정값이 아니다. 미수신은 UNKNOWN/null이다. stale 값은 RX 유효성·나이와 함께 읽는다. Mock 검사의 source는 MOCK_UDP이며 실제 하드웨어 결과로 사용하지 않는다.

시험 기록은 다음을 구분한다.

- 명령 처리: 텔레메트리 session/seq/state/echo 일치. 별도 ACK가 아니다.
- 엔코더 증거: 기준 count 대비 변화량, 기대 부호와 일치/반대, 변화 미관측, 정지해야 할 바퀴 변화, 증거 부족.
- 육안 관찰: NOT TESTED / MATCHES EXPECTED / OPPOSITE / NO ROTATION 및 사용자가 적은 메모. 자동으로 PASS를 채우지 않는다.
- STOP: 새 STOP 이후의 fresh 정지 텔레메트리 확인 여부. 엔코더 회전이나 이 확인만으로 본체의 지상 방향·거리·제동이 검증된 것은 아니다.

회전이 보이지 않을 때는 처리 echo 유무 → wheel_target/PWM → encoder 변화 → fault 순으로 비교한다. 예를 들어 처리 echo가 없으면 먼저 네트워크·세션·모드를 보고, target/PWM이 있는데 count 변화가 없으면 전원·배선·드라이버·엔코더와 MOTOR_STALL을 확인한다. 반대 부호면 모터/엔코더 배선과 firmware 방향 플래그를 확인하며 PWM이나 PID를 무작정 올리지 않는다. 0.1초 시험은 200ms 텔레메트리보다 짧아 처리 증거가 잡히지 않을 수 있다.

세션 로그 `logs/.../events.jsonl`에는 MOTOR_TEST_STARTED, MOTOR_TEST_STOP_REQUESTED, MOTOR_TEST_COMPLETED, MOTOR_TEST_OPERATOR_OBSERVATION 사건을 남긴다. 완료 사건은 방향·시간·session/seq·명령·기대 휠 값·기준 counts·최대 64개의 수신 sample·분류·정지 확인·육안 관찰을 보존한다. 화면은 최근 25개 완료 요약을 유지한다. 각 송신의 host 시각과 수동 operator lease 시각을 기록하며, 수동 모터 명령에 camera frame timestamp를 꾸며 넣지 않는다.

통신 지연·잘못된 packet·재부팅·fault·긴급 정지·UI 중단·시간 만료에서는 movement와 heartbeat를 중단하고 STOP으로 전환한다. Fault는 자동 reset하지 않는다. 창 닫기·Ctrl+C·전송 오류도 STOP을 시도하지만 강제 프로세스 종료·OS·전원 장애에서 송신을 보장할 수 없으므로 ESP32 워치독과 물리 전원 차단 수단이 필요하다.

## 변경 파일

추가: `communication/motor_test.py`, `tests/test_motor_test.py`, `test_motor_ui.py`, `test_motor_engine.py`, 이 안내와 검증 보고서·출력·화면.

수정: `communication/esp32_udp.py`(공통 정책 hook·표시 별칭·유한 timestamp), `dashboard_runtime.py`(수동 전용 연결/분리/기록), `visualization/dashboard.py`(모터 동작 시험), `config/default.json`, `config/__init__.py`, `main.py`(자동 하드웨어 진입 차단), `.gitignore`, README 및 관련 문서.

기존 47개 테스트 파일, 사람·제스처 인식 알고리즘, ESP32 펌웨어·원본 ZIP·모델 파일은 이번 단계에서 수정하지 않았다. 기존 `acknowledged_command` JSON 키는 호환용 seq 매칭 별칭이며 실제 ACK 수신을 뜻하지 않는다.
