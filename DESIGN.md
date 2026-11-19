# D.I.G. Blue — PySide6

운영 화면의 가독성과 안전 상태를 우선한다. 제공된 Blue kit 시안과
`design_reference/dig_blue/design/tokens.json`을 시각적 기준으로 사용한다.

- 앱 `#071324`, 사이드바 `#0A1A30`, 표면 `#10253E`, 주색 `#2379F5`.
- 글자 `#EAF3FF`, 보조 글자 `#97B1CA`, 강조 `#40CBFF`.
- 안전 상태는 녹색/노랑/빨강으로 구분하고 비상 정지는 빨강을 유지한다.
- 248px 고정 사이드바: AI 대화 / 카메라·손동작 / 수동 / 진단 / 설정.
- AI는 단일 요청과 검증된 계획을 두 열로 표시한다. 작은 창에서는 세로 배치한다.
- 카메라 직접 손동작/사람 추종은 같은 CameraView를 공유하는 하위 화면이다.
- 수동 패드는 실제 QPushButton의 pressed/released를 유지한다.
- QSS의 기본/Hover/Pressed/Disabled/선택/키보드 포커스 상태와 SVG QIcon을 사용한다.
- 기본 창 1320×920, 검증 크기 940×680 및 1600×980. 내용은 스크롤하고
  공통 레이더와 비상 정지는 스크롤 밖에 고정한다.

리소스: `python_controller/visualization/dig_blue.qss`, `dig_theme.py`, `assets/icons/`.
실행 캡처와 검증 근거: `docs/blue_ui_verification/`.
