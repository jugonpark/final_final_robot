# 누적 미션과 로봇 문서

## 시작과 저장 위치

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-agent.txt -r requirements-knowledge.txt
.\run_web.bat --agent-enabled
```

브라우저 단독 중앙관제는 `run_web_control.bat`로 시작하며 Agent를 기본 활성화한다. `run_web.bat --agent-enabled`는 합성 영상 시뮬레이션이고 기존 Qt 관제는 `run_simulation.bat --agent-enabled`로 시작한다. 두 화면 모두 기존 Python Agent·문서 검색·미션 저장소를 사용한다. 이전 SQLite 실행 이력은 기존 경로에 그대로 둔다. 첫 Agent 실행에서 `logs/mission_drafts.sqlite3`를 만들고, 처음 문서를 열거나 검색할 때 `logs/robot_knowledge.sqlite3`를 만든다. 구버전의 메모리상 계획과 승인 상태는 복원되지 않는다.

## 초안 편집과 실행 요청

1. `앞으로 5cm 이동해`, `그다음 오른쪽으로 5cm 이동해`, `마지막에 뒤로 10cm 추가해`를 각각 보내면 같은 미션에 블록이 쌓인다.
2. `두 번째를 맨 앞으로 옮겨`라고 말하거나, 웹에서 블록 손잡이를 끌어 파란 삽입선에 놓는다. 키보드와 터치에서는 ↑ ↓ 버튼을 누른다. **블록 추가**에서 중간이나 맨 뒤를 선택할 수 있고, 각 블록에는 편집과 삭제가 있다. 저장 전 전체 계획을 검증하며 기존 4행동·100cm·회전 제한을 적용한다.
3. `이 계획들 실행해`는 현재 버전의 검토 요청이다. 웹과 Qt는 같은 저장 초안을 읽는다. 브라우저 단독 중앙관제에서는 **실행 요청 → 실물 실행 승인**으로 현재 버전을 확인하고 현장 안전을 명시적으로 확인한다. 기존 Qt에서도 같은 운영자 승인 절차를 사용할 수 있다. 웹 시뮬레이션은 실물 승인을 허용하지 않는다. Python이 제어권, 레이더, 새 STOP 응답과 계획 버전을 검증한다.
4. 수정, 긴급 정지, 레이더 및 세션 변화는 기존 실행 권한을 무효화한다. 대화 삭제는 대화와 검토 요청을 지우고 저장된 초안은 보존한다. 초안 자체는 웹의 **초안 비우기**를 사용하거나 `초안 삭제 확인`을 입력한다.
5. 승인된 실행은 동작 버전을 고정하고 저장된 활성 초안에서 분리한다. 이때부터 새 이동 요청은 다음 미션 초안을 만든다. 초안의 편집 이력과 실제 수행 증거는 서로 다른 SQLite 파일에 저장한다.

브라우저 단독 관제는 마지막으로 보이는 관제 탭의 운영자 확인이 1.5초 이상 끊기면 진행 중인 LLM 계획에도 긴급 정지를 요청한다. 재접속은 승인을 복원하거나 실행을 재개하지 않는다. 서버 종료는 실행 콘솔의 **Ctrl+C**를 사용한다. [실행 방식 비교](WEB_PYTHON_COMPARISON.md)

현재 스키마와 Nemotron 처리 흐름, 일반 질문 응답 계획은 [Agent 스키마와 대화 처리](DIG_AGENT_SCHEMA_AND_DIALOGUE.md)에서 볼 수 있다.

## 로봇 문서와 출처

브레드보드 시제품, PCB V18 설계, PCB V19 설계의 BOM과 핀맵은 각각 조회합니다. 버전이 모호한 질문에는 먼저 기준을 확인합니다. 웹 인용은 해당 버전과 출처 유형, 검증 상태, 리비전을 표시합니다. [버전 맵](hardware/README.md) · [기술 설명 v0.2](hardware/review/DIG_TECHNICAL_v0.2.md)

**로봇 문서**에서 검색하거나 채팅에 `문서: 레이더 UNKNOWN 정지`처럼 입력한다. 기본 색인은 저장소의 아키텍처, 관제 가이드, ESP32 기준선·사전 점검, 레이더 운영 및 연동 문서다. 저장소 문서 출처에는 Git 커밋과 내용 SHA-256을 표시한다. 업로드 문서에는 내용 SHA-256 및 Markdown 절이나 PDF 쪽을 표시한다.

PDF·UTF-8 Markdown·TXT를 파일당 4MB까지 등록할 수 있다. PDF는 잠금이 해제된 120쪽 이하의 문서에 텍스트가 있어야 한다. 사용자 등록 문서는 로컬 참고 문서로 표시되며 항상 로컬에서 검색한다. 민감한 파일명과 인식한 비밀 패턴은 색인에서 제외한다. 재등록하면 동일 파일명의 직전 버전은 이전 버전으로 보관되고 새 버전이 검색된다. 문서 인용은 관제 명령이나 실시간 상태 확인의 근거가 될 수 없다.

사용자 참고 자료를 Markdown 원본으로 관리하려면 `docs/user_reference/` 바로 아래에 UTF-8 `.md` 파일로 저장한다. 문서 제목은 `# 홍승조`처럼 질문할 이름과 정확히 맞추고 소개 내용을 본문에 적는다. 새 `KnowledgeLibrary`가 처음 문서 목록·검색·문서 질문을 처리할 때 이 폴더의 Markdown을 자동 색인한다. 별도 업로드나 이름별 코드 등록은 필요 없다. 하위 폴더·심볼릭 링크·민감 파일명·4MB 초과 문서는 읽지 않으며 비밀 패턴은 기존 등록 검사로 제외한다. 사용자 참고 자료는 `user_reference`와 `USER_UPLOAD`로 분류하고 외부 LLM에 전송하지 않는다.

색인된 사용자 Markdown 제목과 일치하는 이름에 한해 `홍승조가 누구야`, `홍승조는 누구예요?`, `홍승조 누구인가요?`를 문서 질문으로 바로 처리한다. 다른 이름에도 같은 규칙을 적용하며, 이름이 없거나 이동 명령이 섞인 질문·자기소개 질문을 이 경로로 바꾸지 않는다. `문서: 홍승조가 누구야`처럼 명시적인 문서 질문도 계속 지원한다. 같은 파일의 내용이 바뀌면 새 라이브러리에서 원본을 다시 읽어 이전 버전을 보관하고 새 내용을 검색한다.

이 자동 색인은 실행 중인 서버의 파일 변경 감시나 Python 코드 자동 재적재가 아니다. 코드를 갱신하거나 Markdown을 변경한 뒤 이미 실행 중인 서버에 반영하려면 장치를 정지·연결 해제한 뒤 서버를 종료하고 다시 실행한다. 재시작 전의 기존 프로세스에는 새 질문 분류가 적용되지 않는다.

기본 응답은 로컬 FTS5 검색과 원문 발췌다. `DIG_KNOWLEDGE_CLOUD=1`을 명시한 경우에만 Nemotron에 질문과 허용된 Git 문서 발췌를 전송한다. 사용자 등록 문서나 저장소 밖 문서가 검색 결과에 포함되면 로컬 발췌로 답한다. 모델이 반환한 인용은 동일한 원문에서 확인된 부분만 표시한다.

## 선택 기능인 로컬 다국어 벡터 검색

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-knowledge-vector.txt
.venv\Scripts\python.exe -m python_controller.knowledge.install_models --download
.venv\Scripts\python.exe -m python_controller.knowledge.evaluate --vector
```

위 명령은 공개 `intfloat/multilingual-e5-small` 모델을 내려받아 로컬에서 실행한다. 이후 `DIG_KNOWLEDGE_VECTOR=1`을 환경 변수로 설정하면 캐시한 모델과 FTS5를 함께 사용한다. 실행 중 모델을 다운로드하지 않는다. 파일이 없거나 벡터 검색이 실패하면 FTS5로 돌아간다. 초기 구현은 활성 청크 1,000개까지 벡터를 결합한다.

## 검증 범위

- 기준 커밋 `b03f7d2`에서 자동 테스트 229개를 실행했다. 228개 통과와 비공개 원본 펌웨어 아카이브 검사 1개 건너뜀을 기록했다.
- 고정 검색 질의 9개에서 기대 문서의 Top-3 포함은 FTS5가 7개, 벡터 결합이 6개였다. 초기 모델 다운로드·임베딩 이후 응답은 벡터 결합 17~21ms였다. 이 결과에 따라 FTS5를 기본으로 사용한다.
- 통합 후 자동 테스트 241개를 실행했다. 240개 통과와 같은 아카이브 검사 1개 건너뜀을 기록했다. 명령 누적, 버전 충돌, 재시작, 실행 중 다음 초안, 레이더·STOP 승인 무효화, 검증된 단일 실행을 모의 ESP32에서 확인했다.
- Chrome 1440px 및 390px에서 채팅 누적, 드래그·키보드 재정렬, 블록 편집, 2D 경로, 읽기 전용 실행 요청, PDF 2쪽 인용, 카메라 해제 및 긴급 정지를 확인했다. PDF는 2쪽짜리 테스트 문서다.
- Nemotron 답변 연동은 모의 응답으로 확인했다. 실제 프로젝트 문서 발췌의 외부 전송은 자동 승인 검토에서 허용되지 않았다. 실물 ESP32·레이더·웹캠, 모터 동작과 위치 측정은 미검증이다.

## 변경 파일

| 구분 | 파일 |
| --- | --- |
| 초안 저장소 | `python_controller/mission/draft_store.py` |
| Agent 스키마, 그래프, Qt 승인 연결 | `python_controller/llm/agent_intent.py`, `python_controller/llm/agent_graph.py`, `python_controller/llm/unified_agent.py`, `python_controller/visualization/agent_panel.py`, `python_controller/visualization/dashboard.py` |
| 블록 UI, 조회 및 등록 API | `python_controller/web_dashboard.py`, `python_controller/web/index.html`, `python_controller/web/app.js`, `python_controller/web/style.css` |
| 색인·검색·인용과 벡터 평가 | `python_controller/knowledge/library.py`, `python_controller/knowledge/answer.py`, `python_controller/knowledge/vector.py`, `python_controller/knowledge/evaluate.py`, `requirements-knowledge.txt`, `requirements-knowledge-vector.txt` |
| 테스트 및 운영 가이드 | `tests/test_draft_mission.py`, `tests/test_unified_agent.py`, `tests/test_knowledge_rag.py`, `docs/DRAFT_MISSION_KNOWLEDGE.md` |

## Robot Knowledge RAG v2

구조 청킹·증분 캐시·백그라운드 임베딩·RRF·선택형 리랭커·근거 답변과 평가 결과는 [RAG v2 구현 및 검증](ROBOT_KNOWLEDGE_RAG_V2.md)을 참고한다. RAG 전용 답변 제한은 기본 90초, 리랭커는 15초다. 로봇 안전 타이머는 바꾸지 않는다.
