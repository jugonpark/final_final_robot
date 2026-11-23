# 대시보드 구현·검증 결과 — 2026-11-01

이 문서는 47개 테스트 시점의 기록이다. 수동 모터 시험 추가 후의 현재 결과는 [MOTOR_TEST_VALIDATION.md](MOTOR_TEST_VALIDATION.md)에 있다. 본 문서의 과거 실제 웹캠 증거를 실제 ESP32·모터 검증으로 대체하지 않는다.

## 최종 자동 검사

` .venv\Scripts\python.exe -m unittest discover -s tests -v `: **47 tests / OK / exit 0**. 최종 출력은 [dashboard_unit_tests.txt](dashboard_unit_tests.txt)에 저장했다. 기존 30개를 유지하고 새 테스트 17개를 추가했다. `compileall -q python_controller scripts tests`도 exit 0이었다.

검사 범위는 Raw 보존·다인 차단·거부 사유, confidence/시간/프레임 안정화, Confirmed와 Applied 분리, unarmed 승인 미적용, 요청/안전 명령 구분, 누락 피드백 null, RPM 환산, 최신 프레임 단일 슬롯, stale frame 정지, 모드 전환 조건과 재활성화 금지, 긴급 정지 유지·입력 우선순위, 송신 성공/seq ACK/STOP 확인 분리, 잘못된 추가 텔레메트리 배열, 결과 기본 NOT TESTED·메모 요구·실제 로그 저장이다.

Qt 오프스크린 테스트는 실제 QWidget와 버튼 신호, 작은 960x700 창, 4탭 렌더, 긴급 정지, 메모 입력이 제어 단축키에 소모되지 않는 동작까지 확인했다. 시뮬레이션/합성 경로는 socket 생성 시 실패시키는 검사로 ESP32 통신 차단을 확인했다. 실제 UDP 모터 연결을 사용한 테스트는 없다.

## 실제 웹캠 실행

실행:

```powershell
.\.venv\Scripts\python.exe -u -m python_controller --ui dashboard --smoke-seconds 30
```

프로세스 종료 코드: **0**. 선택한 증거는 `logs/20261101_145012_265595/events.jsonl`이다. 이후 다른 실행의 값을 이 결과로 대체하지 않는다.

| 관찰 | 결과 |
|---|---|
| 로그 행 | 240 |
| 서로 다른 최신 추론 frame timestamp | 169개 |
| 검출된 최대 person 박스 수 | 2개. 실제 인원 정확도를 채점한 결과는 아님 |
| 최대 hand 검출 수 | 0 |
| Raw labels / confirmed / applied | 없음 / 0 / 0 |
| 마지막 fresh sample 처리 시간 | 약 112.43ms. 일반 성능·Pi 성능으로 확대하지 않음 |
| 모든 sample의 armed | false |
| 실제 송신 패킷 / 텔레메트리 | 모두 null |
| 모드 | SIMULATION |

카메라·추론 worker, Qt event loop, 상태 표시용 스냅샷, JSONL writer, 자동 종료의 실제 경로를 확인했다. 이 검사는 사람이 손 제스처를 반복 제시하며 정답을 비교한 시험이 아니다. 첫 12초 검사에서는 유효 영상 결과가 없어 성공 판정하지 않았고, 이후 30초 검사에서 최신 결과를 확인했다. 모델 초기화 시간은 변동될 수 있다.

## 화면 확인

`dashboard_preview/`의 dashboard / vision / motion / test_log / small PNG는 **합성 미리보기**다. 실제 촬영 영상이나 모터 피드백을 넣지 않았다. 1320x920과 960x700에서 카메라 우선 배치·3단계 제스처·스크롤·미수신 값·NOT TESTED 기본 상태를 확인했다. 미리보기의 Open Palm 0.92는 합성 테스트 값이며 실제 인식 성능으로 보고하지 않는다.

Qt의 제한된 오프스크린 Windows 실행에서 font database가 비어 글자가 사각형으로 보이는 현상을 확인했다. 시스템에 이미 있는 Segoe UI를 직접 등록하는 보정 후 읽을 수 있는 렌더를 확인했다. 글꼴 파일을 프로젝트에 복사하거나 배포하지 않았다.

## 안전 관련 원인과 수정

- 기존 긴급 정지는 속도와 armed를 해제했지만 다음 프레임의 목표 상실 사유가 표시 상태를 덮을 수 있었다. 명시 ARM 성공 전까지 유지하는 emergency latch를 추가했다.
- 기존 UdpClient.arm은 최신 STOPPED만 검사하여, 새 STOP 직전에 받은 이전 STOPPED를 재사용할 수 있었다. 새 정지 요청 이후 송신한 STOP seq와 그 이후의 fresh STOPPED 응답을 요구하도록 수정했다. 실패 재현 후 수정한 회귀 테스트가 통과했다.
- 기존 Raw 결과는 목표/다인 필터 이후에만 UI에 전달되었다. 이제 Raw는 먼저 기록하되 제어 입력 차단은 유지한다.
- 기존 OpenCV 루프는 추론 중 입력 처리가 지연되었다. Qt UI와 최신 슬롯 기반 camera/vision worker를 분리하고 독립 UDP freshness 가드를 유지했다.

송신 성공은 수신·모터 성공이 아니며, seq 매칭도 지상 방향 검증이 아니다. `cmd_vx/cmd_vy/cmd_w`는 firmware desired 값이고, 제한 이후 body 명령은 현재 보고되지 않는다. 기존 firmware는 수정하지 않았다. 원본 보존과 credential 미복사 검사는 기존 테스트에서 계속 통과했다.

## 실제 검증 상태

| 항목 | 상태 |
|---|---|
| 실제 카메라→YOLO/MediaPipe 호출→UI snapshot→로그 | 짧은 실행 확인 |
| 단일 인물 4종 제스처 반복 정확도·오인식률·실제 적용 연결 | NOT TESTED |
| 다인 가림·ID swap·실제 손 소유자 정확도 | NOT TESTED, 다인 제스처 차단 유지 |
| 실제 ESP32 연결·fault·송신/응답·STOP 확인 | NOT TESTED; 자동 검사는 fake socket/telemetry |
| 띄운 바퀴 방향·목표/측정 RPM·엔코더·정지 반응 | NOT TESTED |
| 본체 지상 이동·전후/회전 부호·정지 거리 | NOT TESTED |
| 후방 레이더·전방 충돌 회피·Pi 카메라/성능 | 범위 밖 / NOT TESTED |
| 새 PC 전체 설치 | NOT TESTED |

수동 검증 표는 자동 검사 결과로 채우지 않았다. 실제 시험은 UI에서 수행 확인과 메모를 남긴 뒤 별도로 기록해야 한다. 하드웨어 시험 전 배선·물리 비상 차단·한 명의 감독자·통제 공간·저속 제한을 확인하고, 띄운 바퀴부터 단계적으로 진행한다. 자동 후진은 계속 차단한다.
