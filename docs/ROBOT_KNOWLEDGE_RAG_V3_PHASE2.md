# D.I.G. RAG v3 Phase 2 구현 및 재현 안내

작성일 2026-11-26, 기준 `8861848`, 작업 브랜치 `codex/grounded-rag-v3`.

## 결과의 범위

평가 코드, 120개 질문/180개 provisional semantic pair, 실제 NVIDIA 표본 실행, 오류 분석, 개발용 정책 후보 재검증, 그래프와 회귀 검사를 수행했다. 독립 사람이 검토한 gold와 최종 출력 atom 주석은 확보하지 못했다. 따라서 전체 품질 개선·Pareto 최적성·Phase 2 합격을 입증한 상태는 아니다. 운영 기본값은 유지한다.

이번 작업은 Phase 1의 미커밋 파일을 삭제하거나 대체하지 않았다. 시작 시 `.tmp/rag-phase2-baseline/phase1.patch`, `phase1-files.zip`, `files.json`에 37개 변경 파일과 SHA-256을 보관했다. v2 기준선은 `git show 8861848:python_controller/knowledge/answer.py`의 실제 코드다.

## 구현 파일

| 파일 | 역할 |
|---|---|
| `knowledge/experiments.py` | 실패 포함 영속 12회 제한, SDK 사용량 기록, 독립 final-reply atom 지표, verifier 혼동행렬 |
| `scripts/rag_phase2_preflight.py` | 공개 저장소 확인, NVIDIA 모델 목록·안전한 환경 정보·반환된 한도 헤더 조회 |
| `scripts/build_rag_phase2_dataset.py` | 고정 리비전 근거, 기존 60개 질문 포함 120개 후보, 180개 provisional NLI pair, 문서군별 분할 |
| `scripts/evaluate_rag_v3.py` | 격리 SQLite에서 A 실제 v2, C 실제 v3 실행; B는 같은 C 생성 결과에 인용 존재 검사만 적용하는 실험 전용 경로 |
| `scripts/evaluate_semantic_verifier.py` | gold를 보내지 않는 실제 batch NLI 평가; 독립 모델은 명시 인자와 모델 목록 확인 필요 |
| `scripts/evaluate_rag_policy_candidate.py` | 개발용 061 사례에서만 목표/설계 규칙 후보와 실제 의미 검증, 운영 기본값 변경 없음 |
| `scripts/audit_rag_phase2_gold.py` | 원문 무결성·분할 검사와 발견된 gold 오류의 감사 기록; 원본 gold 자동 수정 금지 |
| `scripts/analyze_rag_tradeoff.py` | 실패 포함 비교표, 검토용 블라인드 답변, 오류/호출/Trade-off 기록 |
| `scripts/plot_rag_phase2.py` | 실제 측정값 Matplotlib 그래프; 미측정 품질·임계값·Pareto는 명시적 placeholder |
| `tests/test_rag_phase2.py` | 예산·실패 분모·최종 출력 주석·실험 B 경계 검사 |

운영 RAG 생성기·검증기·검색·Agent·UI·안전 정책은 Phase 1 상태를 유지했다. B와 규칙 후보는 앱에 연결하지 않았다. 새로운 모델/의존성 설치·운영 캐시·개인정보 분리 전송 기능도 추가하지 않았다.

## 데이터·평가 규칙

- `gold_cases.jsonl` 120개에는 기존 60개와 구어체·후속·범위 혼동·비공개·공격 질문 후보를 포함한다.
- `semantic_cases.jsonl`은 실제 고정 문서의 60문단에 직접 진술/명시 부정/근거 없는 정밀 지연을 적용한 180쌍이다. 템플릿 의존성이 크며 독립적으로 작성한 180개 전문 NLI 사례가 아니다.
- 모든 gold는 `provisional_gold`, `human_review_pending`이다. 개발 33, 검증 44, 시험 43개로 문서군을 묶었다. 문서군 간 같은 의미의 패러프레이즈 누출 여부는 독립 검토 전까지 보장하지 않는다.
- Gold fact가 문단 단위인 경우가 있어 atomic required-fact 분해가 미완료다. 061의 gold 관련성 오류, 062의 바인딩 정답 불완전성을 발견했다. 원본 hash를 바꾸지 않고 `gold_audit.json`에 남겼다. 이 데이터로 confirmatory Coverage를 계산하지 않는다.
- 생성 모델에는 질문과 검색 근거만 보낸다. expected_facts·forbidden_claims·gold·split은 payload에 넣지 않는다. verifier에도 gold label을 보내지 않는다.
- 평가용 final_reply_atoms는 모델 claims와 분리한다. 지원 여부가 미주석/unknown이거나 사실 claim이 없으면 Faithfulness는 null이다. 미검토 naturalness/relevance도 null이다.

## 실제 실험과 예산

사용자 승인 한도는 최대 12회다. `api_budget.json`은 전송 전에 예약하며 재시작해도 누적한다. 실패도 예약을 사용하고 SDK retry는 0이다. 첫 Windows lock 오류는 HTTP 전 예약만 남았다. 실제 HTTP 수와 보수적인 예약 수는 `request_audit.json`에서 분리한다. 추가 자동 유료 재실행은 하지 않았다.

A/C 질문은 061(UDP 통신), 062(Qt 기술), 063(UNKNOWN) 3개다. C의 실제 end-to-end 경로에서 생성된 같은 근거/claims를 B가 재사용한다. B의 지연은 공유 생성 구간에 B의 인용 검사를 더한 counterfactual이며 별도 네트워크 요청이 아니다. A와 C는 각자 실제 end-to-end 실행이다.

실제 NLI 표본은 개발 분할 30쌍(10/10/10)이며 한 요청으로 batch 처리했다. 모델은 설정된 `nvidia/nemotron-3-ultra-550b-a55b`, 공식 NVIDIA endpoint, 추론 모드 false다. 계정 quota와 가격은 API 응답으로 확인하지 못했으며 무료라고 가정하지 않았다. 키는 결과에 저장하지 않았다.

독립 모델 D는 실행하지 않았다. 모델 목록만으로 한국어 검증 성능·라이선스·비용·메모리를 확인했다고 간주하지 않는다. 12회 예산에서 A/B/C와 기존 검증기, 실제 개발 오류 후보 검사를 우선했다.

## 재현

```powershell
# 로컬 검사 (외부 호출 없음)
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_rag_phase2.py -q
.\.venv\Scripts\python.exe -X utf8 scripts/audit_rag_phase2_gold.py
.\.venv\Scripts\python.exe -X utf8 scripts/analyze_rag_tradeoff.py
.\.venv\Scripts\python.exe -X utf8 scripts/plot_rag_phase2.py

# 기존 결과를 읽는 것이 기본이다. 아래는 추가 과금 가능한 명시 live 진입점이며
# 이번 12회 장부는 소진되어 더 이상 요청할 수 없다. 새 예산 동의 없이 장부를 지우지 않는다.
# python scripts/evaluate_rag_v3.py --live
# python scripts/evaluate_semantic_verifier.py --live
```

`experiment_manifest.json`, `dataset_manifest.json`, raw JSONL과 요청 hash·토큰·시간이 재현 근거다. 기존 파일이 있으면 평가가 건너뛰거나 재실행을 거절한다. 잘못된 gold를 고칠 때는 기존 manifest/raw 결과를 보존하고 새 데이터 버전으로 별도 평가해야 한다. 이번 샘플의 결과를 맞추기 위해 gold나 시험 프롬프트를 수정하지 않았다.

상세 결과, 실제 예시와 제한은 [Phase 2 보고서](verification/RAG_V3_PHASE2_REPORT.md)를 참조한다.
