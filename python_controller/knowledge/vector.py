"""Optional local multilingual embeddings and hybrid ranking for small libraries."""
import logging
import os
from pathlib import Path
from threading import Lock

MODEL = 'intfloat/multilingual-e5-small'
MODEL_REV = 'ccc66d3'
logger = logging.getLogger(__name__)
_registry_lock = Lock()


class VectorRanker:
    def __init__(self, cache, *, download=False):
        self.cache = Path(cache)
        self.download = download
        self.lock = Lock()
        self.encoder = None
        self.unavailable = False

    @classmethod
    def configured(cls, cache):
        return cls(cache) if os.environ.get('DIG_KNOWLEDGE_VECTOR') == '1' else None

    def _model(self):
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        with _registry_lock:
            if not any(model['model'] == MODEL for model in TextEmbedding.list_supported_models()):
                TextEmbedding.add_custom_model(model=MODEL, pooling=PoolingType.MEAN, normalization=True,
                                               sources=ModelSource(hf=MODEL), dim=384, model_file='onnx/model.onnx')
        from huggingface_hub import snapshot_download

        model_dir = snapshot_download(MODEL, revision=MODEL_REV, cache_dir=self.cache,
                                      local_files_only=not self.download, max_workers=2,
                                      allow_patterns=['*.json', 'onnx/*.json', 'onnx/model.onnx'])
        return TextEmbedding(model_name=MODEL, specific_model_path=model_dir, threads=2, local_files_only=True)

    def rank(self, db, query, limit):
        import numpy as np

        if self.unavailable:
            return []
        with self.lock:
            if self.unavailable:
                return []
            if self.encoder is None:
                self.encoder = self._model()
            model_version = f'{MODEL}:{MODEL_REV}'
            rows = db.execute('''SELECT c.id,c.heading,c.body,e.vector FROM chunks c
                                 JOIN documents d ON d.id=c.document_id
                                 LEFT JOIN embeddings e ON e.chunk_id=c.id AND e.model=?
                                 WHERE d.status='indexed' AND d.trust NOT IN ('hardware_reference','review_draft')
                                 ORDER BY c.id LIMIT 1001''', (model_version,)).fetchall()
            if not rows or len(rows) > 1000:
                return []
            new = [row for row in rows if row['vector'] is None]
            saved = {row['id']: row['vector'] for row in rows if row['vector'] is not None}
            if new:
                docs = [f'passage: {row["heading"]}\n{row["body"]}' for row in new]
                for row, vector in zip(new, self.encoder.embed(docs, batch_size=8), strict=True):
                    saved[row['id']] = np.asarray(vector, dtype='<f4').tobytes()
                db.executemany('INSERT OR IGNORE INTO embeddings VALUES (?,?,?)',
                               [(row['id'], model_version, saved[row['id']]) for row in new])
            query_vector = next(iter(self.encoder.embed([f'query: {query}'])))
            candidates = [(float(np.dot(query_vector, np.frombuffer(vector, dtype='<f4'))), identifier)
                          for identifier, vector in saved.items()]
            return [identifier for _, identifier in sorted(candidates, reverse=True)[:limit]]


def hybrid_rank(db, query, lexical, vector, limit):
    from python_controller.knowledge.library import SOURCES

    try:
        ranked = vector.rank(db, query, 12)
    except Exception as exc:
        vector.unavailable = True
        logger.warning('Knowledge vector index unavailable (%s). FTS5 remains available.', type(exc).__name__)
        return lexical[:limit]
    if not ranked:
        return lexical[:limit]
    scores = {row['id']: 1.4 / (24 + rank) for rank, row in enumerate(lexical)}
    for rank, identifier in enumerate(ranked):
        scores[identifier] = scores.get(identifier, 0) + 1 / (24 + rank)
    ids = sorted(scores, key=scores.get, reverse=True)[:limit]
    rows = db.execute(SOURCES + ' WHERE c.id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
    lookup = {row['id']: dict(row) for row in rows}
    return [lookup[identifier] for identifier in ids]
