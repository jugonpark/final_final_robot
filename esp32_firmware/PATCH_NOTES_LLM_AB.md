# Prototype A+B Mode Guard 검토

## 필요성

참조본의 `serviceUdp()`는 디코딩에 성공한 패킷을 `handleAcceptedCommand()`에 넘긴다. 여기서 NETWORK 모드를 확인하기 전에 `acceptCommand()`를 호출한다. `enterManualMode()` 직후에는 수락 상태와 운동이 초기화되므로, 정지 상태에서 새 UDP 세션의 유효 MOVE가 운동 메일박스에 들어갈 수 있다. 직접 PWM 실행 중에는 제어 틱의 메일박스 소비가 건너뛰어지지만, 수동 모드 전체가 격리되지는 않는다. UDP 명령으로 수동 모드에서 운동이 시작되면 NETWORK의 300ms watchdog 대신 수동 모드의 60초 분기가 적용된다.

PC 실행기는 NETWORK 텔레메트리를 요구한다. 보드의 모드 가드는 PC를 우회한 송신자가 수동 모드에서 운동을 시작하는 경로를 닫는다.

## 후보 패치

[mode_guard.patch](candidates/llm_ab_v1/mode_guard.patch)는 스케치의 `handleAcceptedCommand()`에만 적용한다.

| 입력 | 후보 동작 |
| --- | --- |
| NETWORK의 UDP | 기존 명령 수락 및 watchdog 경로 |
| MANUAL_TEST의 UDP MOVE·VELOCITY·HEARTBEAT·RESET_FAULT | 수락기 진입 전 거절. 메일박스·세션·fault에 영향 없음 |
| MANUAL_TEST의 유효한 UDP STOP | 수동 PWM 해제 및 즉시 정지. 송신자 주소·세션·fault·fault 발생 시각 유지 |
| Serial 명령 | `fromNetwork=false`로 기존 경로 진입 |
| 해석할 수 없는 UDP 패킷 | 기존 `serviceUdp()`의 BAD_PACKET 정지 경로 |

수동 모드의 유효 UDP STOP은 순서 번호나 송신자 권한과 무관하게 현재 출력을 정지시킨다. 기존 프로토콜에는 UDP 인증이 없으므로 분리된 네트워크에서만 운용한다. NETWORK 모드에서 타 호스트가 보낸 명령의 처리 방식은 기존 정책을 따른다.

## 검증 기록

- 후보 SHA-256 `8653F0505426985B242282FB61780E0B8E7156DA169EC215670DFEE6C79301C0`
- `git apply --check --directory=esp32_firmware/esp32_omni_controller esp32_firmware/candidates/llm_ab_v1/mode_guard.patch` 통과.
- 참조본과 후보를 별도 임시 스케치로 복사하고 플레이스홀더 `secrets.h`로 컴파일했다. Arduino CLI 1.5.1, ESP32 코어 3.3.12, ArduinoJson 7.4.3, 범용 타깃 `esp32:esp32:esp32`를 사용했다. 참조본 950,483B, 후보 950,547B. 빌드 로그는 `.tmp/llm-ab-build/reference.log`와 `.tmp/llm-ab-build/candidate.log`에 있다.
- 원본 ZIP 및 펌웨어 해시 회귀시험 통과. 실제 보드 모델·업로드 버전과 Serial 수동 명령, UDP 모드 격리 및 STOP의 실물 회귀는 미검증이다. 보드 업로드는 수행하지 않았다.
