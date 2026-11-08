"""Two-version diagnostic replay: fixed prompts, alternating order, full evidence.

Read-only with respect to product source. Versions are loaded by explicit ID from
the sandbox store (never via active), and every instance uses its own session and
a temporary business database. No candidate generation, no release, no pointer
change.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from business_agent import Agent                     # noqa: E402
from evolution import BudgetClient                   # noqa: E402
from evolution_store import Store, dumps, now        # noqa: E402
from rsi_loop import judge, load_suite               # noqa: E402
from server import Backend                           # noqa: E402


class Stub:
    model = 'stub-offline'

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=220):
        self.calls += 1
        return {'text': json.dumps({'intent': 'unclear', 'topics': [], 'issue_numbers': []}),
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                'finish_reason': 'stop', 'model': self.model}


def run_instance(version, case, client, attempt, batch, arm, run_id):
    """One full scenario instance on an isolated session and temp business DB."""
    trace = {'run_id': run_id, 'batch': batch, 'attempt': attempt, 'arm': arm,
             'version_id': version['id'], 'prompt_hash': version['content_hash'],
             'case': case['id'], 'family': case['family'], 'turns': [], 'errors': []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as temp:
        backend = Backend(Path(temp) / 'business.sqlite3')
        app = case.get('application_id', 'QUAL-001')
        sid = backend.call('start_session', {'application_id': app})['session_id']
        agent = Agent(backend, sid, app, classifier_prompt=version['prompt'], version_id=version['id'])
        asyncio.run(agent.turn('开始通话', client, False))
        if case.get('identity') == 'verified':
            backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
            agent.step = 'availability'
        trace['loaded_version_id'] = agent.version_id
        trace['prompt_injected'] = agent.classifier_prompt == version['prompt']

        original, calls, events = backend.call, [], []

        def traced(name, params):
            calls.append(name)
            event = {'tool': name, 'parameters': params}
            events.append(event)
            try:
                result = original(name, params)
                event['result'] = result
                return result
            except Exception as exc:
                event['error'] = type(exc).__name__
                raise

        backend.call = traced
        for n, turn in enumerate(case['turns']):
            calls.clear(); events.clear()
            entry = {'turn': n, 'text': turn['text'], 'expect': turn['expect']}
            began = time.monotonic()
            try:
                answer = asyncio.run(agent.turn(turn['text'], client, False))
                records = original('get_results', {'session_id': sid})['results']
                actual = {'reply': answer['text'], 'identity': backend.identity_status(sid),
                          'records': len(records), 'step': agent.step, 'decision': agent.last_decision,
                          'calls': calls[:], 'tool_events': events[:], 'payloads': records}
                checks = judge(turn['expect'], actual)
                entry.update(actual=actual, checks=checks, passed=all(checks.values()),
                             ms=round((time.monotonic() - began) * 1000),
                             classifier_context=bool(agent.classifier_messages),
                             last_model=agent.last_model, last_error=agent.last_error,
                             usage=dict(agent.usage or {}))
            except Exception as exc:
                entry.update(execution_error=type(exc).__name__ + ': ' + str(exc),
                             ms=round((time.monotonic() - began) * 1000))
                trace['errors'].append({'turn': n, 'error': entry['execution_error']})
                trace['turns'].append(entry)
                break
            trace['turns'].append(entry)
    trace['ms'] = round((time.monotonic() - started) * 1000)
    return trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--versions-db', type=Path, default=ROOT / 'runtime/rsi-existing-data-r2.sqlite3')
    parser.add_argument('--ledger-db', type=Path, default=ROOT / 'runtime/rsi-diagnose-20261108.sqlite3')
    parser.add_argument('--development', type=Path, default=ROOT / 'data/rsi-existing-r2/development.json')
    parser.add_argument('--baseline', default='base-6b13e03f1b116f21')
    parser.add_argument('--candidate', default='candidate-561a113dbc8f')
    parser.add_argument('--out', type=Path, default=ROOT / 'runtime/rsi-diagnose-20261108')
    parser.add_argument('--attempts', type=int, default=10)
    parser.add_argument('--batch', type=int, default=1)
    parser.add_argument('--batches', type=int, default=2)
    parser.add_argument('--max-calls', type=int, default=80)
    parser.add_argument('--token-budget', type=int, default=520000)
    parser.add_argument('--stub', action='store_true', help='count calls offline, no provider')
    args = parser.parse_args()

    suite = load_suite(args.development, 'development')
    ids = {'E-dev-complaint', 'E-dev-auth-number', 'E-dev-view-materials', 'E-dev-third-party-callback'}
    cases = [c for c in suite['cases'] if c['id'] in ids]
    if len(cases) != 4:
        raise SystemExit('expected the four diagnostic cases')

    versions = Store(args.versions_db)
    baseline = versions.version(args.baseline)
    candidate = versions.version(args.candidate)
    if candidate['parent'] != baseline['id']:
        raise SystemExit('candidate is not a child of the frozen baseline')
    active_before = versions.active()

    ledger = Store(args.ledger_db)
    metadata = {'kind': 'two_version_diagnostic', 'attempts': args.attempts, 'batch': args.batch,
                'baseline': baseline['id'], 'candidate': candidate['id'],
                'hashes': {baseline['id']: baseline['content_hash'], candidate['id']: candidate['content_hash']},
                'source_hash': __import__('rsi_loop').engine_hash(),
                'model': None, 'sampling': 'request_body has no temperature/top_p/seed; provider defaults',
                'cases': [c['id'] for c in cases], 'stub': args.stub}
    run_id = ledger.start_run('diagnostic', baseline['id'], metadata)
    args.out.mkdir(parents=True, exist_ok=True)
    ledger_path = args.out / f'batch{args.batch}-{run_id}.jsonl'
    ledger_path.write_text('', encoding='utf-8')

    if args.stub:
        client = Stub()
        budget = client
    else:
        from qwen_client import load_config
        from text_client import text_client
        load_config()
        raw = text_client()
        metadata['model'] = raw.model
        budget = BudgetClient(ledger, run_id, raw, args.max_calls, args.token_budget)

    started = time.monotonic()
    written = 0
    lo = (args.batch - 1) * args.attempts // args.batches
    hi = args.batch * args.attempts // args.batches
    try:
        for attempt in range(lo, hi):
            order = [(baseline, 'baseline'), (candidate, 'candidate')]
            if attempt % 2:
                order.reverse()                      # alternate which arm goes first
            rotated = cases[attempt % len(cases):] + cases[:attempt % len(cases)]
            for case in rotated:
                for version, arm in order:
                    trace = run_instance(version, case, budget, attempt, args.batch, arm, run_id)
                    with ledger_path.open('a', encoding='utf-8') as handle:
                        handle.write(json.dumps(trace, ensure_ascii=False) + '\n')
                    written += 1
    except Exception as exc:
        metadata['aborted'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        calls = ledger.rows('SELECT status,usage,reserved_tokens FROM evolution_calls WHERE run_id=?', (run_id,))
        tokens = sum(json.loads(c['usage'] or '{}').get('total_tokens', 0) for c in calls)
        summary = {'run_id': run_id, 'instances': written, 'calls': len(calls), 'tokens': tokens,
                   'reserved': sum(c['reserved_tokens'] for c in calls),
                   'statuses': sorted({c['status'] for c in calls}),
                   'ms': round((time.monotonic() - started) * 1000),
                   'active_before': active_before, 'active_after': versions.active(),
                   'file': str(ledger_path), 'metadata': metadata}
        ledger.finish_run(run_id, 'diagnostic_done', summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
