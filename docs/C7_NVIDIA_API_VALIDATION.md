# D.I.G. Prototype A+B C7 NVIDIA API 검증

2026-11-17 검증 결과. 기존 키는 NVIDIA Chat Completions 인증에 성공했다. 지정한 두 Qwen 모델은 NVIDIA 호스팅 경로에서 모두 HTTP 410을 반환했다. 동작이 확인된 Qwen 모델이 없어 `NVIDIA_MODEL` 기본값을 비웠다. Qwen을 통한 실제 자연어 계획 검증은 미완료다.

## C7-1 설정과 인증

- NVIDIA 공식 호스트는 `https://integrate.api.nvidia.com`이고 요청 경로는 `POST /v1/chat/completions`다. 어댑터는 `NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1`에서 OpenAI SDK의 `chat.completions.create()`를 사용한다. `NVIDIA_BASE_URL`과 `NVIDIA_MODEL`은 환경변수나 로컬 `.env`에서 지정할 수 있다. 동일 키가 있으면 프로세스 환경변수 값이 우선한다. [NVIDIA LLM API 레퍼런스](https://docs.api.nvidia.com/nim/reference/llm-apis)
- 기존 키는 로컬 `.env`에서 읽었다. 사용자가 확인한 계정 표시 상태는 `ACTIVE`, 표시된 만료일은 2126-07-08이다. SDK를 생성하기 전에 키 누락은 `NVIDIA_API_KEY_MISSING`, 모델 누락은 `NVIDIA_MODEL_MISSING`으로 처리한다. API 오류 본문은 출력하지 않고 진단에서는 알려진 키를 응답 요약에서 가린다.
- 같은 계정 키로 진행한 인증 대조 시험에서 성공 응답을 확인했다. 키 재발급은 수행하지 않았다.

## C7-2 모델 접근 비교

기존 Qwen 모델부터 요청하고 대체 후보에 같은 32토큰 상한의 `Reply with just OK.` 요청을 보냈다. 두 Qwen 모두 각 1회만 호출했다. 응답 시간은 이 PC에서 한 번 측정한 HTTP 요청 왕복 경과다.

| 모델 | HTTP | 호출 성공 | 경과 | 반환 모델 | 응답 텍스트 | 오류 유형 |
| --- | ---: | --- | ---: | --- | --- | --- |
| `qwen/qwen3-next-80b-a3b-instruct` | 410 | 아니요 | 175.0ms | 없음 | 없음 | Gone |
| `qwen/qwen3.5-122b-a10b` | 410 | 아니요 | 46.5ms | 없음 | 없음 | Gone |

NVIDIA Build는 두 모델의 Free Endpoint에 Deprecated를 표시한다. 후보의 `qwen/qwen3.5-122b-a10b` 식별자는 공식 문서의 자체 호스팅 예제에서 확인된다. 해당 페이지에는 파트너와 자체 호스팅 옵션도 있다. 이번 요청은 NVIDIA의 공개 통합 API 호스트에서 검증했다. [기존 Qwen 모델](https://build.nvidia.com/qwen/qwen3-next-80b-a3b-instruct), [후보 Qwen의 NVIDIA 문서](https://build.nvidia.com/qwen/qwen3.5-122b-a10b?nim=self-hosted)

`GET /v1/models`는 기존 키를 포함했을 때 200, 인증 헤더를 빼도 200이었다. 키를 넣은 응답에서 모델 32개가 확인됐으며 `qwen/`으로 시작하는 항목은 없었다. 인증 여부는 같은 호스트의 모델 추론 요청으로 따로 확인했다.

| 인증 대조 모델 | 키 사용 | HTTP | 경과 | 반환 모델 | 응답 |
| --- | --- | ---: | ---: | --- | --- |
| `nvidia/nemotron-3.5-bolt-e4b` | 없음 | 401 | 47.8ms | 없음 | 없음 |
| `nvidia/nemotron-3.5-bolt-e4b` | 기존 키 | 200 | 370.1ms | `nvidia/nemotron-3.5-bolt-e4b` | `OK` |

**판정** — 기존 키는 이 호스트의 추론 요청에서 인증됐다. 두 Qwen의 410과 공식 Deprecated 표시는 해당 모델의 호스팅 경로를 현재 사용할 수 없다는 근거다. 대조 모델은 인증과 CLI 경로를 검증할 때만 프로세스 환경변수로 지정했다.

## C7-3 모델 교체와 진단 도구

프로덕션 Python 코드에서 기존 Qwen 기본값을 제거했다. `.env.example`의 `NVIDIA_MODEL`은 비어 있으며, 키가 든 로컬 `.env`는 그대로 유지했다. 실제 접근을 확인한 모델이 생기면 `.env`나 프로세스 환경변수의 `NVIDIA_MODEL`을 설정한다. 모델 변경 시 검증기, 실행기, ESP32 통신, heartbeat, 텔레메트리, STOP 및 watchdog에는 변경이 필요 없다.

모델별 진단은 프로젝트 루트에서 다음과 같이 실행한다. 명시한 모델당 한 번 호출하고 결과를 JSON 행으로 출력한다. 진단 모듈은 로봇 실행기를 불러오지 않는다.

```powershell
.\.venv\Scripts\python.exe -m python_controller.llm.nvidia_diagnostics qwen/qwen3-next-80b-a3b-instruct qwen/qwen3.5-122b-a10b
```

401은 인증 실패, 403은 접근 거절, 404는 요청한 리소스 미확인, 410은 엔드포인트 종료, 429는 요청 제한, 5xx는 서버 오류로 기록한다. 타임아웃과 연결 실패는 각각 `NVIDIA_TIMEOUT`, `NVIDIA_NETWORK_ERROR`이며 HTTP 상태가 비어 있다. 도구는 오류 본문과 자격 증명을 기록하지 않는다.

## C7-4 자연어 dry-run

두 Qwen의 호출은 실패했다. 위 인증 대조 모델을 임시로 지정한 실제 API dry-run에서는 아래와 같이 확인했다. 실제 출력에는 전진 15cm, 오른쪽 10cm가 순서대로 표시됐다. 모델 응답은 엄격한 JSON 파싱, Pydantic 스키마 및 원문 일치 검사를 통과했고 `READY`와 `DRY_RUN`이 기록됐다. 아래는 기록의 `actions` 부분이다. `distance_cm`의 문자열 표기는 검증을 끝낸 Decimal의 로그 표현이다.

```json
[
  {"type": "move", "direction": "forward", "distance_cm": "15"},
  {"type": "move", "direction": "right", "distance_cm": "10"}
]
```

| 입력 | 결과 |
| --- | --- |
| 앞으로 15cm 가고 오른쪽으로 10cm 이동해 | 실제 대조 모델 dry-run 통과, `forward 15 → right 10` |
| 앞으로 150mm 가고 오른쪽으로 0.1m 이동해 | 로컬 단위 환산은 성공. 실제 대조 모델이 재확인을 반환해 계획 거절. 같은 문장의 올바른 모의 JSON은 검증 통과 |
| 앞으로 150 mm 이동해 | 실제 대조 모델이 재확인을 반환해 계획 거절 |
| 오른쪽으로 0.1 m 이동해 | 실제 대조 모델의 계획이 원문과 불일치해 거절 |
| 앞으로 조금 가, 대각선으로 10cm 가 | API 호출 전에 모호한 표현·미지원 방향 거절 |
| 앞으로 55cm 가, 이동 4개 뒤에 정지 추가 | API 호출 전에 거리 초과와 행동 5개 거절 |

기존 정책인 이동당 5–50cm, 합계 100cm, 최대 4행동은 유지했다. 실제 CLI 로그에 `HANDSHAKE`, `APPROVED`, `UDP_TX`가 없음을 확인했고 모의 HTTP 회귀시험에서도 dry-run에서 로봇 실행기가 생성되지 않았다.

## C7-5 변경 및 회귀 결과

| 파일 | 변경 |
| --- | --- |
| `python_controller/llm/nvidia_client.py` | 기존 OpenAI 어댑터 유지, 명시적 모델 설정, 오류 분류, 단일 HTTP 진단 추가 |
| `python_controller/llm/nvidia_diagnostics.py` | 모델별 응답 요약을 출력하는 진단 CLI |
| `python_controller/llm_cli.py`, `.env.example` | 검증된 모델의 명시적 설정과 모델 미설정 오류 |
| `tests/test_nvidia_diagnostics.py` | 실제 SDK + 모의 HTTP로 200, HTTP 오류, 네트워크 오류, 타임아웃, 자격 증명 가림, 무재시도 검증 |
| `tests/test_llm_cli.py`, `tests/test_llm_planning.py` | 기존 모의 설정에 모델 ID 명시. 환경변수 우선순위, 단위 환산, 미설정 모델에서 fallback 차단 시험 |
| `docs/C7_NVIDIA_API_VALIDATION.md`, `docs/PROTOTYPE_AB_VALIDATION.md` | 검증 결과와 현재 설정 방법 |

`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`에서 **기존 100개와 신규 6개, 총 106개 통과**. 건너뜀 0개. 사람 추종, 제스처, 대시보드, MOTOR TEST, ESP32 소프트웨어 통신 회귀검증을 포함한다. `compileall`도 통과했다. 프롬프트, Pydantic 정책, 구동 승인 및 실행 경로와 펌웨어는 그대로다. 원본 증거는 `.tmp/c7/live_probe.jsonl`, `.tmp/c7/auth_control.jsonl`, `.tmp/c7/control_dry_run.jsonl`, `.tmp/c7/unittest.log`에 있다.

## 남은 확인과 C6 진입

NVIDIA 호스팅 Qwen 호출과 Qwen 자연어 dry-run은 현재 미확인이다. Qwen 사용이 확인된 호스트에서 예시 문장과 단위 변환을 다시 검증해야 자연어 `cmd_move` 시험의 전제 조건을 충족한다. [C6 준비 절차](C6_HARDWARE_PREFLIGHT.md)의 정지 연결·전원 분리 시험은 별도 승인 절차를 따른다. 이번 작업에서는 ESP32 실물 연결·이동 UDP 송신·펌웨어 업로드·모터 구동을 수행하지 않았다.
