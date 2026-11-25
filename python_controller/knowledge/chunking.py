"""Structure-preserving chunks measured with the locally installed E5 tokenizer."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

VERSION = 'structure-e5-v2.1'


@lru_cache(maxsize=4)
def tokenizer(cache):
    from tokenizers import Tokenizer
    files = sorted(Path(cache).glob('models--intfloat--multilingual-e5-small/snapshots/*/tokenizer.json'))
    if not files:
        raise FileNotFoundError('E5 tokenizer is not installed; run knowledge.install_models --download')
    result = Tokenizer.from_file(str(files[-1]))
    result.no_truncation()
    result.no_padding()
    return result


def count(text, cache):
    return len(tokenizer(str(cache)).encode(text).ids)


def chunks(groups, cache, target=400, overlap=40):
    if not 300 <= target <= 500 or not 0 <= overlap < target // 2:
        raise ValueError('Chunk target must be 300..500 with overlap below half the target')
    for heading, page, line, body in groups:
        prefix = f'passage: {heading}\n'
        lines = body.splitlines(keepends=True)
        blocks, current, fenced = [], [], False
        for offset, value in enumerate(lines):
            if not current:
                start = offset
            current.append(value)
            if value.lstrip().startswith('```'):
                fenced = not fenced
            if not fenced and (not value.strip() or offset == len(lines)-1):
                blocks.append((''.join(current).strip(), start))
                current = []
        if current:
            blocks.append((''.join(current).strip(), start))
        pending, pending_line = '', 0
        for block, offset in blocks:
            if not block:
                continue
            if count(prefix + pending + '\n\n' + block, cache) <= target:
                if not pending:
                    pending_line = offset
                pending = (pending + '\n\n' + block).strip()
                continue
            if pending:
                yield _row(heading, page, line, pending_line, pending, cache, None)
                pending = ''
            if count(prefix + block, cache) <= target:
                pending, pending_line = block, offset
                continue
            # Oversize structures are split on complete lines when possible.
            table = block.splitlines()
            header = '\n'.join(table[:2]) + '\n' if len(table) > 2 and re.search(r'\|?\s*:?-{3,}', table[1]) else ''
            fragment, fragment_line = '', offset
            for index, value in enumerate(table):
                if fragment and count(prefix + fragment + '\n' + value, cache) > target:
                    yield _row(heading, page, line, fragment_line, fragment, cache, 'split_structure')
                    fragment, fragment_line = header, offset + index
                if count(prefix + fragment + value, cache) > target:
                    # Long single lines use real token offsets, with bounded overlap.
                    encoded = tokenizer(str(cache)).encode(value)
                    capacity = max(32, target-count(prefix + fragment, cache)-4)
                    for first in range(0, len(encoded.ids), max(1, capacity-overlap)):
                        offsets = encoded.offsets[first:first+capacity]
                        if offsets:
                            part = value[offsets[0][0]:offsets[-1][1]]
                            yield _row(heading, page, line, offset+index, fragment+part, cache, 'split_long_line')
                    fragment = ''
                else:
                    fragment = (fragment + '\n' + value).strip()
            if fragment:
                yield _row(heading, page, line, fragment_line, fragment, cache, 'split_structure')
        if pending:
            yield _row(heading, page, line, pending_line, pending, cache, None)


def _row(heading, page, line, offset, body, cache, warning):
    if body.count('```') % 2:
        warning = (warning+', ' if warning else '')+'unclosed_fence'
    return dict(heading=heading.rsplit(' / ',1)[-1][:240], heading_path=heading, page=page, line=None if line is None else line+offset,
                body=body, chunk_hash=sha256((heading+'\n'+body).encode()).hexdigest(),
                token_count=count(f'passage: {heading}\n{body}', cache), split_warning=warning)
