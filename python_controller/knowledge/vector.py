"""Optional local multilingual embeddings and hybrid ranking for small libraries."""
import logging
import os
from pathlib import Path
from threading import Lock
import multiprocessing as mp
import atexit

MODEL = 'intfloat/multilingual-e5-small'
MODEL_REV = 'ccc66d3'
logger = logging.getLogger(__name__)
_registry_lock = Lock()
_services = {}


class VectorRanker:
    def __init__(self, cache, *, download=False):
        self.cache = Path(cache)
        self.download = download
        self.lock = Lock()
        self.encoder = None
        self.unavailable = False

    @classmethod
    def configured(cls, cache):
        from python_controller.knowledge.settings import option
        return cls(cache) if option('DIG_KNOWLEDGE_VECTOR','0') == '1' else None

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

    def _encode_local(self, texts):
        import numpy as np
        with self.lock:
            if self.encoder is None:
                self.encoder = self._model()
            vectors = list(self.encoder.embed(texts, batch_size=8))
            result = []
            for value in vectors:
                array = np.asarray(value, dtype='<f4')
                if array.shape != (384,) or not np.isfinite(array).all():
                    raise ValueError('Invalid E5 embedding shape or values')
                norm = np.linalg.norm(array)
                if not norm:
                    raise ValueError('Zero E5 embedding')
                result.append((array/norm).astype('<f4').tobytes())
            return result

    def encode(self, texts):
        # Loading/inference runs outside the controller's Python interpreter/GIL.
        key=(str(self.cache.resolve()), self.download)
        with _registry_lock:
            service=_services.get(key)
            if service is None or not service[0].is_alive():
                context=mp.get_context('spawn')
                parent,child=context.Pipe()
                process=context.Process(target=_embedding_worker,args=(str(self.cache),self.download,child),daemon=True)
                process.start()
                child.close()
                service=(process,parent,Lock())
                _services[key]=service
        process,connection,lock=service
        with lock:
            connection.send(texts)
            if not connection.poll(90):
                process.terminate()
                process.join(timeout=1)
                raise TimeoutError('Local E5 worker timed out')
            status,result=connection.recv()
            if status != 'ok':
                raise ValueError('Local E5 unavailable: '+result)
            return result

    def rank(self, db, query, limit=20, *, variant=None):
        import numpy as np
        if self.unavailable:
            return []
        model_version = f'{MODEL}:{MODEL_REV}'
        scope = "c.active=1 AND d.status='scoped' AND d.variant_id=?" if variant else "c.active=1 AND d.status='indexed' AND d.trust NOT IN ('hardware_reference','review_draft')"
        params = (model_version, variant) if variant else (model_version,)
        rows = db.execute("SELECT c.id,e.vector FROM chunks c JOIN documents d ON d.id=c.document_id "
                          "JOIN embeddings e ON e.chunk_id=c.id AND e.model=? WHERE (d.embedding_status='ready' OR EXISTS(SELECT 1 FROM chunks staged WHERE staged.document_id=d.id AND staged.active=2)) AND " + scope, params)
        first = rows.fetchone()
        if first is None:
            return []
        from itertools import chain
        rows = chain((first,), rows)
        query_vector = np.frombuffer(self.encode(['query: '+query])[0], dtype='<f4')
        import heapq
        best = []
        for row in rows:
            vector = np.frombuffer(row['vector'], dtype='<f4')
            if vector.shape != (384,) or not np.isfinite(vector).all():
                continue
            item = (float(np.dot(query_vector, vector)), row['id'])
            if len(best) < limit:
                heapq.heappush(best, item)
            elif item > best[0]:
                heapq.heapreplace(best, item)
        return [identifier for _, identifier in sorted(best, reverse=True)]


def hybrid_rank(db, query, lexical, vector, limit):
    from python_controller.knowledge.library import SOURCES

    try:
        ranked = vector.rank(db, query, 20)
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


def _embedding_worker(cache, download, connection):
    os.environ['TOKENIZERS_PARALLELISM']='false'
    ranker=VectorRanker(cache,download=download)
    try:
        while True:
            texts=connection.recv()
            if texts is None:
                break
            try:
                connection.send(('ok',ranker._encode_local(texts)))
            except Exception as exc:
                connection.send(('failed',type(exc).__name__))
    except (EOFError,OSError):
        pass
    finally:
        connection.close()


def close_workers():
    for process,connection,_ in list(_services.values()):
        try:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            connection.close()
        except (OSError,ValueError):
            pass
    _services.clear()


atexit.register(close_workers)
