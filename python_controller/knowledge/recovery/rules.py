"""Isolated rule candidate; no mutable production globals or semantic shortcuts.
Base guards are copied from Phase 1 so normal/counterexample changes remain local.
"""
from decimal import Decimal
import re
from python_controller.knowledge.verifier import (ENTITIES,SUBJECTS,LIVE,QUANTITY,quantities,entities)
from python_controller.knowledge.evidence import UNTRUSTED_COMMAND
from python_controller.knowledge.answer_metrics import prose_copy_length
DESIGN=re.compile('설계|(?:개발|성능|연구|제작)\\s*목표|목표(?:치|입니다|이다)|예정|계획|CAD|SOURCE_CODE_ONLY|BOM|소스 기준',re.I)
NEGATIVE=re.compile('않|없|아니|못|불가|금지|미확인|미수행|미검증|보류|not\\b|never\\b|false\\b|unverified|아닙|아닌|아님|아닐',re.I)
VOLTAGE=re.compile('(ESP32|모터|센서|배터리|서보|로직)(?:의)?\\s*(?:전원|공급 전압|전압|정격 전압)?\\s*(?:은|는|이|가|:|=)?\\s*(\\d+(?:\\.\\d+)?)\\s*V',re.I)
SINGLE_VOLTAGE=re.compile('(ESP32|모터|센서|배터리|서보|로직)(?:의)?\\s*(?:전원|공급 전압|전압|정격 전압)?\\s*(?:은|는|이|가|:|=)?\\s*(\\d+(?:\\.\\d+)?)\\s*V\\s*(?:입니다|예요|이에요)\\.?',re.I)
NEGATIVE=re.compile(NEGATIVE.pattern+r"|미지원|제외|사용하지\s*않",re.I)
CONDITIONAL=re.compile('경우|때만|에서만|전까지|이후에만|단,|다만|제외|except|unless|only\\b',re.I)
CONTEXTUAL_DESIGN=DESIGN


def _base_reason(text, quotes, rows):
    support = '\n'.join(quotes)
    if re.search(r'\[\d+\]|\S+\.(?:md|pdf|txt)\b|https?://',text,re.I):
        return 'SOURCE_TEXT_IN_REPLY'
    if UNTRUSTED_COMMAND.search(text) or LIVE.search(text):
        return 'UNSAFE_OR_LIVE_CLAIM'
    variants = {r['variant_id'] for r in rows if r.get('variant_id') not in (None,'UNCLASSIFIED')}
    if len(variants) > 1:
        return 'MIXED_VARIANT'
    from python_controller.knowledge.variants import hardware_scope
    explicit = hardware_scope(text)
    if explicit and variants and not set(explicit).issubset(variants):
        return 'VARIANT_MISMATCH'
    if not entities(text).issubset(entities(support)):
        return 'ENTITY_MISMATCH'
    # Quantities must occur with their entities/subjects in the same source clause.
    clauses = re.split(r'\n|(?<=[.!?;])\s+|이고\s*|이며\s*|,\s*(?=[A-Z]|모터|센서|배터리)', support)
    for dimension, value in quantities(text):
        anchors = entities(text) | set(SUBJECTS.findall(text))
        if not any((dimension,value) in quantities(clause) and
                   len({amount for unit,amount in quantities(clause) if unit == dimension}) == 1 and
                   anchors.issubset(entities(clause) | set(SUBJECTS.findall(clause))) for clause in clauses):
            return 'QUANTITY_BINDING_MISMATCH'
    # Raw numbers (ports/GPIO/counts) must be present; semantic judge checks associations.
    if not set(re.findall(r'(?<![A-Za-z0-9])\d+(?![A-Za-z0-9])', QUANTITY.sub('',text))).issubset(set(re.findall(r'(?<![A-Za-z0-9])\d+(?![A-Za-z0-9])', support))):
        return 'NUMBER_MISMATCH'
    if NEGATIVE.search(support) and not NEGATIVE.search(text):
        return 'NEGATION_DROPPED'
    if CONDITIONAL.search(support) and not CONDITIONAL.search(text):
        return 'CONDITION_DROPPED'
    for left,right in (('이하','이상'),('미만','초과'),('더 작','더 크')):
        if (left in support and right in text and left not in text) or (right in support and left in text and right not in text):
            return 'COMPARISON_REVERSED'
    if (DESIGN.search(support) or any(r.get('evidence_status') in ('CAD_DESIGN','SOURCE_CODE_ONLY','USER_BOM_LIST') for r in rows)) and not DESIGN.search(text):
        return 'DESIGN_QUALIFIER_DROPPED'
    if re.search(r'안전(?:하게|하|해|을 보장)|항상|완벽|guarantee|safe(?:ly)?\b',text,re.I) and not re.search(r'안전|항상|완벽|guarantee|safe',support,re.I):
        return 'UNSUPPORTED_SAFETY'
    if prose_copy_length(text,quotes) >= 45:
        return 'RAW_COPY'
    return None


def candidate_reason(text,quotes,rows):
    reason=_base_reason(text,quotes,rows)
    support='\n'.join(quotes)
    # A strictly parsed subject/value record is narrower than accepting any
    # occurrence of the number. All original quotes remain semantic context.
    claim=SINGLE_VOLTAGE.fullmatch(text)
    if reason=='QUANTITY_BINDING_MISMATCH' and claim:
        if any(pattern.search(support) for pattern in (NEGATIVE,CONDITIONAL,CONTEXTUAL_DESIGN)):
            return reason
        bindings={}
        spans={}
        for match in VOLTAGE.finditer(support):
            name=match[1].lower();bindings.setdefault(name,set()).add(Decimal(match[2]));spans.setdefault(name,[]).append(match.group())
        name=claim[1].lower()
        if bindings.get(name)=={Decimal(claim[2])}:
            # Re-run every other production guard on the actual matched span.
            return _base_reason(text,spans[name],rows)
    if reason in ('NEGATION_DROPPED','CONDITION_DROPPED','DESIGN_QUALIFIER_DROPPED'):
        found=next(ENTITIES.finditer(text),None)
        subjects=SUBJECTS.findall(text)
        primary=found.group().lower() if found else (subjects[0] if subjects else None)
        if primary is None:return reason
        clauses=[c for q in quotes for c in re.split(r'(?<=[.!?])\s+|\n',q) if c.strip()]
        relevant=[]
        for clause in clauses:
            anchors=entities(clause)|set(SUBJECTS.findall(clause))
            implicit=bool(re.match(r'\s*(?:다만|단[, ]|그|이 |해당|except|unless)',clause,re.I))
            if primary in anchors or not anchors or implicit:
                relevant.append(clause)
        if relevant:
            return _base_reason(text,relevant,rows)
    return reason
