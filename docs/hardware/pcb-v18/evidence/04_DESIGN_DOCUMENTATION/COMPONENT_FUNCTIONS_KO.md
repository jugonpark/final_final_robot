# 부품 역할과 실제 위치

좌표는 KiCad 절대 원점(mm), X 오른쪽/Y 아래쪽이며 외곽은 (20,20)~(119.8,119.8)이다. U1/MD1/MD2는 두 개의 소켓을 하나의 CAD 참조번호로 표현한다. BOM의 모듈 본체·소켓·오프보드 물품을 중복 구매하지 않도록 구분한다. PCB 패드 네트 1:1 보존은 `05_VALIDATION/PAD_NET_PARITY.csv`에 기록했다.

| 참조 | 값 | 중심_XY_mm | 회전_deg | 기능 | 패드네트 |
|---|---|---|---|---|---|
| C1 | 100nF | [24.0, 82.0] | 90.0 | VBAT_ADC 100nF 저역통과/입력 평활. | 1=/VBAT_ADC; 2=/GND |
| C10 | 100nF | [31.0, 106.0] | 90.0 | 5V_ESP 100nF 바이패스. | 1=/5V_ESP; 2=/GND |
| C2 | 100nF | [96.3, 46.0] | 90.0 | MD1 VM 100nF 바이패스. | 1=/VM; 2=/GND |
| C3 | 100nF | [96.3, 76.0] | 90.0 | MD2 VM 100nF 바이패스. | 1=/VM; 2=/GND |
| C4 | 470uF / 16V | [73.0, 38.0] | 90.0 | VM 470uF/16V 벌크 전해 커패시터. | 1=/VM; 2=/GND |
| C5 | 470uF / 16V | [62.0, 89.0] | -90.0 | VM 470uF/16V 추가 벌크 전해 커패시터. | 1=/VM; 2=/GND |
| C6 | 100nF | [96.3, 53.0] | 0.0 | MD1 3V3 로직 100nF 바이패스. | 1=/3V3; 2=/GND |
| C7 | 100nF | [96.3, 83.0] | 0.0 | MD2 3V3 로직 100nF 바이패스. | 1=/3V3; 2=/GND |
| C8 | 100uF / 16V | [55.0, 113.5] | 0.0 | 5V_ESP 100uF/16V 벌크 전해 커패시터. | 1=/5V_ESP; 2=/GND |
| C9 | 10uF / 25V | [66.0, 103.5] | 0.0 | VM 10uF/25V MLCC, Buck 입력 측 완충. | 1=/VM; 2=/GND |
| F1 | MINI HOLDER +5A CANDIDATE | [100.0, 27.0] | 0.0 | BAT_RAW와 VM 사이 직렬 퓨즈 홀더. 5A 삽입물은 후보이며 보호협조 승인 아님. | 1=/BAT_RAW; 1=/BAT_RAW; 2=/VM; 2=/VM |
| H1 | 장착홀 | [25.5, 25.5] | 0.0 | 3.2mm NPTH 장착 후보. 최대 외경7mm 체결부 가정, R4 구리 제한. 하판 호환성 미확정. | = |
| H2 | 장착홀 | [114.3, 37.0] | 0.0 | 3.2mm NPTH 장착 후보. 최대 외경7mm 체결부 가정, R4 구리 제한. 하판 호환성 미확정. | = |
| H3 | 장착홀 | [25.5, 114.3] | 0.0 | 3.2mm NPTH 장착 후보. 최대 외경7mm 체결부 가정, R4 구리 제한. 하판 호환성 미확정. | = |
| H4 | 장착홀 | [114.3, 93.35] | 0.0 | 3.2mm NPTH 장착 후보. 최대 외경7mm 체결부 가정, R4 구리 제한. 하판 호환성 미확정. | = |
| JBAT1 | XT60PW-M MALE FUSED INPUT | [84.0, 38.0] | 0.0 | XT60PW-M 수형 배터리 입력. 1=BAT_RAW 양극, 2=GND. 정확한 납품 리비전·삽탈 여유 확인 필요. | =; =; 1=/BAT_RAW; 2=/GND |
| JBK1 | BUCK SOCKET CANDIDATE DO NOT FIT | [63.0, 109.0] | 0.0 | Buck용 1x4 탈착 소켓. 위→아래 EN/IN+/GND/VO+. EN 패드는 의도적 미연결; 실제 오픈 동작 미확정. | 1=unconnected-(JBK1-EN_NC_VERIFY-Pad1); 2=/VM; 3=/GND; 4=/5V_BUCK |
| JM1 | MOTOR 6P 2.54 MALE | [111.0, 48.0] | 0.0 | 모터/Hall 통합 6핀 수헤더. 1=M+,2=M-,3=GND,4=3V3,5=A,6=B. M-는 GND 아님. | 1=/M1_OUT_P; 2=/M1_OUT_N; 3=/GND; 4=/3V3; 5=/ENC1_A; 6=/ENC1_B |
| JM2 | MOTOR 6P 2.54 MALE | [111.0, 74.0] | 0.0 | 모터/Hall 통합 6핀 수헤더. 1=M+,2=M-,3=GND,4=3V3,5=A,6=B. M-는 GND 아님. | 1=/M2_OUT_P; 2=/M2_OUT_N; 3=/GND; 4=/3V3; 5=/ENC2_A; 6=/ENC2_B |
| JM3 | MOTOR 6P 2.54 MALE | [111.0, 100.0] | 0.0 | 모터/Hall 통합 6핀 수헤더. 1=M+,2=M-,3=GND,4=3V3,5=A,6=B. M-는 GND 아님. | 1=/M3_OUT_P; 2=/M3_OUT_N; 3=/GND; 4=/3V3; 5=/ENC3_A; 6=/ENC3_B |
| JP1 | EXTERNAL 5V DISCONNECT | [52.0, 106.8] | 0.0 | 5V_BUCK와 5V_ESP를 수동 분리하는 2핀 점퍼. USB 사용 시 제거. 역류차단 소자 아님. | 1=/5V_BUCK; 2=/5V_ESP |
| MD1 | TB6612 #1 - M1/M2 | [80.5, 58.0] | 0.0 | TB6612FNG 모듈용 2열 소켓. MD1=M1/M2, MD2=M3와 미사용 채널. 본체·소켓 높이 미확정. | 1=/M1_PWM; 2=/M1_IN2; 3=/M1_IN1; 4=/STBY; 5=/M2_IN1; 6=/M2_IN2; 7=/M2_PWM; 8=/GND; 9=/VM; 10=/3V3; 11=/GND; 12=/M1_OUT_P; 13=/M1_OUT_N; 14=/M2_OUT_N; 15=/M2_OUT_P; 16=/GND |
| MD2 | TB6612 #2 - M3 | [80.5, 88.0] | 0.0 | TB6612FNG 모듈용 2열 소켓. MD1=M1/M2, MD2=M3와 미사용 채널. 본체·소켓 높이 미확정. | 1=/M3_PWM; 2=/M3_IN2; 3=/M3_IN1; 4=/STBY; 5=/GND; 6=/GND; 7=/GND; 8=/GND; 9=/VM; 10=/3V3; 11=/GND; 12=/M3_OUT_P; 13=/M3_OUT_N; 14=unconnected-(MD2-BO2-Pad14); 15=unconnected-(MD2-BO1-Pad15); 16=/GND |
| R1 | 330k 1% | [64.599999, 45.5] | 180.0 | 배터리 ADC 상단 330k 분압 저항. VM 측 공급을 VBAT_ADC로 낮춤. | 1=/VM; 2=/VBAT_ADC |
| R2 | 100k 1% | [24.0, 77.0] | 0.0 | VBAT_ADC 하단 100k 분압 저항. R1과 함께 분압비 약0.23256. | 1=/VBAT_ADC; 2=/GND |
| R3 | 47k 1% | [60.0, 70.0] | 0.0 | STBY 신호의 47k 풀다운. 드라이버 모듈 내장 풀업·부팅 상태는 별도 확인. | 1=/STBY; 2=/GND |
| RP1 | 10k 1% | [24.1, 62.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC1_A |
| RP2 | 10k 1% | [24.1, 66.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC1_B |
| RP3 | 10k 1% | [24.1, 54.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC2_A |
| RP4 | 10k 1% | [24.1, 58.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC2_B |
| RP5 | 10k 1% | [59.0, 80.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC3_A |
| RP6 | 10k 1% | [59.0, 76.0] | 0.0 | 엔코더 A/B 입력의 외부 10k 풀업. RP1/2=M1, RP3/4=M2, RP5/6=M3. | 1=/3V3; 2=/ENC3_B |
| TP1 | 5V_BUCK | [24.0, 103.5] | 0.0 | 전원 확인용 테스트 패드. 이번에 실제 전압 측정하지 않음. | 1=/5V_BUCK |
| TP2 | GND | [24.0, 108.0] | 0.0 | 전원 확인용 테스트 패드. 이번에 실제 전압 측정하지 않음. | 1=/GND |
| TP3 | 5V_ESP | [57.0, 107.0] | 0.0 | 전원 확인용 테스트 패드. 이번에 실제 전압 측정하지 않음. | 1=/5V_ESP |
| TP4 | VM | [98.0, 39.0] | 0.0 | 전원 확인용 테스트 패드. 이번에 실제 전압 측정하지 않음. | 1=/VM |
| U1 | ESP32 DevKit 38P | [41.5, 75.0] | 0.0 | 38핀 ESP32 개발보드용 2열 소켓. 제어·PWM·엔코더 입력·ADC. 모듈 본체는 탈착형 별도 구매. | 1=/3V3; 2=unconnected-(U1-EN-Pad2); 3=/ENC2_A; 4=/ENC2_B; 5=/ENC1_A; 6=/ENC1_B; 7=/VBAT_ADC; 8=unconnected-(U1-GPIO33-Pad8); 9=/M1_PWM; 10=/M2_PWM; 11=/STBY; 12=/M3_PWM; 13=unconnected-(U1-GPIO12-Pad13); 14=/GND; 15=/M3_IN2; 16=unconnected-(U1-FLASH_D2-Pad16); 17=unconnected-(U1-FLASH_D3-Pad17); 18=unconnected-(U1-FLASH_CMD-Pad18); 19=/5V_ESP; 20=/GND; 21=/M3_IN1; 22=/M2_IN2; 23=unconnected-(U1-UART0_TX-Pad23); 24=unconnected-(U1-UART0_RX-Pad24); 25=/M2_IN1; 26=/GND; 27=/M1_IN2; 28=/M1_IN1; 29=unconnected-(U1-GPIO5-Pad29); 30=/ENC3_B; 31=/ENC3_A; 32=unconnected-(U1-GPIO4-Pad32); 33=unconnected-(U1-GPIO0-Pad33); 34=unconnected-(U1-GPIO2-Pad34); 35=unconnected-(U1-GPIO15-Pad35); 36=unconnected-(U1-FLASH_D1-Pad36); 37=unconnected-(U1-FLASH_D0-Pad37); 38=unconnected-(U1-FLASH_CLK-Pad38) |

SMT는 저항9개(R1~R3,RP1~RP6)+커패시터10개(C1~C10)=19개다. 공장 THT 검토 대상은 CAD10개 참조/실물13개이며 2대 조립 시26개다. ESP32 본체1/TB 모듈2/Buck 본체1, 모터·배터리·퓨즈 삽입물·점퍼캡·M3 부품은 SMT19개에 포함되지 않는다. LCSC 번호는 유통사 식별자이고 제조사 MPN은 제조사 제품 식별자다. 후보 번호가 기재돼 있어도 소켓/리비전/공장 실장 승인을 뜻하지 않는다.
