# ESP32 펌웨어 분석

분석 기준은 작업 폴더에 제공된 `esp32_omni_controller (.zip`이며 SHA-256은 `esp32_firmware/source_manifest.json`에 기록했다. 원본은 변경하지 않았다. 참조본의 모터·통신·안전 로직 파일 11개는 원본과 바이트가 같다. `.ino`는 Wi-Fi 자격 증명 대체값만 컴파일 오류로 교체했다. 원래 `secrets.h` 및 원래 예제는 복사하지 않고 플레이스홀더 예제를 새로 제공했다. 참조본을 빌드하려면 로컬 `secrets.h`가 필요하다.

## 확인한 기존 기능

| 파일 | 확인한 내용 |
|---|---|
| esp32_omni_controller.ino | 부팅 시 출력 정지, Wi-Fi·UDP 초기화, 예산 제한 UDP 수신, 10ms 제어, 시리얼 수동/네트워크 모드, 200ms 텔레메트리 |
| robot_config.h | UDP 8888/8889, watchdog 300ms, SLOW 5cm/s·0.5rad/s, PWM/엔코더 핀, 방향 플래그, PID·가속도·피드포워드 상수 |
| motor_encoder.cpp/.h | A상 상승 에지 x1 계수·B상 방향 판독, 외부 풀업 필요 GPIO34/35, encoder jump 검사, 속도 필터, PWM 반전 deadtime, 즉시 정지 |
| kinematics_motion.cpp/.h | body 속도→3개 휠 목표, SLOW 제한, body/휠 가속도 제한, PID+측정 테이블, bounded start assist, MOTOR_STALL, 거리 명령의 진행·횡오차·제동 |
| network_protocol.cpp/.h | typed/legacy 디코드, 유한 숫자·상태·세션·시퀀스 검증, 최신 명령 mailbox, STOP·heartbeat·fault reset |
| safety_telemetry.cpp/.h | moving 상태에서 watchdog, recoverable/latched fault, 출력 먼저 차단, typed 텔레메트리와 이전 alias |
| odometry_math.h | 0/120/240도 역기구학·정기구학, 바퀴 이동량, 진행/횡오차, encoder bound, braking/slew 수식 |
| firmware_self_test.h | 운동학 round trip·제동·slew 등의 constexpr static_assert. 이번 작업에서 실제 펌웨어 컴파일은 실행하지 않음 |

## 실제 통신 계약

원본 `network_protocol.cpp:64–109`에 따른 typed 메시지이다. 모든 메시지에 0이 아닌 uint32 `session_id`, uint32 `seq`가 필요하다. `cmd_vel`은 `status`, `vx`, `vy`, `w`가 필수다. 선속도는 cm/s, 각속도는 rad/s다. Python은 SLOW만 사용한다.

```json
{"type":"stop","session_id":123,"seq":1}
{"type":"cmd_vel","session_id":123,"seq":2,"status":"SLOW","vx":2.0,"vy":0.0,"w":0.1}
{"type":"heartbeat","session_id":123,"seq":3}
{"type":"reset_fault","session_id":123,"seq":4}
```

동일 세션에서는 `seq <= lastSequence`를 무시한다(`network_protocol.cpp:230–244`). uint32 wrap 처리가 없으므로 Python은 소진 전에 전송을 중단하고 새 프로세스/세션을 요구한다. 예제 세션값은 실제 운용에서 난수로 생성한다.

새 세션이 이동 중 도착하면 먼저 정지하고 handshake를 미완료로 만든다(`217–229`). 다음 더 큰 seq의 STOP/heartbeat가 handshake를 완성한다(`252–260`). 정지 중 새 세션은 처음부터 handshake가 완료될 수 있어 펌웨어가 STOP handshake를 항상 강제하는 것은 아니다. Python은 더 엄격하게 첫 STOP을 보내고, 해당 세션과 전송 seq 범위에 맞는 fresh STOPPED 응답, fault NONE, 0 명령·PWM, 낮은 휠 속도를 확인한 후 활성화한다.

STOP은 mailbox와 움직임을 지운다. heartbeat는 lastAcceptedAtMs와 handshake를 갱신하지만 기존 이동은 지우지 않는다. 기존 자동 추종 전송부는 heartbeat를 사용하지 않는다. 현재 추가된 MOTOR TEST 전송부는 STOP 이후 정지 증명을 얻은 무구동 상태에서만 heartbeat를 사용한다. 이동·정지 대기·고장 중에는 heartbeat로 기존 이동을 유지하지 않으며 독립 시간/생존 검사가 STOP으로 전환한다.

워치독은 moving 상태에서만 Wi-Fi 및 마지막 수락 명령의 나이를 검사한다(`safety_telemetry.cpp:140–160`). 원본은 hard limit 30cm/s·2rad/s 초과를 fault로 처리하지만 Python은 더 낮은 SLOW 한계를 적용한다. 펌웨어의 가속도·휠 제한은 유지한다.

텔레메트리는 `type=telemetry`, `boot_id`, `session_id`, `last_seq`, `uptime_ms`, `mode`, `state`, `fault`, `cmd_vx/cmd_vy/cmd_w`, `command_age_ms`, `wheel_target`, `wheel_speed`, `wheel_pwm`, `encoder_count` 등을 포함한다(`safety_telemetry.cpp:168–237`). `seq/status/counts/rpm` 등은 migration alias다. Python은 typed 필드를 검증한다.

ESP32는 마지막으로 기억한 컨트롤러 IP의 **고정 8889 포트**로 응답한다. commandUdp 소켓은 8888에 bind되어 응답 source도 8888이다(`.ino`의 `rememberController`, `runTelemetryIfDue`). Python은 RX 8889를 별도 bind하며 source ESP32 IP:8888을 검사한다. 원본은 일부 NEEDS_HANDSHAKE/속도 제한 분류에도 컨트롤러 IP를 기억할 수 있어, 수신만으로 명령 성공을 가정하지 않는다. session·seq·mode·state를 확인한다.

WIFI_LOSS/CMD_TIMEOUT/BAD_PACKET은 recoverable, 나머지는 latched다(`safety_telemetry.cpp:16–18,101–137`). latched fault는 명시 reset authorization 후 STOP/heartbeat handshake로 해제한다. Python에는 reset_fault 패킷 생성만 제공하고 자동 reset은 하지 않는다. ESP32가 recoverable fault를 STOP으로 해제해도 Python의 이전 이동은 자동 복귀하지 않는다. MANUAL_TEST 응답은 네트워크 활성화 조건을 충족하지 않는다. 실제 운용 전 Serial AUTO 상태를 확인한다.

## 자격 증명과 미검증 항목

원본은 secrets.h 외에 `.ino` fallback에도 자격 증명을 포함했다. 원본 ZIP 자체는 공유 금지 대상으로 취급하고, 외부 공유 전 자격 증명을 교체한다. 새 코드·문서에는 값을 기록하지 않는다. 참조본 수정 이유는 자격 증명 재배포 방지이며 제어 로직 변경은 없다.

모터 방향·카메라 방향·전후 축·회전 부호는 수식만으로 실물 일치를 확정하지 않는다. 펌웨어의 기존 피드포워드 테이블과 주석은 이번 작업의 측정 결과가 아니다. 보드 업로드, 전기 배선, 엔코더 방향, 실제 정지 거리, 저속 실물 구동, RF, 레이더는 검증하지 않았다.
