"""Explicit model preparation; runtime search never downloads models."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true',required=True)
    parser.add_argument('--reranker',action='store_true')
    parser.add_argument('--cache',type=Path,default=Path('logs/knowledge-models'))
    args=parser.parse_args()
    if args.reranker:
        from huggingface_hub import snapshot_download
        from python_controller.knowledge.reranker import MODEL
        directory=args.cache/'bge-reranker-v2-m3'
        snapshot_download(MODEL,local_dir=directory,allow_patterns=['*.json','*.safetensors','sentencepiece*','*.model'])
        metadata=directory/'.cache/huggingface/download/config.json.metadata'
        revision=metadata.read_text().splitlines()[0] if metadata.exists() else 'unknown'
        (directory/'rag-model-manifest.json').write_text(json.dumps({'model':MODEL,'revision':revision}),encoding='utf-8')
    else:
        from python_controller.knowledge.vector import VectorRanker
        VectorRanker(args.cache,download=True)._model()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
