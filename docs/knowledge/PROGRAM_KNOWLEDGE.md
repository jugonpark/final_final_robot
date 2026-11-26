# D.I.G. 프로그램 지식

로컬 구조화 지식 초안. 사람 검토 완료가 아니며, 자유 생성의 의미 검증 결과도 아닙니다. 원문 SHA와 적용 시점을 확인한 항목만 제공합니다.

```json
{
  "schema_version": 1,
  "human_approved": false,
  "review_status": "agent_source_checked_human_review_pending",
  "facts": [
    {
      "key": "purpose",
      "topic": "purpose",
      "text": "D.I.G.는 3륜 로봇의 상태를 확인하고 이동 계획을 대화로 만드는 관제 프로그램이에요.",
      "simple_text": "로봇의 상태를 살펴보고, 로봇이 어떻게 움직일지 계획하는 프로그램이에요.",
      "evidence": [
        {
          "path": "python_controller/llm/unified_agent.py",
          "sha256": "3d2c60d7a94304f7de2e6e44a3dca77b0751f7966bd80450f9ccf60f390f32a7",
          "excerpt": "D.I.G.는 3WD 로봇의 상태를 확인하고 이동 계획을 대화로 만드는 미션 관제 프로그램이에요.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "86a08a8e565bb37bac8589ca91bb7f4097d7bdf57921d0b9f1bc2e43250f7c3a"
        }
      ]
    },
    {
      "key": "interfaces",
      "topic": "interfaces",
      "text": "현재 브라우저 관제는 HTML·CSS·JavaScript 화면과 Python 백엔드로 구성돼요. Qt 현장 관제도 별도 실행 경로로 남아 있어요.",
      "simple_text": "웹 화면에서 조작하면 Python 프로그램이 요청을 처리해요. Qt 화면을 여는 방식도 있어요.",
      "evidence": [
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "HTML/CSS/JavaScript 화면 + Python 백엔드",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        },
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "기존 Qt 공유 관제는",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        }
      ]
    },
    {
      "key": "features",
      "topic": "features",
      "text": "손동작 직접 제어와 AI 이동 계획을 함께 사용할 수 있고, 계획을 작성하거나 블록을 편집할 수 있어요. 실제 이동 제어권은 한 번에 하나만 가져요.",
      "simple_text": "손동작으로 조종하거나 AI와 이동 계획을 만들 수 있어요. 두 기능은 열어 둘 수 있지만, 실제 조종권은 하나만 사용해요.",
      "evidence": [
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "손동작으로 조종하는 동안에도 AI 대화와 계획 작성·블록 편집을 사용할 수 있지만, 실제 이동 제어권은 하나만 가진다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        }
      ]
    },
    {
      "key": "ai",
      "topic": "ai",
      "text": "Agent는 일반 대화와 문서 설명, 이동 계획을 구분해 처리해요. 등록된 지식은 로컬에서 조회하고, 복잡한 문서 해석은 클라우드 설정과 근거 검증 조건을 충족할 때 사용해요.",
      "simple_text": "AI는 대화를 나누고 자료를 찾거나 이동 계획을 만드는 역할을 해요. 로컬 지식 조회와 외부 AI 해석은 별도 경로예요.",
      "evidence": [
        {
          "path": "python_controller/llm/agent_graph.py",
          "sha256": "78f3749158880a18f5db01146253425c4abe26fc226dc53826c4cafc8ed983aa",
          "excerpt": "if kind in ('ask_docs','explain_program'):",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "eda7f6fe4ae02f51744c7b8be92fc48983e0a82b9ac788996bdf20d008409ee1"
        },
        {
          "path": "python_controller/knowledge/answer.py",
          "sha256": "f9d4da316f78187f80b864a759defebf9912ede0351292431992159c23fbd5bb",
          "excerpt": "env.get('DIG_KNOWLEDGE_CLOUD')!='1'",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "f40141448529fa91367536d2399317c997c8da3f71edf9532af09e67524a11de"
        }
      ]
    },
    {
      "key": "communication",
      "topic": "communication",
      "text": "AI가 제안한 계획과 실제 로봇 실행은 분리돼 있어요. Python 관제가 ESP32와 UDP로 통신하고, ESP32의 상태 응답을 확인해요.",
      "simple_text": "AI가 만든 계획을 바로 모터에 보내는 구조는 아니에요. Python 관제가 ESP32에 요청을 보내고 상태 응답을 확인해요.",
      "evidence": [
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "Python `DashboardEngine`과 ESP32·레이더·카메라 제어 코드를 사용한다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        },
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "웹 실물 계획 승인에는 현재 계획 버전과 운영자 확인이 필요하다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        },
        {
          "path": "docs/ARCHITECTURE.md",
          "sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc",
          "excerpt": "UDP snapshot은 성공한 sendto 패킷, 수신 나이·유효성",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc"
        }
      ]
    },
    {
      "key": "approval",
      "topic": "approval",
      "text": "실물 AI 계획 실행에는 현재 계획 버전에 대한 운영자 승인과 장치의 안전 조건 확인이 필요해요. 계획을 설명하거나 편집하는 것만으로 이동을 실행하지는 않아요.",
      "simple_text": "계획을 만드는 것과 실제로 움직이는 것은 달라요. 실물 실행은 승인과 장치 상태 확인을 거쳐요.",
      "evidence": [
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "웹 실물 계획 승인에는 현재 계획 버전과 운영자 확인이 필요하다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        },
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "이전 동작은 재출발하지 않으며 새 수동 입력·손동작 활성화·AI 승인이 필요하다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        }
      ]
    },
    {
      "key": "safety",
      "topic": "safety",
      "text": "문서나 이동 거리만으로 실제 주행의 안전을 보장할 수는 없어요. STOP 응답 같은 통신 확인과 물리적인 정지·주행 검증도 구분해야 해요.",
      "simple_text": "거리가 짧다고 무조건 안전한 것은 아니에요. 실제 장치 상태와 주행 환경을 확인해야 해요.",
      "evidence": [
        {
          "path": "docs/ARCHITECTURE.md",
          "sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc",
          "excerpt": "송신 성공이나 정지 텔레메트리 확인을 물리 주행 성공으로 승격하지 않는다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc"
        },
        {
          "path": "docs/ARCHITECTURE.md",
          "sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc",
          "excerpt": "화면의 긴급 정지는 소프트웨어 정지다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc"
        }
      ]
    },
    {
      "key": "qt_interface",
      "topic": "qt_interface",
      "text": "Qt 현장 관제는 PySide6를 사용하는 Python 데스크톱 화면이에요. 현재 HTML 웹 관제와는 별도의 실행 경로예요.",
      "simple_text": "Qt는 Python으로 여는 데스크톱 관제 화면이고, 웹 관제는 브라우저로 여는 화면이에요.",
      "evidence": [
        {
          "path": "docs/ARCHITECTURE.md",
          "sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc",
          "excerpt": "배치 실행의 기본 UI는 PySide6이며",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "e00a2672438f1d66177265315a6950845136d10a3337ea5d6efc54277443a9cc"
        },
        {
          "path": "README.md",
          "sha256": "6f00e867142da3f24ae03509dc891b84309b711897883a387993f8b8deea621b",
          "excerpt": "기존 Qt 공유 관제는",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "13f55ce2b5c94d059b2dc17b2734128831ae7efd641a60531789b084eea09afd"
        }
      ]
    },
    {
      "key": "pcb_v18_driver",
      "topic": "hardware:PCB_V18_DESIGN",
      "variant_id": "PCB_V18_DESIGN",
      "text": "PCB V18은 HW166 형상의 TB6612FNG 모듈용 소켓을 기준으로 한 설계예요.",
      "simple_text": "V18은 HW166 형상의 모터 드라이버 모듈 소켓을 쓰도록 설계됐어요.",
      "evidence": [
        {
          "path": "docs/hardware/pcb-v18/FACTS.md",
          "sha256": "48f5fa12271205dd7a8f089e759794d7c4981edfb61709411d3b8f69bc2f1a30",
          "excerpt": "V18의 MD1과 MD2는 TB6612FNG HW166 형상의 모듈용 소켓입니다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "48f5fa12271205dd7a8f089e759794d7c4981edfb61709411d3b8f69bc2f1a30"
        }
      ]
    },
    {
      "key": "pcb_v19_relation",
      "topic": "hardware:PCB_V19_DESIGN",
      "variant_id": "PCB_V19_DESIGN",
      "text": "V19 문서에는 모터 제어·엔코더·STBY GPIO 16개가 V18과 회로도 기준으로 같다고 명시돼 있어요.",
      "simple_text": "V19 문서에서는 주요 제어 핀 16개가 V18과 같다고 설명해요. 설계 자료에 관한 설명이에요.",
      "evidence": [
        {
          "path": "docs/hardware/pcb-v19/FACTS.md",
          "sha256": "4cc81815e4662096f4639bdfc024258c071956608291963b3722a33077a4ecc8",
          "excerpt": "V19와 V18의 모터 제어·엔코더·STBY GPIO 16개는 회로도 기준으로 같습니다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "4cc81815e4662096f4639bdfc024258c071956608291963b3722a33077a4ecc8"
        }
      ]
    },
    {
      "key": "pcb_comparison_limits",
      "topic": "hardware:comparison_limits",
      "text": "두 설계 자료만으로 실제 모듈의 교체 호환성이나 실물 장착 상태까지 확정할 수는 없어요. 비교할 때는 핀맵과 모듈 형상·실장 조건을 따로 확인해야 해요.",
      "simple_text": "설계상의 핀이 같아도 실제 부품을 그대로 바꿔 써도 된다는 뜻은 아니에요.",
      "evidence": [
        {
          "path": "docs/hardware/pcb-v18/FACTS.md",
          "sha256": "48f5fa12271205dd7a8f089e759794d7c4981edfb61709411d3b8f69bc2f1a30",
          "excerpt": "다른 모듈의 교체 호환은 검증되지 않았습니다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "48f5fa12271205dd7a8f089e759794d7c4981edfb61709411d3b8f69bc2f1a30"
        },
        {
          "path": "docs/hardware/pcb-v19/FACTS.md",
          "sha256": "4cc81815e4662096f4639bdfc024258c071956608291963b3722a33077a4ecc8",
          "excerpt": "물리 체결, 모듈 방향, 모듈 간 대체 호환성은 확인되지 않았습니다.",
          "revision": "worktree-content-sha256",
          "review_status": "agent_source_checked_human_review_pending",
          "lf_sha256": "4cc81815e4662096f4639bdfc024258c071956608291963b3722a33077a4ecc8"
        }
      ]
    }
  ]
}
```
