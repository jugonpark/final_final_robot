"""Measure FTS and the optional local model against fixed repository queries."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from python_controller.knowledge.library import KnowledgeLibrary
from python_controller.knowledge.vector import VectorRanker

QUERIES = (
    ('레이더 udp 8891 발행자', 'RADAR_OPERATION_GUIDE.md'),
    ('bpmCfg', 'RADAR_OPERATION_GUIDE.md'),
    ('엔코더 정책 FILTERED_TEST', 'CONTROL_SIMPLE_V2_PLAN.md'),
    ('C6 하드웨어 시험 준비', 'C6_HARDWARE_PREFLIGHT.md'),
    ('수동 시험 정책', 'ARCHITECTURE.md'),
    ('레이더 UNKNOWN 이동 차단', 'RADAR_LLM_INTEGRATION.md'),
    ('UDP watchdog heartbeat', 'CONTROL_SIMPLE_V2_PLAN.md'),
    ('motor thermal latch inspection', 'benchmark-en.md'),
    ('과열 보호가 동작하면 어떻게 되나요', 'benchmark-en.md'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vector', action='store_true')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--database', type=Path, default=Path('.tmp/knowledge-evaluation.sqlite3'))
    parser.add_argument('--cache', type=Path, default=Path('logs/knowledge-models'))
    args = parser.parse_args()
    library = KnowledgeLibrary(args.database, Path(__file__).resolve().parents[2])
    library.vector = None
    # This fixture measures cross-language search without relying on a document upload.
    library.register('benchmark-en.md', b'# Motor overheat protection\nA thermal latch opens when the motor overheats. Inspect the motor and power system before service.')
    for mode in ('FTS5', 'HYBRID') if args.vector else ('FTS5',):
        if mode == 'HYBRID':
            library.vector = VectorRanker(args.cache, download=args.download)
        records = []
        for question, expected in QUERIES:
            start = perf_counter()
            found = library.search_robot_documents(question, limit=3)
            records.append({'question': question, 'expected': expected,
                            'top_three': [Path(row['path']).name for row in found],
                            'ms': round((perf_counter() - start) * 1000, 1)})
        print(json.dumps({'mode': mode, 'model_available': not library.vector or not library.vector.unavailable,
                          'hits_at_3': sum(row['expected'] in row['top_three'] for row in records),
                          'total': len(records), 'results': records}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
