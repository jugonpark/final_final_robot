"""Read-only help for the web sidebar. Navigation is handled elsewhere."""

import re
from typing import Literal

from python_controller.planning.schema import StrictModel


class MenuHelp(StrictModel):
    intent: Literal['explain_menu'] = 'explain_menu'
    menu: Literal['ai_chat', 'history', 'knowledge', 'camera', 'manual', 'diagnostics', 'settings', 'all']


MENU_GUIDE = {
    'ai_chat': ('미션 컨트롤', r'미션컨트롤|ai(?:대화|채팅)|미션블록',
                '대화나 블록 추가로 미실행 초안을 만들고, 손잡이 또는 ↑↓ 버튼으로 순서를 바꿔요. '
                '가상 경로 재생은 예상 움직임을 보여 줍니다. 실행 요청은 현재 버전의 검토 요청이며, 실물 실행은 Qt 운영자가 승인합니다. '
                '실행 타임라인은 단계별 목표 도달과 STOP 확인을 표시합니다.'),
    'history': ('작업 이력', r'작업이력|실행이력|미션이력',
                '최근 실행에서 작업을 선택하면 계획 버전, 동작 목록, 단계별 이벤트, 중단 사유를 볼 수 있어요. '
                '목표 도달과 정지 응답은 별도 사건으로 표시됩니다. 이 메뉴에서 실행을 다시 시작할 수는 없습니다.'),
    'knowledge': ('로봇 문서', r'로봇문서|문서검색|문서라이브러리|rag',
                  '한국어 질문이나 기술 용어로 문서를 검색하세요. 인용을 열면 문서 절이나 페이지, 리비전과 검증 수준을 볼 수 있어요. '
                  'PDF·Markdown·TXT를 최대 4MB까지 등록할 수 있습니다. 브레드보드와 PCB V18 자료는 범위를 나눠 조회합니다.'),
    'camera': ('카메라 · 손동작', r'카메라(?:[·ㆍ・]?손동작)?|손동작|사람추종',
               '카메라 켜기로 영상을 열어 손동작 직접 제어 또는 사람 추종의 인식 결과를 확인해요. '
               '사람 추종에서는 영상 속 대상 선택 후 시작·일시정지·목표 해제를 사용할 수 있습니다. '
               '화면을 벗어나면 카메라가 꺼지며, 웹은 시뮬레이션입니다.'),
    'manual': ('수동 제어', r'수동제어|직접제어',
               '전후좌우·회전 패드, 정지 버튼, 구동 준비 상태와 바퀴 피드백을 볼 수 있어요. '
               '웹의 실물 방향 버튼은 비활성화되어 있습니다. 실물 수동 제어는 Qt에서 연결과 안전 상태를 확인하고 사용합니다.'),
    'diagnostics': ('상태 · 진단', r'상태[·ㆍ・]?진단|진단',
                    '연결 · 안전에서 레이더 차단, ESP32 통신, 정지 사유를 확인해요. '
                    '카메라 · 추론은 영상 처리 상태, 바퀴 텔레메트리는 목표·실측 RPM, PWM, 엔코더를 표시합니다. '
                    '표시된 값은 장치가 보낸 텔레메트리이며 미수신 상태는 별도로 나타납니다.'),
    'settings': ('설정', r'설정|환경설정',
                 'ESP32 연결, 제스처 방향, Pi 레이더, 카메라와 추종 설정값을 확인해요. '
                 '웹에서는 Python 관제가 읽은 값이 표시됩니다. 변경은 Qt 관제에서 합니다.'),
}
_QUESTION = (r'(?:은|는|이|가|을|를|에서는|에서|의|에대한)?(?:자세히|상세)?'
             r'(?:뭐야|뭐예요|무엇인가요|뭘할수있어|뭐하는곳이야|어떻게써|어떻게써요|어떻게사용해|'
             r'설명(?:해(?:줘|주세요|줄래)?)?|소개(?:해줘)?|사용법(?:알려줘)?|기능(?:을)?(?:알려줘|설명해줘)?|자세히알려줘)')


def menu_question(text):
    text = re.sub(r'\s+', '', text.casefold()).strip('.!?。！？')
    if re.fullmatch(r'(?:각|모든|전체|사이드바)?메뉴(?:별)?' + _QUESTION, text):
        return MenuHelp(menu='all')
    for menu, (_, aliases, _) in MENU_GUIDE.items():
        if re.fullmatch(rf'(?:그럼|그러면)?(?:{aliases})(?:메뉴|화면|탭)?(?:에대해(?:서)?)?(?:좀)?' + _QUESTION, text):
            return MenuHelp(menu=menu)
    return None


def explain_menu(menu):
    if menu == 'all':
        return '\n\n'.join(f'{title}\n{detail}' for title, _, detail in MENU_GUIDE.values())
    title, _, detail = MENU_GUIDE[menu]
    return f'{title}\n\n{detail}'
