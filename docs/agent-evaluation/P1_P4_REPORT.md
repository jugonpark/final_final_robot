# D.I.G. Agent P1~P4 검증 기록

측정 환경은 Windows 11, Python 3.13.14입니다. 변경 전 기준은 `b03f7d2` 위의 기존 로컬 작업이며, 이전 워크트리의 메뉴 설명·스트리밍·RAG 변경을 포함합니다.

## 현행 분석

- Unified Agent의 분류기는 [NvidiaIntentInterpreter와 배포 Pydantic 스키마](../../python_controller/llm/agent_intent.py)에 있습니다. 패키지의 `parse_model_intent`에 대응하는 저장소 함수는 `parse_intent`이며, 실제 생산 경로는 중복 필드를 거부하는 JSON 읽기와 `INTENT.validate_python`을 직접 사용합니다. discriminator는 `intent`입니다.
- 이동 스키마 `Move`의 좌표는 로봇 몸체 기준 `forward/backward/left/right`이고, `distance_cm`은 5~50cm입니다. `Rotate`는 각도 미검증으로 미리보기만 제공하며, `Stop`은 이동 계획 마지막에만 둘 수 있습니다. `chat`은 분류 라벨이고 읽기 전용 답변 데이터는 별도 경로입니다. 초안 버전은 SQLite 저장소에서 관리합니다. 제어 모드 enum은 `LLM_COMMAND`, `GESTURE_DIRECT`, `GESTURE_FOLLOW`, `DIRECT_CONTROL`입니다.
- [NVIDIA 클라이언트](../../python_controller/llm/nvidia_client.py)가 분류의 system/user 메시지를 전송합니다. 기존 온도 설정은 일반 0.1, Nemotron Ultra 0.2입니다. [LangGraph](../../python_controller/llm/agent_graph.py)가 분류·검토 interrupt를 담당합니다. [UnifiedAgent](../../python_controller/llm/unified_agent.py)의 정지 선행 분기와 Qt 승인, [DashboardEngine](../../python_controller/dashboard_runtime.py) 및 [RobotSession](../../python_controller/communication/robot_session.py)이 실제 제어를 담당합니다.
- [변경 전 SYSTEM 원문과 SHA-256](../../prompts/01_CURRENT_SYSTEM_PROMPT_PASTE_HERE.md)

## 변경 사항

| 항목 | 적용 |
| --- | --- |
| P1 | `dig_intent_v2` 분류 프롬프트에서 인용·부정·지원되지 않는 행동, 화면 이동과 제어권, 이동과 실행 승인을 구분합니다. 진단 로그는 버전과 실제 system 메시지의 SHA-256을 기록합니다. |
| P2 | 골든 질문과 문장이 겹치지 않는 스키마 유효 few-shot 12개를 실제 system 메시지에 넣었습니다. 단위 환산, 화면 이동, 모드 전환, 복합 명령, 편집과 모호한 요청을 포함합니다. |
| P3 | 같은 대화의 직전 사용자 턴에서 확인한 단일 이동 초안이 60초 이내이고, 현재 버전 및 라이브 바인딩이 같을 때만 지시 대명사를 허용합니다. 모델에는 초안 동작, 리비전, 단일 블록 참조 가능 여부만 전달합니다. 참조를 잃으면 단계 번호를 묻습니다. |
| P4 | [의미 검증](../../python_controller/llm/agent_semantics.py)이 구조 검증 뒤 사용자 원문과 동작·거리·순서·편집대상·페이지·모드를 대조합니다. 불일치는 계획 적용이나 화면·모드 변경 전에 거부합니다. 새 초안 버전은 이전 승인 요청을 무효화합니다. |

주요 변경은 `agent_intent.py`, `agent_graph.py`, `agent_semantics.py`, `unified_agent.py`, `tests/test_unified_agent.py`, [평가 도구](../../scripts/evaluate_agent_intents.py)와 이 폴더의 데이터·결과입니다.

## 테스트 명령과 결과

```powershell
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.venv\Scripts\python.exe -X utf8 scripts\evaluate_agent_intents.py --repo .tmp\menu-stream-regression
.venv\Scripts\python.exe -X utf8 scripts\evaluate_agent_intents.py
```

기준 전체 테스트는 261개 중 260개 통과입니다. 변경 후 Agent 집중 테스트는 47개 통과, 최종 전체 테스트는 265개 중 264개 통과입니다. 두 전체 실행에서 비공개 펌웨어 아카이브 검사 1개를 건너뛰었습니다. 전체 테스트는 비밀 설정과 실물 장치 없이 격리 사본에서 실행했습니다.

```text
Baseline  Ran 261 tests in 66.055s · OK (skipped=1)
Candidate Ran 265 tests in 71.659s · OK (skipped=1)
```

## 모의 응답 비교

[원본 골든 질문](gold_intent_cases.jsonl) 84개를 사용했습니다. 동일한 고정 응답을 입력해 실제 NVIDIA 어댑터의 구조 검사와 실제 LangGraph의 의미 검사·라우팅을 비교했습니다. 원본 중 거리 규칙에 맞지 않는 17개는 **평가 기대값만** 확인 필요로 정규화했습니다. 실제 모델의 프롬프트 응답 정확도는 이 비교로 추정할 수 없습니다. [기준 결과](baseline.json) · [개선 결과](candidate.json)

| 지표 | 기준 | 개선 |
| --- | ---: | ---: |
| 의도 및 필드 일치 | 68/78 | 78/78 |
| 정상 요청의 불필요한 되묻기 | 0/38 | 0/38 |
| 잘못된 입력 응답에서 이동 초안 통과 | 0/4 | 0/4 |
| 잘못된 입력 응답에서 화면·모드·동작 제안 통과 | 1/4 | 0/4 |
| 모델 호출 전 STOP | 6/6 | 6/6 |
| 모의 처리 시간 P50/P95 | 7.63/13.82ms | 7.50/14.28ms |

## 핀맵과 남은 검증

[핀맵 차이 CSV](../hardware/FIRMWARE_PINMAP_DIFF.csv)는 최신 패키지의 2026-10-19 후보, 저장소 SIMPLE V2 상수, 로컬 `V18_FINAL_RELEASE` 회로도를 비교한 결과입니다. KiCad CLI `sch export netlist --format kicadxml`으로 확인했습니다. CAD 열의 번호는 설계상의 U1·MD1·MD2·JM 패드입니다.

- 후보와 V18 넷리스트는 16개 신호에서 일치합니다. 현행 펌웨어 소스는 M1 IN1/IN2 및 M2·M3 엔코더 A/B 총 6개가 다릅니다.
- 비교에 사용한 펌웨어 SHA-256은 `b4bed0ce9a364ba2bc78e54e8f178620efd4c68c2dd8d74f5208523c8bbcc25a`입니다. 로컬 V18 회로도 SHA-256은 `6d3626ad934bc5906bca7bee616b4f1204fe84b0b0ffe1ef16fc3dc8ec7b73a6`입니다.
- 실제 Nemotron A/B는 **NOT_RUN**입니다. 실물 breakout 방향, 실제 헤더 배열, 엔코더 풀업, 배선 연속성, 현재 장치에 올라간 펌웨어는 **확인 필요**입니다. 실물 주행 및 자동 보정은 **NOT_RUN**, 핀맵은 **`FINAL_PINMAP_APPROVED=false`**입니다.
