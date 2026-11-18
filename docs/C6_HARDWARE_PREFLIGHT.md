# Prototype A+B C6 하드웨어 시험 준비

2026-11-17 소프트웨어 기준선에서 전체 unittest **100개 통과, 건너뜀 0개**를 다시 확인했다. 사람 추종, 제스처, 대시보드, MOTOR TEST, 펌웨어 원본 해시 검사가 포함된다. 이번 작업의 변경 사항은 이 문서와 기록 양식이다. 보드 연결, 펌웨어 업로드, 모터 전원 인가, 실물 주행은 미실시다.

아래의 빌드와 타이밍은 원본 및 첫 Mode Guard 후보의 기록이다. 이후 추가한 `control_v2` 후보의 타이밍과 판정 기준은 [Watchdog 분리·엔코더 복구 구현 결과](C:/Users/jugon/Desktop/NEW_ROBOT_START/docs/WATCHDOG_ENCODER_FIX_IMPLEMENTATION.md)를 따른다.

## 펌웨어 비교

현재 보드에 플래시된 이미지와 실제 업로드용 FQBN은 미확인이다. `STATUS`, `PIN`, `ENC`는 동작 상태·핀·카운터 확인에 사용한다. 해당 출력과 부팅마다 다시 생성되는 `boot_id`로 빌드의 바이트 동일성을 입증할 수 없다. 보드 모듈 표기와 기존 업로드 기록을 먼저 확인한다.

| 경로 | 원본 참조 펌웨어 | Mode Guard 후보 |
| --- | --- | --- |
| NETWORK의 UDP | typed/legacy 해석, 수락기, 300ms watchdog | 동일 |
| MANUAL_TEST의 유효 UDP MOVE·VELOCITY·HEARTBEAT·RESET_FAULT | 수락기에 도달한다. 정지 중 새 세션의 이동이 수락되면 60초 수동 watchdog 분기가 적용될 수 있다 | 수락 전에 거절하고 rejected 카운터를 올린다 |
| MANUAL_TEST의 유효 UDP STOP | 일반 수락기에서 세션·순서와 fault 복구 조건을 처리한다 | 수동 PWM과 운동을 정지한다. 송신 주소·세션·순서·기존 fault와 발생 시각을 보존한다 |
| Serial 경로 및 잘못된 UDP | Serial은 기존 처리. 디코딩 실패 UDP는 `BAD_PACKET` 정지 | 동일. 모드 가드는 디코딩 성공 이후에 실행된다 |

임시 스케치 두 벌을 파일별 SHA-256으로 비교했다. 수정된 파일은 `esp32_omni_controller.ino` 한 개이며, `handleAcceptedCommand()` 시작에 가드 14줄이 추가된다. 모터 핀, PID, 엔코더, 거리 제어, 텔레메트리 루프는 동일하다. **수동 모드 격리 시험은 Mode Guard 후보를 대상으로 한다.** 실제 모듈·핀·FQBN을 확인하고 해당 타깃에서 다시 빌드한 다음, 별도 업로드 승인 시 시험 이미지를 확정한다. [후보 diff와 기존 빌드 기록](../esp32_firmware/PATCH_NOTES_LLM_AB.md)

### C6 준비 빌드

Arduino CLI 1.5.1, Arduino ESP32 코어 3.3.12, ArduinoJson 7.4.3, 범용 타깃 `esp32:esp32:esp32`. 두 임시 스케치에는 플레이스홀더 `secrets.h`를 사용했다.

| 스케치 | 빌드 | 플래시 | 전역 변수 RAM |
| --- | --- | ---: | ---: |
| 참조본 | 성공 | 950,483 B | 49,044 B |
| Mode Guard 후보 | 성공 | 950,547 B | 49,044 B |

재빌드 로그는 `.tmp/c6-preflight/reference.log`와 `.tmp/c6-preflight/candidate.log`에 있다. 플레이스홀더 구성의 앱 바이너리 SHA-256은 참조본 `AD323E1DC745D1C647874CE58060A292F2894AB32095FBD393E7EBDBA461C325`, 후보 `FDFCC75A6B095533BA55EE0A1122DF7C0FE57CCB4D8A564A63D79F2C98012B8F`다. 시험 이미지 기록에는 실제 타깃 설정과 그 설정으로 생성한 앱 바이너리 해시를 사용한다.

## 명령 처리와 확인 범위

| 항목 | 코드 검토 결과 |
| --- | --- |
| STOP | NETWORK의 최신 순서 STOP은 메일박스를 비우고 PWM과 방향 핀 출력을 0으로 만든다. 같은 세션의 중복·이전 순서 typed STOP은 무시된다. 정상 완료 경로는 새 STOP 이후 2초 이내에 해당 요청의 `seq` 이상, `STOPPED`, 명령 속도 0, PWM 0, 세 바퀴 각각 `abs(wheel_speed) < 0.3 cm/s`를 보고한 새 샘플을 요구한다. 중단 경로는 3회 송신을 시도하고 수신 확인을 기다리지 않는다. |
| heartbeat | PC는 움직이는 동안 100ms 간격을 목표로 송신한다. 보드는 동작 중 마지막으로 **수락한 명령** 이후 300ms 초과 또는 Wi-Fi 연결 상실을 제어 틱에서 검사한다. 10ms 제어 주기, 200ms 텔레메트리 주기, 실제 송수신 간격과 기계 정지 시간은 각기 다른 값이다. |
| `cmd_move` | CLI는 축 하나, `SLOW` 5cm/s, `w=0`, 5–50cm를 사용한다. 초기 STOP과 정지 상태 heartbeat가 모두 확인된 뒤 MOVE를 보낸다. 동일 패킷을 heartbeat보다 먼저 최대 한 번 재시도하며 새로운 목표 번호로 자동 재시도하지 않는다. 보드의 범용 명령 수락기는 CLI보다 넓은 이동 범위를 허용한다. |
| `session_id`와 `seq` | PC는 난수 세션과 증가하는 순서 번호를 쓴다. 보드는 세션이 움직이는 중 바뀌면 즉시 정지한다. 정지 상태에서의 새 세션에는 별도의 송신자 인증이나 선행 STOP 요건이 없다. 같은 PC의 UDP 8889 배타 바인드는 다른 LAN 송신기를 막지 못한다. |
| `motion_id` | 펌웨어는 마지막 목표 ID와 내용을 STOP 뒤에 보존할 수 있다. 같은 ID·같은 내용은 새 이동을 시작하지 않고, 같은 ID·다른 내용은 오류 처리한다. CLI는 시작 STOP의 보존 ID를 피해 목표를 배정하고 현재 목표 ID와 거리의 일치 여부를 검사한다. |

[펌웨어 수락기](../esp32_firmware/esp32_omni_controller/network_protocol.cpp), [펌웨어 안전 경로](../esp32_firmware/esp32_omni_controller/safety_telemetry.cpp), [PC 실행기](../python_controller/execution/motion_executor.py)

### 텔레메트리와 목표 판정

- 설정된 ESP32의 IP와 UDP 8888에서 온 패킷만 사용한다. 유효한 `boot_id`, 동일 세션, 송신 이력의 `last_seq`, 증가하는 보드 uptime, NETWORK 모드, `fault=NONE`, 보드의 `command_age_ms ≤ 300`을 검사한다. 송신 이력이 기준이며 기본 신선도 한도는 0.8초다. 외부 주소와 정상 형식의 중복·역순 패킷은 신선도를 갱신하지 못한다. 대상 주소에서 잘못된 데이터, 모드·세션·부팅 불일치 또는 fault를 보고하면 중단한다.
- `DISTANCE_ACTIVE`, `DISTANCE_BRAKING`, `GOAL_REACHED`에는 목표 ID 및 유한한 목표 필드가 필요하다. `STOPPED`의 목표 필드는 null이거나 이전 값일 수 있다. PC가 목표 완료를 인정하려면 `GOAL_REACHED` 상태와 boolean 플래그, 현재 목표의 거리·ID, PWM 0, 잔여 거리 약 0.5cm 이내, 횡오차 약 2cm 이내, 바퀴 속도 약 0.8cm/s 이내가 맞아야 한다. 그 뒤 새 STOP을 확인해야 다음 이동을 보낸다.
- 펌웨어는 오도메트리상 목표선을 넘으면 `STOPPED`로 정지하고 초과량을 기록한다. `GOAL_REACHED`가 없으므로 PC는 성공으로 처리하지 않는다. 궤도에서 5cm를 초과해 벗어나면 `PATH_DEVIATION`이다. 200ms 주기 때문에 시험 로그에서 모든 중간 상태를 반드시 볼 수 있는 것은 아니다.

[PC 텔레메트리 검사](../python_controller/execution/llm_telemetry.py), [펌웨어 거리 판정](../esp32_firmware/esp32_omni_controller/kinematics_motion.cpp)

### 하드웨어 합격 판정에 영향을 주는 항목

1. **NETWORK 제어권 격리는 운영 조건이다.** 정지된 보드는 새 세션의 첫 유효 MOVE를 수락할 수 있다. Mode Guard는 MANUAL_TEST만 격리한다. C6는 격리된 네트워크에서 송신기 한 대로 진행한다. 다중 송신기 시험은 모의 또는 모터 전원이 격리된 상태에서만 진행한다.
2. **모순된 목표 완료를 PC가 수락한다.** 소켓 없이 기존 모의 ESP32에서 5cm 목표의 `goal_progress_cm=0`, `remaining_cm=0`, `overshoot_cm=6`, `cmd_vx=5`를 같은 `GOAL_REACHED` 샘플에 넣었을 때 `COMPLETED`가 기록됐다. 출력은 `.tmp/c6-preflight/contradictory-goal.log`에 있다. 사후 확인에서 목표−진행량과 잔여량, 초과량과 목표 도달 조건, 완료 시 명령 속도 0의 일관성을 대조한다. 모순이 하나라도 있으면 해당 실행은 실패로 기록한다. 자동 완료를 C6 합격 근거로 쓰려면 이 교차 검증과 회귀 테스트를 별도 변경으로 승인받아야 한다.
   - 중앙 제어 탭 통합 이후 PC 실행기는 위 필드가 모순되면 `INCONSISTENT_GOAL_COMPLETION`으로 중단한다. 같은 입력을 재현한 소프트웨어 회귀시험에서 STOP 시도와 다음 목표 차단을 확인했다. 실물 검증은 남아 있다.
3. **중단 시 fault와 정지 증거가 로그에 남지 않을 수 있다.** PC는 abort STOP의 송신 결과까지만 남긴다. NETWORK의 STOP/heartbeat 수락 경로는 `CMD_TIMEOUT`, `WIFI_LOSS`, `BAD_PACKET` 같은 복구 가능 fault를 지울 수 있다. 시험에서 관측한 fault와 출력·기계 정지 시점을 각각 기록하고, 후속 `fault=NONE`만으로 장애가 없었다고 판정하지 않는다.
4. **현재 모델 엔드포인트로 정상 CLI 이동 계획을 생성할 수 있다는 증거가 없다.** 마지막 인증 dry-run은 NVIDIA `410 Gone`이었다. 실제 CLI로 `cmd_move` 단계에 들어가기 전에 사용자가 승인한 활성 모델 설정으로 dry-run이 성공해야 한다. C6 준비 중 모델 설정이나 검증기를 변경하지 않았다.

## 승인 이후 단계별 시험

각 단계는 이전 단계의 통과 기록을 확인한 뒤 진행한다. 예상치와 다르거나 제어권, 모드, 정지 여부를 확인할 수 없으면 모터 전원을 차단하고 해당 시험을 종료한다.

| 단계 | 절차 | 예상 정상 결과 |
| --- | --- | --- |
| C6-0 보드 및 구동 전원 확인 | 모터 전원을 분리한 채 보드·모듈 표기, 드라이버와 전원 사양, 공통 GND, STBY, 엔코더 신호와 배선을 확인한다. 승인 후 시리얼 115200으로 `STATUS`, `PIN`, `ENC`를 기록한다. 실제 타깃 빌드와 업로드 대상 해시는 장비 정보 확인 후 확정한다. | 배선과 소스 핀이 한 행씩 일치한다. ESP32 모듈에서 GPIO16/17이 엔코더에 사용 가능하고 GPIO34/35에는 센서 출력 방식에 맞는 외부 풀업이 확인된다. |
| C6-1 정지 연결 | 모터 전원 분리 상태에서 새 부팅 후 NETWORK와 UDP ON, 현재 IPv4를 확인한다. 기존 대시보드의 통신 연결 승인만 사용해 STOP과 정지 heartbeat를 확인한다. CLI를 별도로 확인할 때는 대시보드를 종료한다. | 같은 부팅·세션 내 `last_seq`가 증가한다. fresh `STOPPED`, 명령 속도·PWM 0, 낮은 휠 속도. 다른 수신기가 UDP 8889를 점유하면 새 CLI의 바인드가 실패한다. |
| C6-2 후보 모드 격리 | 후보가 별도 승인 아래 올라간 보드에서 모터 전원을 계속 분리한다. MANUAL_TEST에서 유효한 UDP MOVE·VELOCITY·heartbeat·reset을 각각 넣고 시리얼 `STATUS`와 가능한 출력 신호를 대조한다. 별도로 UDP STOP과 Serial 경로를 확인한다. 구동 전원을 복구하기 전에 STOP, NETWORK, 새로운 정지 확인을 거친다. | 차단 명령마다 rejected 증가. 운동 메일박스가 실행되지 않고 세션·fault가 유지된다. 유효 UDP STOP은 출력을 끈다. 후보는 새 송신처를 등록하지 않으므로 첫 MANUAL STOP의 성공을 UDP 에코만으로 판정할 수 없다. |
| C6-3 바퀴를 띄운 방향 시험 | 세 바퀴가 모두 지면에 닿지 않게 본체를 고정한다. 모터 전원 인가를 승인받은 뒤 기존 MOTOR TEST에서 짧은 저속 시험을 방향별로 한 번씩 승인한다. 관측한 회전 방향과 휠 속도·엔코더 부호를 비교한다. | 전진에서 M1 목표는 약 0, M2 음수, M3 양수다. 좌측 이동에서 M1 양수, M2·M3 음수다. 각 반대 방향의 부호는 반전된다. STOP 뒤 PWM 0, 바퀴의 실제 회전 종료가 확인된다. |
| C6-4 바퀴를 띄운 거리·정지 시험 | 승인된 모델 dry-run과 방향 시험을 마친 뒤 5cm 단일 목표부터 확인한다. 정상 완료, Ctrl+C, 송신 단절, Wi-Fi 끊김 시험을 각각 별도 실행한다. 워치독 시험에서는 끊어지는 경로와 시각을 기록하고, 실제 정지를 독립적으로 관측한다. | 목표 ID가 유지되고 정상 실행에서 `GOAL_REACHED` 뒤 새 STOP이 확인된다. Ctrl+C와 통신 장애에서 후속 목표를 보내지 않는다. 송신 단절 시 워치독, 명시적 Wi-Fi 연결 상실 시 Wi-Fi fault가 출력을 끄는지 확인한다. 복구 뒤 자동 재출발이 없어야 한다. |
| C6-5 바닥 소거리 시험 | 앞 단계 통과 후 승인된 거리·횡오차·정지 지연 기준을 기록한다. 5cm 전진부터 시작해 10cm 전진, 5cm 좌·우·후진을 각각 확인한다. 마지막에 전진 15cm와 오른쪽 10cm를 실행하고 각 구간을 따로 실측한다. | 실제 몸체 방향, 바닥 변위 및 정지 지연이 사전 합의 기준 내에 있다. 텔레메트리 거리·오차를 줄자 결과와 각각 비교한다. 복합 시험에서 첫 STOP 확인이 두 번째 MOVE보다 앞선다. |

GPIO16/17의 사용 가능 여부는 모듈에 따라 다르다. GPIO34/35에는 소프트웨어 풀업이 없다. 드라이버의 전원·STBY 동작은 실제 모듈과 데이터시트를 대조한다. [Espressif DevKitC 핀 안내](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html), [Espressif GPIO 안내](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/gpio.html), [Toshiba TB6612FNG 데이터시트](https://toshiba.semicon-storage.com/info/TB6612FNG_datasheet_en_20141001.pdf?did=10660&prodName=TB6612FNG)

## 로그 형식

기존 `logs/llm/<run_id>.jsonl`은 원본 그대로 보관한다. [실물 시험 기록 양식](c6_trial_log.example.json)을 구간 또는 장애 시험마다 한 건 복사해 사용한다. `run_id`·`boot_id`·`session_id`·`motion_id`로 매칭한다. 값이 확인되지 않으면 null과 관찰 근거를 기록한다.

| 기록 대상 | 추출·측정 방법 |
| --- | --- |
| 주행 거리와 오차 | `cmd_move`의 목표 거리, 완료 텔레메트리의 진행·잔여·횡오차·초과량, 줄자의 목표 방향 투영 거리와 횡방향 오차를 각각 기록한다. 부호 있는 실측 거리 오차는 `physical_along_cm - target_distance_cm`다. 장애 시험처럼 완료되지 않은 실행도 마지막 유효 텔레메트리와 실측 위치를 남긴다. |
| heartbeat | 주행 구간의 `UDP_TX.command.type=heartbeat`에서 연속 `sent_monotonic_s` 차이를 ms로 기록한다. 첫 MOVE→첫 heartbeat 간격은 별도 필드에 넣는다. `HEARTBEAT_INTERVAL`의 첫 샘플에는 이 첫 간격이 포함된다. `echoed_packet_age_s`는 송신부터 그 순서를 포함하는 텔레메트리 도착까지 걸린 시간이다. |
| 소프트웨어 정지 확인 지연 | `STOP_CONFIRMED.stop_barrier`에 해당하는 STOP의 `sent_monotonic_s`부터, 동일 부팅·세션·순서·uptime의 `TELEMETRY.received_monotonic_s`까지의 차이다. 중단 경로에서는 값이 null일 수 있다. |
| PWM 차단·실제 정지 지연 | 동일한 측정 시계에서 STOP 조작 또는 장애 주입 시점, 마지막 PWM 차단 시점, 바퀴 또는 본체가 완전히 멈춘 시점을 별도 기록한다. 영상은 조작 표식과 바퀴/본체가 같은 시간축에 있도록 촬영한다. 보드가 마지막 명령을 수락한 시점을 동일 시간축에서 계측한 경우에만 watchdog 기준 지연을 산출한다. |

텔레메트리 수신 시간, PWM 차단, 실제 바퀴·본체 정지를 각기 다른 증거로 남긴다. 현재 C6 판정은 `NOT_TESTED`다.
