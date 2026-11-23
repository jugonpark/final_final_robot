# 사람 추종·손 제스처 3륜 옴니 로봇 — 실시간 검증 대시보드

기본 실행은 **SIMULATION**이다. 웹캠에서 사람을 선택·추적하고 목표 인물의 손 제스처에 따라 추종 명령을 계산해 화면에 표시한다. **SIMULATION은 UDP 소켓을 열지 않으며 ESP32에 명령을 보내지 않는다.**

[운영 문서와 실행 경로](docs/README.md) · [시험 및 구현 기록](docs/verification/README.md) · [레이더 복귀 설계와 PI 실험 도구](docs/operations/RADAR_RECOVERY_AND_CALIBRATION.md)

## D.I.G. 웹 관제

실물 제어 화면은 `run_web_control.bat`를 실행한다. 기존 Qt 관제와 공유 웹 서버가 한 프로세스에서 시작되고 기본 브라우저에 [공유 웹 관제](http://127.0.0.1:8766)가 열린다. **설정 → ESP32 연결**에서 통신을 시작한다. Qt와 웹은 같은 카메라, ESP32 세션, 제어 모드, Agent와 레이더 상태를 사용한다. 기본 보드는 `BREADBOARD`이고 PCB V19에는 `--hardware-profile PCB_V19`를 지정한다. 웹 포트는 `run_web_control.bat --web-port 8770`처럼 바꿀 수 있다. 관제를 끝낼 때는 Qt 창을 닫는다.

주소를 바꾸려면 ESP32를 정지 후 연결 해제한다. 웹 수동 입력의 갱신이 끊기면 STOP 경로로 전환한다. 웹 실물 계획 승인에는 현재 계획 버전과 운영자 확인이 필요하다. 레이더 미연결 벤치 시험은 Qt에서 실행한다.

### 웹 시뮬레이션

`run_web.bat`를 실행하고 [로컬 웹 관제](http://127.0.0.1:8765)를 연다. 기본은 합성 영상이며, 웹캠과 모델을 쓰려면 `run_web.bat --web-camera`로 실행한다. **카메라 · 손동작** 화면에서 영상이 시작되며, **카메라 끄기**를 누르거나 화면을 벗어나면 연결을 해제한다. 같은 카메라를 사용할 경우 데스크톱 관제를 먼저 닫는다. 포트가 사용 중이면 `--web-port`로 다른 포트를 지정하고 콘솔에 표시된 주소를 연다.

독립 `run_web.bat`는 시뮬레이션으로 실행한다. 웹 설정에서 ESP32와 레이더 Pi 주소를 로컬에 저장할 수 있다.

## 통합 AI Agent

프로젝트 가상환경에서 `.venv\Scripts\python.exe -m pip install -r requirements-agent.txt`로 선택 의존성을 설치한다. 기존 NVIDIA 키와 모델 설정을 사용한다. 기본 관제는 이전대로 실행되고, Agent 화면은 `--agent-enabled`로 켠다.

- 웹 시뮬레이션은 `run_web.bat --agent-enabled`로 실행한다.
- 데스크톱 합성 영상은 `run_simulation.bat --dashboard-preview --agent-enabled`로 실행한다.
- 데스크톱 실카메라는 `run_simulation.bat --agent-enabled`로 실행한다. Agent의 실물 이동 승인에는 기존 중앙 ESP32 연결과 LLM 제어권, 레이더 CLEAR 및 차단 해제, 새 STOP 응답이 필요하다.

“카메라 화면 보여줘”, “지금 레이더 상태 알려줘”, “앞으로 10cm 가고 뒤로 5cm 가”, “아니 첫 번째 5cm로 수정”을 입력해 볼 수 있다. 실행 승인과 긴급 정지 확인은 Qt의 명시적 운영자 조작으로 처리한다. “멈춰”와 긴급 정지 버튼은 모델 응답과 별개로 처리한다. [Agent 통합 및 검증 기록](docs/verification/UNIFIED_AGENT_VALIDATION.md)

## D.I.G. Blue UI (2026-11-19)

`run_simulation.bat`로 실행하면 **AI 대화 제어** 화면이 먼저 열린다. 왼쪽 사이드바에서
카메라·손동작, 수동 제어, 상태·진단, 설정으로 이동한다. 카메라 화면의 **손동작 직접 제어**와
**사람 추종**은 같은 영상을 공유하며 각 모드의 제스처 의미를 따로 표시한다.
ESP32와 레이더 Pi 주소, 손동작 매핑, 추종 설정은 **설정**에서 변경한다.
비상 정지와 레이더 차단 상태는 모든 화면에서 유지된다.

이번 변경은 PySide6 UI와 시각 리소스에 한정하며 기존 연결·승인·정지 조건을 유지한다.
화면 선택은 실제 제어권과 구분되며 기본 화면을 여는 것으로 장치가 연결되지 않는다.
[UI 변경 및 검증 보고서](docs/verification/blue_ui_verification/REPORT.md),
[1320×920 화면 모음](docs/verification/blue_ui_verification/overview_1320x920.png).
아래의 탭 구성과 테스트 수치는 이전 단계의 기록이다.

ESP32 공식 개발 기준은 [control_simple_v2.ino](esp32_firmware/control_simple_v2/control_simple_v2/control_simple_v2.ino)다. 수동과 자연어 이동에서 공통 Feedforward+PI를 사용한다. 빌드하기 전에 같은 폴더의 `secrets.example.h`를 로컬 `secrets.h`로 복사해 Wi-Fi 설정을 입력한다. 브레드보드 GPIO가 기본이며 PCB V19는 펌웨어와 Qt에 각각 지정한다. 기존 `control_v2`와 원본 펌웨어는 참고용이다. [보드 프로필 선택과 빌드](docs/hardware/HARDWARE_PROFILES.md) · [기준선과 개선 계획](docs/CONTROL_SIMPLE_V2_PLAN.md)

## 자연어 제어 Prototype A+B

별도 `python -m python_controller.llm_cli`에서 NVIDIA API의 한국어 거리 이동 계획을 검증하고 기본 dry-run으로 표시한다. 사용법, 실물 승인 절차, 단계별 결과는 [Prototype A+B 검증 기록](docs/verification/PROTOTYPE_AB_VALIDATION.md)에 있다. [Mode Guard 후보](esp32_firmware/PATCH_NOTES_LLM_AB.md)는 별도 패치로 관리한다.

`run_simulation.bat`의 기본 **AI 대화 제어** 화면에서도 같은 검증기를 사용할 수 있다. `앞으로 5cm 이동해`를 입력하고 **계획 생성**을 누르면 이동 순서가 표시된다. 프로젝트의 로컬 `.env`에서 NVIDIA 키와 모델을 읽는다.

실제 장치 시험은 [C6 절차](docs/C6_HARDWARE_PREFLIGHT.md)의 정지 통신과 바퀴 방향을 확인한 뒤 진행한다. 모터 동작 시험 연결을 해제하고 현재 ESP32 주소, 공중 고정과 전원 차단 준비를 확인한 뒤 **이 계획 1회 실행**을 누른다. 상단 긴급 정지와 탭의 **정지 / 계획 취소**는 실행을 중단하며, 결과는 `logs/.../llm/`에 기록된다.

## 레이더 연동

실물 제어에는 Raspberry Pi의 레이더 상태가 필요합니다. Pi 발행자 설치, 프로파일 실행, 대시보드 수신 확인과 `bpmCfg` 오류 대응은 [레이더 연결 및 실행 방법](docs/RADAR_OPERATION_GUIDE.md)을 참고하세요.

## 한국어 UI와 연결 진단

한국어 탭·버튼·상태·손동작·안전 사유·경고창과 **연결 및 통신 진단** 여섯 단계를 제공한다. 송수신 주소·시각·세션·순서·수신 거절 사유·소켓 오류를 구분하며 **진단 보고서 저장 (JSON·Markdown)**으로 원문과 근거를 저장한다. 내부 프로토콜/JSON 식별자는 유지한다.

기존 64개를 유지한 **81개 자동 검사 통과**이며 실제 ESP32·모터는 **미검증 (NOT TESTED)**이다. 현재 보드 IP·전원·배선이 확인되지 않아 모터 미동작의 하드웨어 원인은 확정하지 않았다. 펌웨어·PID·핀·CPR·모터 방향은 이번 변경에서 수정하지 않았다.

최신 사용 안내: [한국어 대시보드와 연결 진단](docs/KOREAN_DIAGNOSTICS_GUIDE.md). 수정 파일·현재 네트워크 재확인·검증 증거: [구현 및 검증 보고서](docs/verification/KOREAN_DIAGNOSTICS_VALIDATION.md). 아래 64개·47개·30개 수치는 이전 단계의 기록이다.

## 이전 단계: ESP32 통신·수동 모터 시험

새 **MOTOR TEST** 탭에서 ESP32 IPv4 저장·UDP 연결·정지 증명 이후 heartbeat·6방향 짧은 수동 시험·모터 피드백·시험 기록을 제공한다. 연결 승인과 실제 모터 시험 승인은 별도다. `SIMULATION` 기본값은 유지하고, **현재 하드웨어 연결에서는 자동 추종·제스처 출력이 모터에 전달되지 않는다.**

기존 47개를 유지한 **64개 자동 테스트가 통과**했다. 이번 단계의 통신·모터 검사는 fake socket/Mock 텔레메트리로 수행했으며 **실제 ESP32·실제 모터는 NOT TESTED**다. 자동으로 실제 모터를 구동하는 시험은 실행하지 않았다. 아래 47개 결과는 이전 UI 단계의 기록이다.

실행·통신·바퀴를 띄운 시험 절차: [MOTOR_TEST_GUIDE.md](docs/MOTOR_TEST_GUIDE.md). 이전 단계 증거: [MOTOR_TEST_VALIDATION.md](docs/verification/MOTOR_TEST_VALIDATION.md).

## 현재 UI: PySide6 검증 대시보드

`run_simulation.bat`는 PySide6 대시보드를 연다. 종합 현황 / 영상 인식 / 이동 및 모터 / 시험 및 기록 / 모터 동작 시험 탭에서 다음을 구분한다.

- 원시 손 분류·confidence·소유자 → 시간/프레임 안정화·승인 → 실제 적용 행동·거부 사유
- 알고리즘 요청 → 상태·속도·가속도 제한 이후 명령 → 실제 UDP 송신 패킷 → 매칭된 seq와 ESP32 보고값
- 텔레메트리의 바퀴 목표/측정 cm/s와 환산 RPM, PWM, 엔코더, stale/fault, 정지 송신 및 응답 검증
- 7개 수동 검증 단계의 PASS / FAIL / NOT TESTED 및 세션별 JSONL 기록

미수신 값은 UNKNOWN/null이며, 여러 사람의 제스처 제한은 유지한다. 카메라·추론은 각 worker에서 최신 프레임 한 개만 유지한다. GUI는 가벼운 제어·표시를 담당하고 UDP 가드와 파일 기록도 별도 스레드에서 실행한다.

현재 자동 테스트는 **47개 통과**했다. 실제 웹캠 30초 검사에서는 최신 추론 프레임 169개가 로그에 기록되었고 송신·텔레메트리 값은 모두 null이었다. 손은 검출되지 않았으며 실제 제스처 반복 성능과 실제 모터는 검증하지 않았다.

상세 실행·로그·파일 목록: [DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md). 이전 단계 검증 증거: [DASHBOARD_VALIDATION.md](docs/verification/DASHBOARD_VALIDATION.md). [VALIDATION.md](docs/verification/VALIDATION.md)는 1차의 과거 30개 테스트 결과다.

## 바로 실행

현재 PC에는 모델 파일과 프로젝트 `.venv`가 준비되어 있다.

```powershell
cd C:\Users\jugon\Desktop\NEW_ROBOT_START
.\.venv\Scripts\python.exe -m python_controller --ui dashboard
```

또는 `run_simulation.bat`를 더블클릭한다. 최초 모델 추론은 수 초 걸릴 수 있으며 이 동안 이동은 허용되지 않는다. 창에서 상태와 정지 원인을 확인한다.

직접 `python -m python_controller`를 실행하면 호환성을 위해 기존 OpenCV UI가 열린다. 새 UI에는 `--ui dashboard`를 명시한다. 카메라 없는 UI 확인은 `--ui dashboard --dashboard-preview`를 사용한다. 합성 미리보기는 상단과 로그에 SYNTHETIC_PREVIEW로 표시되며 하드웨어 연결이 금지된다.

카메라 없이 제어·정지 동작만 확인:

```powershell
.\.venv\Scripts\python.exe -m python_controller --demo
```

`--demo`는 고정된 가짜 관측으로 실행하며 YOLO·MediaPipe·카메라를 검증하는 모드가 아니다. 외부 라이브러리 없이 `python -m python_controller --demo`도 가능하다.

## 새 환경 설치

현재 검증 환경은 Windows·Python 3.13.14이다. 아래는 새 PC용 설치 절차이며 **깨끗한 새 PC에서의 전체 설치는 검증하지 않았다.** 현재 `.venv`는 설치된 전역 패키지를 재사용하는 `--system-site-packages` 방식으로 구성했고, `lap` 및 Qt Core/Gui/Widgets를 제공하는 `PySide6-Essentials 6.10.0`을 가상환경에 추가했다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/download_models.py
.\.venv\Scripts\python.exe -m python_controller --ui dashboard
```

네트워크는 의존성·공식 모델 설치 시 필요하다. 모델은 런타임에 자동 다운로드하지 않는다. `models/manifest.json`에 출처·크기·SHA-256이 있다. Python을 찾지 못하면 Python 3.13 x64를 설치한 뒤 터미널을 다시 연다. 가상환경 생성 중 임시 폴더 권한 오류가 발생하면 프로젝트 내부의 쓰기 가능한 폴더를 TEMP/TMP로 지정하고 다시 생성한다.

웹캠을 다른 프로그램이 사용 중이면 닫는다. Windows 카메라 개인정보 권한을 확인한다. 카메라 번호·해상도·백엔드는 설정으로 변경한다.

## 조작 순서

1. 사람이 한 명이면 후보 ID가 지정된다. 여러 명이면 화면의 원하는 사람을 클릭한다. 겹치는 박스 클릭은 선택하지 않는다.
2. **Enter**로 목표를 확정한다. 다른 사람이 나타나도 목표를 자동 교체하지 않는다.
3. **A**로 활성화한다. 활성화만으로는 이동 명령을 만들지 않는다.
4. **R** 또는 안정적으로 인식된 **Open Palm**으로 추종을 시작·재개한다.
5. **Closed Fist / P**는 정지, **Thumb Up**은 상대 목표 크기를 증가시켜 접근, **Thumb Down**은 감소시켜 더 먼 간격을 요청한다.
6. **Space / E / 화면 빨간 버튼**은 긴급 정지한다. 이후 A와 새 재개가 필요하다.
7. **C**는 목표 선택을 해제한다. **Q / Esc / 창 닫기 / Ctrl+C**는 정지 후 종료한다.

제스처는 기본 신뢰도 0.75·연속 0.6초·최소 3프레임을 만족해야 한다. 손목·손바닥 관절이 유일하게 목표 박스 안에 있어야 하므로 손을 몸 옆으로 크게 뻗거나 사람이 겹치면 무시될 수 있다. 다른 사람의 제스처·소유자가 모호한 손은 제어에 반영하지 않는다. 두 손의 서로 다른 명령은 무시하고 정지를 우선한다. 같은 제스처 유지로 거리값을 반복 변경하지 않는다.

**여러 사람이 검출되면 1차에서는 손 제스처 명령 전체를 비활성화한다.** 박스 포함 검사만으로 다른 사람의 팔이 목표 영역에 들어오는 경우까지 입증할 수 없기 때문이다. 다인 목표 선택·추적·키보드 제어는 가능하며, 다인 제스처 활성화는 포즈 손목 연결 및 실측 검증 후의 2차 범위다.

인식된 손이 없으면 현재 상태를 유지한다. PAUSED는 명시적인 재개 전까지 유지된다. 목표 상실·겹침·박스 급변·오래된 영상·통신 오류·레이더 위험 이후에도 자동으로 재출발하지 않는다. 안전 정지 전부터 Open Palm을 유지한 경우에는 손을 내렸다가 다시 제시한다. 처음 몇 초의 긴 추론으로 FAULT가 표시되면 영상이 정상 갱신된 뒤 A·R 순서로 다시 활성화한다.

## 설정

기본값은 `python_controller/config/default.json`에 있다. 별도 JSON으로 필요한 키만 덮어쓴다.

```json
{
  "camera": {"index": 0, "backend": "DSHOW"},
  "follow": {"desired_height_ratio": 0.60, "max_linear_cm_s": 3.0}
}
```

```powershell
.\.venv\Scripts\python.exe -m python_controller --config my_config.json
```

카메라 backend는 AUTO/DSHOW/MSMF/V4L2이다. YOLO 모델 경로·버전 표시·추적기·추론 해상도·device·제스처 모델 경로도 설정에서 관리한다. MediaPipe 제스처 이름과 RESUME/PAUSE/CLOSER/FARTHER 매핑을 변경할 수 있다. 숫자·부호·속도·시간 제한의 잘못된 설정은 시작 전에 거부한다.

**상대 높이 0.60, deadband 0.06, 속도·gain·부호는 초기 제안값이다.** 사람 박스는 실제 거리 센서가 아니다. 신장·자세·가림·카메라 각도에 따라 달라지며 cm 거리값을 출력하지 않는다. 초기 제어는 전후·회전만 사용하고 `vy=0`이다. 출력 제한은 최대 5cm/s·0.5rad/s이며 펌웨어의 제한·가속도 제어도 유지한다.

## 구성과 선택 이유

| 경로 | 역할 |
|---|---|
| `python_controller/main.py` | CLI·웹캠 처리·입력·전체 수명 관리 |
| `models.py`, `config/` | 단위가 명확한 관측·명령과 검증된 설정 |
| `vision/` | OpenCV 카메라, YOLO11n+ByteTrack, 목표 선택, MediaPipe와 목표 손 연결 |
| `control/` | 모든 요구 상태, 정지·재개 규칙, 상대 크기와 방향 제어 |
| `communication/esp32_udp.py` | typed UDP 패킷·세션·seq·텔레메트리·독립 freshness 가드 |
| `radar/radar_interface.py` | 미연결 UNKNOWN 인터페이스, 실제 수신부 없음 |
| `visualization/debug_view.py` | 영상·목표·명령·상태·정지 원인·긴급 정지 버튼 |
| `tests/` | 카메라·ESP32 없이 실행 가능한 제어·통신·선택·안전·출처 검사 |
| `esp32_firmware/` | 자격 증명을 제거한 기존 펌웨어 참조본과 출처 manifest |
| `scripts/` | 명시 모델 준비 및 무구동 영상 경로 점검 |

OpenCV는 플랫폼별 캡처와 간단한 디버그 화면, YOLO11n은 작은 CPU 검출기, ByteTrack은 추가 ReID 모델이 필요 없는 초기 추적기, MediaPipe Tasks는 기존 학습 제스처를 사용하는 데 선택했다. `lap`는 ByteTrack의 할당 알고리즘 의존성이다. 상태·제어·UDP는 Python 표준 라이브러리로 작성했다.

[Ultralytics 공식 tracking 문서](https://docs.ultralytics.com/modes/track/)에 맞춰 `persist=True`, `classes=[0]`, `bytetrack.yaml`을 명시한다. [MediaPipe 공식 Python 안내](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python)의 VIDEO Tasks API와 버전이 지정된 `.task` 모델을 사용한다. 코드의 실제 옵션은 현재 설치 API로 확인했다. Ultralytics의 배포·상용 라이선스 조건은 외부 배포 전에 확인한다.

설계 상세: [ARCHITECTURE.md](docs/ARCHITECTURE.md). 펌웨어 계약과 분석: [FIRMWARE_AUDIT.md](docs/verification/FIRMWARE_AUDIT.md). 실행 증거와 미검증 범위: [VALIDATION.md](docs/verification/VALIDATION.md).

## 실제 ESP32 연결 전

**이번 작업에서는 ESP32에 명령을 전송하거나 펌웨어를 업로드하지 않았다.** 아래는 다음 단계의 사전 조건이다.

- 전원을 차단한 상태에서 TB6612FNG 배선·STBY·공통 GND·전원 전압·외부 엔코더 풀업을 확인한다.
- 바퀴를 지면에서 띄워 모터·엔코더 방향 및 카메라 기준 전후·회전 부호를 검증한다. default `forward_sign=1`, `turn_sign=-1`을 실측값으로 가정하지 않는다.
- 원본에 포함된 Wi-Fi 자격 증명을 교체하고 로컬 secrets.h에만 둔다. 원본 ZIP에는 비밀 정보가 남아 있으므로 그대로 공유하지 않는다.
- ESP32 NETWORK 모드, IP, 8888/8889, Windows 방화벽·동일 네트워크, 단일 상위 제어기를 확인한다. Python 수신 포트는 8889에 고정된다.
- SLOW 정지·명령 워치독·카메라 분리·영상 지연·통신 단절·fault·비상 정지를 각각 실물로 확인한다. 소프트웨어 테스트는 물리 정지 거리의 증거가 아니다.
- 후방 레이더 미검증 상태에서 **실제 자동 후진은 차단**된다. 레이더가 없는 상태에서 이동 경로의 안전을 가정하지 않는다. 후방 레이더 하나는 전방·측면 안전을 보장하지 않는다.

아래 명령은 이전 단계의 하드웨어 경로 예시다. **현재는 `--ui dashboard`를 추가하고 MOTOR TEST 절차를 따른다.** 무구동 연결에는 부호 교정을 완료했다고 허위로 설정할 필요가 없으며, 이동은 바퀴 띄움 확인과 별도 1회 승인 이후에만 허용한다. 자동 추종의 하드웨어 출력은 차단된다.

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard --mode HARDWARE --esp32-host ESP32_IP --acknowledge-hardware-risk
```

`ESP32_IP`는 실제 숫자 IPv4로 바꾼다. CLI 인자만으로 이동하지 않는다. 프로그램은 STOP을 반복하고 matching fresh STOPPED 텔레메트리를 확인한 뒤 A와 새 재개를 요구한다. `reset_fault`는 패킷 생성 함수만 제공하며 자동 fault 해제는 하지 않는다. 키보드 P로 하드웨어를 정지하면 전송 활성화도 해제되어 A·R이 다시 필요할 수 있다.

소프트웨어 비상 정지는 물리 비상 전원 차단 장치를 대체하지 않는다. 전송 스레드도 운영체제 전체 정지를 제어하지 못한다. 기존 UDP에 인증·암호화가 없으므로 신뢰된 분리 네트워크에서만 시험한다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q python_controller scripts tests
.\.venv\Scripts\python.exe scripts/check_vision.py
.\.venv\Scripts\python.exe scripts/check_vision.py --camera
```

마지막 명령은 웹캠 세 프레임을 읽고 개수·처리 시간만 출력하며 영상은 저장하지 않는다. 모델 빈 이미지 검사는 추론 API와 모델 로딩만 검증한다. 카메라 접근 권한 오류와 모델 오류를 구분한다.

## 완료 및 남은 범위

요청된 1차 코드 경로: 웹캠·검출·선택·추적·단일 인물 목표 손 연결·제스처 필터·추종·상태·안전 정지·화면·시뮬레이션·UDP·모의 테스트를 구현했다. 웹캠의 실제 사람 검출을 짧게 확인했지만 실제 손 제스처 시연과 장시간 다인 추적은 완료 증거가 없다. 다인 손 연결 유틸리티는 준비했으나 런타임 제스처 제어는 차단했다.

미구현: 레이더 실제 수신·거리 기반 알고리즘, Pi 전용 카메라 드라이버·최적화, 전방/측면 장애물 회피, 신원 재식별, 실물 추종 성능 인증. Raspberry Pi에서는 카메라 어댑터와 플랫폼 패키지를 검증해 교체하며 특정 구매 제품에 종속되지 않는다.

2차 우선순위는 ① 단일 인물 4종 제스처 실측 및 오인식 평가 ② 다인·가림·추적 ID 교체·손 소유자 실패 평가 ③ 띄운 바퀴에서 통신·부호·정지 검증 ④ 실제 레이더 데이터 확인 및 후방 감지·제동 검증 ⑤ 통제된 저속 실물 추종 ⑥ Pi 이식·처리 지연 측정이다.
"# final_robot" 
