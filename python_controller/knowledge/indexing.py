"""One background index worker per database; inference never holds a SQLite write lock."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Lock
import sqlite3

_workers = {}
_lock = Lock()


def schedule(library, document_id):
    if library.vector is None:
        return
    key = str(library.path.resolve())
    with _lock:
        worker = _workers.setdefault(key, ThreadPoolExecutor(max_workers=1, thread_name_prefix='rag-index'))
        if not hasattr(library, '_index_jobs'):
            library._index_jobs = {}
        previous = library._index_jobs.get(document_id)
        if previous and not previous.done():
            return previous
        future = worker.submit(build, library, document_id)
        library._index_jobs[document_id] = future
        return future


def build(library, document_id):
    from python_controller.knowledge.vector import MODEL, MODEL_REV
    model = f'{MODEL}:{MODEL_REV}'
    try:
        with library._db() as db:
            staged = db.execute('SELECT 1 FROM chunks WHERE document_id=? AND active=2 LIMIT 1',(document_id,)).fetchone()
            active = 2 if staged else 1
            rows = db.execute('SELECT id,heading,heading_path,body,chunk_hash FROM chunks WHERE document_id=? AND active=?', (document_id,active)).fetchall()
            db.execute("UPDATE documents SET embedding_status='building',embedding_total=?,embedding_done=0,updated_at=datetime('now') WHERE id=?", (len(rows),document_id))
        for row in rows:
            text = f'passage: {row["heading_path"] or row["heading"]}\n{row["body"]}'
            digest = sha256(text.encode()).hexdigest()
            with library._db() as db:
                cached = db.execute('SELECT vector FROM embedding_cache WHERE input_hash=? AND model=?', (digest,model)).fetchone()
            vector = cached['vector'] if cached else library.vector.encode([text])[0]
            import numpy as np
            checked = np.frombuffer(vector,dtype='<f4')
            if checked.shape != (384,) or not np.isfinite(checked).all():
                raise ValueError('Invalid cached E5 embedding')
            with library._db() as db:
                db.execute('INSERT OR IGNORE INTO embedding_cache VALUES(?,?,?)',(digest,model,vector))
                db.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?)',(row['id'],model,vector))
                db.execute("UPDATE documents SET embedding_done=embedding_done+1,updated_at=datetime('now') WHERE id=?",(document_id,))
        activate(library,document_id,model,'ready')
    except Exception as exc:
        activate(library,document_id,model,'failed',type(exc).__name__)
        return False
    return True


def activate(library, document_id, model, state, error=None):
    """Publish a complete revision, or an explicitly degraded FTS-only revision."""
    from python_controller.knowledge.library import SCOPED_STATUSES
    with library._db() as db:
        db.execute('BEGIN IMMEDIATE')
        document=db.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        newest=db.execute("SELECT MAX(build_order) FROM documents WHERE path=? AND trust=? AND status IN ('indexed','scoped','preparing')",(document['path'],document['trust'])).fetchone()[0]
        if newest is not None and document['build_order'] < newest:
            db.execute("UPDATE documents SET status='outdated',embedding_status=?,embedding_error=? WHERE id=?",(state,error,document_id))
            db.execute('UPDATE chunks SET active=0 WHERE document_id=? AND active=2',(document_id,))
            return
        staged=db.execute('SELECT 1 FROM chunks WHERE document_id=? AND active=2 LIMIT 1',(document_id,)).fetchone()
        if staged:
            if state != 'ready':
                db.execute('DELETE FROM embeddings WHERE model=? AND chunk_id IN (SELECT id FROM chunks WHERE document_id=? AND active=2)',(model,document_id))
            db.execute('UPDATE chunks SET active=0 WHERE document_id=? AND active=1',(document_id,))
            db.execute('UPDATE chunks SET active=1 WHERE document_id=? AND active=2',(document_id,))
        db.execute("UPDATE documents SET status='outdated' WHERE path=? AND trust=? AND id!=? AND status IN ('indexed','scoped','preparing')",(document['path'],document['trust'],document_id))
        db.execute("UPDATE documents SET status=?,embedding_status=?,embedding_error=?,updated_at=datetime('now') WHERE id=?",(SCOPED_STATUSES.get(document['trust'],'indexed'),state,error,document_id))


def shutdown():
    with _lock:
        workers = list(_workers.values())
        _workers.clear()
    for worker in workers:
        worker.shutdown(wait=True, cancel_futures=True)
