"""Optional local CrossEncoder in a bounded, terminable process."""
import logging
import multiprocessing as mp
import os
from pathlib import Path
from threading import Lock

MODEL = 'BAAI/bge-reranker-v2-m3'
_lock = Lock()
logger = logging.getLogger(__name__)


def _score(path, query, passages, output):
    try:
        os.environ['TOKENIZERS_PARALLELISM']='false'
        import torch
        from sentence_transformers import CrossEncoder
        torch.set_num_threads(2)
        model = CrossEncoder(path, local_files_only=True, device='cpu')
        scores = model.predict([(query, body) for body in passages], batch_size=4)
        output.send(('ok', [float(value) for value in scores]))
    except Exception as exc:
        output.send(('failed',type(exc).__name__))
    finally:
        output.close()


def rerank(query, rows, cache, timeout=None):
    from python_controller.knowledge.settings import option
    if timeout is None:
        try:
            timeout = max(1., min(120., float(option('DIG_KNOWLEDGE_RERANK_TIMEOUT_S','15'))))
        except ValueError:
            timeout = 15.
    directory = Path(cache)/'bge-reranker-v2-m3'
    if not directory.is_dir():
        return [dict(row,rerank_status='unavailable') for row in rows]
    if not _lock.acquire(blocking=False):
        return [dict(row,rerank_status='busy') for row in rows]
    process = None
    receiver = sender = None
    try:
        context = mp.get_context('spawn')
        receiver,sender = context.Pipe(duplex=False)
        process = context.Process(target=_score,args=(str(directory),query,[r['body'] for r in rows],sender),daemon=True)
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            return [dict(row,rerank_status='timeout') for row in rows]
        status,scores = receiver.recv()
        import math
        if status != 'ok' or len(scores) != len(rows) or not all(math.isfinite(s) for s in scores):
            return [dict(row,rerank_status='failed') for row in rows]
        return sorted([dict(row,rerank_score=score,rerank_status='ready') for row,score in zip(rows,scores)],key=lambda row:row['rerank_score'],reverse=True)
    except (OSError,EOFError,ValueError,RuntimeError):
        return [dict(row,rerank_status='failed') for row in rows]
    finally:
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
        for connection in (receiver,sender):
            if connection is not None:
                connection.close()
        _lock.release()
