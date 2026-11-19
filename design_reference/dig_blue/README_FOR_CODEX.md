# D.I.G. Blue Sidebar UI Kit — Codex 전달 패키지

**목표:** 기존 `jugonpark/final_robot`의 **PySide6 중앙관제 UX/UI만 전면 개편**. 기능·모터·레이더·LLM 스키마/프롬프트/실행기/안전 로직은 수정하지 않는다.

**참조 GitHub HEAD:** `0fb0a6fae4a39d757e06e117a0d3934a6e07c6d1` (`main`, 2026-11-19 확인). 실제 작업 전 `git status`, 최신 HEAD와 로컬 미커밋 변경을 다시 확인한다. 참고 파일은 `docs/EXISTING_PLAN.md`.

## 패키지 파일 구조

| 경로 | 용도 |
|---|---|
| `assets/previews/01_ai_chat.png` | 메인 AI 대화 화면 완성형 시안 |
| `assets/previews/02_camera_gestures.png` | 손동작 직접 제어 화면 시안 |
| `assets/previews/06_person_follow.png` | 같은 카메라 화면의 사람 추종 하위 보기 |
| `assets/previews/03_manual_control.png` | 수동 버튼 배치 시안 |
| `assets/previews/04_diagnostics.png` | 진단 화면 시안 |
| `assets/previews/05_settings.png` | 설정 화면 시안 |
| `assets/previews/08_button_system.png` | 버튼 종류·크기·상태·색상 가이드 |
| `assets/icons/*.svg` | PySide6 `QIcon` 사용 가능한 31개 SVG 아이콘 |
| `assets/icons_png/*.png` | SVG 아이콘의 렌더링 PNG 복제본(배포 참조용) |
| `assets/dig_mark.svg` | 브랜드 로고 원본 |
| `assets/images/camera_sample_DEMO.png` | **가상 카메라 영상**. 실제 CameraView와 혼동 금지 |
| `design/tokens.json` | 공통 디자인 토큰 (색상·크기·간격) |
| `qt/dig_blue.qss` | PySide6 재사용 가능한 QSS 스타일 |
| `qt/theme_helper.py` | QSS 로딩, SVG 아이콘, 버튼 동적 속성 헬퍼 |
| `prototype/index.html`, `styles.css`, `app.js` | 브라우저에서 탐색 가능한 **오프라인 레이아웃 프로토타입**. 로봇 호출 없음 |
| `prototype/button-gallery.html` | 버튼 프로토타입 HTML |
| `docs/CODEX_TASK.md` | 파일별 구현 지시서, 기존 API 연결표, 안전 기준, 검증 항목 |
| `docs/UI_SPEC.md` | 5개 화면의 상세 UX·표시 데이터·디자인 원칙 |
| `render_static_mockups.py` | 정적 목업 PNG 재생성 스크립트 (Pillow, CairoSVG) |

## 처음 확인하는 순서

1. `assets/previews/01_ai_chat.png`, `02_camera_gestures.png`, `08_button_system.png`를 연다.
2. `prototype/index.html`을 브라우저로 열면 사이드바를 전환하는 **디자인 데모**를 볼 수 있다. 브라우저 실행 환경에 따라 로컬 파일 제한이 있으면 정적 파일 서버를 사용한다.
3. `docs/CODEX_TASK.md`를 읽고 `qt/dig_blue.qss`와 `assets/icons`를 기존 PySide6 프로그램에 단계별로 포팅한다.
4. **HTML을 앱으로 전면 교체하라는 뜻이 아니다.** 현재 앱이 PySide6이므로 최종 구현은 기존 Python 위젯/시그널을 보존하는 방식이다.

### 중요한 제한

- 이 ZIP은 **디자인 전달용**, 새로운 운영 앱이나 실제 ESP32 펌웨어가 아니다.
- `prototype/app.js`는 페이지 전환, 예시 텍스트 입력, 시안 토스트만 구현한다. UDP/TCP, 카메라, NVIDIA API 사용 없음.
- PNG 목업은 **Pillow로 만든 정적 레이아웃 시안**, 실행 중인 PySide6 앱 캡처가 아니다. HTML/CSS 프로토타입도 이 환경에서 브라우저 시각 검증까지 성공하지 못했으며 구동/레이아웃 정합성은 Codex 측에서 확인해야 한다.
- `camera_sample_DEMO.png`는 생성된 가상 참고 이미지이며, 실제 손동작 인식 성능을 보여주지 않는다. PySide6에서는 기존 `CameraView`를 계속 사용한다.
- E-STOP 버튼은 파란색 디자인의 예외로 **빨간색**. 실제 동작 연결은 반드시 기존 `Dashboard.action('ESTOP')` 그대로 유지.
- 이미지로 만든 버튼을 `QLabel`에 넣어 클릭하는 구현 금지. `QPushButton` + `QIcon(SVG)` + QSS + 기존 `pressed/released` 사용.
- 앱 로고가 기존 프로젝트에 이미 있다면 기존 자산을 우선 사용할 수 있다. 본 패키지에는 사용자가 선택할 수 있는 디자인 예시가 들어 있다.
