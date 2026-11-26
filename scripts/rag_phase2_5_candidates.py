"""Offline-only rule/prompt candidates. No model calls and no production registration.

None means eligible for a FUTURE semantic check, never ENTAILMENT or verified.
The DESIGN patch is scoped to this single-process research helper only.
"""
from decimal import Decimal
import re
from unittest.mock import patch

from python_controller.knowledge import verifier as production

CONTEXTUAL_DESIGN=re.compile(r'설계|(?:개발|성능|연구|제작)\s*목표|목표(?:치|입니다|이다)|예정|계획|CAD|SOURCE_CODE_ONLY|BOM|소스 기준',re.I)
KOREAN_NEGATIVE=re.compile(production.NEGATIVE.pattern+r'|아닙|아닌|아님|아닐',re.I)
VOLTAGE=re.compile(r'(ESP32|모터|센서|배터리|서보|로직)(?:의)?\s*(?:전원|공급 전압|전압|정격 전압)?\s*(?:은|는|이|가|:|=)?\s*(\d+(?:\.\d+)?)\s*V',re.I)
SINGLE_VOLTAGE=re.compile(VOLTAGE.pattern+r'\s*(?:입니다|예요|이에요)\.?',re.I)


def candidate_reason(text,quotes,rows):
    with patch.object(production,'DESIGN',CONTEXTUAL_DESIGN),patch.object(production,'NEGATIVE',KOREAN_NEGATIVE):
        reason=production.deterministic_reason(text,quotes,rows)
        support='\n'.join(quotes)
        # A strictly parsed subject/value record is narrower than accepting any
        # occurrence of the number. All original quotes remain semantic context.
        claim=SINGLE_VOLTAGE.fullmatch(text)
        if reason=='QUANTITY_BINDING_MISMATCH' and claim:
            if any(pattern.search(support) for pattern in (production.NEGATIVE,production.CONDITIONAL,CONTEXTUAL_DESIGN)):
                return reason
            bindings={}
            spans={}
            for match in VOLTAGE.finditer(support):
                name=match[1].lower();bindings.setdefault(name,set()).add(Decimal(match[2]));spans.setdefault(name,[]).append(match.group())
            name=claim[1].lower()
            if bindings.get(name)=={Decimal(claim[2])}:
                # Re-run every other production guard on the actual matched span.
                return production.deterministic_reason(text,spans[name],rows)
        if reason in ('NEGATION_DROPPED','CONDITION_DROPPED','DESIGN_QUALIFIER_DROPPED'):
            found=next(production.ENTITIES.finditer(text),None)
            subjects=production.SUBJECTS.findall(text)
            primary=found.group().lower() if found else (subjects[0] if subjects else None)
            if primary is None:return reason
            clauses=[c for q in quotes for c in re.split(r'(?<=[.!?])\s+|\n',q) if c.strip()]
            relevant=[]
            for clause in clauses:
                anchors=production.entities(clause)|set(production.SUBJECTS.findall(clause))
                implicit=bool(re.match(r'\s*(?:다만|단[, ]|그|이 |해당|except|unless)',clause,re.I))
                if primary in anchors or not anchors or implicit:
                    relevant.append(clause)
            if relevant:
                return production.deterministic_reason(text,relevant,rows)
        return reason


def render_candidate(claims):
    """Spacing-only assembly of already approved text; no new connectors/facts."""
    return ' '.join(c['text'].strip() for c in claims)


PROMPT_CANDIDATE='''기존 근거·보안·스키마 규칙을 모두 유지하세요.
1. 사용자가 실제로 물은 핵심 질문에 직접 답하는 사실을 첫 claim으로 쓰세요.
2. 하나의 claim에는 독립 사실 하나만 넣으세요. 통신 주체/프로토콜/파일 소유권은 나누세요.
3. 각 supports의 실제 quote_candidates만으로 그 claim 전체를 설명할 수 있어야 합니다.
4. quote_index는 제공된 배열의 0..len-1 범위입니다. 모르는 번호를 추측하지 마세요.
5. 질문에 필요하지 않은 영상 파이프라인·설치·부가 기능은 나열하지 마세요.
6. 문장 연결은 같은 주제의 자연스러운 순서로 하되 새로운 인과·안전 성능을 추가하지 마세요.
7. 필요한 예외·부정·문서 기준과 버전을 문장 안에 보존하세요.
8. 사용자가 묻지 않은 선택적 상세 정보의 부재를 unanswered_aspects로 나열하지 마세요.
9. 파일명·경로·출처 번호를 본문과 speech_text에 넣지 마세요. 출처는 UI에 따로 표시합니다.
10. 제공된 근거로 핵심에 답할 수 없을 때만 그 핵심을 unanswered_aspects에 적으세요.
출력은 기존 claims/supports/unanswered_aspects JSON 계약만 사용하세요.'''
