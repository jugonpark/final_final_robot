"""Assemble only approved claims. No final model may introduce extra facts."""
from python_controller.knowledge.evidence import citations


def value_items(value):
    """Split lists without detaching negation/exception inside parentheses."""
    parts=[];start=0;depth=0
    for i,char in enumerate(value):
        if char in '([{':depth+=1
        elif char in ')]}':depth=max(0,depth-1)
        elif char in ',、' and depth==0:
            parts.append(value[start:i].strip());start=i+1
    return [p for p in [*parts,value[start:].strip()] if p]


def profile_reply(claims):
    """Only formatting of typed fields; opaque values (including negatives) survive."""
    groups={}
    for claim in claims:
        item=claim.get('presentation',{})
        if item.get('kind')!='profile' or claim.get('verification_method')!='structured_template':return None
        if set(item)-{'kind','subject','field','value','display_values'}:return None
        if 'display_values' in item and (not item['display_values'] or any(v not in value_items(item['value']) for v in item['display_values'])):return None
        if claim['text']!=f"{item['subject']}의 {item['field']}: {item['value']}.":return None
        groups.setdefault(item['subject'],[]).append(item)
    parts=[]
    for subject,items in groups.items():
        basic=[i for i in items if i['field']!='좋아하는 것'];likes=[i for i in items if i['field']=='좋아하는 것']
        def pair(i):return f"{i['field']}{'은' if (ord(i['field'][-1])-0xAC00)%28 else '는'} ‘{i['value']}’"
        text=f'등록된 소개에 따르면 {subject}의 '
        if basic:
            text+=', '.join(pair(i) for i in basic[:2])+'이라고 적혀 있어요.'
            if len(basic)>2:text+=' '+', '.join(pair(i) for i in basic[2:])+'로 적어 주셨어요.'
        else:text=f'등록된 소개에 따르면 {subject}가 '
        if likes:
            detail=' '.join(f"좋아하는 것으로는 ‘{', '.join(i.get('display_values',[i['value']]))}’이 적혀 있어요." for i in likes)
            text+=('\n\n' if basic else '')+detail
        parts.append(text)
    return '\n\n'.join(parts)


def render_reply(claims, *, partial=False):
    text = profile_reply(claims) if claims else None
    if text is None:text = '\n\n'.join(' '.join(c['text'] for c in claims[i:i+2]) for i in range(0,len(claims),2))
    return text + ('\n\n나머지 내용은 근거 검증을 통과하지 못해 설명을 보류했어요.' if partial else '')


def validate_final(reply, claims, *, partial=False):
    return reply == render_reply(claims,partial=partial)


def response(reply, evidence=(), *, mode='abstained', status='not_verified', claims=(), rejected=(), partial=False, reason=None):
    if claims and not validate_final(reply,claims,partial=partial):
        return response('최종 답변과 검증된 사실이 일치하지 않아 답변을 보류했어요.',mode='error',status='failed')
    used = {i for claim in claims for i in claim['evidence_ids']}
    sources = [row for row in evidence if row['id'] in used] if claims else evidence
    return {'reply':reply, 'speech_text':'' if status == 'citation_only' else reply, 'answer_mode':mode, 'verification_status':status,
            'insufficient_evidence':not bool(claims) or partial,
            'claims':list(claims), 'citations':citations(sources,claims),
            # Model-supplied unanswered text is never rendered as a new factual claim.
            'unanswered_aspects': ['확인되지 않은 나머지 내용'] if partial else ([] if claims else ['직접 근거 또는 검증 가능한 설명']),
            'reason_code':reason or ('ANSWER_PARTIAL' if partial else None),
            'verification_failures':list(rejected)}
