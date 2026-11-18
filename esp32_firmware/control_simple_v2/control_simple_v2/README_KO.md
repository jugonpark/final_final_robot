# D.I.G. CONTROL SIMPLE V2 — 단일 파일 펌웨어

**사용 파일:** `control_simple_v2.ino` (헤더 분할 없음)

이 파일은 실제 구동 경험이 있는 옛 ESP32 코드의 핀맵·엔코더·옴니 운동학을 기준으로, 이전 `control_simple_bench.ino`의 **Python 중앙제어 UDP V2 호환 기능을 보존**하면서 수동/카메라/LLM용 **공통 Feedforward + PI 속도 제어기**를 구현한 실험 후보입니다. 실제 보드 컴파일·실물 구동/거리 정확도는 미검증입니다.

## 1. 변경점

- `cmd_vel`(수동·카메라)과 `cmd_move`(LLM)를 **동일한 `runWheelController()`** 함수로 출력. 거리 이동은 목표 거리와 감속 속도만 별도로 계산합니다.
- `writeMotorPwm()`에서 **같은 방향의 듀티 변경마다 PWM을 0으로 내리는 동작 제거**. 실제 방향 반전 및 STOP에서만 0 인가.
- PID 전체 출력 대신 **모터별·정/역방향 Feedforward + PI 오차 보정**. `Kd=0`, PI 보정 상한 및 적분 포화 방지 포함.
- 목표 바퀴 속도와 실제 PWM 모두 램프 적용. **STOP은 램프를 거치지 않고 즉시 PWM=0**.
- LLM 거리 이동의 접근 속도 초기값 `1.5 → 3.0 cm/s`, 감속 계수 `6 → 8 cm/s²`. 이는 저속 끊김 감소 시험 설정일 뿐, 물리적 제동거리와 목표 도달 정밀도를 보장하지 않습니다.
- UDP 진단 필드 추가: `wheel_control_target`, `wheel_ff_pwm`, `wheel_pi_trim_pwm`, `wheel_target_rpm`, `wheel_measured_rpm`. 기존 `wheel_target`, `wheel_speed`, `wheel_pwm`, `encoder_count`, `goal_*`, `session_id`, `seq`, `heartbeat_*` 유지.
- 기존 `BENCH ON`은 진단용으로만 남김: **`cmd_vel`에만 PWM 80 고정**. `cmd_move`는 BENCH ON에서도 Feedforward+PI를 사용합니다. `BENCH OFF`가 일반 구동 모드입니다.

## 2. 설정 / 업로드

1. Arduino IDE에서 폴더 `control_simple_v2`의 같은 이름 `control_simple_v2.ino`를 엽니다.
2. Arduino-ESP32 **3.x** 및 **ArduinoJson 7** 설치 후 ESP32 보드/COM 포트를 선택합니다.
3. 코드 상단 `WIFI_SSID`, `WIFI_PASS`를 **로컬에서만** 입력하세요. 이 ZIP에는 실제 비밀번호가 들어 있지 않습니다. **Wi-Fi 정보가 담긴 수정 파일을 GitHub에 공개 업로드하지 마세요.**
4. `USE_STATIC_IP = false`이므로 DHCP로 IP가 바뀔 수 있습니다. 업로드 후 시리얼 모니터 **115200bps**에서 `STATUS`를 입력해 **현재 IP**를 확인하고 중앙 대시보드의 ESP32 IP와 맞추세요.
5. 제어 명령 UDP **8888**, 텔레메트리 UDP **8889**. `HEARTBEAT_TIMEOUT_MS=600`, `VELOCITY_TTL_MS=300`은 Python 중앙제어와 맞춘 값이므로 변경하지 마세요.
6. 로봇을 평평하게 고정하고 바퀴를 **공중에 띄운 상태에서만** 초기 테스트하세요. 전원 차단 스위치를 가까이 두세요.

## 3. 권장 실험 순서

1. 새 펌웨어 업로드 → 시리얼 `STATUS`로 `MODE = NETWORK`, `WiFi = CONNECTED`, `UDP = ON` 확인.
2. 필요시 `M1 80` / `M2 80` / `M3 80`로 개별 회전 방향 및 엔코더 부호 확인. **시리얼 수동 PWM은 약 2초 후 자동 정지**합니다. 수동 시험 후 `AUTO`를 입력해 다시 중앙제어 모드로 전환하세요.
3. 중앙 대시보드 연결 → STOP 정지 응답 확인 → **BENCH OFF** 상태에서 수동 전진/좌회전/우회전 비교.
4. 회전 중 텔레메트리에서 `state=VELOCITY`, `control_style=FEEDFORWARD_PI`, `wheel_target_rpm`, `wheel_measured_rpm`, `wheel_pwm`를 확인. 같은 명령 크기에서 좌/우회전 RPM을 모터별로 기록하세요.
5. 수동에서 정상 확인 후 LLM으로 **5cm 전진처럼 짧은 목표부터** 테스트. `state=DISTANCE_ACTIVE` 또는 `DISTANCE_BRAKING`, `control_style=FEEDFORWARD_PI`, `goal_progress_cm`, `remaining_cm`, `wheel_pwm`를 함께 확인하세요. 목표 거리를 넘어가면 성공으로 표시하지 않습니다.
6. 그 뒤 카메라/손동작 테스트. Python의 영상 임대시간·대상 검증 등은 본 ESP32 펌웨어와 별개이므로 카메라 STOP 문제는 Python에서도 확인해야 합니다.

## 4. 튜닝 위치

`control_simple_v2.ino` 상단에 있습니다. **아래 초기값은 일부 기존 후보의 공중부양 측정 데이터를 기반으로 했지만, 새 펌웨어/현재 장치에서 다시 측정해야 합니다.**

| 설정 | 초기값 | 의미 |
|---|---|---|
| `MIN_RUN_PWM_POS` | `{52, 58, 54}` | 각 모터 정방향 최소 지속 PWM(검증 전 가정) |
| `MIN_RUN_PWM_NEG` | `{52, 54, 52}` | 각 모터 역방향 최소 지속 PWM(검증 전 가정) |
| `FF_SPEED_CM_S` | `0,2.5,5,7.5,10,12.5,15` | Feedforward 기준 바퀴 속도 |
| `FF_PWM_POS`, `FF_PWM_NEG` | 모터별 표 | 좌/우회전 속도 차이 보정을 위한 방향별 추정치 |
| `WHEEL_PI_KP` | `1.3` | 측정 속도 오차의 비례 보정 |
| `WHEEL_PI_KI` | `1.5` | 적분 보정 |
| `PI_TRIM_LIMIT_PWM` | `24` | PID가 FF를 무리하게 흔들지 않도록 제한 |
| `WHEEL_TARGET_SLEW_CM_S2` | `35` | 바퀴 속도 목표 변화율 제한 |
| `PWM_SLEW_COUNTS_PER_SEC` | `320` | 일반 출력에서의 PWM 변화율 제한 |
| `MIN_CONTINUOUS_WHEEL_CM_S` | `0` | **기본 비활성화**. 최저 안정 RPM을 실측한 뒤 필요할 때만 설정 |

**좌회전 > 우회전 측정 결과**: 전체 좌/우 회전 속도 비율만 곱하지 마세요. M1/M2/M3 각 바퀴에 대해 `+방향`/`-방향`에서 동일 목표 RPM 대비 실제 RPM이 얼마나 다른지 기록하고 `FF_PWM_POS/NEG`의 해당 행만 보정하는 방식이 정확합니다. `MIN_RUN_PWM`은 너무 낮으면 끊기고, 너무 높으면 저속 목표보다 빨라져 거리 오차가 납니다.

**물리적인 저속 한계**: 2.5cm/s 본체 전진 명령에서 구동 바퀴 속도는 약 7.1RPM로, PWM 80 고정 실험과는 요구 속도가 다릅니다. 모터가 7RPM에서 연속적으로 회전할 수 없다면 PI를 바꿔도 해당 속도를 부드럽게 유지할 수 없습니다. 먼저 최소 안정 RPM을 실측해야 합니다. `MIN_CONTINUOUS_WHEEL_CM_S`를 0보다 크게 지정하면 더 빨리 움직이므로, 거리 목표와 실제 주행 속도에 영향을 줍니다.

## 5. 문제별 확인할 로그

| 문제 | 확인할 텔레메트리 |
|---|---|
| 수동에서 끊김 | `state`, `wheel_control_target`, `wheel_ff_pwm`, `wheel_pi_trim_pwm`, `wheel_pwm`, `wheel_measured_rpm` |
| LLM에서 끊김 | 위 값 + `goal_active`, `goal_progress_cm`, `remaining_cm`, `last_fault`, `udp_accepted` |
| 모터가 정지하고 제어 재시작 필요 | `state`, `last_fault`, `last_seq`, `motion_permit`, `heartbeat_age_ms`, `velocity_age_ms` |
| 좌회전이 더 빠름 | 같은 `w` 크기로 좌우 실행해 `wheel_target_rpm` vs `wheel_measured_rpm` 비교 |

## 6. 검증 범위와 제한

- 수행: 대체 헤더를 이용한 데스크톱 C++17 문법 검사, UDP·엔코더 모의 실행 테스트, ASan/UBSan 실행. 초기 STOP→V2, 명령 만료, 오래된 seq 차단, `cmd_move` 재전송, 모의 엔코더 거리 완료, 한 번의 엔코더 이상 샘플 무시, 동일 방향 PWM 갱신 시 0 출력이 없음을 확인했습니다.
- 미수행: Arduino IDE/arduino-cli **실제 보드 컴파일**, 실물 ESP32 업로드, 실제 전원/부하 실험, 목표 거리와 좌·우회전 RPM 실측 검증.
- **실물 정확도 보장 없음**: 예전 펌웨어의 엔코더 `COUNTS_PER_OUTPUT_REV=823.13`을 그대로 사용합니다. 이전 V2 후보의 `898`과 차이가 있으므로 반드시 바퀴 1회전 엔코더 실측 후 값 보정이 필요합니다. GPIO34/35에는 내부 풀업이 없어 M1 엔코더 배선도 확인해야 합니다.
- `ENCODER_JUMP` 영구 Fault 래치는 없지만, LLM 거리 목표에서 연속 엔코더 무효는 현재 목표를 중단합니다. STOP/Heartbeat/TTL/토큰 순서 검사는 그대로 유지합니다.
- **실물에서 안정적인 PWM·정확한 목표 RPM이 검증되기 전 자율 무인 주행은 하지 마세요.**
# 현재 개발 기준

단일 스케치 `control_simple_v2.ino`가 공식 기준이다. 이 스케치와 같은 폴더에 `secrets.example.h`를 복사해 `secrets.h`를 만든 뒤 로컬 Wi-Fi 값을 입력한다. 원본에서 Wi-Fi 선언만 분리했다. 사용자가 확인한 수동 및 자연어 구동 결과와 후속 작업은 [기준선 기록](../../../docs/CONTROL_SIMPLE_V2_PLAN.md)에 있다. 아래의 검증 상태는 제공 파일의 작성 당시 기록이다.
