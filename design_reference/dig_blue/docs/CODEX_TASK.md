# Codex 구현 지시서 — UI 디자인 전용

## 작업 시작

1. `jugonpark/final_robot` 로컬 작업 디렉터리와 Git 상태를 확인하고 **미커밋 변경을 덮어쓰지 않는다**.
2. 레퍼런스 기준 main HEAD는 `0fb0a6fae4a39d757e06e117a0d3934a6e07c6d1`이지만 최신 코드와 다를 수 있다. 로컬 최신 상태를 우선 분석한다.
3. `assets/previews/01_ai_chat.png`, `02_camera_gestures.png`, `03_manual_control.png`, `04_diagnostics.png`, `05_settings.png`, `08_button_system.png`를 우선 검토한다.
4. `design/tokens.json`, `qt/dig_blue.qss`, `qt/theme_helper.py`, `assets/icons/*.svg`를 실작업 PySide6용으로 통합한다.

**변경 범위:** `python_controller/visualization/*` 중심. 미세한 읽기 전용 뷰 어댑터가 필요하면 `dashboard.py` 내부에서 처리. 다음 파일/모듈은 **동작 변경 금지**: `esp32_firmware/control_simple_v2/*`, `communication/*`, `control/safety_supervisor.py`, `dashboard_runtime.py`(제어 동작), `llm/*`, `planning/*`, `execution/*`, `vision/*`, `pi_radar/*`.

## 코드 연결표 (현재 GitHub 실제 코드)

| UI 컴포넌트 | 기존 소스 및 연결 대상 | 반드시 보존할 상태/이벤트 |
|---|---|---|
| 상단 E-STOP | `visualization/dashboard.py` `Dashboard.estop`, `Dashboard.action('ESTOP')` | 모든 화면, 전역 키 이벤트 (Space/E), 실행 취소·정지 래치 |
| 공통 레이더 배지 | `Dashboard.update_display`의 `radar['state']`, `['blocked']`, `['reason']`, `['age_s']` | `CLEAR`만으로 준비 완료 표시 금지 |
| 레이더 재허용 | `Dashboard.rearm_radar`, `self.radar_rearm` | `CLEAR`+STOP 확인+LLM 비실행 등 기존 enable 조건 |
| ESP32 상태 | `self.engine.shared_session.snapshot()`, 기존 `self.telemetry` | Connected/Ready/Stop Verified 구별 |
| 사이드바 제어 모드 전환 | `Dashboard.select_page_mode()` → `DashboardEngine.select_control_mode` | 실패 시 시각적 선택과 실제 제어권 분리, STOP/대기/재동기화 |
| AI 자연어 입력 | `NaturalControlPanel.command`, `.plan_button`→`._plan()` | 입력 수정 시 기존 `discard`/승인 초기화 유지 |
| AI 계획 카드 | `NaturalControl.plan.actions`, `planning.schema.describe_action` | 검증된 action만 표시; 회전은 미검증 상태 표시 |
| AI 실행 | `NaturalControlPanel._execute`→`DashboardEngine.start_natural_motion` | `refresh()`의 실제 enabled 조건 필수, 시안 상태 복사 금지 |
| AI 상태 메시지 | `NaturalControl.phase`, `.detail`, `.last_feedback`, `.stop_result` | PLANNING/READY/EXECUTING/STOPPING/DONE/FAILED 구분 |
| 카메라 영상 | `CameraView` 단일 인스턴스, `Dashboard.update_camera()` | 실제 inference 프레임과 warmup raw capture 구별 |
| 손동작 직접 제어 | `Dashboard.hand_preview`, `Dashboard.hand_mapping`, `HandDrive.DEFAULT_MAPPING` | Victory 후진/Closed_Fist 정지/유효 프레임 조건 |
| 사람 추종 | `Dashboard.follow_panel`, `Dashboard.engine.c`, `self.tracking` | 대상 선택·확정·재개, 직접 제스처와 다른 의미 |
| 수동 방향 패드 | `self.test_buttons`, `start_motor_test`, `end_motor_hold` | 반드시 `pressed`/`released`; 클릭으로 지속이동 금지 |
| 바퀴 시험/복구 | `self.wheel_buttons`, `diagnosis_pages`, `recovery_fields`, `turn_trial` | 안전 조건·공중 고정 승인·Fault 정책 유지 |
| 설정 화면 | `self.host`, `self.radar_host`, `save_ip`, `save_radar_ip` | 연결 중 편집 금지 등 기존 enabled 조건 유지 |
| 데이터 갱신 | `Dashboard.poll()` / `update_display()` 50ms | 이중 타이머 생성·GUI 스레드 모델 실행 금지 |

## 구현 단계

### 1. QSS/공통 시각 스타일
- 신규 파일 `python_controller/visualization/dig_theme.py`(원하는 이름으로)와 `assets/icons` 추가.
- `Dashboard.apply_style()`는 해당 QSS를 적용. `variant` 속성 사용. E-STOP 빨간색.
- 기존 위젯 이름과 연결을 유지. 스크린샷으로 스타일 검증.

### 2. 사이드바 셸
- 왼쪽 `QVBoxLayout` 네비게이션 버튼과 오른쪽 `QStackedWidget` 화면 컨테이너.
- 기존 `QTabWidget` 속성(`tabs`, `workspaces`, `diagnosis_pages`, 인덱스)과 기존 테스트가 강하게 결합되어 있으므로 **한 번에 제거하지 말고 어댑터/단계적 이관**을 사용.
- 화면 전환 시 수동 홀드 정지 이벤트 보존. 진단/설정의 단순 탐색으로 제어권을 자동 획득하지 않음.
- AI 대화 기본 화면으로 열되 실제 LLM 세션 활성화/하드웨어 연결을 자동으로 발생시키지 않음.

### 3. AI 대화 화면
- 대화형 버블은 **현재 단건 명령과 그 검증 결과를 보여주는 View**. LLM 히스토리 생성, 추론 재호출, 허위 승인 메시지 금지.
- 새 카드 디자인에 기존 명령 입력과 상태 갱신을 연결. 새 입력 시 기존 계획 폐기 정책 보존.
- `Rotate`가 있으면 실행 불가 게이트를 강조. 레이더 미수신·EVADE·STOP 미확인도 실행 차단 상태 표시.
- '계획 생성' 버튼은 기존 계획 수립만; '계획 실행'은 기존 승인 절차 보존.

### 4. 카메라·손동작 화면
- **CameraView 하나**만 활용. `GESTURE_DIRECT`와 `GESTURE_FOLLOW`는 내부 세그먼트로 화면 전환. 실물 제어 모드 변경에는 기존 엔진 규칙.
- 손동작 기본 매핑 `Open_Palm→FORWARD`, `Victory→BACKWARD`, `Thumb_Up→LEFT`, `Thumb_Down→RIGHT`, `Closed_Fist→STOP`.
- 사람 추종 기본 매핑 `Open_Palm→RESUME`, `Closed_Fist→PAUSE`, `Thumb_Up→CLOSER`, `Thumb_Down→FARTHER`.
- *사진 예시 `camera_sample_DEMO.png`를 실물 CameraView 대신 배포하지 말 것.* 카메라 미연결 시 원래 안전 표시 사용.

### 5. 수동·상태·설정
- 수동 6방향 `pressed/released`, 홀드 중 탭 전환·창 비활성화·비상정지 시험.
- 기존 진단 하위 페이지, 바퀴 시험, 네트워크 진단, 보고서 내보내기, 엔코더 복구, 제스처/추종 설정 **누락 없는 이관**.

### 6. 회귀/증거
다음 필수 검증을 증거와 함께 남긴다.

1. `python -m unittest discover -s tests -v` 기존 전체 테스트 통과 (베이스라인과 수치 비교).
2. `QT_QPA_PLATFORM=offscreen`으로 UI 스모크 테스트 및 940×680, 1320×920, 1600×980 스크린샷.
3. 기본 시뮬레이션 시작에서 **새로운 UDP 전송 없음**.
4. Sidebar 전환 시 `end_motor_hold` 및 기존 정지 규칙; mode switch 거부 시 연결 상태/소유권 화면 반영.
5. E-STOP이 모든 페이지에서 보이고 Space/E 가 실제 기존 핸들러 호출.
6. 레이더 `EVADE/UNKNOWN`에 실행 버튼 비활성·재허용 조건·STOP 확인 유지.
7. LLM `plan_button → preview → execute_button`, 계획 수정 시 승인 폐기, 회전 계획 전체 실행 차단.
8. 손동작 직접 제어/추종의 서로 다른 제스처 가이드 실제 설정과 일치.
9. 인식 모델 warmup의 RAW preview를 감지 성공으로 표시하지 않음.
10. UI 변경 전후 네트워크/프로토콜/제어 로직의 diff 없음을 보고.

## 최종 산출물
- 블루 사이드바 실제 PySide6 UI 코드.
- 버튼 아이콘 리소스와 QSS.
- 변경 파일 목록, 기존 동작 보존 근거, 테스트 결과.
- 5개 주요 페이지 + E-STOP/레이더 차단 상황의 실제 **PySide6 실행 스크린샷**.
- 바퀴/레이다/카메라/LLM 실물 동작은 별도 물리 검증 없이는 성공했다고 주장하지 말 것.
