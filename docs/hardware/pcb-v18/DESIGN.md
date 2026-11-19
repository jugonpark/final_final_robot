# PCB V18 설계 — 브레드보드와 별개의 구성

- **variant_id** `PCB_V18_DESIGN`
- **정본** `omni_3wd_v18_final_FAB_AUDIT.zip` 내부 `omni_3wd_v18_final_FAB_AUDIT/V18_FINAL_RELEASE/`
- **주의** 원본 아카이브 루트의 `kicad/`, `reports/`, `manufacturing/`는 **구버전/감사 이력**이다. V18 설명·KiCad 정본은 `V18_FINAL_RELEASE/`에서 읽는다.

## 정적 설계 사실

- 크기 99.8 × 99.8 mm, 2층 PCB.
- 주요 모듈: ESP32 DevKit **38핀 탈착형**, TB6612FNG **HW166 형태의 2개 모듈 소켓**(MD1: M1/M2, MD2: M3), Buck 1개 후보.
- 배터리 입력 XT60PW-M 수형 `JBAT1`, 직렬 퓨즈 홀더 `F1` (실제 보호 적합성과 정품 리비전은 미승인), `JP1` 외부 5V 분리 점퍼.
- M1~M3 각각 6핀 모터/홀 엔코더 연결 (`JM1`/`JM2`/`JM3`). 주행 모터 3개/홀 센서와 결합하는 **설계 경로**이지 실물 결선 확인은 아님.
- 회로에는 저항 9개·커패시터 10개 = SMT 19개. THT는 CAD 10 참조/실물 13개. 서보 MG90S/PCA9685/브레드보드 터미널 블록은 PCB V18 주요 CAD 구성에 명시되지 않음.
- 최신 V18 릴리스 검사 보고: ERC 오류 0, DRC 오류 0, 미연결 0, 회로도 패리티 0. **이는 정적 검증이며 제작·실물 적합 통과가 아님**.
- `H1~H4`의 3.2mm NPTH 장착홀. `H2/H4`는 모서리가 아닌 부품 사이 후보로 하판 결합 확인 필요.

## 제작 승인 상태 (중요)

- **`FAB_APPROVED=false` / `BLOCKED` / `QUOTE_ONLY`**.
- 미해결 위험: 모터 지속 스톨과 TB6612 전류·퓨즈·배선 보호 협조(`A08 FAIL`), Buck EN/보드/모듈 실제 호환성, 전원과 열, XT60 극성·푸트프린트 리비전, 실제 하판 장착/안테나 RF/공장 CAM 등.
- `ERC0`, `DRC0`만으로 안전 제작 승인·전원인가·모터 구동 허가를 추론하지 않는다.
- PCB 핀배열은 `V18_FINAL_RELEASE/04_DESIGN_DOCUMENTATION/COMPONENT_FUNCTIONS_KO.md`와 `V18_FINAL_RELEASE/03_BOM_AND_ASSEMBLY/`로 확인. **브레드보드 `SZH-MDBL-00` 핀배열을 복사하지 않음**.

## 출처 (아카이브 상대경로)

- `V18_FINAL_RELEASE/README_KO.md`
- `V18_FINAL_RELEASE/05_VALIDATION/MANUFACTURING_APPROVAL.json`
- `V18_FINAL_RELEASE/05_VALIDATION/BLOCKED_ITEMS.md`
- `V18_FINAL_RELEASE/05_VALIDATION/FINAL_FAB_AUDIT_KO.md`
- `V18_FINAL_RELEASE/04_DESIGN_DOCUMENTATION/COMPONENT_FUNCTIONS_KO.md`

**실물 제작/전원인가/동작/EMI/RF/체결 적합 시험은 확인되지 않음.**
