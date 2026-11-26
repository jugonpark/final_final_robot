"""Bounded read-only evidence. Instructions and fenced code are never generation context."""
import re

UNTRUSTED_COMMAND = re.compile(
    r'ignore (?:all |previous |system )?instructions|(?:system|developer|assistant)\s*:|'
    r'<\/?(?:system|developer|assistant)>|승인.{0,12}(?:무시|우회|건너뛰|없이)|'
    r'이전\s*(?:지시|프롬프트).{0,12}무시|send.{0,18}motor command|'
    r'(?:skip|bypass).{0,20}approval|"(?:approved|tool_calls|authorization_ticket)"\s*:', re.I)

CITATION_FIELDS = ('id', 'document_id', 'path', 'heading', 'title', 'section', 'page', 'line', 'version', 'revision',
                   'sha', 'source_sha256', 'trust', 'variant_id', 'source_kind', 'evidence_status', 'source_path',
                   'effective_date', 'reviewed_by_user', 'index_version', 'heading_path', 'chunk_hash', 'token_count', 'split_warning')


def safe_body(body):
    lines, fenced = [], False
    for line in body.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced and not UNTRUSTED_COMMAND.search(line):
            lines.append(line)
    return '\n'.join(lines)


def select_evidence(rows, question):
    # Preserve complete sentences/conditions; never truncate a negation off a quote.
    from python_controller.knowledge.library import query_terms
    terms = query_terms(question)
    selected = []
    remaining = 8000
    for row in rows[:6]:
        if any(warning in (row.get('split_warning') or '') for warning in ('split_long_line','unclosed_fence')):
            continue
        body = safe_body(row['body'])
        quotes = [p.strip() for p in re.split(r'\n\s*\n', body) if 6 <= len(p.strip()) <= 1200]
        if not quotes:
            quotes = [line.strip() for line in body.splitlines() if 6 <= len(line.strip()) <= 600]
        # Rank paragraphs, retaining within-paragraph qualifiers and associations.
        quotes.sort(key=lambda p: sum(term in p.lower() for term in terms), reverse=True)
        bounded = []
        for quote in quotes[:3]:
            if len(quote) <= remaining:
                bounded.append(quote)
                remaining -= len(quote)
        quotes = bounded
        if quotes:
            selected.append({**row, 'body': body, 'quote_candidates': quotes})
    return selected


def citations(rows, claims=()):
    result = []
    for row in rows:
        linked = [c for c in claims if row['id'] in c['evidence_ids']]
        used = {s['quote_index'] for c in linked for s in c.get('supports', []) if s['evidence_id'] == row['id']}
        result.append({**{key: row.get(key) for key in CITATION_FIELDS},
                       'excerpts': [row['quote_candidates'][i] for i in sorted(used)],
                       'related_claims': [c['claim_id'] for c in linked]})
    return result


def current(library, row):
    """Old citations remain viewable but cannot justify new answers after retirement."""
    with library._db() as db:
        found = db.execute('SELECT c.body,d.sha,d.version,d.variant_id,d.source_digest FROM chunks c '
                           'JOIN documents d ON c.document_id=d.id WHERE c.id=? AND c.active=1 '
                           "AND d.status IN ('indexed','scoped','review_only')", (row['id'],)).fetchone()
    return bool(found and found['sha'] == row['sha'] and found['version'] == row['version'] and
                found['variant_id'] == row['variant_id'] and
                (found['source_digest'] or found['sha']) == row['source_sha256'] and
                all(quote in safe_body(found['body']) for quote in row['quote_candidates']))


def cloud_allowed(question, rows, env):
    from python_controller.knowledge.library import SECRET
    # Applies to the ENTIRE retrieved scope, including sources later filtered out.
    return bool(rows and env.get('DIG_KNOWLEDGE_CLOUD') == '1' and not SECRET.search(question) and
                all(row['trust'] == 'project_doc' and not SECRET.search(row['body']) for row in rows))
