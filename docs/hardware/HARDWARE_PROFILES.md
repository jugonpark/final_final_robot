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

옵션을 지정하지 않으면 브레드보드 GPIO로 빌드된다. PCB V19용은 `DIG_BOARD_PCB_V19`를 명시한다. 두 보드 매크로를 동시에 지정하면 컴파일 오류다.

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 esp32_firmware/control_simple_v2/control_simple_v2
```

PCB V19를 빌드할 때는 위 명령에 `--build-property 'compiler.cpp.extra_flags=-DDIG_BOARD_PCB_V19'`를 추가한다.

Qt와 공유 웹의 기본값은 `BREADBOARD`다. PCB V19는 `--hardware-profile PCB_V19` 또는 JSON `communication.hardware_profile`로 지정한다. 과거 JSON의 `null`은 기본값을 따른다.

브레드보드의 구형 펌웨어가 세 프로필 항목을 모두 생략하면 `LEGACY_BREADBOARD`로 표시하고 기존 STOP·레이더·승인 조건을 적용한다. 같은 연결에서 선언을 한 번이라도 받은 뒤에는 선언 누락을 차단한다. 일부 선언이 누락됐거나 선택한 값과 다르면 이동을 차단한다. PCB V19와 레이더 미연결 벤치 시험은 `MATCH`가 필요하다. `MATCH`는 펌웨어가 선언한 값의 일치다. 실제 배선 검증과 제작 승인 상태는 [검증 보고서](HARDWARE_PROFILE_VALIDATION_REPORT.md)를 참고한다.

## RAG

- `BREADBOARD_PROTO`는 [사용자 BOM](breadboard/FACTS.md)과 [펌웨어 GPIO](breadboard/PIN_PROFILE.md)를 각각 인용한다. 배터리는 제공 BOM의 사용자 확인값인 7.4V로 설명한다.
- `PCB_V18_DESIGN`과 `PCB_V19_DESIGN`은 [V18 CAD](pcb-v18/FACTS.md)와 [V19 CAD](pcb-v19/FACTS.md)를 각각 조회한다. `현행 PCB 설계`는 V19로 조회한다.
- 어느 보드인지 불분명한 배선·핀 질문은 기준을 확인한다. RAG가 장치의 실물 보드 리비전을 판정하거나 보드 프로필을 선택할 수 없다.
