# 1차 검증 결과 — 2026-11-01 (과거 기록)

대시보드 변경 후 최신 결과는 [DASHBOARD_VALIDATION.md](DASHBOARD_VALIDATION.md)를 참조한다. 아래 30개 테스트는 최초 구현 시점의 기록이며 현재 테스트 수와 구분한다.

## 실행 환경

Windows, Python 3.13.14. 프로젝트 가상환경은 기존 전역 설치를 재사용했다. OpenCV 5.0.0.93, Ultralytics 8.4.98, MediaPipe 0.10.35, NumPy 2.5.3, lap 0.5.13, PyTorch 2.13.0을 확인했다. lap은 프로젝트 가상환경에만 추가했다. 완전히 새 환경에서 requirements 전체 설치는 실행하지 않았다.

공식 YOLO11n(5,613,764 bytes), GestureRecognizer float16 version 1(8,373,440 bytes)을 다운로드했다. URL과 SHA-256은 `models/manifest.json`에 기록했다. 초기 sandbox DNS 제한으로 다운로드가 실패했고, 허용된 네트워크 실행에서 완료했다. ensurepip 초기 실패는 프로젝트 내부 TEMP/TMP를 사용한 재시도에서 해결했다.

## 확인 결과

| 검사 | 실제 실행 | 결과와 해석 |
|---|---|---|
| 모의 입력 자동 테스트 | `.venv\Scripts\python.exe -m unittest discover -s tests -v` | 30 tests OK. 카메라·ESP32 없이 실행 |
| 문법·import bytecode | `.venv\Scripts\python.exe -m compileall -q python_controller scripts tests` | exit 0 |
| 합성 제어 데모 | `.venv\Scripts\python.exe -m python_controller --demo` | TRACKING → FOLLOWING → TARGET_LOST → 명시 RESUME → OBSTACLE_STOP → 명시 RESUME → PAUSED. 실제 소켓 없음 |
| 두 모델 추론 API | `.venv\Scripts\python.exe scripts/check_vision.py` | 빈 이미지 3프레임, person 0·hand 0, exit 0. 첫 5,807ms, 이후 49.44/48.81ms. 정확도 평가가 아님 |
| 실제 웹캠 경로 | `.venv\Scripts\python.exe scripts/check_vision.py --camera` | 권한 허용 환경에서 3프레임 모두 person 1·hand 0, exit 0. 첫 3,781.3ms, 이후 86.05/62.47ms. 이미지 미저장 |
| 디버그 화면 | 합성 프레임 렌더·이미지 시각 확인 | `debug_preview.png`에서 텍스트·목표·긴급 정지 버튼 확인 |
| 네이티브 OpenCV 창 | 합성 이미지를 300ms 표시 후 닫기 | 창 visible 검사 성공, exit 0. 실제 웹캠 상호작용 전체 검증은 아님 |
| 긴급 정지 버튼 | 클릭 좌표→ESTOP event 검사 | 통과. 소프트웨어 이벤트 경로 확인 |
| 펌웨어 출처·자격 증명 | 테스트 내 원본 hash·참조본 파일 hash·토큰 복사 검사 | 원본 ZIP 보존, 제어 파일 11개 동일, `.ino` credential-only 변경, 새 소스에 원본 자격 증명 없음 |

제한된 실행에서 처음 웹캠 열기는 실패했다. 동일 코드를 권한이 허용된 환경에서 실행하면 성공했다. 따라서 이 첫 실패를 카메라나 패키지 자체 고장으로 판정하지 않았다. MediaPipe의 feedback tensor/NORM_RECT 경고와 sandbox의 SDK 연결 경고가 있었으나 위 추론 프로세스는 exit 0으로 종료했다. 이 SDK 로그를 제스처 인식 성공의 증거로 사용하지 않는다.

분석 중 원본 `.ino` 대체 설정에 있던 Wi-Fi 자격 증명이 도구 출력에 한 차례 포함되었다. 새 참조본의 해당 값은 제거했고 새 코드·문서의 미복사 검사도 통과했다. 이미 출력된 내용을 회수할 수는 없으므로 해당 Wi-Fi 비밀번호 교체가 필요하다. 값은 이 문서에 기록하지 않는다.

## 30개 테스트가 확인한 경계

미활성화 정지, 확정·활성화·재개 순서, 목표 ID 유지 및 임의 전환 금지, 목표 상실 정지 유지, 카메라 timestamp 지연·미래 값, 통신 단절·ESP32 fault, 레이더 위험·UNKNOWN, 겹친 사람, 방향·deadband·가속도·속도 제한, 잘못된 박스, 후진 제한, 제스처 시간·신뢰도·목표 ID·중복 처리, 정지 우선, 유지한 재개 제스처의 위험 후 재출발 차단, 여러 사람일 때 제스처 차단을 확인했다.

UDP 생성의 단위·SLOW·session·seq·uint32 소진, malformed/외부 source/다른 session/반복 uptime/boot 변경/0이 아닌 정지 응답/고장 텔레메트리 거부를 확인했다. 별도 가드 스레드가 실제로 실행되는 **가짜 소켓 테스트**에서 주 루프의 perception lease 갱신을 중단하자 STOP을 출력하고 활성화가 해제되었다. 이 테스트는 실제 네트워크 지연이나 물리 정지 시간을 측정하지 않는다.

SIMULATION demo와 모의 카메라 전체 루프는 socket 생성 시 테스트를 실패시키는 방식으로 무전송을 검사했다. 카메라 전체 루프에서는 선택·확정·활성화·재개·긴급 정지·자원 해제를 검사했다. 잘못된 설정과 하드웨어 CLI 조합을 거부했다.

## 미검증 / 미구현

- 실제 4종 손 제스처가 사람에게서 정확히 인식되는지: **미검증**. 실제 웹캠 검사에는 손이 없었다.
- 장시간 추적, 다인 이동·가림·ID swap, 제스처 오인식률·지연 통계: **미검증**.
- 다인 손 소유권의 안정성: **미검증**. 1차 런타임은 다인 제스처를 차단한다. 단일 인물의 공간적 손 연결도 신원 인증은 아니다.
- 실제 ESP32 패킷 전송·응답 handshake·fault reset·모터 이동·물리 긴급 정지: **실행하지 않음**.
- Arduino/ESP32 펌웨어 빌드·업로드: **실행하지 않음**. 원본 static_assert가 있다는 사실과 컴파일 성공은 다르다.
- 부호·치수·전류·저속 제어·정지 거리·RF·배선: **미검증**.
- 레이더 실제 수신과 장애물 거리 기반 정지: **미구현**. 미연결은 UNKNOWN, 자동 하드웨어 후진은 차단.
- Pi 카메라·성능·전체 새 PC 설치: **미검증**.

모터 명령은 실제 ESP32에 전송하지 않았다. 위 결과는 소프트웨어 기반과 짧은 인식 입출력 경로의 증거이며 실물 추종 안전 승인이나 성능 인증이 아니다.
