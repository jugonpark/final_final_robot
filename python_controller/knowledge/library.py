"""Local document indexing and retrieval. No dependency on the robot controller."""
from contextlib import closing, contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from threading import Lock
import unicodedata

from python_controller.knowledge.variants import (BREADBOARD, BREADBOARD_PIN_SOURCE, SOURCES as HARDWARE_SOURCES,
                                                   clarification, fact_section, hardware_scope)

SEED_FILES = ('ARCHITECTURE.md', 'DASHBOARD_GUIDE.md', 'CONTROL_SIMPLE_V2_PLAN.md',
              'RADAR_OPERATION_GUIDE.md', 'RADAR_LLM_INTEGRATION.md', 'C6_HARDWARE_PREFLIGHT.md')
MAX_DOCUMENT = 4 * 1024 * 1024
SECRET = re.compile(r'(?:nvapi-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|'
                    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|(?im:^[ \t]*(?:export\s+|#define\s+)?'
                    r'[\w]*(?:PASSWORD|PASSWD|API_KEY|SECRET_KEY|ACCESS_TOKEN|WIFI_SSID)[\w]*'
                    r'\s*(?::|=|[ \t]+)[ \t]*["\']?[^\s"\'`<>]{3,}))', re.I)
SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, path TEXT NOT NULL, sha TEXT NOT NULL, version TEXT NOT NULL,
 trust TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, UNIQUE(path, sha, version, trust)
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), heading TEXT NOT NULL,
 page INTEGER, line INTEGER, body TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(path, title, body, tokenize="unicode61 tokenchars '_'");
CREATE TABLE IF NOT EXISTS embeddings (
 chunk_id INTEGER NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(chunk_id, model)
);
'''
DOCUMENT_FIELDS = '''d.*, COALESCE(d.source_path, d.path) AS provenance_path, d.version AS revision'''
SOURCES = '''SELECT c.id, c.document_id, c.heading, c.heading AS title, c.heading AS section, c.page, c.line, c.body,
                    d.path, d.sha, COALESCE(d.source_digest, d.sha) AS source_sha256,
                    d.version, d.version AS revision, d.trust,
                    d.effective_date, 0 AS reviewed_by_user, '0.2' AS index_version,
                    d.variant_id, d.source_kind, d.evidence_status, COALESCE(d.source_path, d.path) AS source_path
             FROM chunks c JOIN documents d ON d.id=c.document_id'''
METADATA_COLUMNS = {
    'variant_id': "TEXT NOT NULL DEFAULT 'UNCLASSIFIED'",
    'source_kind': "TEXT NOT NULL DEFAULT 'REFERENCE'",
    'evidence_status': "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
    'source_path': 'TEXT',
    'source_digest': 'TEXT',
    'effective_date': 'TEXT',
}
SCOPED_STATUSES = {'hardware_reference': 'scoped', 'review_draft': 'review_only'}
STOP_WORDS = set('문서 매뉴얼 설명서 알려줘 확인 어떻게 있나요 인가요 무엇 뭐야 설정값 대한 가이드 자료 참고 검색 하는지 관한 로봇'.split())
ALIASES = (
    ('레이더', 'radar', 'iwr6843aop'), ('정지', 'stop', 'stopped'), ('긴급정지', '비상정지', 'estop', 'emergency_stop'),
    ('구동', '이동', 'motion'), ('통신', '연결', 'communication', 'connection', 'udp'),
    ('오류', 'fault', 'error'), ('초기화', 'reset_fault', 'reset'), ('대시보드', 'dashboard'),
    ('엔코더', 'encoder'), ('텔레메트리', 'telemetry'), ('신선도', 'freshness', 'stale'),
    ('심박', 'heartbeat', '하트비트'), ('워치독', 'watchdog'), ('소유권', '제어권', 'owner'),
    ('차단', 'blocked', 'interlock'), ('거리', 'distance'), ('설치', 'setup', 'install'), ('경로', 'path', 'trajectory'),
)


class KnowledgeError(ValueError):
    pass


def _check_name(name):
    if (not isinstance(name, str) or len(name) > 160 or re.search(r'[/\\:\x00-\x1f]', name)
            or name.startswith('.') or re.search(r'secret|credential|\.env\b|password|token', name, re.I)
            or Path(name).suffix.lower() not in ('.md', '.txt', '.pdf')):
        raise KnowledgeError('PDF·Markdown·TXT 문서만 등록할 수 있습니다. 민감 파일은 제외됩니다.')


def _extract(raw, extension):
    if extension == '.pdf':
        if not raw.startswith(b'%PDF-'):
            raise KnowledgeError('PDF 파일 형식을 확인하세요.')
        try:
            from pypdf import PdfReader
            pdf = PdfReader(BytesIO(raw))
            if pdf.is_encrypted or len(pdf.pages) > 120:
                raise KnowledgeError('암호가 없고 120쪽 이하인 PDF를 사용하세요.')
            return [(f'{index + 1}쪽', index+1, None, page.extract_text() or '') for index, page in enumerate(pdf.pages)]
        except ImportError as exc:
            raise KnowledgeError('PDF 색인에는 requirements-knowledge.txt 설치가 필요합니다.') from exc
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeError('PDF에서 텍스트를 읽지 못했습니다.') from exc
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as exc:
        raise KnowledgeError('텍스트 문서는 UTF-8로 저장해 주세요.') from exc
    if '\x00' in text:
        raise KnowledgeError('텍스트 파일 형식을 확인하세요.')
    groups = []
    heading, start, lines = '본문', 1, []
    fenced = False
    for index, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        title = re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*$', line) if extension == '.md' and not fenced else None
        if title:
            groups.append((heading, None, start, '\n'.join(lines)))
            heading, start, lines = title[1], index, []
        else:
            lines.append(line)
    return [*groups, (heading, None, start, '\n'.join(lines))]


def _chunks(groups):
    for heading, page, line, body in groups:
        body = body.strip()
        while body:
            split = max(body.rfind('\n', 700, 1250), body.rfind(' ', 900, 1250)) if len(body) > 1250 else len(body)
            split = split if split > 0 else 1250
            yield heading[:240], page, line, body[:split].strip()
            body = body[split:].lstrip()


def query_terms(query):
    query = unicodedata.normalize('NFKC', query).lower()
    words = re.findall(r'[a-z0-9_]+|[가-힣]+', query)
    terms = []
    for word in words:
        word = re.sub(r'(?:에서는|이라면|이면|에서|으로|에는|입니다|인가요|을|를|은|는|이|가|의|에)$', '', word) if len(word) >= 3 and word[0] >= '가' else word
        if len(word) > 1 and word not in STOP_WORDS:
            terms.append(word)
    for group in ALIASES:
        if any(term in terms for term in group):
            terms.extend(group)
    return list(dict.fromkeys(terms))[:32]


class KnowledgeLibrary:
    def __init__(self, path, root):
        from python_controller.knowledge.vector import VectorRanker

        self.path, self.root = Path(path), Path(root)
        self._initialized = False
        self._init_lock = Lock()
        self.vector = VectorRanker.configured(self.path.parent / 'knowledge-models')

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def _ready(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.executescript(SCHEMA)
                db.execute('BEGIN IMMEDIATE')
                columns = {row['name'] for row in db.execute('PRAGMA table_info(documents)')}
                for name, definition in METADATA_COLUMNS.items():
                    if name not in columns:
                        db.execute(f'ALTER TABLE documents ADD COLUMN {name} {definition}')
                db.execute("""UPDATE documents SET variant_id='SOFTWARE_COMMON', source_kind='GIT_DOCUMENT',
                              evidence_status='SOFTWARE_IMPLEMENTATION', source_path=path
                              WHERE trust='project_doc' AND variant_id='UNCLASSIFIED'""")
                db.execute("""UPDATE documents SET source_kind='USER_UPLOAD', source_path=path
                              WHERE trust='user_reference' AND source_kind='REFERENCE'""")
            self._seed()
            self._remove_superseded_hardware()
            self._initialized = True

    def _remove_superseded_hardware(self):
        # Old citations must not reopen withdrawn or corrected evidence.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            active = tuple(HARDWARE_SOURCES)
            placeholders = ','.join('?' for _ in active)
            stale = f"""SELECT id FROM documents WHERE
                       (trust='hardware_reference' AND variant_id NOT IN ({placeholders})) OR
                       (status='outdated' AND (trust='hardware_reference'
                         OR trust='review_draft' AND path='docs/hardware/review/DIG_TECHNICAL_v0.2.md'))"""
            chunks = 'SELECT id FROM chunks WHERE document_id IN (' + stale + ')'
            db.execute('DELETE FROM chunk_search WHERE rowid IN (' + chunks + ')', active)
            db.execute('DELETE FROM embeddings WHERE chunk_id IN (' + chunks + ')', active)
            db.execute('DELETE FROM chunks WHERE document_id IN (' + stale + ')', active)
            db.execute('DELETE FROM documents WHERE id IN (' + stale + ')', active)

    def _seed(self):
        git = ['git', '-C', str(self.root)]
        try:
            commit = subprocess.check_output([*git, 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, timeout=4).decode().strip()
        except (OSError, subprocess.SubprocessError):
            commit = None
        for name in SEED_FILES:
            path = 'docs/' + name
            try:
                if commit:
                    raw = subprocess.check_output([*git, 'show', f'{commit}:{path}'], stderr=subprocess.DEVNULL, timeout=4)
                else:
                    raw = (self.root / path).read_bytes()
            except (OSError, subprocess.SubprocessError):
                continue
            self._ingest(path, raw, commit, 'project_doc' if commit else 'local_reference')
        # Local allowlist: seed only the curated excerpts, even before the worktree is committed.
        manifest_path = self.root / 'docs/hardware/SOURCE_MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig')) if manifest_path.is_file() else {}
        hashes = {Path(row['path']).name: row['sha256'] for row in manifest.get('original_assets', [])}
        for variant, (relative, revision, kind, evidence, source) in (*HARDWARE_SOURCES.items(), (BREADBOARD, BREADBOARD_PIN_SOURCE)):
            path = 'docs/hardware/' + relative
            asset = Path(source.split('!/', 1)[1]).name if kind == 'BOM_IMAGE' else source.split('!/', 1)[0]
            source_digest = hashes.get(asset)
            if kind == 'FIRMWARE_SOURCE':
                source_file = self.root / source
                if not source_file.is_file():
                    continue
                source_digest = sha256(source_file.read_bytes()).hexdigest()
            if (self.root / path).is_file():
                self._ingest(path, (self.root / path).read_bytes(), revision, 'hardware_reference',
                             metadata=(variant, kind, evidence, source), source_digest=source_digest,
                             effective_date=manifest.get('created_on'))
        review_path = 'docs/hardware/review/DIG_TECHNICAL_v0.2.md'
        if (self.root / review_path).is_file():
            self._ingest(review_path, (self.root / review_path).read_bytes(), '0.2', 'review_draft',
                         metadata=('SOFTWARE_COMMON', 'TECHNICAL_DRAFT', 'REVIEW_ONLY', review_path))

    def register(self, name, raw):
        _check_name(name)
        if not raw or len(raw) > MAX_DOCUMENT:
            raise KnowledgeError('문서는 4MB 이하로 등록하세요.')
        self._ready()
        return self._ingest(name, raw, None, 'user_reference')

    def _ingest(self, path, raw, version, trust, *, metadata=None, source_digest=None, effective_date=None):
        digest = sha256(raw).hexdigest()
        version = version or digest
        args = (path, digest, version, trust)
        indexed = SCOPED_STATUSES.get(trust, 'indexed')
        if metadata is None:
            if trust in ('project_doc', 'local_reference'):
                metadata = ('SOFTWARE_COMMON', 'GIT_DOCUMENT' if trust == 'project_doc' else 'LOCAL_DOCUMENT',
                            'SOFTWARE_IMPLEMENTATION', path)
            else:
                metadata = ('UNCLASSIFIED', 'USER_UPLOAD', 'UNVERIFIED', path)
        with self._db() as db:
            prior = db.execute('SELECT * FROM documents WHERE path=? AND sha=? AND version=? AND trust=?', args).fetchone()
            if prior and prior['status'] == indexed:
                return dict(prior)
        try:
            groups = _extract(raw, Path(path).suffix.lower())
            if SECRET.search(path) or any(SECRET.search(heading) or SECRET.search(body) for heading, _, _, body in groups):
                raise KnowledgeError('민감 정보가 감지되어 문서를 색인하지 않았습니다.')
            chunks = list(_chunks(groups))
            if not chunks:
                raise KnowledgeError('검색할 텍스트가 없습니다. 스캔 PDF는 텍스트가 있는 문서로 등록하세요.')
            if len(chunks) > 600:
                raise KnowledgeError('텍스트가 너무 많습니다. 문서를 작은 파일로 나누어 등록하세요.')
            status, detail = indexed, None
        except KnowledgeError as exc:
            status, detail, chunks = 'failed', str(exc), []
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM documents WHERE path=? AND sha=? AND version=? AND trust=?', args).fetchone()
            if row and row['status'] == indexed:
                return dict(row)
            if status == indexed:
                db.execute("UPDATE documents SET status='outdated' WHERE path=? AND trust=? AND status IN (?, 'indexed')",
                           (path, trust, indexed))
            if row:
                identifier = row['id']
                db.execute('''UPDATE documents SET status=?, detail=?, variant_id=?, source_kind=?, evidence_status=?,
                              source_path=?, source_digest=?, effective_date=? WHERE id=?''',
                           (status, detail, *metadata, source_digest, effective_date, identifier))
            else:
                identifier = db.execute('''INSERT INTO documents(path, sha, version, trust, status, detail,
                                        variant_id, source_kind, evidence_status, source_path, source_digest, effective_date)
                                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                                        (*args, status, detail, *metadata, source_digest, effective_date)).lastrowid
            has_chunks = db.execute('SELECT 1 FROM chunks WHERE document_id=? LIMIT 1', (identifier,)).fetchone()
            if not has_chunks:
                for heading, page, line, body in chunks:
                    chunk = db.execute('INSERT INTO chunks(document_id, heading, page, line, body) VALUES(?,?,?,?,?)',
                                       (identifier, heading, page, line, body)).lastrowid
                    db.execute('INSERT INTO chunk_search(rowid, path, title, body) VALUES(?,?,?,?)', (chunk, path, heading, body))
            return dict(db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone())

    def documents(self):
        self._ready()
        with self._db() as db:
            return [dict(row) for row in db.execute(f'SELECT {DOCUMENT_FIELDS} FROM documents d ORDER BY trust, path, id DESC')]

    def get_document_chunk(self, identifier):
        self._ready()
        with self._db() as db:
            row = db.execute(SOURCES + " WHERE c.id=? AND d.status IN ('indexed','scoped','review_only','outdated')",
                             (identifier,)).fetchone()
            return dict(row) if row else None

    def search_robot_documents(self, query, *, limit=6):
        variants = hardware_scope(query)
        if variants == ():
            raise KnowledgeError(clarification(query))
        if variants:
            results = []
            for variant in variants:
                results.extend(self._search(query, variant, max(1, limit // len(variants))))
            return results[:limit]
        return self._search(query, None, limit)

    def _search(self, query, variant, limit):
        self._ready()
        with self._db() as db:
            if variant and (section := fact_section(query)):
                rows = db.execute(SOURCES + " WHERE d.status='scoped' AND d.trust='hardware_reference'"
                                  ' AND d.variant_id=? AND c.heading=? ORDER BY c.id LIMIT ?', (variant, section, limit)).fetchall()
                return [dict(row) for row in rows]
        terms = query_terms(query)
        if variant:
            terms = [term for term in terms if term not in ('pcb', 'v18', 'v19', '브레드보드', '빵판', 'breadboard', '후보', '설계')]
        if not terms:
            return []
        match = ' OR '.join(f'"{term}"' for term in terms)
        if variant:
            match = '{title body} : (' + match + ')'
        with self._db() as db:
            scope = ("d.status='scoped' AND d.trust='hardware_reference' AND d.variant_id=?" if variant else
                     "d.status='indexed' AND d.trust NOT IN ('hardware_reference','review_draft')")
            binds = (match, variant, max(12, limit)) if variant else (match, max(12, limit))
            rows = db.execute(SOURCES + ' JOIN chunk_search ON chunk_search.rowid=c.id'
                              ' WHERE chunk_search MATCH ? AND ' + scope +
                              ' ORDER BY bm25(chunk_search,2.0,3.0,1.0), c.id LIMIT ?', binds).fetchall()
            lexical = [dict(row) for row in rows]
            if self.vector and not variant:
                from python_controller.knowledge.vector import hybrid_rank
                return hybrid_rank(db, query, lexical, self.vector, limit)
            return lexical[:limit]
