# 레이더 연결 및 실행 방법

Raspberry Pi는 레이더 상태를 PC의 UDP `8891`로 보냅니다. PC 중앙 대시보드가 이 상태를 받아 ESP32 제어와 정지를 처리합니다. Pi에는 프로젝트의 [`pi_radar/publisher.py`](../pi_radar/publisher.py)와 장비에서 사용하는 `.cfg`가 필요합니다.

## 1. 발행자 복사

1. Pi에서 실행 중인 `corki_7.py` 또는 이전 `publisher.py`를 `Ctrl+C`로 종료합니다. 두 프로그램이 같은 UART를 사용하고, `corki_7.py`는 ESP32에 직접 명령을 보냅니다.
2. PC와 Pi를 같은 로컬 네트워크에 연결합니다. 아래 예시의 `192.168.1.20`을 Pi의 실제 IPv4로 바꿉니다. Pi 로그인 계정과 폴더가 다르면 `dig`와 `radar_test_new`도 바꿉니다.
3. Windows PowerShell에서 저장소 루트로 이동해 복사합니다.

   ```powershell
   scp .\pi_radar\publisher.py dig@192.168.1.20:~/radar_test_new/
   ```

   처음 접속할 때 호스트 키 확인이 나오면 Pi 터미널에서 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`를 실행합니다. Windows에 표시된 지문과 일치하면 연결을 수락합니다.

## 2. Pi에서 발행자 실행

`~/radar_test_new/`에 `publisher.py`와 `profile_3d_aop_evade_040m_fov30.cfg`를 함께 둡니다. Python에 `serial`이 없으면 Raspberry Pi OS에서 `sudo apt install python3-serial`로 설치합니다.

Pi 터미널에서 실행합니다. `192.168.1.30`은 **PC의 실제 IPv4**로 바꿉니다. Windows에서 `ipconfig`를 실행하면 Pi와 통신하는 Wi-Fi 또는 이더넷 어댑터의 IPv4를 확인할 수 있습니다.

```bash
cd ~/radar_test_new
python3 publisher.py --pc 192.168.1.30 --port 8891 --profile ./profile_3d_aop_evade_040m_fov30.cfg
```

기본 UART는 CLI `/dev/ttyUSB0` 115200 baud, 데이터 `/dev/ttyUSB1` 921600 baud입니다. 실제 장치 경로가 다를 때는 `--cli-port`와 `--data-port`로 지정합니다. 실행 중 터미널이 조용할 수 있으므로 수신 여부는 대시보드에서 확인합니다.

### `29/30 bpmCfg`에서 멈출 때

테스트 장비에서 `Radar CLI did not acknowledge 29/30 bpmCfg (last reply b'mmwDemo:/>')`가 반복됐습니다. 이 경우 발행자가 다음 명령인 `sensorStart`를 보내기 전에 중단합니다. `bpmCfg -1 0 0 0`은 BPM을 끄는 명령이며, 일부 HWA 기반 데모에서는 이 명령이 필요하지 않습니다. [TI mmWave SDK 가이드](https://dr-download-cdn.ti.com/software-development/software-development-kit-sdk/MD-PIrUeCYr3X/03.06.00.00-LTS/mmwave_sdk_user_guide.pdf#page=32)

이 오류가 재현된 프로파일에서만 아래 복사본을 시험합니다. `Ctrl+C`로 발행자를 종료한 다음, Pi 터미널에서 실행합니다.

```bash
cd ~/radar_test_new
cfg=profile_3d_aop_evade_040m_fov30.cfg
trial=profile_3d_aop_evade_040m_fov30_no_bpm.cfg
sed -E 's/^(bpmCfg -1 0 0 0)[[:space:]]*$/% \1/' "$cfg" > "$trial"
diff -u "$cfg" "$trial"
```

`diff`에서 `bpmCfg -1 0 0 0`이 `% bpmCfg -1 0 0 0`으로 바뀐 한 줄을 확인합니다. `sensorStart`는 남겨둡니다. `--profile`에 **`_no_bpm.cfg`로 끝나는 파일**을 지정합니다.

```bash
python3 publisher.py --pc 192.168.1.30 --port 8891 --profile ./profile_3d_aop_evade_040m_fov30_no_bpm.cfg
```

다른 CLI 명령에서 오류가 나면 발행자가 출력한 명령 이름과 마지막 응답을 기록하고, 장치의 펌웨어 버전에 맞는 프로파일을 확인합니다. `_no_bpm.cfg`로 실행한 뒤의 실물 상태 전환은 아직 확인되지 않았습니다.

## 3. PC 대시보드에서 수신 확인

저장소 루트에서 대시보드를 엽니다.

```powershell
.\.venv\Scripts\python.exe -m python_controller --ui dashboard
```

1. **ESP32 연결** 영역에서 **레이더 Pi**에 Pi의 실제 IPv4를 입력하고 **Pi 주소 저장**을 누릅니다. 주소를 고쳐야 할 때는 정지 후 ESP32 연결을 해제하고 변경합니다. 설정은 PC의 로컬 `settings/radar.json`에 저장됩니다.
2. 기존 ESP32 주소로 연결한 다음 **모터가 정지한 상태**에서 레이더가 `CLEAR → EVADE → CLEAR`로 전환되는지 확인합니다. 센서 앞을 비운 뒤 장애물을 감지 범위에 놓았다가 치웁니다.
3. 첫 연결·위험 차단·소프트웨어 긴급 정지 후에는 운영자 화면이 활성 상태이고 최신 `CLEAR`가 연속 1초 유지되며 새 ESP32 STOP·정상 세션이 확인되면 차단을 자동 해제합니다. 이전 수동 입력·추종·미션은 재개하지 않습니다. 방향을 다시 누르거나 손동작을 새로 활성화하거나 현재 미션을 다시 승인하세요. 위험·UNKNOWN·잘못된 패킷 및 지연된 CLEAR는 이 1초를 충족하지 않습니다.
4. `RADAR_TIMEOUT`은 수신 지연 경고입니다. 마지막 레이더 상태·경과 시간을 유지하고 수신을 계속하며 단순 지연만으로 새 차단을 만들지 않습니다. 이미 EVADE·UNKNOWN 등으로 차단된 상태는 유지합니다.
5. ESP32 통신 지연은 새 정상 STOP 수신 후 해제합니다. 프로토콜 2의 `ENCODER_JUMP`/`ENCODER_INVALID`는 출력 STOP과 정상 엔코더 샘플 10개를 확인하면 기존 오류 초기화·STOP 재동기화를 한 번 시도합니다. 실제 Fault 해제와 새 STOP을 기다리며, 지속 오류·미지원 Fault·보드 불일치·제어기 충돌·Python 오류를 자동으로 무시하지 않습니다.

### 레이더를 연결하지 않은 바퀴 시험

세 바퀴를 바닥에서 띄워 고정한 경우에만 Qt의 벤치 옵션을 사용합니다. 다음은 브레드보드 펌웨어를 탑재한 장치의 실행 예시입니다. PCB V19 장치는 `PCB_V19`를 지정합니다.

```powershell
.\run_simulation.bat --hardware-profile BREADBOARD --radarless-bench
```

ESP32 연결 후 보드 프로필 일치와 STOP 확인을 기다립니다. **수동 제어 → 바퀴를 띄워 고정했습니다 · 벤치 시험 허용**을 선택하고 방향 버튼을 누릅니다. 입력 명령은 최대 `1.0 cm/s`, `0.10 rad/s`이며 한 번에 최대 2초입니다. 버튼을 놓거나 창에서 벗어나면 STOP을 요청합니다. 레이더가 수신되기 시작하면 벤치 허용을 해제합니다. 같은 연결에서 레이더가 다시 끊겨도 벤치로 전환하지 않습니다.

손동작, 추종, 계획 실행, 시간 지정 시험은 벤치 옵션에서 차단합니다. 일반 운용 시 `--radarless-bench` 없이 Qt를 시작합니다.

| 표시 | 의미와 확인할 사항 |
| --- | --- |
| `RADAR_NOT_CONFIGURED` | 레이더 Pi 주소를 저장합니다. |
| `RADAR_NO_DATA` | 설정한 Pi에서 아직 유효한 상태 패킷을 받지 못했습니다. `publisher.py` 실행 여부, `--pc`의 PC 주소, Pi 출발지 IPv4, PC의 UDP `8891` 수신 허용 여부를 확인합니다. |
| `RADAR_SENSOR_UNKNOWN` | Pi 패킷은 받았으나 센서 상태가 유효하지 않습니다. Pi 터미널의 UART 오류와 설정 명령 응답을 확인합니다. |
| `RADAR_TIMEOUT` | 수신 지연 경고입니다. 마지막 상태와 경과 시간을 유지하며 계속 수신합니다. 시간 초과만으로 새 차단은 만들지 않고 이전 차단은 유지합니다. 발행자와 네트워크를 확인합니다. |
| `EVADE` | 장애물이 감지되어 이동이 차단되고 현재 동작과 남은 LLM 계획이 취소됩니다. |
| `CLEAR` | 유효한 레이더 프레임에서 장애물 없음이 확인됐습니다. 수신 사유와 ESP32 STOP 응답을 확인합니다. |
| `SESSION_SYNC_WAITING_STOP` | 이전 세션의 정지 응답이 지연되고 있습니다. 현재 세션의 새 STOP 응답을 기다리는 동안 이동은 차단됩니다. |
| `SESSION_CONFLICT` | 현재 세션과 충돌하는 새 텔레메트리가 감지됐습니다. 다른 송신기를 종료한 뒤 진단에서 STOP 전용 재동기화를 요청하세요. |

[레이더 송신 규격과 검증 범위](RADAR_LLM_INTEGRATION.md)
