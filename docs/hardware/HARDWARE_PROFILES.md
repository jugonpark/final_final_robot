# D.I.G. 보드별 펌웨어 프로필

`BREADBOARD`는 브레드보드 시제품의 SIMPLE V2 펌웨어 설정이다. `PCB_V19`는 `FINAL_PROJECT_V1.zip`의 V19 설계 설정이다. V18은 과거 CAD 리비전으로 관리한다. V18과 V19는 아래 16개 신호의 GPIO가 같으며 부품과 실장 조건은 리비전별로 확인한다. `SIMPLE_V2`는 공통 펌웨어 이름이다.

## GPIO

| 모터 | BREADBOARD (IN1 / IN2 / PWM / ENC A / ENC B) | PCB V19 (IN1 / IN2 / PWM / ENC A / ENC B) |
| --- | --- | --- |
| M1 | 19 / 18 / 25 / 34 / 35 | 18 / 19 / 25 / 34 / 35 |
| M2 | 21 / 22 / 26 / 16 / 17 | 21 / 22 / 26 / 36 / 39 |
| M3 | 23 / 13 / 14 / 32 / 33 | 23 / 13 / 14 / 16 / 17 |

두 프로필의 STBY는 GPIO27이다. [비교표](HARDWARE_PROFILE_PINMAP_COMPARE.csv)의 `PROFILE_DELTA` 6건은 프로필 사이의 GPIO 차이다. 실제 배선과 탑재 펌웨어가 일치하는지는 16건 모두 `DEPLOYMENT_MATCH=UNKNOWN`이다.

V19에서 MD1 A/B는 M1/M2, MD2 A는 M3에 연결한다. MD2 B 제어 입력은 GND이고 B 출력은 미접속이다. U1 패드 7(GPIO32)은 `/VBAT_ADC`이며 패드 8(GPIO33)은 미접속이다. GPIO34/35/36/39에는 내부 풀업이 없어 펌웨어가 `INPUT`으로 설정한다. 엔코더 출력, 외부 풀업, 장착 방향과 모터 부호는 실물 검증이 필요하다.

## 빌드 및 Python 선택

아래 명령은 ESP32용 스케치 빌드 예시다. PCB V19용은 마지막 인자의 매크로를 `DIG_BOARD_PCB_V19`로 설정한다. 미선택과 두 매크로 동시 선택은 컴파일 오류다.

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 `
  --build-property 'compiler.cpp.extra_flags=-DDIG_BOARD_BREADBOARD' `
  esp32_firmware/control_simple_v2/control_simple_v2
```

Qt는 `--hardware-profile BREADBOARD` 또는 `--hardware-profile PCB_V19`로 시작한다. JSON 설정을 사용하는 경우 `communication.hardware_profile`에 같은 값을 넣는다. 기본값은 `null`이다. Qt 진단 페이지는 운영자 선택, 펌웨어 보고, 핀맵 리비전, 일치 상태를 표시한다.

RobotSession은 선택과 새 텔레메트리의 `hardware_profile`, `firmware_version`, `pinmap_revision`이 일치할 때 구동 준비를 판단한다. 미선택, 미확인, 불일치 시 이동을 차단하고 STOP 송신과 응답 확인을 계속한다. 재연결이나 재부팅 후에는 다시 일치 확인과 새 입력이 필요하다. `MATCH`는 펌웨어가 선언한 값의 일치를 뜻한다. 실제 배선 검증과 제작 승인 상태는 [검증 보고서](HARDWARE_PROFILE_VALIDATION_REPORT.md)를 참고한다.

## RAG

- `BREADBOARD_PROTO`는 [사용자 BOM](breadboard/FACTS.md)과 [펌웨어 GPIO](breadboard/PIN_PROFILE.md)를 각각 인용한다. 배터리는 제공 BOM의 사용자 확인값인 7.4V로 설명한다.
- `PCB_V18_DESIGN`과 `PCB_V19_DESIGN`은 [V18 CAD](pcb-v18/FACTS.md)와 [V19 CAD](pcb-v19/FACTS.md)를 각각 조회한다. `현행 PCB 설계`는 V19로 조회한다.
- 어느 보드인지 불분명한 배선·핀 질문은 기준을 확인한다. RAG가 장치의 실물 보드 리비전을 판정하거나 보드 프로필을 선택할 수 없다.
