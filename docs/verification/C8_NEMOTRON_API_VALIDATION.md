# D.I.G. Prototype A+B C8 Nemotron 3 Ultra 검증

## 설정 및 실제 연결

2026-11-17 프로젝트의 로컬 `.env`에 `NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b`와 `NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1`을 설정했다. `.env.example`에는 같은 모델과 빈 키 항목을 제공한다. 기존 API 키는 로컬 설정에서 읽었으며 코드·콘솔·검증 로그에 기록하지 않았다. 프로세스 환경변수가 로컬 설정에 우선한다.

| 항목 | 값 |
| --- | --- |
| 인터페이스 | OpenAI SDK 2.45.0에서 `POST /v1/chat/completions` |
| 계획의 temperature, max_tokens | 0.2, 1024 |
| 추론 | 기본 OFF. 평가에서만 `chat_template_kwargs.enable_thinking=True`로 비교 |
| 스트리밍 | `False` |
| 네트워크 설정 | SDK 타임아웃 20초, 자동 재시도 0회 |

NVIDIA 레퍼런스에서 temperature가 조정 가능하고 상한이 1이며, 모델 카드에서 `enable_thinking` ON/OFF를 지원하는 것을 확인했다. 두 모드 모두 temperature 0.2 요청이 실제로 성공해 그 값을 유지했다. 어댑터는 최종 `message.content`를 사용한다. 추론 본문은 출력하거나 로그에 쓰지 않으며, 비교 기록에는 존재 여부와 집계 토큰 수만 남긴다. [NVIDIA 호스팅 API](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-ultra-550b-a55b-infer) · [NVIDIA 모델 카드](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard)

최소 인증 요청은 추론 OFF와 32토큰 상한으로 1회 호출했다. **HTTP 200**, SDK 기준 1,943.5ms, 반환 모델 `nvidia/nemotron-3-ultra-550b-a55b`, `finish_reason=stop`, 최종 텍스트 `OK`. 프롬프트 21토큰, 완료 2토큰, 추론 0토큰이었다. HTTP 401/403, 404/410, 429, 5xx는 개별 HTTP 상태로 기록한다. 응답 시간 초과와 연결 실패에는 각기 다른 오류 코드가 기록된다.

## 실제 명령과 검증 결과

```powershell
.\.venv\Scripts\python.exe -m python_controller.llm_cli --dry-run "앞으로 15cm 이동하고 오른쪽으로 10cm 가"
```

실제 CLI가 전진 15cm → 오른쪽 10cm를 표시했다. 별도의 비교 호출에서 두 모드 모두 아래 최종 JSON을 반환했다. 공백을 정리했다.

```json
{
  "needs_clarification": false,
  "explanation": "앞으로 15cm 이동 후 오른쪽으로 10cm 이동",
  "actions": [
    {"type": "move", "direction": "forward", "distance_cm": 15},
    {"type": "move", "direction": "right", "distance_cm": 10}
  ]
}
```

기대 결과와 방향·거리·순서가 일치했다. `explanation`은 기존 스키마의 필수 필드다. 두 응답에서 엄격한 JSON, Pydantic, 안전 정책 및 원문 대조를 통과했다.

| 예시 요청 | HTTP | 경과 | finish_reason | 완료 토큰 | 추론 토큰 |
| --- | ---: | ---: | --- | ---: | ---: |
| 추론 OFF | 200 | 4,407.0ms | stop | 64 | 0 |
| 추론 ON | 200 | 2,801.8ms | stop | 161 | 96 |

CLI에서 `앞으로 150mm 이동하고 오른쪽으로 0.1m 가`는 전진 15cm → 오른쪽 10cm로 검증됐다. `앞으로 조금 가`와 `앞으로 55cm 가`는 로컬 검증에서 거절됐다.

## 기존 한국어 문장 100개로 추론 모드 비교

정상 25, 복합 25, 모호 25, 한계 초과·미지원 25개 문장과 정답을 기존 회귀시험에서 그대로 재사용했다. 평가기는 각 문장을 모드별로 한 번씩 보내 모델 응답을 채점한다. 실제 CLI의 로컬 파서는 모호·금지 50개를 모델 호출 전에 차단한다. 평가기는 이 50개에 대한 모델 자체의 거부 성능도 측정하기 위해 독립적으로 호출하며 로봇 실행기를 불러오지 않는다.

비교 호출은 동일 파라미터에서 추론 플래그만 바꿨다. 서로 다른 사례의 요청 시작은 약 1.65초 간격으로 배정했다. 정확한 해석은 원문 및 고정 정답과 방향·수치·순서가 일치한 계획이다. 모델 거부 성공은 유효한 JSON·스키마와 함께 빈 행동 목록 및 `needs_clarification=true`를 반환한 경우다. JSON·Pydantic 비율의 분모는 전체 시도이며, P50·P95는 최종 본문을 얻은 요청의 SDK 경과 시간이다. P95는 오름차순의 95% 순위 값을 사용한다. 앞 절의 예시 요청과 최소 인증 요청은 본 비교 집계에서 제외한다.

| 지표 | 추론 OFF | 추론 ON |
| --- | ---: | ---: |
| 정상 명령 정답 | 23/25 | 25/25 |
| 복합 명령 정답 | 23/25 | 25/25 |
| 모호한 명령의 모델 거부 | 21/25 | 23/25 |
| 한계 초과·미지원 명령의 모델 거부 | 19/25 | 19/25 |
| 전체 기대 결과 일치 | 86/100 | 92/100 |
| 엄격한 JSON 파싱 | 97/100 | 97/100 |
| Pydantic 검증 | 94/100 | 97/100 |
| 유효한 최종 텍스트 수신 | 97/100 | 97/100 |
| HTTP 200 | 97 | 98 |
| HTTP 429 · 타임아웃 · 토큰 상한 종료 | 2 · 1 · 0 | 2 · 0 · 1 |
| P50 · P95 | 1,563.6ms · 4,174.5ms | 3,731.8ms · 8,937.0ms |
| 사용량이 반환된 응답 | 97 | 98 |
| 프롬프트 토큰 | 31,637 | 31,954 |
| 완료 토큰 | 4,849 | 18,149 |
| 그중 추론 토큰 | 0 | 13,315 |
| 총 토큰 | 36,486 | 50,103 |

추론 ON에서 잘린 요청 한 건은 `finish_reason=length`였고, 생성 상한 1,024토큰을 모두 추론에 사용했다. 최종 계획으로 처리하지 않았다. 각 모드의 429 두 건은 같은 사례에 대해 재호출하지 않았고 이후 요청 간격을 일시적으로 늘렸다. 모델이 모호·금지 입력을 충분히 거부하지 못한 경우도 있었다. 실제 로컬 검증은 두 평가에서 동일한 이 입력 50개 전부를 차단했다. 추론 ON은 이번 측정에서 정상·복합 정답이 더 많았고, P50과 토큰 사용량이 늘었다. CLI 기본 추론 모드는 요청한 OFF로 유지했다.

실제 NVIDIA 요청은 총 205건으로, 비교 200건, 비교용 예시 2건, 최소 인증 1건, CLI의 정상·단위 변환 dry-run 2건이다. 위 사용량은 비교 200건에서 API가 반환한 토큰 집계다. 연결이나 호출 제한 오류의 사용량 및 CLI의 단위별 토큰은 기록되지 않았다.

## 변경 파일과 검증

| 파일 | 변경 |
| --- | --- |
| `python_controller/llm/nvidia_client.py` | Nemotron 전용 옵션, 단일 진단의 finish·시간·토큰 필드, 최종 텍스트 추출 |
| `python_controller/llm_cli.py`, `.env.example` | CLI 설명에서 특정 모델 이름 제거, 실제 호출된 Nemotron 설정 예제 |
| 로컬 `.env` | 공식 Base URL과 선택한 모델로 교체. 기존 자격 증명 유지 |
| `tests/korean_command_cases.py`, `tests/test_llm_planning.py` | 기존 평가 문장과 기대 조건 공유 |
| `tests/test_nemotron_client.py` | OpenAI SDK와 모의 HTTP로 ON/OFF 옵션, 숫자 usage, 추론과 최종 텍스트 분리, 잘린/빈 응답 거절 확인 |
| `scripts/evaluate_nemotron.py` | 모델별 채점 및 JSONL 증거·집계 |
| `README.md`, `docs/verification/C8_NEMOTRON_API_VALIDATION.md`, `docs/verification/PROTOTYPE_AB_VALIDATION.md` | CLI 안내, 검증 보고서와 현재 설정 링크 |

기준선 106개를 재실행해 모두 통과했고, 마무리 실행에서는 **기존 106개와 신규 4개, 총 110개 통과, 건너뜀 0개**였다. `compileall`도 통과했다. 프롬프트·Pydantic과 구동 정책, 통신, 텔레메트리, 펌웨어, 제어 기능은 이번 변경 범위에서 그대로 유지했다. 실제 CLI에서는 네 건의 dry-run 로그에 `HANDSHAKE`, `APPROVED`, `UDP_TX` 이벤트가 없는 것을 확인했다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m scripts.evaluate_nemotron .tmp/c8/new_corpus.jsonl
```

재현 시 평가기는 100개씩 두 모드와 예시 1건씩, 총 202개의 API 요청을 보낸다. 집계 및 요청별 증거는 `.tmp/c8/corpus_summary.txt`, `.tmp/c8/live_corpus.jsonl`에 있다. 인증 및 dry-run 출력은 `.tmp/c8/api_probe.jsonl`, `.tmp/c8/cli_dry_run.txt`, `.tmp/c8/cli_additional.jsonl`에 보관한다.

## 하드웨어 진입 판단

NVIDIA 인증, 예시 계획, 단위 환산과 로컬 거절을 소프트웨어에서 확인했다. 보드 연결, 펌웨어 업로드, 모터 동작은 미실시다. C6의 전원 분리·정지 연결 절차와 실제 주행의 별도 승인은 여전히 필요하다. C6에서 발견한 모순된 완료 텔레메트리의 수락 사례는 이번 API 범위에서 수정하지 않았다. 바닥 실측과 해당 완료 판정 확인 전에는 실물 주행을 합격 처리할 수 없다. [C6 준비 및 안전성 검토](../C6_HARDWARE_PREFLIGHT.md)
