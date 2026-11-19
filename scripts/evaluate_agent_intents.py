"""Measure the real Agent parser and routing using fixed model responses and no robot."""

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from statistics import median, quantiles
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, default=ROOT, help='Code snapshot to evaluate')
args = parser.parse_args()
sys.path.insert(0, str(args.repo.resolve()))

from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine
from python_controller.llm.agent_intent import NvidiaIntentInterpreter
from python_controller.llm.unified_agent import UnifiedAgent


def card_for(row):
    pending = row.get('context', {}).get('pending_plan')
    if not pending:
        return None
    return {'id': pending['plan_id'], 'revision': pending['revision'], 'actions': pending['actions']}


def comparable(actions):
    return [{key: Decimal(value) if key in ('distance_cm', 'angle_deg') else value
             for key, value in action.items()} for action in actions]


def expected_for(row):
    intent = row['expected_intent']
    fields = row['expected_fields']
    moves = fields['plan']['actions'] if intent == 'create_plan' else [fields['replacement']] if intent == 'modify_plan' else []
    # The supplied fixture predates the current repo's 5 cm and total-distance constraints.
    unsupported = [action['distance_cm'] for action in moves if action['type'] == 'move']
    if any(not 5 <= value <= 50 for value in unsupported) or sum(unsupported) > 100:
        return 'clarify'
    return intent


def outcome(result, desired):
    if result.get('edit'):
        return 'create_plan' if result['edit']['kind'] == 'append' else 'modify_plan'
    if result.get('intent'):
        return result['intent']['intent']
    if result.get('error') or result.get('reply'):
        return 'clarify' if desired == 'clarify' or result.get('reply') else 'error'
    return 'error'


def exact(row, result, observed):
    fields = row['expected_fields']
    if observed == 'create_plan':
        return comparable(result['edit']['actions']) == comparable(fields['plan']['actions'])
    if observed == 'modify_plan':
        index = fields['action_index'] - 1
        return (result['edit']['index'] == index and
                comparable([result['edit']['actions'][index]]) == comparable([fields['replacement']]))
    if observed == 'clarify':
        return True
    return all(result.get('intent', {}).get(key) == value for key, value in fields.items())


cases = [json.loads(line) for line in (ROOT / 'docs/agent-evaluation/gold_intent_cases.jsonl').read_text(encoding='utf-8').splitlines()]
counts = Counter()
rows = []
latencies = []
fixed_env = {'NVIDIA_API_KEY': 'fixture-key', 'NVIDIA_MODEL': 'fixture-model'}


class FixtureResponder:
    def respond(self, *_args, **_kwargs):
        return '확인 질문'
with (
    TemporaryDirectory() as folder,
    patch('socket.socket', side_effect=AssertionError('No real sockets')),
    patch('python_controller.llm_cli.load_environment', return_value=fixed_env),
    patch('python_controller.llm.nvidia_client.NvidiaPlanner.complete') as complete,
):
    engine = DashboardEngine(load_config(), folder, 'SYNTHETIC_PREVIEW', logger=False)
    agent = UnifiedAgent(engine, NvidiaIntentInterpreter(), FixtureResponder())
    try:
        for case in cases:
            desired = expected_for(case)
            start = perf_counter()
            if case.get('safety_route') == 'STOP_PRE_LLM':
                complete.reset_mock()
                with patch.object(agent, 'emergency') as emergency:
                    agent.submit(case['user'])
                good = emergency.call_count == 1 and not complete.called
                counts['stop_cases'] += 1
                counts['stop_passed'] += good
                observed = 'STOP_PRE_LLM' if good else 'STOP_ROUTE_MISMATCH'
            else:
                card = card_for(case)
                complete.return_value = json.dumps({'intent': case['expected_intent'], **case['expected_fields']})
                result = agent.graph.turn(case['user'], [], card)
                observed = outcome(result, desired)
                good = observed == desired and exact(case, result, observed)
                counts['classification_cases'] += 1
                counts['classification_exact'] += good
                counts['normalized_contract_cases'] += desired != case['expected_intent']
                counts['unnecessary_clarify'] += observed == 'clarify' and desired != 'clarify'
                counts['unexpected_edits'] += bool(result.get('edit')) and not good
                counts['nonclarify_cases'] += desired != 'clarify'
                if 'bad_model_output' in case:
                    complete.return_value = json.dumps(case['bad_model_output'])
                    bad = agent.graph.turn(case['user'], [], card)
                    counts['adversarial_cases'] += 1
                    counts['adversarial_effect_proposals'] += bool(bad.get('edit') or
                        (bad.get('intent') or {}).get('intent') in ('navigate', 'switch_mode', 'mode_then_plan', 'request_execution'))
                    counts['adversarial_motion_proposals'] += bool(bad.get('edit'))
            latencies.append((perf_counter() - start) * 1000)
            rows.append({'id': case['id'], 'expected': desired or 'STOP_PRE_LLM', 'observed': observed, 'pass': good})
    finally:
        agent.close()
        engine.close()

for key in ('stop_passed', 'unexpected_edits', 'unnecessary_clarify', 'adversarial_effect_proposals', 'adversarial_motion_proposals'):
    counts.setdefault(key, 0)
print(json.dumps({'backend': 'fixture responses through NvidiaIntentInterpreter and LangGraph',
                  'summary': counts,
                  'mock_ms': {'p50': round(median(latencies), 2), 'p95': round(quantiles(latencies, n=20)[18], 2)},
                  'cases': rows}, indent=2))
