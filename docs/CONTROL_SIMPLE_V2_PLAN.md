# control_simple_v2 기준선과 작업 순서

2026-11-18에 GitHub main의 46b6969와 로컬에서 제공된 control_simple_v2.ino를 대조했다. GitHub에는 단일 파일이 없었다. 제공 원본 SHA-256은 `04361d704319f67cefee757444693c84fec0a7b142ae7b13d0efaecb80872b45`다. 수동 및 자연어 이동의 부드러운 모터 회전과 중앙제어 UDP 연결은 사용자가 확인한 실물 기준선이다.

| 순서 | 범위와 파일 | 확인 |
| --- | --- | --- |
| 1 | `esp32_firmware/control_simple_v2/control_simple_v2/`를 기준으로 등록. Wi-Fi 문자열만 무시된 로컬 헤더로 분리. 이전 firmware는 보존 | 원본과 자격 증명 분리본의 바이트 대조, ESP32 Dev Module 컴파일 |
| 2 | `planning/`, `llm/prompt.py`, CLI·대시보드·실행기. 명시된 순서와 정규화된 단위를 검증. 회전은 방향/각도를 표현하며 전체 계획을 미리 보여줌 | 복합 이동 순서 및 승인, 상대 좌우/전후 보존, 회전 포함 계획의 UDP 실행 차단 |
| 3 | `control/hand_drive.py` 및 매핑 UI. MediaPipe Victory를 직접 손동작 후진으로 사용 | 기존 매핑 호환, 정지 및 오래된 프레임 차단, 추종 후진 금지 유지 |
| 4 | `dashboard_runtime.py`와 영상 진단. 모델 준비 직후 최신 프레임 사용. 캡처 시각 기준 유효시간을 정하고 지연 원인을 표시 | 큐·추론·캡처 지연 확인, 오래된 같은 프레임으로 새 이동 금지 |
| 5 | 펌웨어의 모터별 목표·실측 RPM 및 FF/PI 값을 진단에서 보관하고 좌우 비교 | 같은 각속도에서 모터마다 정·역방향 RPM 및 보정값 비교. 실측을 바탕으로 각 방향의 FF 표를 보정하고 이후 PI를 검토 |

거리 구간마다 기존 실행기가 완료와 STOP을 확인한 뒤 다음 구간을 보낸다. 각도 제어와 좌우 RPM 숫자 보정은 실측이 필요하다. 펌웨어 업로드와 모터 구동은 별도 실물 시험에서 수행한다.

## 기준선 차이와 구현 결과

| 항목 | 공식 `control_simple_v2` | 이전 `control_v2` 후보 |
| --- | --- | --- |
| 모터 제어 | 하나의 Feedforward+PI에서 속도·거리 명령 처리. 동일 방향 PWM 갱신에 0 펄스를 삽입하지 않음 | 모듈로 분리한 바퀴 PID와 급변 정책 |
| 출력축 1회전 계수 | 설정 823.13 | 설정 898 |
| 엔코더 | 급변 샘플 거부. 거리 이동 중 무효 지속 시 현재 목표 중단 | FILTERED_TEST 창에서 허용 횟수 초과 시 중단 |
| PC 통신 | UDP 8888/8889, heartbeat 100/600 ms, 속도 TTL 300 ms, STOP과 순서·토큰 확인 | 같은 V2 연결 계약 |
| 좌우 회전 진단 | 목표·실측 RPM, Feedforward PWM, PI 보정 PWM을 함께 보고 | 속도 기반 RPM과 PWM |

GitHub `main`의 공식 기준선 등록 커밋은 `abf466a`다. 제공 원본은 로컬 `.tmp/control-simple-v2-original/`에 보관했다. Wi-Fi 선언을 되돌려 넣으면 원본 바이트와 일치하는 것을 확인했다. 공식 등록본의 ESP32 Dev Module 컴파일 결과는 플래시 956,779바이트, 정적 RAM 49,132바이트다. 기준 스케치는 모터 제어를 수정하지 않았다.

| 단계 | 변경과 확인 결과 | 실물에서 확인할 항목 |
| --- | --- | --- |
| 자연어 | `planning/`, `llm/prompt.py`, `llm/central_control.py`, `llm_cli.py`, `visualization/natural_control.py`, `execution/motion_executor.py`를 수정했다. 두 복합 이동은 입력 순서 유지와 모델의 순서 변경 거부를 확인했다. 방향 없는 한 바퀴는 방향 미지정 360도 계획으로 표시하고 실행 전체를 차단한다. 거리 구간별 종료와 STOP 확인 테스트를 통과했다. | 실제 NVIDIA 응답과 두 구간의 주행 거리 확인. 회전은 외부 실측으로 각도 제어를 검증한 뒤 별도 연결 |
| 손동작 | `control/hand_drive.py`의 기본 직접 구동 매핑에 Victory → BACKWARD를 추가했다. 기존 매핑을 명시해 사용하는 설정은 Victory를 정지로 유지한다. 기본 매핑의 서로 다른 프레임 조건과 손 소실 시 정지를 검사했다. | 카메라에서 Victory 연속 인식과 실제 후진 방향 확인 |
| 카메라 | `dashboard_runtime.py`, `config/default.json`, `visualization/korean.py`를 수정했다. YOLO 준비 뒤 최신 프레임을 다시 읽고, 추론 전에 기한이 지난 캡처는 건너뛴다. 추론 완료 시 지연 상태를 표시한다. 기본 유효시간 350 ms는 시험값이다. 캡처 시각 기반 만료와 동일 프레임 반복의 제스처 누적 금지를 유지한다. | 같은 카메라·CPU 조건에서 전후 지연과 인물 ID·손 귀속 정확도 재측정 |
| RPM | `diagnostics.py`, `monitoring.py`, `visualization/dashboard.py`, `scripts/compare_turn_rpm.py`를 수정했다. ESP32 RPM 우선 표시와 좌우 시험값을 모터별로 비교하는 오프라인 도구를 모의 로그로 확인했다. | 동일 속도와 부하에서 세 모터의 목표·실측 RPM 및 FF/PI 보정값을 수집 |

이전 대시보드 로그 한 세션에서 동일 캡처를 중복 제거한 YOLO 처리 표본 9,608개를 집계했다. 단위는 ms다.

| 구간 | P50 | P95 |
| --- | ---: | ---: |
| 캡처 시작 → 인식 완료 | 213.9 | 381.4 |
| 캡처 | 33.1 | 67.4 |
| 큐 | 11.0 | 49.0 |
| YOLO | 123.7 | 292.0 |
| MediaPipe | 43.9 | 75.8 |

250 ms를 초과한 프레임은 3,282개였고 350 ms를 초과한 프레임은 830개였다. 이 비교는 이전 로그에 두 기준을 적용한 결과다. 변경 후 지연 단축이나 인식률 향상 수치로 해석하지 않는다.

## 좌·우 RPM 비교

같은 세션과 부하에서 본체 각속도 크기를 맞춰 좌우 회전 로그를 수집한다. 아래 도구는 UDP를 사용하지 않고 JSONL을 읽는다. 각 방향의 시작 600 ms와 Fault·엔코더 경고·고정 PWM 시험을 제외한다. 양쪽에 유효 샘플이 3건 이상이면 세 모터의 목표 RPM, 실측 RPM, 오차, 실제 PWM, FF 및 PI 값의 중앙값을 출력한다.

```powershell
.\.venv\Scripts\python.exe scripts\compare_turn_rpm.py logs\<세션>\events.jsonl --angular-speed 0.2
```

이전 로그에는 새 RPM 및 PI 진단 필드가 없어 `INSUFFICIENT_DATA`가 나왔다. 새로 확보할 동일 조건의 표본으로 `FF_PWM_POS`와 `FF_PWM_NEG`의 해당 행을 먼저 보정한 뒤 PI 잔여 오차를 검토한다. 계수와 임계값은 실측 전에는 확정하지 않았다.

회귀 테스트 166개가 통과했다. 자격 증명을 제외한 격리 사본을 사용했고 UDP 독점 검사는 다른 루프백 포트에 바인딩했다. 원시 결과는 `.tmp/simple-v2-baseline/regression.txt`에 있다. 기준선 이후 Python 변경은 로컬에 있다. 이번 작업에서 새 NVIDIA 호출, 펌웨어 업로드 및 모터 구동은 수행하지 않았다.
