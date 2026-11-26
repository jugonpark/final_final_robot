"""Fail-closed claim validation. Lexical guards never constitute semantic entailment."""
from decimal import Decimal
import re
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from python_controller.knowledge.evidence import UNTRUSTED_COMMAND, current
from python_controller.knowledge.answer_metrics import prose_copy_length


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Support(Strict):
    evidence_id: Annotated[int, Field(gt=0)]
    quote_index: Annotated[int, Field(ge=0)]


class Claim(Strict):
    claim_id: Annotated[str, Field(min_length=1, max_length=40)]
    text: Annotated[str, Field(min_length=3, max_length=600)]
    supports: Annotated[list[Support], Field(min_length=1, max_length=4)]


class Claims(Strict):
    claims: Annotated[list[Claim], Field(max_length=8)]
    unanswered_aspects: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=6)]


Relation = Literal['ENTAILMENT','CONTRADICTION','NEUTRAL','UNAVAILABLE']


class SemanticVerifier(Protocol):
    def __call__(self, pairs: list[dict]) -> list[Relation]: ...


ENTITIES = re.compile(r'(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)+|[A-Za-z]+\d[A-Za-z0-9-]*|\d+[A-Za-z]+\d+|UDP|TCP|Bluetooth|WiFi|HTTP|UART|SPI|VM|VCC|GND|VIN|VBAT|STBY|PWM|Qt|Flask|ESP32|GPIO\d+|STOP[A-Z_]*|CLEAR|EVADE|UNKNOWN|RobotSession)(?![A-Za-z0-9])', re.I)
SUBJECTS = re.compile(r'모터|센서|배터리|서보|로직|퓨즈|전원|입력|출력|엔코더')
NEGATIVE = re.compile(r'않|없|아니|못|불가|금지|미확인|미수행|미검증|보류|not\b|never\b|false\b|unverified', re.I)
CONDITIONAL = re.compile(r'경우|때만|에서만|전까지|이후에만|단,|다만|제외|except|unless|only\b', re.I)
DESIGN = re.compile(r'설계|목표|예정|계획|CAD|SOURCE_CODE_ONLY|BOM|소스 기준', re.I)
LIVE = re.compile(r'(?:현재|지금).{0,24}(?:움직이|주행|연결되어|연결돼|정지해|안전해|정상 동작|장착되어|장착돼)', re.I)
UNITS = {'mm':('distance',Decimal('.1')), 'cm':('distance',Decimal(1)), 'm':('distance',Decimal(100)),
         'ms':('time',Decimal('.001')), 's':('time',Decimal(1)), '초':('time',Decimal(1)),
         'v':('voltage',Decimal(1)), 'a':('current',Decimal(1)), 'ma':('current',Decimal('.001')),
         'cm/s':('speed',Decimal(1)), 'm/s':('speed',Decimal(100)), 'mm/s':('speed',Decimal('.1'))}
QUANTITY = re.compile(r'(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)\s*(mm/s|cm/s|m/s|mm|cm|ms|mA|V|A|초|s|m)(?![A-Za-z0-9])', re.I)


def quantities(text):
    return [(UNITS[m[2].lower()][0], Decimal(m[1])*UNITS[m[2].lower()][1]) for m in QUANTITY.finditer(text)]


def entities(text):
    return {m.group().lower() for m in ENTITIES.finditer(text)}


def deterministic_reason(text, quotes, rows):
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


def verify_claims(raw, evidence, library, *, semantic: SemanticVerifier | None = None):
    try:
        parsed = Claims.model_validate(raw)
    except ValidationError:
        return [], [{'reason':'INVALID_CLAIM_SCHEMA'}]
    lookup = {row['id']:row for row in evidence}
    pending, rejected = [], []
    for claim in parsed.claims:
        rows, quotes = [], []
        reason = None
        for support in claim.supports:
            row = lookup.get(support.evidence_id)
            if row is None or support.quote_index >= len(row['quote_candidates']):
                reason = 'INVALID_CITATION'
                break
            if not current(library,row):
                reason = 'STALE_EVIDENCE'
                break
            quote = row['quote_candidates'][support.quote_index]
            if quote not in row['body']:
                reason = 'INVALID_QUOTE'
                break
            rows.append(row)
            quotes.append(quote)
        reason = reason or deterministic_reason(claim.text,quotes,rows)
        if reason:
            rejected.append({'reason':reason})
        else:
            pending.append((claim,quotes))
    try:
        # Judge sees other retrieved evidence in the same profile too, so it can
        # detect a conflicting source rather than validate a cherry-picked quote.
        pairs = []
        for claim,quotes in pending:
            profiles = {lookup[s.evidence_id]['variant_id'] for s in claim.supports}
            context = list(dict.fromkeys(quotes + [q for row in evidence if row['variant_id'] in profiles
                                                   for q in row['quote_candidates']]))
            pairs.append({'text':claim.text,'quotes':quotes,'context':context})
        relations = semantic(pairs) if semantic and pending else ['UNAVAILABLE']*len(pending)
        if not isinstance(relations,list) or len(relations) != len(pending):
            relations = ['UNAVAILABLE']*len(pending)
    except Exception:
        relations = ['UNAVAILABLE']*len(pending)
    accepted = []
    for (claim,quotes), relation in zip(pending,relations):
        if relation != 'ENTAILMENT':
            rejected.append({'reason':relation if relation in ('CONTRADICTION','NEUTRAL','UNAVAILABLE') else 'UNAVAILABLE'})
            continue
        accepted.append({'claim_id':f'c{len(accepted)+1}', 'text':claim.text,
                         'evidence_ids':list(dict.fromkeys(s.evidence_id for s in claim.supports)),
                         'supports':[s.model_dump() for s in claim.supports],
                         'verification_status':'supported', 'verification_method':'nemotron_semantic_and_rules'})
    return accepted, rejected
