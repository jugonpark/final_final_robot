# D.I.G. 하드웨어 버전 맵 — 반드시 먼저 읽기

> **사용자 확정 구분:** 브레드보드 버전과 PCB 버전은 서로 다르다. **동일 부품·동일 핀배치·동일 배선·동일 전원 회로라고 가정하지 않는다.**
>
> 이 문서는 자료 구분이며 실물 장착 증명이 아니다. 버전 미지정 질문에는 먼저 어느 버전인지 확인하거나 **각 버전을 명시해 비교**한다.

| 하드웨어 ID | 자료 범위 | 증거 | 상태 | 무엇을 의미하나 |
|---|---|---|---|---|
| `BREADBOARD_PROTO` | 사용자가 제공한 9항목 BOM 캡처 | `BREADBOARD_BOM_from_image.csv`, 원본 PNG | 사용자 제공 목록; 실물 배선과 장착은 부분 미확인 | **브레드보드 시제품 측 BOM**으로 분리 관리. PCB 제조 BOM으로 절대 취급하지 않음 |
| `PCB_V18_DESIGN` | 별도 KiCad PCB 설계 | 원본 PCB AUDIT ZIP의 `V18_FINAL_RELEASE/` | **QUOTE_ONLY, FAB_APPROVED=false** | 99.8×99.8mm/2층 설계, 제작 적합 정적검사와 실물 검증 별개 |
| `PCB_V19_DESIGN` | 현행 PCB 설계 | `FINAL_PROJECT_V1.zip`의 회로도·PCB·패드 표 | **FAB_APPROVED=false** | 생산과 실물 체결 확인이 필요한 설계 기준 |
| `SOFTWARE_COMMON` | Windows Python / Qt / web / RobotSession / Agent | GitHub `jugonpark/final_robot` | 코드/논리 통합 경로 | 어떤 실물 PCB가 연결되어 있는지는 별도 확인 |
| `VENDOR_3WD_REFERENCE` | 판매처 `Omnidirectional wheel trolley 3WD datasheet.pdf` | 사용자 원본 PDF | 제조사 안내자료 | 판매처 옵션·예시 부품을 현재 구성으로 승격 금지 |

## 지켜야 할 문장

- 브레드보드 부품: “**제공된 브레드보드 BOM에는** TB6612FNG `SZH-MDBL-00` 2개, PCA9685 `TS0185` 1개, MG90S 4개가 기재되어 있다.”
- PCB 부품: “**PCB V18 설계에는** TB6612FNG `HW166` 형상의 탈착 모듈용 소켓이 계획되어 있고, MD1이 M1/M2, MD2가 M3를 다룬다.”
- 금지: “두 버전의 TB6612 모듈은 똑같은 핀 배열이다.” (증거 없음)
- 금지: “PCB가 완성되어 로봇이 실물 주행 중이다.” (현재 감사 결과로는 판단 불가)
- 구형 Pi ESP32 직접 송신/레이더 위험 중 전진 허용은 현행 중앙제어의 안전 원칙과 다르므로 현행 적용을 주장하지 않음.

## RAG 우선순위

1. 질문에 `브레드보드`, `빵판`, `BREADBOARD` → `BREADBOARD_PROTO`만 직접 설명.
2. `PCB V18`, `V18`, `회로판 V18` → `PCB_V18_DESIGN`.
3. `PCB V19`, `V19`, `현행 PCB 설계` → `PCB_V19_DESIGN`.
4. 버전 없는 일반 핀·결선·부품 질문 → 어느 버전 기준인지 확인. 여러 버전을 명시한 비교 질문에는 버전별 출처 제공.
5. `현재 레이더 상태`·`모터 움직임` 등 live 질문 → 런타임 상태 Tool.

## 프로젝트 파일

- 브레드보드 · [BOM 설명](breadboard/BOM.md), [9항목 CSV](breadboard/BOM.csv), [원본 이미지](breadboard/bom_original.png)
- PCB V18 · [설계 설명](pcb-v18/DESIGN.md), [설계 BOM](pcb-v18/evidence/03_BOM_AND_ASSEMBLY/BOM_FULL_MASTER.csv), [CAD 핀·넷](pcb-v18/evidence/04_DESIGN_DOCUMENTATION/COMPONENT_FUNCTIONS_KO.md), [제작 승인 감사](pcb-v18/evidence/05_VALIDATION/MANUFACTURING_APPROVAL.json)
- PCB V19 · [설계 근거](pcb-v19/FACTS.md)
- 보드별 펌웨어 · [프로필 선택과 기준 핀맵](HARDWARE_PROFILES.md), [16개 신호 비교](HARDWARE_PROFILE_PINMAP_COMPARE.csv)
- 공통 소프트웨어 · [기술 설명 v0.2 검토본](review/DIG_TECHNICAL_v0.2.md)

첨부 [출처 매니페스트](SOURCE_MANIFEST.json)의 SHA-256과 선별 발췌를 대조했다. PCB 원본 감사 ZIP은 첨부 패키지에 보존돼 있다. 저장소에는 위에 연결된 근거 발췌와 버전별 로컬 검색 문서만 둔다.
