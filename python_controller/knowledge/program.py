"""Curated local fact document, source-bound and reusable across question scopes.

This is structured editorial knowledge, NOT an LLM entailment judgment.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

CATALOG='docs/knowledge/PROGRAM_KNOWLEDGE.md'


def source_matches(raw,proof):
    # Git may change CRLF/LF on checkout. No other content normalization.
    canonical=raw.replace(b'\r\n',b'\n').replace(b'\r',b'\n')
    digest_ok=(sha256(raw).hexdigest()==proof['sha256'] or
               sha256(canonical).hexdigest()==proof.get('lf_sha256'))
    return digest_ok and proof['excerpt'] in raw.decode('utf-8-sig')


def scope(question):
    if re.search(r'승인.{0,12}(?:무시|우회|없이)|지시.{0,12}무시|너는\s*이제',question):return None
    if re.search(r'오류|버그|원인|스레드|TTL|PID|GPIO|RAG|임베딩|리랭|청킹',question,re.I):return None
    if re.search(r'(지금|현재).{0,25}(움직|주행|연결|상태|장착)',question):return None
    subject=bool(re.search(r'프로그램|시스템|D\.?I\.?G\.?|우리\s*앱|관제|AI.{0,15}(ESP32|로봇)',question,re.I))
    generic=bool(re.fullmatch(r'(?:우리\s*)?(?:어떤|무슨)\s*기능(?:이|들이)?\s*있[어나요?？. ]*',question))
    if not (subject or generic):return None
    if not re.search(r'설명|뭘|뭐|무슨|어떤|어떻게|기능|작동|동작|구조|원리|만든|기술',question):return None
    if re.search(r'AI.{0,15}(ESP32|로봇)|통신|연결',question,re.I):return ('ai','communication','approval')
    if re.search(r'Qt|PySide',question,re.I):return ('qt_interface',)
    if re.search(r'기술|UI|화면|프론트|백엔드',question,re.I):return ('interfaces',)
    if re.search(r'기능',question):return ('purpose','features','ai')
    return ('purpose','interfaces','features','ai')


def local_program(library,question):
    topics=scope(question)
    from python_controller.knowledge.variants import hardware_scope
    variants=hardware_scope(question)
    if variants and len(variants)>1 and re.search(r'차이|비교',question):
        topics=tuple('hardware:'+v for v in variants)+('hardware:comparison_limits',)
    if re.search(r'이동해도|주행해도',question) and re.search(r'무조건|항상|안전.*보장',question):topics=('approval','safety')
    if topics is None:return [],[],False
    path=library.root/CATALOG
    try:
        raw=path.read_bytes();data=json.loads(re.search(r'```json\s*\n(.*?)\n```',raw.decode('utf-8'),re.S)[1])
        if data['schema_version']!=1 or type(data['human_approved']) is not bool:return [],[],True
    except (OSError,ValueError,TypeError,KeyError):return [],[],True
    # A comparison catalogue may not cover every requested profile. Preserve
    # the existing scoped lookup instead of substituting a different version.
    if variants and len(variants)>1:
        available={f['topic'] for f in data['facts']}
        if not {'hardware:'+v for v in variants}.issubset(available):return [],[],False
    selected=[];missing=False
    for topic in topics:
        fact=next((f for f in data['facts'] if f['topic']==topic),None)
        if fact is None:missing=True;continue
        valid=True
        for proof in fact['evidence']:
            relative=Path(proof['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('docs','python_controller','README.md'):
                valid=False;break
            source=library.root/relative
            try:
                value=source.read_bytes()
                valid=not source.is_symlink() and source.resolve().is_relative_to(library.root.resolve()) and source_matches(value,proof)
            except (OSError,UnicodeError):valid=False
            if not valid:break
        if valid:selected.append(fact)
        else:missing=True
    if not selected:return [],[],missing
    # Project-only local projection: no registered documents are sent anywhere.
    library._ready()
    version='worktree:'+sha256(raw).hexdigest()
    from python_controller.knowledge.library import SOURCES
    rows=[]
    for variant in dict.fromkeys(f.get('variant_id','SOFTWARE_COMMON') for f in selected):
        group=[f for f in selected if f.get('variant_id','SOFTWARE_COMMON')==variant]
        body='\n\n'.join('# '+f['key']+'\n'+f['text']+'\n\n'+f['simple_text'] for f in group)
        doc=library._ingest(CATALOG+'.'+variant+'.md',body.encode(),version,'project_doc',
            metadata=(variant,'LOCAL_CURATED_FACT','CAD_DESIGN' if variant.startswith('PCB_') else 'DOCUMENTED_IMPLEMENTATION',CATALOG),source_digest=sha256(raw).hexdigest())
        with library._db() as db:rows.extend(dict(r) for r in db.execute(SOURCES+' WHERE c.document_id=? AND c.active=1',(doc['id'],)))
    easy=bool(re.search(r'쉽게|간단|한마디|요약',question))
    evidence=[];claims=[]
    for fact in selected:
        text=fact['simple_text'] if easy else fact['text']
        row=next((r for r in rows if text in r['body']),None)
        if row is None:missing=True;continue
        if any(r['id']==row['id'] for r in evidence):row=next(r for r in evidence if r['id']==row['id'])
        else:row={**row,'quote_candidates':[]};evidence.append(row)
        index=len(row['quote_candidates']);row['quote_candidates'].append(text)
        claims.append({'claim_id':f'c{len(claims)+1}','text':text,'evidence_ids':[row['id']],
                       'supports':[{'evidence_id':row['id'],'quote_index':index}],
                       'verification_status':'supported','verification_method':'local_curated_source_bound',
                       'source_review_status':'agent_source_checked_human_review_pending','human_approved':False})
    return claims,evidence,missing
