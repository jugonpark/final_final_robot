# D.I.G. 하드웨어 프로필 검증 보고서

설계 기준은 기존 브레드보드 SIMPLE V2 소스(`b03f7d2`), V18 회로도 넷리스트와 `FINAL_PROJECT_V1.zip`의 V19 설계다. V19 원본 아카이브는 읽기 전용으로 검토했다.

| V19 출처 | SHA-256 |
| --- | --- |
| `FINAL_PROJECT_V1.zip` | `5674b8d9beaa1cdecd8da37d9e68d0071faff8f283e3797236fa71d736337219` |
| `01_KICAD_REVISED/omni_3wd_v19_smt_only_tht_manual_candidate.kicad_pcb` | `e2305e29dbd56346b2c35d2e903e2a47e05b41d6993f92aaf5df6247205960dd` |
| `01_KICAD_REVISED/omni_3wd_v19_smt_only_tht_manual_candidate.kicad_sch` | `62c8486240dcfe2a6c37b51bd6ef4686ca5f1e0202c1ac9e0f0646204f34778f` |
| `03_THT_HAND_ASSEMBLY/THT_PHYSICAL_PINMAP.csv` | `bc53d3abb90bd9dc45a0502dbbfd533460cea8824c7ef817a41be76938792823` |

PCB 파일의 SHA-256은 아카이브의 `05_DECISIONS/MANUFACTURING_READINESS.json`과 일치한다. KiCad CLI로 V19 넷리스트를 추출해 U1의 핀 기능·네트·패드 표를 16개 신호 전체에서 대조했다. V18과 V19의 GPIO는 16개 모두 일치한다. 브레드보드와 V19의 GPIO 차이는 6개다. GPIO32의 `/VBAT_ADC` 배정과 GPIO33의 미접속도 확인했다. [신호별 대조표](HARDWARE_PROFILE_PINMAP_COMPARE.csv)

## 변경 범위

| 파일 | 적용 내용 |
| --- | --- |
| `esp32_firmware/control_simple_v2/control_simple_v2/{control_simple_v2.ino,hardware_profiles.h}` | 보드별 GPIO, 컴파일 선택 검사, 입력 전용 핀 설정, 시리얼·UDP 식별 정보 |
| `python_controller/{main.py,config/__init__.py,config/default.json,communication/robot_session.py,diagnostics.py}` | 운영자 선택, 식별 대조, 이동 패킷 차단과 STOP 응답 처리 |
| `python_controller/{visualization/dashboard.py,visualization/korean.py,web/app.js}` | Qt 진단 상태와 웹 RAG 출처 라벨 |
| `python_controller/knowledge/{variants.py,library.py,answer.py}` | BOM·펌웨어·CAD 색인 구분, 보드별 조회와 출처, 구형 V19 후보 레코드 제거 |
| `docs/hardware/` | 보드 프로필, 핀맵 비교표, 버전별 RAG 자료와 출처 매니페스트 |
| `tests/{test_hardware_profiles.py,test_hardware_variants.py}` | 핀맵·모의 ESP32·버전별 검색 회귀 검사 |

SIMPLE V2 제어 코드에서 Wi-Fi 연결 상실, heartbeat 만료, 속도 명령 TTL, 목표 타임아웃에 대한 정지 경로가 있는 것을 확인했다. 이 동작의 실물 시험은 `NOT_RUN`이다. 승인 관리와 RobotSession의 정지 응답 확인은 기존 경로를 사용한다.

## 실행 결과

| 검증 | 결과 |
| --- | --- |
| Arduino CLI 1.6.0, ESP32 코어 3.3.12, `esp32:esp32:esp32` | BREADBOARD와 PCB_V19 컴파일 통과. 두 임시 스케치 빌드에 예제 Wi-Fi 값을 사용했다. |
| ESP32 C++ 정적 컴파일 | 두 단독 선택 성공. 미선택 및 동시 선택에 대해 의도한 컴파일 오류 확인. |
| 하드웨어 프로필·RAG 자동 테스트 | 10개 통과. GPIO 16개, 프로필 오류 시 이동 및 이동 허용 heartbeat 차단, STOP 확인, 재동기화, 기존 V19 후보 색인 정리와 보드별 출처를 검사했다. |
| 격리된 전체 회귀 | 269개 중 268개 통과, 1개 건너뜀. Agent 승인·레이더·STOP·Qt·웹 모의 테스트를 포함한다. 건너뛴 항목은 비공개 원본 아카이브가 필요한 펌웨어 출처 검사다. |

[프로필별 빌드 명령](HARDWARE_PROFILES.md#빌드-및-python-선택)

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_hardware_*.py' -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 실물 검증과 승인

`DEPLOYMENT_MATCH=UNKNOWN`이며 다음 상태는 모두 `false`다. 이 검증에서 펌웨어 업로드와 모터 출력 시험은 수행하지 않았다.

| 상태 | 필요한 증거 |
| --- | --- |
| `BOARD_IDENTITY_VERIFIED` | 실물 ESP32 DevKit 모델·헤더·삽입 방향 |
| `DRIVER_ORIENTATION_VERIFIED` | 실제 TB6612 모듈 모델·방향·연속성 |
| `MOTOR_ENCODER_WIRING_VERIFIED` | 실제 JM1~JM3 결선과 엔코더 신호 |
| `POWER_SAFETY_VERIFIED` | 전원 극성, 퓨즈, 부하, 전류와 온도 |
| `FLASHED_FIRMWARE_VERIFIED` | 장치에 탑재된 펌웨어의 프로필과 해시 |
| `STOP_WATCHDOG_VERIFIED` | 통신 또는 프로세스 중단 시 실물 정지 |
| `PHYSICAL_MOTION_TESTED` | 실물 이동과 엔코더 방향 |
| `FINAL_PINMAP_APPROVED` | CAD·펌웨어·실제 배선의 일치 승인 |
| `FAB_APPROVED` | 제작·실장 검토 승인 |
