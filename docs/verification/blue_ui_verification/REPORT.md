# D.I.G. Blue UI 변경 및 검증

2026-11-19 · 원본 HEAD `0fb0a6fae4a39d757e06e117a0d3934a6e07c6d1`

## 구현

- 248px 고정 사이드바와 파란 다크 테마. 기본 화면은 AI 대화이며 자동 연결 없음.
- 단일 명령 버블, 검증된 action 카드, 이동 단계/거리, 기존 실행·취소·승인 UI.
  회전은 360° 등을 표시하되 전체 계획의 실행 차단 조건을 유지.
- 카메라 화면의 직접 손동작/사람 추종 하위 모드. 하나의 CameraView 재사용.
  최초 인식, 지속 확인, 미리보기와 실제 적용 명령을 구별.
- 여섯 방향의 수동 패드와 중앙 STOP. 기존 pressed/released 연결 유지.
- 진단 하위 페이지와 바퀴 시험·복구·기록 기능 보존. 연결/레이더/매핑/추종 설정 재배치.
- 손동작 가이드는 엔진에 적용된 매핑을 표시하고 편집값은 미적용으로 구분한다.
  설정의 적용 버튼도 기존 손동작 적용 버튼의 click 슬롯을 사용해 기존 비활성 조건을 따른다.
- 모든 화면의 고정 레이더 표시와 빨간 비상 정지. Space/E 처리 보존.
- 상단 카메라 배지는 합성/인식/원본 영상/대기를 실제 표시 상태에 따라 구분한다.
- SVG QIcon 및 QSS의 기본/Hover/Pressed/Disabled/선택/포커스 상태.
- 1150px보다 좁은 창에서는 AI·수동·설정 열을 세로 배치하고 내용을 스크롤.

## 변경 파일

| 경로 | 내용 |
|---|---|
| `python_controller/visualization/dashboard.py` | 공통 셸, 화면 탐색, 기존 위젯 재배치, 읽기 전용 상태 표시 |
| `python_controller/visualization/natural_control.py` | 기존 입력/실행 위젯을 유지하는 대화·계획 카드 레이아웃 |
| `python_controller/visualization/dig_theme.py`, `dig_blue.qss`, `assets/icons/` | Qt 테마·버튼·SVG 리소스 |
| `tests/test_blue_dashboard.py` | 신규 UI 회귀 검사 7개 |
| `tests/test_korean_diagnostics_ui.py` | 추가된 설정 화면 기대값 |
| `tests/test_robot_session.py` | AI 기본 화면 변경에 맞춰 수동 시험 페이지를 명시적으로 선택 |
| `scripts/capture_blue_ui.py` | 소켓 생성을 금지한 Qt 화면 캡처 재현 스크립트 |
| `PRODUCT.md`, `DESIGN.md`, `README.md`, 계획 문서 | 범위와 사용법·디자인 기준 |
| `design_reference/dig_blue/` | ZIP의 명세·토큰·정적 시안 보관 |

실행 애플리케이션에서 HTML, 가상 카메라 사진 또는 비트맵 버튼을 사용하지 않는다.
기존 QTabWidget의 내부 스택과 인덱스를 유지하고 탭바를 숨겨 사이드바와 연결했다.
진단의 기존 인식·추종 설정 탭은 옮긴 설정 화면의 바로가기를 제공한다.

## 동작 보존 근거

제어 엔진 `dashboard_runtime.py`, `communication/`, `planning/`, `llm/`, `execution/`,
`control/`, `vision/`, `pi_radar/`, `esp32_firmware/`에 diff가 없다.
시그널은 원래 위젯/슬롯을 재사용한다. 사이드바 탐색은 기존 `select_page_mode()`를
한 번 호출하며 진단/설정 탐색은 제어권을 요청하지 않는다. 바퀴 시험 하위 페이지를
명시적으로 선택할 때의 기존 MOTOR_TEST 전환은 유지된다.

계획 실행의 can_prepare/회전/레이더/STOP/승인 조건은 원래 코드를 유지했다.
새 UI는 이 조건으로 결정된 enabled 상태를 변경하지 않는다. 표시상 CLEAR라도
정지·준비 조건이 충족되지 않으면 새 입력 허용으로 표시하지 않는다.

## 자동 검사

| 실행 | 결과 |
|---|---|
| 변경 전 `.venv` 전체 검사, 샌드박스 | 178개: 175 통과, 2 실패, 1 건너뜀 |
| 변경 후 전체 검사, 로컬 루프백 허용 | 185개: 184 통과, 기존 파일 검사 1 실패 |
| 신규 UI 검사 | 7개 통과 |
| compileall, git diff --check | 통과 |

변경 전 실패: 로컬 `esp32_firmware/esp32_omni_controller/secrets.h`가 존재해
`test_reference_provenance_and_no_copied_credentials`의 부재 조건이 실패한다.
해당 파일을 읽거나 삭제·이동하지 않았다. 레이더 실제 로컬 UDP 루프백 검사는
샌드박스에서 실패하고 제한 밖에서 통과했다. 마지막 전체 검사는 로컬 루프백을
허용한 환경에서 실행했으며 원래 펌웨어 검사 외의 실패는 없다.

로그: [변경 전](baseline_suite.txt), [최종 전체](full_suite.txt),
[신규 UI 검사](blue_ui_tests.txt), [구현 전 실패 확인](new_tests_before_implementation.txt).

신규 검사는 기본 AI/무소켓 시작, 다섯 화면/세 크기의 안전 표시,
화면 전환·창 비활성 시 홀드 해제, 거절된 제어권 전환,
카메라 단일 인스턴스와 실제 설정별 가이드, Space/E, SVG,
반복 계획 교체·입력 수정·회전·레이더·STOP 게이트를 확인한다.
기존 모의 장치 검사는 실제 pressed/released, 모드 전환 중 STOP,
계획 실행/승인 폐기, warmup 원본 영상과 인식의 구분 등을 계속 검증한다.

## 화면 검증

모든 이미지는 기존 PySide6 위젯을 직접 렌더링한 **SYNTHETIC_PREVIEW**다.
공유용 캡처의 ESP32·레이더 주소는 문서용 예시 주소다.
사진 시안 대신 기존 PreviewStream의 합성 입력을 사용했다. 캡처 중 소켓 생성은
AssertionError로 차단했다. [캡처 메타데이터](captures.json).

- [940×680 화면 모음](overview_940x680.png)
- [1320×920 화면 모음](overview_1320x920.png)
- [1600×980 화면 모음](overview_1600x980.png)
- [검증된 이동 계획](ai_validated_plan.png), [회전 차단](ai_rotation_blocked.png)
- [레이더 UNKNOWN](radar_unknown_blocked.png), [EVADE](radar_evade_blocked.png), [CLEAR·정지 미확인](radar_clear_blocked.png)
- [비상 정지](emergency_stop.png), [버튼 상태](button_states.png)

레이더 상황은 표시 검증을 위해 주입한 합성 snapshot이며 실제 레이더 수신 증거가 아니다.
버튼 상태 모음은 Qt 스타일 옵션으로 각 상태를 지정해 같은 QSS를 렌더링한다.
작은 창에서는 긴 진단 표와 설정의 일부가 스크롤되며 공통 비상 정지는 고정된다.
계획 교체 때 이전 카드가 남던 문제는 재현 테스트 후 즉시 숨김/분리하도록 수정했다.
독립 코드 리뷰에서 확인한 적용 전 매핑 표시 문제도 엔진 매핑을 기준으로 수정하고,
편집 전후 가이드·기존 적용 버튼 비활성 조건·긴 안전 문구의 배치를 회귀 검사에 포함했다.
재리뷰에서 남은 차단 사항은 없었다.

재현: `.venv/Scripts/python.exe scripts/capture_blue_ui.py`.
일반 실행: `run_simulation.bat`.

실제 LLM API, 카메라 인식 성능, 레이더 센서, ESP32·모터의 물리 동작은 이번 검증에서
시험하지 않았다. 자동 검사/합성 캡처는 실물 동작 확인을 대신하지 않는다.
