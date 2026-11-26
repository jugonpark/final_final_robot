# D.I.G. Conversational RAG — 사용성 복구 및 실제 통합

2026-11-26, Asia/Seoul. `codex/grounded-rag-v3`의 기존 미커밋 변경 위에 추가했다. **실제 로컬 Web/Qt 챗봇 경로에 통합했으며 NVIDIA API 호출은 0회다.** 실행 중인 8766 관제는 종료·재시작하지 않았다.

## 구현 결과

검증용 사실과 사용자에게 보이는 문장을 분리했다. 인물 소개는 같은 출처 문구를 반복하지 않고, 프로그램 설명은 cloud=0에서도 제공한다. 후속 질문에는 관련 필드·항목을 골라 답한다. 확인된 지식의 로컬 조회, 기존 일반 Nemotron 대화, 기존 복잡 문서 생성·검증 경로가 공존한다. Phase 2.6의 미평가 자유 생성 후보는 자동 등록하지 않았다.

| 파일 | 변경과 이유 |
|---|---|
| [facts.py](../../python_controller/knowledge/facts.py) | 원래 값·출처를 유지하는 profile 구조. 임의 인물/한국어 필드 지원, 질문 관련 필드 선택, 괄호 안 부정과 예외를 보존한 취향 항목 선택 |
| [rendering.py](../../python_controller/knowledge/rendering.py) | 원자적 사실과 문장 표현 분리. 소개의 출처 한정 한 번, 연결된 문장/문단. 최종 문자열은 구조화 입력에서 결정론적으로 재구성해 검사 |
| [program.py](../../python_controller/knowledge/program.py) | 질문 범위별 재사용 지식 선택, 원문 SHA/발췌 검사, 버전별 출처와 미검토 상태 유지. 개별 질문의 정답 문자열 분기 없음 |
| [프로그램 지식 MD](../knowledge/PROGRAM_KNOWLEDGE.md) | 목적·현재 Web/Qt·기능·AI·통신·승인/안전·버전 비교의 11개 재사용 지식 항목. 원문 경로/해시/발췌/시점 기록 |
| [answer.py](../../python_controller/knowledge/answer.py) | 로컬 개요를 모델 전송 전에 처리. 정책 미실행/비공개 제한/검색 누락/생성·검증 실패 분리. 로그에는 상태·시간·사유만 기록 |
| [agent_graph.py](../../python_controller/llm/agent_graph.py) | 실제 ConversationGraph.docs 연결, 쉬운 설명과 소개 후속 질문의 문맥 유지 |
| [unified_agent.py](../../python_controller/llm/unified_agent.py) | 근거 없는 옛 Qt 고정 설명의 직접 진입 분기 제거. 읽기 전용 현재 상태 질문은 기존 bridge.get_status 경로 재사용 |
| [Web](../../python_controller/web/app.js), [Qt](../../python_controller/visualization/agent_panel.py) | 정책상 미실행/비공개 제한/실시간 스냅숏 상태를 사실 검증 실패와 구분. 출처 접기와 원문 보기 유지 |
| [로컬 회귀 검사](../../tests/test_conversational_recovery.py), 기존 knowledge/Agent 검사 | 임의 인물·값/부정 보존·개요 표현 변형·후속 문맥·정책/개인정보·실험 후보 비등록 검증. 이전 딱딱한 출력 기대값만 새 표현 계약으로 수정 |
| [격리 서버](../../scripts/run_conversational_rag_preview.py), [실제 UI 검사](../../scripts/verify_conversational_rag_ui.py) | 실제 Agent와 HTML/JS 및 PySide6 위젯으로 입력→답변→출처 검증. 모델 응답 Mock으로 답을 공급하지 않음 |

### 구조화 자료의 의미

소개 값은 등록된 MD 필드에서 읽는다. 프로그램 지식 MD는 문서와 소스를 대조해 작성한 로컬 편집 자료다. 프로그램 지식은 `agent_source_checked_human_review_pending`, `human_approved=false`이며, 이를 사람 검토 완료나 LLM 의미 검증 통과로 표시하지 않는다. UI도 **구조화 자료 기준**으로 표시한다. 원문 SHA와 발췌가 달라지면 해당 지식을 보류하고, 나머지 확인된 항목은 유지한다.

과거의 `program_detail()`/`_describe_program()` 문장은 현재 웹 관제를 Qt 승인 전용 구조로 설명하는 문제가 있어 기본 응답으로 재사용하지 않았다. 호환용 private 메서드는 남아 있으나 실제 개요 진입은 근거를 동반하는 문서 작업 경로로 통일했다. 일반 대화·미션 검증·실행 승인의 모델 경로는 바꾸지 않았다.

## 실제 화면 답변

이전 답변은 사용자가 제공한 현상과 당시 코드에서 확인한 기준선이다. 현재 8766 서버에서 재질문하거나 그 서버의 환경을 실험 환경과 같다고 가정하지 않았다.

**홍승조 소개 — 이전:** 각 필드마다 `사용자가 제공한 소개에서 홍승조의 …`를 반복했다.

**실제 Web/Qt 최종 답변:**

> 등록된 소개에 따르면 홍승조의 직업은 ‘뮤즈 장로드래곤’, 전공은 ‘20학번 전전’이라고 적혀 있어요. 역할은 ‘넘어지기’로 적어 주셨어요.
>
> 좋아하는 것으로는 ‘자칭 삼성팬, 브리또, 원피스(옷 → 절대 애니 아님), 포켓몬’이 적혀 있어요.

**후속 질문: 원피스 애니를 좋아한다는 뜻이야?**

> 등록된 소개에 따르면 홍승조가 좋아하는 것으로는 ‘원피스(옷 → 절대 애니 아님)’이 적혀 있어요.

첫 실제 화면 검사에서는 후속 질문에 소개 전체를 반복했다. 이를 발견해 필드·항목 선택을 추가했고, 최종 Web/Qt에서 위 문장을 확인했다. 원래 필드 값은 내부 claim에 온전히 남기고, 표시 항목도 원문 목록의 완전한 항목인지 검사한다. 부정을 잘라내는 부분 문자열 선택은 허용하지 않는다.

**우리 프로그램 설명해 줘 — 이전:** 관련 문서가 있어도 설명을 보류했다.

**실제 Web/Qt 최종 답변:**

> D.I.G.는 3륜 로봇의 상태를 확인하고 이동 계획을 대화로 만드는 관제 프로그램이에요. 현재 브라우저 관제는 HTML·CSS·JavaScript 화면과 Python 백엔드로 구성돼요. Qt 현장 관제도 별도 실행 경로로 남아 있어요.
>
> 손동작 직접 제어와 AI 이동 계획을 함께 사용할 수 있고, 계획을 작성하거나 블록을 편집할 수 있어요. 실제 이동 제어권은 한 번에 하나만 가져요. Agent는 일반 대화와 문서 설명, 이동 계획을 구분해 처리해요. 등록된 지식은 로컬에서 조회하고, 복잡한 문서 해석은 클라우드 설정과 근거 검증 조건을 충족할 때 사용해요.

**새 인물 김연구**는 격리 DB에만 등록한 합성 시험 자료다. 같은 renderer가 연구원·소속·커피·산책(등산 아님)을 출력했다. 인물별 전용 답변은 없다.

## 실제 Web 및 Qt 통합 검증

- **Web:** 설치된 Microsoft Edge의 실제 HTML/JavaScript 렌더링을 headless로 확인했다. 내장 브라우저 제어 서비스가 응답하지 않아 Playwright로 설치된 Edge를 사용했다. 페이지 입력창에 질문을 제출하고, 화면의 bubble 텍스트가 서버 최종 응답과 일치하는지 확인했다. 외부 페이지 요청은 차단했으며 앱이 시도한 외부 요청은 없었다.
- **Qt:** 실제 PySide6 `AgentPanel`을 offscreen으로 띄워 입력창→send→UnifiedAgent→ConversationGraph→history 표시를 확인했다. 출처 목록과 원문 위젯도 열었다. 실제 모니터에서 사람이 읽어 점수를 준 자연도 평가와는 다르다.
- 두 UI 모두 아래 **10개 질문**의 최종 텍스트가 화면에 표시됐고, Web/Qt 텍스트는 **10/10 동일**했다. 답변과 speech_text도 일치했다. 이를 일반화 정확도 100%로 해석하지 않는다.
- [Web 전체 실제 답변·시간·상태·근거](conversational_rag_recovery/web_results.json), [Qt 전체 기록](conversational_rag_recovery/qt_results.json). 첫 화면 결과도 `*_initial.json`에 따로 남겼다.
- Edge에서 실제 출처 버튼을 눌러 [원문 대화상자](conversational_rag_recovery/source_view_check.json)도 확인했다. `HONG_SEUNGJO.md` 원문과 ‘애니 아님’이 표시됐다. [Web 화면](conversational_rag_recovery/web-answer-0.png), [원문 열람 화면](conversational_rag_recovery/web-original-source.png), [Qt 화면](conversational_rag_recovery/qt-final.png)을 보존했다. 검사 스크립트의 문자열 eval이 CSP에 차단된 건은 locator 검사로 수정했으며 페이지 CSP/접근 정책은 완화하지 않았다.

| 질문 | 처리 경로 | 최종 확인 | Web / Qt 소요 ms |
|---|---|---|---:|
| 홍승조가 누구야? | docs → profile fields → renderer | 출처 한정 1회, 4필드 보존 | 2011 / 1590 |
| 원피스 애니를 좋아한다는 뜻이야? | 소개 문맥 → 취향 항목 | ‘옷, 애니 아님’ 보존, 전체 소개 반복 제거 | 453 / 53 |
| 김연구가 누구야? | 같은 profile 경로 | 새 인물·소속·취향으로 동작 | 217 / 521 |
| 우리 프로그램 설명해 줘 | docs → local_program | cloud=0 개요, 현재 HTML/Python와 Qt 구분 | 3015 / 913 |
| 어떤 기능이 있어? | docs → 지식 범위 선택 | 제어/계획/로컬 지식 설명 | 2519 / 1457 |
| AI랑 ESP32는 어떻게 연결돼? | docs → AI/통신/승인 지식 | 계획과 실행 분리, UDP와 응답, 승인 한정 | 900 / 2104 |
| 좀 더 쉽게 설명해 줘 | docs 문맥 → simple_text | 같은 관계를 더 쉬운 표현으로 설명 | 417 / 730 |
| PCB V18과 V19 차이가 뭐야? | 버전별 source-bound 자료 | 설계 범위·같은 GPIO 근거·호환 미검증 보존 | 1606 / 676 |
| 지금 로봇은 움직이고 있어? | 기존 bridge.get_status | SIMULATION, ESP32 미연결/STOP 미확인, 실물 상태 추측 없음 | 270 / 4 |
| 100cm 이동해도 무조건 안전해? | docs → 승인/안전 한정 지식 | 문서·거리만으로 안전 보장 불가 | 1263 / 407 |

이는 한 번의 로컬 UI 진단이며 표본/콜드 캐시/부하/폴링 영향을 받는다. **기존 Nemotron 자유 생성 P50 25.76초와 비교해 속도 개선율을 계산하지 않는다.** 모델 생성/의미 검증 시간을 측정한 것이 아니다. 실제 자유 생성 답변 개선과 독립 Faithfulness/Coverage/Naturalness는 미측정이다.

## 보류 사유 및 출력 계약

`POLICY_DISABLED`, `PRIVATE_SOURCE_RESTRICTED`, `RETRIEVAL_MISS`, `INSUFFICIENT_EVIDENCE`, `GENERATION_FAILED`, `INVALID_CITATION`, `DETERMINISTIC_REJECTION`, `SEMANTIC_REJECTION`, `VERIFIER_UNAVAILABLE`, `ANSWER_PARTIAL`을 결과 메타데이터에서 구분한다. 규칙별 세부 거절 사유도 기존대로 남는다. 정책상 미실행은 `policy_disabled`, 비공개 제한은 `restricted`이며 의미 검증 실패로 표시하지 않는다. 로그에는 질문·소개 값·원문·키 대신 상태/소요 시간/사유 코드만 쓴다.

최종 문장 검사는 renderer의 구조화 입력으로부터 동일 결과를 재구성하는 계약이다. 모델이 임의의 presentation 필드를 넣을 수 없으며 기존 strict 생성 스키마를 유지한다. 원래 값/부정/예외를 바꾸거나 임의 문장을 덧붙인 최종 출력은 계약을 통과하지 못한다. 의미 검증 전 스트리밍/TTS와 자유 재서술은 추가하지 않았다.

프로그램 사실 일부의 출처가 바뀌어도 확인된 나머지를 먼저 설명한다. 해석이 필요한 미지원 질문은 정책/근거의 부족을 안내한다. 새로운 복잡한 질문마다 새 정답 템플릿을 추가하는 방식으로 확장하지 않는다.

## 검사 및 보존

최종 회귀 결과는 **104 passed, 1 skipped, 144 subtests passed (532.12초)**다. [최종 로그](conversational_rag_recovery/validation.log). 신규 로컬 경로와 기존 Knowledge v2/v3/RAG 및 UnifiedAgent 전체 검사를 실행했다. STOP 선점, 늦은 RAG 응답 폐기, 승인 우회 방지와 개인정보 회귀도 포함한다. skip 1건은 미실행으로 남기며 전체 무결점 통과로 표시하지 않는다. 이전 Phase의 전체 회귀 수를 이번 실행 결과로 재사용하지 않는다.

기존 Phase 1~2.6 평가 산출물 102개의 해시가 모두 일치했다. [보존 확인](conversational_rag_recovery/preservation_check.json). 변경한 기존 파일은 RAG/Agent 표현·읽기 전용 라우팅/UI 안내/검사 기대값 9개뿐이며, 원래 바이트는 별도 보관했다. 검증기·생성기·ESP32·UDP·RobotSession·모터/엔코더·레이더·STOP·Lease·승인 정책과 cloud 기본 설정은 수정하지 않았다. 기존 예산 장부는 **12/12**, 이번 신규 외부 AI 요청은 **0회**다.

## 운영 적용과 롤백

현재 [격리 실험 서버](http://127.0.0.1:8771/)에서 개선 답변을 확인할 수 있다. 외부 AI 및 실물 연결은 비활성화했고 저장 경로도 `.tmp` 아래에 분리했다. **8766 운영 서버는 기존 프로세스 그대로이며 새 코드가 반영됐다고 주장하지 않는다. 그 서버의 현재 환경/상태는 이번 검증에서 미확인이다.**

로컬 표현/개요 경로는 운영 채택 후보로 준비됐다. 운영 반영은 운영자가 로봇 정지·연결 상태를 확인하고 기존 서버를 명시적으로 교체할 때 가능하다. 이 작업에서는 교체하지 않았다. 클라우드 설정을 켤 필요 없이 로컬 기능부터 적용할 수 있다. 자유 생성 후보는 별도 실제 API 검증 전까지 유지 보류다.

롤백은 전체 저장소 reset을 사용하지 않는다. [롤백 검사 도구](../../scripts/rollback_conversational_rag.py)와 [9개 파일 manifest](conversational_rag_recovery/rollback_manifest.json)는 작업 전 파일 hash와 현재 작업 후 hash를 검사한다. `.tmp/conversational-rag-before` 백업을 보존해야 한다.

```powershell
# 기본은 읽기 전용 검사
.venv/Scripts/python.exe scripts/rollback_conversational_rag.py
# 이후 롤백이 필요할 때, 해당 미리보기/적용 서버를 먼저 정상 종료한 뒤에만 실행
.venv/Scripts/python.exe scripts/rollback_conversational_rag.py --apply
```

검사에서는 9개 파일이 일치했으며 실제 복원은 하지 않았다. 이후 사용자가 파일을 수정했으면 전체 검사를 먼저 실패시켜 덮어쓰지 않는다. 새 독립 모듈/문서는 남겨 두며 옛 진입점 복원 후 호출되지 않는다. Windows 줄바꿈 때문에 역적용 dry-run을 통과하지 못한 임시 Git 패치는 폐기했고, 원본 바이트/해시 기반 복원으로 준비했다.

## 남은 한계

- 일반 대화와 복잡한 문서 이해의 실제 NVIDIA 품질은 추가 측정하지 않았다. 기존 12/12 장부 및 향후 별도 승인 절차를 유지한다.
- 로컬 편집 지식은 원문 내용 변화 시 갱신/검토가 필요하다. 원문 해시가 바뀌면 해당 사실을 보류하는 보수적 동작이다.
- 주제 분류와 후속 참조는 제한된 한국어 규칙이다. 모호한 인물 지시나 새로운 기술 설명을 모두 해결한 범용 의미 이해라고 주장하지 않는다.
- 실제 UI 답변은 확인했으나 인간 독립 자연도 점수, 최종 Gold 승인, 실물 로봇 구동·정지 성능은 미검증이다.
