"""Small explicit grammars for offline facts; unrecognized prose is not summarized."""
import re
from python_controller.knowledge.evidence import current
from python_controller.knowledge.variants import LABELS


def structured_facts(evidence, question, library):
    facts = []
    def add(row, index, key, value, text, *, presentation=None):
        facts.append({'key': (row.get('variant_id'), key), 'value': value,
                      'claim_id': f'c{len(facts)+1}', 'text':text,
                      'evidence_ids':[row['id']], 'supports':[{'evidence_id':row['id'],'quote_index':index}],
                      'verification_status':'supported', 'verification_method':'structured_template'})
        if presentation:
            facts[-1]['presentation']=presentation
    for row in evidence:
        if not current(library,row):
            continue
        heading = row['heading']
        label = LABELS.get(row.get('variant_id'))
        for index, quote in enumerate(row['quote_candidates']):
            # Explicit user biography fields. Slash-separated fields are independently named.
            if row['trust'] == 'user_reference' and heading in question:
                fields = [m.groups() for part in re.split(r' / |\n',quote)
                          if (m := re.fullmatch(r'\s*(?:[-*]|\d+[.)])?\s*([가-힣][가-힣 ]{0,15})\s*:\s*(.+)',part))]
                if fields:
                    for key,value in fields:
                        key=re.sub(r'\s+',' ',key.strip())
                        if key=='좋아하는것':key='좋아하는 것'
                        value = value.strip().rstrip('.')
                        if 1 <= len(value) <= 160:
                            add(row,index,f'{heading}:{key}',value,f'{heading}의 {key}: {value}.',
                                presentation={'kind':'profile','subject':heading,'field':key,'value':value})
            if not label:
                continue
            if re.search(r'배터리|전압|몇\s*V',question,re.I):
                match = re.search(r'(TERANTY 배터리)의 기준 전압은 사용자가 확인한 (\d+(?:\.\d+)?V)입니다',quote)
                if match:
                    add(row,index,'TERANTY:전압',match[2],f'{label}의 사용자 확인 기준으로 {match[1]} 전압은 {match[2]}예요.')
            if re.search(r'제작|승인|검사|fab_approved',question,re.I):
                for name in ('FAB_APPROVED','FINAL_PINMAP_APPROVED'):
                    match = re.search(r'\b'+name+r'\s*=\s*(false|true)\b',quote,re.I)
                    if match:
                        value = match[1].lower()
                        add(row,index,name,value,f'{label} 문서의 {name} 값은 {value}예요. 실제 제작·장착 상태를 뜻하지는 않아요.')
            # A GPIO table is a typed mapping, not a prose extraction.
            lines = [line.strip().strip('|').split('|') for line in quote.splitlines() if line.strip().startswith('|')]
            if len(lines) >= 3 and [c.strip() for c in lines[0]] == ['모터','IN1','IN2','PWM','ENC A','ENC B']:
                for values in lines[2:]:
                    cells = [c.strip() for c in values]
                    if len(cells) == 6 and re.fullmatch(r'M[123]',cells[0]) and all(re.fullmatch(r'\d{1,2}',v) for v in cells[1:]):
                        requested = re.findall(r'\bM[123]\b',question,re.I)
                        if requested and cells[0].upper() not in [m.upper() for m in requested]:
                            continue
                        mapping = ', '.join(f'{pin}=GPIO{value}' for pin,value in zip(('IN1','IN2','PWM','ENC A','ENC B'),cells[1:]))
                        add(row,index,cells[0]+':GPIO',tuple(cells[1:]),f'{label} 자료에서 {cells[0]}의 핀은 {mapping}예요. 자료상의 설정이며 실물 배선은 별도 확인이 필요해요.')
            # Numbered BOM records preserve listed count and explicitly limit to listed parts.
            if heading == '부품 목록' and re.search(r'BOM|부품|구성',question,re.I):
                for line in quote.splitlines():
                    match = re.fullmatch(r'\s*\d+\. (.{1,100}?) ×(\d{1,2})(?:\..*)?',line)
                    if match:
                        add(row,index,match[1]+':수량',match[2],f'{label} 제공 BOM에는 {match[1]} {match[2]}개가 기재돼 있어요.')
    # A contradictory structured key is not reconciled by retrieval rank.
    values = {}
    for fact in facts:
        values.setdefault(fact['key'],set()).add(fact['value'])
    conflicts = any(len(group) > 1 for group in values.values())
    if conflicts:
        return [], True
    unique = {}
    for fact in facts:
        unique.setdefault((fact['key'],fact['value']), fact)
    clean = []
    for fact in list(unique.values())[:12]:
        clean.append({k:v for k,v in fact.items() if k not in ('key','value')})
    for i,fact in enumerate(clean):
        fact['claim_id'] = f'c{i+1}'
    # Focus only an explicitly mentioned field/value. Whole original values stay
    # in the claim contract; displayed list items keep their full parentheses.
    profiles=[c for c in clean if c.get('presentation',{}).get('kind')=='profile']
    if profiles and len(profiles)==len(clean):
        from python_controller.knowledge.rendering import value_items
        from python_controller.knowledge.library import query_terms
        segments=[s.strip() for s in re.split(r'[?？]',question) if s.strip()]
        focus=segments[-1] if segments else question
        if re.search(r'누구|소개',focus):return clean,False
        terms=[t for t in query_terms(focus) if t not in ('누구야','누구','뜻이야','좋아한다','소개','좋아하는','알려줘')]
        selected=[]
        for c in profiles:
            item=c['presentation'];matches=[v for v in value_items(item['value']) if any(t in v.lower() for t in terms) or v.split('(')[0].strip() in focus]
            if any(re.match(r'단[, ]|다만|제외|아님|않|not\b|except\b',v,re.I) for v in value_items(item['value'])):
                matches=value_items(item['value']) if matches else []
            if matches or item['field'] in focus:
                if item['field']=='좋아하는 것' and matches:item['display_values']=matches
                selected.append(c)
        if selected:clean=selected
    return clean, False
