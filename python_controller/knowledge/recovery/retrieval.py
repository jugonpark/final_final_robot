"""Read-only bounded structural search; no model downloads, writes or gold access."""
from collections import Counter
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
import re
import subprocess
from time import perf_counter

from python_controller.knowledge.library import SOURCES, SECRET, _extract, query_terms
from python_controller.knowledge.evidence import safe_body,UNTRUSTED_COMMAND
from python_controller.knowledge.variants import hardware_scope


@lru_cache(maxsize=32)
def _git_bytes(root,revision,path):
    return subprocess.check_output(['git','-C',root,'show',f'{revision}:{path}'],stderr=subprocess.DEVNULL,timeout=15)

# Domain vocabulary describes relations, never question IDs, answers or source paths.
FACETS = {
    'transport': (r'통신|연결 방식|전송|프로토콜|주고받|패킷', ('통신','프로토콜','명령','텔레메트리','송신','수신','udp','tcp')),
    'ui_technology': (r'(?:화면|gui|ui|qt|대시보드).{0,25}(?:기술|구현|만든|라이브러리|바인딩)|어떤.{0,12}(?:gui|ui)', ('화면','gui','ui','qt','바인딩','기술','기본 ui')),
    'state_meaning': (r'unknown|상태.{0,12}(?:뜻|의미)|무슨 상태', ('unknown','상태','의미','사유','수신','유효')),
}


def analyze(question):
    active = [key for key,(pattern,_) in FACETS.items() if re.search(pattern,question,re.I)]
    return {'facets':active, 'terms':list(dict.fromkeys(query_terms(question)+[t for k in active for t in FACETS[k][1]])),
            'versions':hardware_scope(question),
            'numeric':bool(re.search(r'수치|전압|전류|몇|단위|gpio|핀|포트',question,re.I)),
            'live':bool(re.search(r'(현재|지금).{0,30}(연결|움직|주행|장착|속도|상태)',question))}


def source_text(library, row):
    """Only exact indexed bytes. Never join files, revisions, profiles or uploads."""
    if row['trust'] not in ('project_doc','local_reference','hardware_reference','review_draft'):
        return None
    relative=Path(row['path'])
    if relative.is_absolute() or '..' in relative.parts or relative.suffix.lower() not in ('.md','.txt'):
        return None
    raw=None
    revision=row['version']
    # Optional caller-provided immutable snapshot, still checked against DB SHA.
    raw=getattr(library,'recovery_source_bytes',{}).get((revision,row['path'],row['sha']))
    path=library.root/relative
    if raw is None:
        try:
            if not path.is_symlink() and path.resolve().is_relative_to(library.root.resolve()) and path.stat().st_size<=4*1024*1024:
                candidate=path.read_bytes()
                if sha256(candidate).hexdigest()==row['sha']:raw=candidate
        except OSError:pass
    if raw is None and row['trust']=='project_doc' and re.fullmatch(r'[a-f0-9]{7,40}',revision or ''):
        try:
            raw=_git_bytes(str(library.root),revision,relative.as_posix())
        except (OSError,subprocess.SubprocessError):
            return None
    elif raw is None:
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(library.root.resolve()):return None
            if path.stat().st_size>4*1024*1024:return None
            raw=path.read_bytes()
        except OSError:return None
    if sha256(raw).hexdigest()!=row['sha']:return None
    try:return raw.decode('utf-8-sig')
    except UnicodeError:return None


def units(text):
    """Preserve table heading/header/unit rows and complete prose blocks."""
    for heading,_,line,body in _extract(text.encode(),'.md'):
        if UNTRUSTED_COMMAND.search(heading):continue
        # Mermaid is declarative relation data, never executable code. Only a
        # strict node/edge subset is eligible; links/directives are rejected.
        for graph in re.findall(r'```mermaid\s*\n(.*?)```',body,re.S):
            graph=graph.strip()
            lines=[l.strip() for l in graph.splitlines() if l.strip()]
            if len(graph)>1200 or UNTRUSTED_COMMAND.search(graph):continue
            if not lines or not re.fullmatch(r'(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)',lines[0]):continue
            node=r'[A-Za-z][A-Za-z0-9_]*(?:\["[^"\n<>]*"\])?'
            edge=node+r'\s*-->\s*'+node
            if len(lines)>2 and all(re.fullmatch(edge,l) for l in lines[1:]):
                yield heading,graph,'relation_graph'
        body=safe_body(body)
        for block in re.split(r'\n\s*\n',body):
            block=block.strip()
            if not block:continue
            lines=block.splitlines()
            table=next((i for i in range(len(lines)-1) if '|' in lines[i] and re.search(r'\|\s*:?-{3,}',lines[i+1])),None)
            if table is not None:
                header='\n'.join(lines[:table+2])
                for value in lines[table+2:]:
                    if '|' in value:
                        quote=header+'\n'+value
                        if len(quote)<=1200:yield heading,quote,'table'
            elif 6<=len(block)<=1200 and not re.search(r'(?:을|를|에서|으로)\s*$',block):
                yield heading,block,'paragraph'
            else:
                # Oversize prose uses complete sentences only, never a suffix fragment.
                for sentence in re.split(r'(?<=[.!?。])\s+',block):
                    if 6<=len(sentence)<=1200 and re.search(r'[.!?。]$',sentence):yield heading,sentence,'sentence'


def retrieve(library,question,*,mode='fts',limit=6,structural=True,diverse=True,metrics=None):
    started=perf_counter()
    if not 1<=limit<=6:raise ValueError('Evidence limit must be 1..6')
    plan=analyze(question)
    if SECRET.search(question) or plan['versions']==():return [],[],plan
    # Existing hybrid/FTS rank remains one feature, never a truth score.
    baseline=library.search_robot_documents(question,limit=6,mode=mode,candidate_limit=20)
    if not structural:return baseline,baseline,plan
    library._ready()
    with library._db() as db:
        scope="c.active=1 AND d.status='indexed' AND d.trust NOT IN ('hardware_reference','review_draft')"
        params=[]
        if plan['versions']:
            scope="c.active=1 AND d.status='scoped' AND d.trust='hardware_reference' AND d.variant_id IN ("+','.join('?' for _ in plan['versions'])+')'
            params=list(plan['versions'])
        indexed=[dict(r) for r in db.execute(SOURCES+' WHERE '+scope+' ORDER BY c.id LIMIT 6001',params)]
    if len(indexed)>6000:raise ValueError('Candidate corpus bound exceeded')
    if metrics is not None:metrics['retrieval_ms']=(perf_counter()-started)*1000
    selection_started=perf_counter()
    by_document={}
    for row in indexed:by_document.setdefault(row['document_id'],[]).append(row)
    pool=[]; expanded_scope=list(baseline)
    for group in by_document.values():
        first=group[0];text=source_text(library,first)
        if text is None:
            # Registered private documents stay local; no guessed adjacent context.
            for row in group:
                body=safe_body(row['body'])
                if row.get('index_version','').startswith('legacy') or (row.get('split_warning') or '').startswith('split_'):continue
                for heading,quote,kind in units('# '+row['heading']+'\n'+body):
                    pool.append((row,heading,quote,kind,False))
            expanded_scope.extend(group)
            continue
        expanded_scope.extend(group)
        for heading,quote,kind in units(text):
            # Bind to an active chunk of the SAME source/heading where possible.
            row=next((r for r in group if r.get('heading_path',r['heading'])==heading or r['heading']==heading.rsplit(' / ',1)[-1]),first)
            pool.append((row,heading,quote,kind,True))
    rank={r['id']:1/(i+1) for i,r in enumerate(baseline)}
    terms=plan['terms']; scored=[];seen=set()
    for row,heading,quote,kind,restored in pool:
        if (row['variant_id']=='SOFTWARE_COMMON' and re.search(r'개편 설계|목표 구조|설계안|향후|개선 계획|전환 계획',heading)
                and not re.search(r'설계|계획|목표|비교|개편|향후',question)):
            continue
        norm=re.sub(r'\s+',' ',quote).strip()
        key=(row['variant_id'],row['trust'],norm)
        if key in seen:continue
        seen.add(key)
        content=(heading+' '+quote).lower()
        hits=sum(t.lower() in content for t in terms)
        if not hits:continue
        facet_hits=sum(sum(t in content for t in FACETS[k][1]) for k in plan['facets'])
        # Density prevents long unrelated paragraphs winning solely on term count.
        score=hits+2*facet_hits+rank.get(row['id'],0)+int(kind=='table')+hits/(1+len(quote)/180)
        if 'transport' in plan['facets'] and kind=='relation_graph' and re.search(r'UDP|TCP',quote,re.I):score+=12
        scored.append((score,{**row,'body':quote,'heading':heading,'quote_candidates':[quote],
                              'recovery_source_complete':restored,'recovery_kind':kind}))
    scored.sort(key=lambda pair:(-pair[0],pair[1]['id'],pair[1]['body']))
    chosen=[];counts=Counter()
    for score,row in scored:
        # At most 2 units per document first; fill only when no alternatives remain.
        if diverse and counts[row['document_id']]>=2:continue
        chosen.append(row);counts[row['document_id']]+=1
        if len(chosen)==limit:break
    if len(chosen)<limit:
        for _,row in scored:
            if row not in chosen:chosen.append(row)
            if len(chosen)==limit:break
    # A unit has a unique internal source key, separate from original chunk id.
    for i,row in enumerate(chosen,1):row['source_id']=row['id'];row['id']=i
    if metrics is not None:metrics['evidence_selection_ms']=(perf_counter()-selection_started)*1000
    return chosen,expanded_scope,plan


def fresh(library,row):
    with library._db() as db:
        found=db.execute(SOURCES+" WHERE c.id=? AND c.active=1 AND d.status IN ('indexed','scoped','review_only')",(row['source_id'],)).fetchone()
    if not found:return False
    found=dict(found)
    if any(found[k]!=row[k] for k in ('sha','version','variant_id','trust','document_id','source_sha256')):return False
    if not row['recovery_source_complete']:
        return all(q in safe_body(found['body']) for q in row['quote_candidates'])
    text=source_text(library,found)
    return text is not None and all(q in {q for _,q,_ in units(text)} for q in row['quote_candidates'])
