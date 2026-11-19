"""Hardware scope selection for read-only document queries."""

import re
import unicodedata

BREADBOARD = 'BREADBOARD_PROTO'
V18 = 'PCB_V18_DESIGN'
V19 = 'PCB_V19_DESIGN'
LABELS = {BREADBOARD: '브레드보드 시제품', V18: 'PCB V18 설계', V19: 'PCB V19 설계'}
CLARIFY = '어느 자료 기준인가요? 브레드보드 시제품, PCB V18 설계, PCB V19 설계 중 하나를 알려 주세요.'
PCB_CLARIFY = 'PCB V18 설계와 PCB V19 설계 중 어떤 리비전을 조회할까요?'
AS_BUILT_UNKNOWN = '현재 연결된 PCB 리비전은 문서로 확인할 수 없습니다. 실제 보드의 리비전 표기나 현장 기록을 확인해 주세요.'

SOURCES = {
    BREADBOARD: ('breadboard/FACTS.md', '0.2', 'BOM_IMAGE', 'USER_BOM_LIST',
                 'DIG_Hardware_Variants_RAG_Codex_Final_v02.zip!/assets/breadboard_bom_original.png'),
    V18: ('pcb-v18/FACTS.md', 'V18', 'KICAD_AUDIT', 'CAD_DESIGN',
          'omni_3wd_v18_final_FAB_AUDIT.zip!/V18_FINAL_RELEASE/'),
    V19: ('pcb-v19/FACTS.md', 'V19', 'KICAD_AUDIT', 'CAD_DESIGN',
          'FINAL_PROJECT_V1.zip!/FINAL_PROJECT_V1/'),
}
BREADBOARD_PIN_SOURCE = ('breadboard/PIN_PROFILE.md', 'BREADBOARD_GPIO_V1', 'FIRMWARE_SOURCE', 'SOURCE_CODE_ONLY',
                         'esp32_firmware/control_simple_v2/control_simple_v2/hardware_profiles.h')

_SCOPES = {
    BREADBOARD: r'브레드\s*보드|빵판|breadboard',
    V18: r'(?<![a-z0-9])(?:v(?:er(?:sion)?)?\s*1\.?8|pcb[_\s-]*1\.?8)(?![a-z0-9])',
    V19: r'(?<![a-z0-9])(?:v(?:er(?:sion)?)?\s*1\.?9|pcb[_\s-]*1\.?9)(?![a-z0-9])|FINAL_PROJECT_V1',
}
_CURRENT_DESIGN = re.compile(r'(?:최신|현행|현재)\s*(?:pcb|기판)\s*설계', re.I)
_AS_BUILT = re.compile(r'(?:현재|지금|실제|장착된|연결된).{0,25}(?:pcb|기판).{0,24}(?:쓰|사용|실물|연결|설치|장착(?:됐|돼|되어|한|\s*여부)|리비전)|'
                       r'(?:pcb|기판).{0,19}(?:실물이\s*연결|장착됐|연결됐|쓰고\s*있|설치됐)', re.I)
_HARDWARE = re.compile(r'pcb|기판|하드웨어|부품|\bbom\b|배선|결선|핀\s*(?:맵|번호|배열|배치|은|이|에)|\bgpio\b|'
                       r'전원|전압|전류|배터리|buck|퓨즈|xt60|tb6612|hw166|szh-mdb|mg90s|pca9685|teranty|'
                       r'모터\s*드라이버|서보|장착홀|수동\s*tht\s*후보|\bfab_approved\b', re.I)


def hardware_scope(question):
    """None is general search; () requires a hardware clarification."""
    text = unicodedata.normalize('NFKC', question).lower()
    if _AS_BUILT.search(text):
        return ()
    versions = tuple(key for key, pattern in _SCOPES.items()
                     if re.search(pattern, text, re.I) or (key == V19 and _CURRENT_DESIGN.search(text)))
    # An unspecified PCB next to a breadboard does not select a PCB revision.
    if versions == (BREADBOARD,) and re.search(r'pcb|기판', text):
        return ()
    if not versions and 'teranty' in text and not re.search(r'pcb|기판', text):
        return (BREADBOARD,)
    if not versions and re.fullmatch(r'(?:문서\s*[:：]\s*)?(?:우리\s*)?배터리(?:는|가|의)?\s*'
                                     r'(?:전압|몇\s*(?:v|볼트))(?:은|이|는)?\s*(?:뭐야|야|예요|인가요|알려\s*줘)?[.!? ]*', text):
        return (BREADBOARD,)
    return versions or (() if _HARDWARE.search(text) else None)


def clarification(question):
    if _AS_BUILT.search(question):
        return AS_BUILT_UNKNOWN
    if re.search(r'(?:브레드\s*보드|빵판|breadboard).{0,32}(?:pcb|기판)|(?:pcb|기판).{0,32}(?:브레드\s*보드|빵판|breadboard)', question, re.I):
        if re.search(r'호환|바꿔|그대로|대체', question):
            return '두 구성의 모듈 교체 호환성은 검증되지 않았습니다. ' + PCB_CLARIFY
        return '브레드보드는 사용자 부품 목록이고, PCB는 별도의 KiCad 설계입니다. 부품·핀맵은 각각 확인해야 합니다. ' + PCB_CLARIFY
    return PCB_CLARIFY if re.search(r'pcb|기판', question, re.I) else CLARIFY


def fact_section(question):
    for section, pattern in (
        ('호환성', r'호환|바꿔\s*(?:꽂|끼)|그대로\s*(?:꽂|끼)|대체'),
        ('제작 승인', r'제작|생산|발주|검사|승인|erc|drc|fab_approved|a08'),
        ('서보', r'서보|mg90s|pca9685|ts0185'),
        ('핀맵과 배선', r'gpio|핀|배선|결선|연결돼'),
        ('배터리와 전원', r'배터리|teranty|전압|전원|xt60|buck|퓨즈'),
        ('모터 드라이버', r'드라이버|tb6612|hw166|szh-mdbl'),
        ('장착홀과 차이', r'장착홀|h[1-4]|같아|차이|비교|다름|달라|\bnpth\b'),
        ('부품 목록', r'부품|\bbom\b|구성|어떤|뭐가 있'),
    ):
        if re.search(pattern, question, re.I):
            return section
    # Pure revision names can be answered with the design overview.
    if re.fullmatch(r'(?:문서\s*[:：]\s*)?(?:pcb\s*)?(?:v1[89]|브레드보드|빵판)(?:는|은|\?)?[?!. ]*', question, re.I):
        return '부품 목록'
    return None
