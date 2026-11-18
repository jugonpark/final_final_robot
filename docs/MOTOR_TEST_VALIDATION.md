# ESP32 수동 모터 시험 기능 검증 — 2026-11-01

## 자동 검사

기존 47개를 유지하고 **17개를 추가해 총 64개 테스트가 통과**했다. `unittest discover -s tests -v`와 `compileall -q python_controller scripts tests`는 exit 0이었다. 실제 출력은 [motor_test_unit_tests.txt](motor_test_unit_tests.txt)에 있다. 새 라이브러리 설치나 펌웨어 빌드·업로드는 수행하지 않았다.

새 검사 범위:

- 새 STOP 증명 전 heartbeat 금지, 정지 후 heartbeat만 송신, 연결만으로 cmd_vel 전송 금지.
- 바퀴 띄움/별도 승인/정지 증명/완전한 휠 피드백 요구. 일반 arm/자동 추종 submit으로 수동 시험 활성화 불가.
- 여섯 방향 SLOW 속도, 유효 duration, 독립 deadline 이후 STOP, 시험 후 재승인.
- UI 생존 lease 만료, 텔레메트리 지연, fault, 긴급 정지, 재부팅/잘못된 session/seq 처리.
- 단순 송신·텔레메트리 seq/state/echo 처리 증명·encoder 방향 관측·육안 결과 분리.
- 반대 부호/변화 미관측/회전 대상이 아닌 휠/명령 echo 부재, signed counter wrap.
- 프로그램 종료 STOP 시도와 정지 미검증 기록, 완료 trial의 육안 메모 조건, IP의 로컬 저장·잘못된 주소 거부.
- 실제 Qt 버튼의 별도 승인 흐름, 하드웨어 시험 중 추종 차단, 긴급 정지 후 이동 버튼 비활성화.
- 카메라 없는 벤치 명령의 유한 JSONL 기록, MOCK_UDP source 및 NOT TESTED 물리 결과 보존.

모든 새 이동·UDP 검사는 **fake socket / Mock 텔레메트리**다. 테스트 중 실제 ESP32 주소나 네트워크로 cmd_vel을 송신하지 않았다. Python 메모리 안의 fake sendto에 생성된 JSON을 관찰했다. 자동으로 실제 모터를 구동하는 검사는 실행하지 않았다.

## UI 실행·화면

`--ui dashboard --dashboard-preview --smoke-seconds 2`를 오프스크린 Qt에서 실행해 exit 0으로 종료했다. 별도 렌더에서 MOTOR TEST 탭을 1360x1000 및 960x700으로 확인했고 모든 이동 버튼의 disabled 상태를 검사했다. [motor_test_preview.png](motor_test_preview.png), [motor_test_small.png](motor_test_small.png)는 SYNTHETIC_PREVIEW 화면이며 모터 피드백은 전부 UNKNOWN이다. 합성 제스처나 UI 렌더를 실제 통신·회전 검증으로 사용하지 않는다.

실제 Qt 버튼 검사는 fake controller로 연결 승인 → STOP/수신 증명 → 바퀴 띄움/1회 승인 → 방향 버튼 → 긴급 정지 순서를 확인했다. 카메라/인식 알고리즘은 이번 단계에서 수정하지 않았다. 이전 실제 웹캠 결과와 사용자의 정상 동작 확인은 실제 ESP32 검증과 구분한다.

## 프로토콜 및 보존

현재 firmware의 UDP 8888/8889, telemetry 200ms, watchdog 300ms, SLOW 5cm/s·0.5rad/s, 반지름 9.0/2.9cm와 운동학을 재확인했다. 별도 ACK 패킷은 없다. 기존 session/seq/boot/uptime/숫자·배열 검사와 출력 제한을 재사용했다. `acknowledged_command`는 이전 47개 테스트/호출자 호환용 seq 매칭 별칭이고 현재 UI는 ACK 성공으로 표시하지 않는다.

펌웨어 파일·원본 ZIP·모델 파일과 기존 47개 테스트 파일은 이번 작업에서 수정하지 않았다. 기존 provenance/credential 미복사 검사도 통과했다. 피드포워드·PID·엔코더 ISR·가속도·ESP32 워치독은 변경하지 않았다.

수동 시험은 기존 카메라 freshness와 별개로 GUI 생존 lease를 사용한다. 수동 명령의 camera frame 시각은 null이며 operator lease 시각을 별도로 남긴다. 영상이 없다는 이유로 허위 프레임 시간을 생성하지 않는다. source가 Mock인 encoder 관측은 하드웨어 PASS로 승격하지 않는다.

## 실제 하드웨어 상태

| 항목 | 결과 |
|---|---|
| 실제 ESP32 IP/같은 Wi-Fi 연결 | NOT TESTED |
| 실제 STOP/heartbeat 수신 및 session/last_seq/state/fault | NOT TESTED |
| 실제 3개 모터 목표·측정 cm/s/RPM/PWM/encoder | NOT TESTED |
| 모터/엔코더 물리 방향·MOTOR_STALL 원인 | NOT TESTED |
| 실제 시간 만료·통신 단절·긴급 정지 반응 | NOT TESTED |
| 띄운 바퀴 시험·지상 이동·정지 거리 | NOT TESTED |
| Pi·레이더 | 범위 밖 / NOT TESTED |

실제 시험은 [MOTOR_TEST_GUIDE.md](MOTOR_TEST_GUIDE.md)의 순서로 사용자가 수행해야 한다. 초기에는 세 바퀴를 띄우고 먼저 통신만 확인한다. 실제 telemetry 처리 증명과 encoder 관측이 확보되어도 본체 방향·지상 주행 성능·충돌 안전까지 입증하지 않는다. 하드웨어 사람 추종 출력 연결은 이 단계에서 계속 차단된다.
