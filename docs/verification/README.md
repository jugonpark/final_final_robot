# 시험 및 구현 기록

각 기록은 해당 시점의 코드와 장비 조건을 기준으로 한다. 현재 실행 경로와 운영 절차는 [운영 문서](../README.md)에서 확인할 수 있다.

| 영역 | 기록 |
| --- | --- |
| ESP32·모터 | [펌웨어 분석](FIRMWARE_AUDIT.md) · [바퀴 시험](MOTOR_TEST_VALIDATION.md) · [2026-11-18 공중 시험](BENCH_TURN_20261118.md) · [Watchdog·엔코더 변경](WATCHDOG_ENCODER_FIX_IMPLEMENTATION.md) |
| 중앙제어와 연결 진단 | [중앙제어 적용](CENTRAL_CONTROL_IMPLEMENTATION.md) · [한국어 진단 검증](KOREAN_DIAGNOSTICS_VALIDATION.md) · [연결 진단 결과](connection_diagnostics) |
| 화면 | [초기 검증](VALIDATION.md) · [Qt 대시보드 검증](DASHBOARD_VALIDATION.md) · [Blue UI 보고서와 캡처](blue_ui_verification/REPORT.md) |
| LLM·Agent | [Prototype A+B](PROTOTYPE_AB_VALIDATION.md) · [NVIDIA 연결](C7_NVIDIA_API_VALIDATION.md) · [Nemotron API](C8_NEMOTRON_API_VALIDATION.md) · [Agent 통합 검증](UNIFIED_AGENT_VALIDATION.md) |
