# D.I.G. 운영과 검증 문서

## 실행 경로

| 구성 | 진입점과 관리 폴더 | 운영 문서 |
| --- | --- | --- |
| PC 중앙관제 | [웹 실행](../run_web.bat), [Qt 실행](../run_simulation.bat), [공용 런타임](../python_controller/dashboard_runtime.py), [웹 화면](../python_controller/web), [Qt 화면](../python_controller/visualization), [설정 스키마·기본값](../python_controller/config) | [대시보드](DASHBOARD_GUIDE.md) · [한국어 진단](KOREAN_DIAGNOSTICS_GUIDE.md) |
| 제어·실행 | [제어](../python_controller/control), [RobotSession과 ESP32 UDP](../python_controller/communication), [실행기·이력](../python_controller/execution), [계획 스키마](../python_controller/planning) | [시스템 구조](ARCHITECTURE.md) · [중앙제어 흐름](CENTRAL_CONTROL_WORKFLOW.md) · [모터 시험](MOTOR_TEST_GUIDE.md) |
| Pi 레이더 | [UART 수신과 PC 발행자](../pi_radar), [PC 수신](../python_controller/radar) | [레이더 실행](RADAR_OPERATION_GUIDE.md) · [레이더와 관제 연동](RADAR_LLM_INTEGRATION.md) |
| ESP32 | [현재 SIMPLE V2 스케치](../esp32_firmware/control_simple_v2/control_simple_v2), [이전 실험 후보](../esp32_firmware/candidates) | [현재 펌웨어 사용법](../esp32_firmware/control_simple_v2/control_simple_v2/README_KO.md) · [실물 사전 점검](C6_HARDWARE_PREFLIGHT.md) |
| 대화형 미션 | [Agent](../python_controller/llm), [초안 저장](../python_controller/mission), [문서 검색](../python_controller/knowledge) | [미션·RAG 안내](DRAFT_MISSION_KNOWLEDGE.md) · [스키마와 대화 흐름](DIG_AGENT_SCHEMA_AND_DIALOGUE.md) |
| 교정·분석 | [거리 실험 도구](../scripts/compare_distance_trials.py), [회전 RPM 비교](../scripts/compare_turn_rpm.py) | [레이더 복귀 설계와 PI 실험](operations/RADAR_RECOVERY_AND_CALIBRATION.md) |

## 하드웨어 버전과 기술 문서

[하드웨어 버전 맵](hardware/README.md) · [브레드보드 BOM](hardware/breadboard/BOM.md) · [PCB V18](hardware/pcb-v18/DESIGN.md) · [기술 설명 v0.2 검토본](hardware/review/DIG_TECHNICAL_v0.2.md)

## 시험과 구현 기록

날짜별 실험 보고서, 모의 캡처, 테스트 출력은 [검증 기록](verification/README.md)에 모았다. 로컬에서 생성하는 주행 로그, SQLite 데이터, 운영자 측정 원본은 기존 `logs/`에 보관한다.
