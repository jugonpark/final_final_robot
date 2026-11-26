"""Scoped rollback; default is read-only validation. Never resets Git/user edits."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'docs/verification/conversational_rag_recovery/rollback_manifest.json'


def checked_path(relative):
    path=ROOT/relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Unsafe rollback path')
    return path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    rows=json.loads(MANIFEST.read_text(encoding='utf-8'))['files'];prepared=[]
    for row in rows:
        target=checked_path(row['path']);backup=checked_path(row['backup']);before=backup.read_bytes()
        if sha256(before).hexdigest()!=row['before_sha256']:raise ValueError('Backup changed: '+row['path'])
        if sha256(target.read_bytes()).hexdigest()!=row['after_sha256']:raise ValueError('Later edits detected; no files changed: '+row['path'])
        prepared.append((target,before))
    if args.apply:
        for target,before in prepared:target.write_bytes(before)
        print(f'Restored {len(prepared)} pre-task files; new standalone files left in place.')
    else:print(f'CHECK ONLY: {len(prepared)} files match; no files changed. Keep preview stopped before --apply.')


if __name__=='__main__':main()
