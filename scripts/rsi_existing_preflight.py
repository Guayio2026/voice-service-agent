"""Preflight for the existing-data RSI cycle. No cloud calls, no writes outside a temp DB.

Validates the three suites against `rsi_loop.load_suite`, checks cross-suite
family/utterance overlap, and counts how many model calls each turn would make
by running the real Agent with a stub client.
"""
import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rsi_loop import load_suite, engine_hash  # noqa: E402
from business_agent import Agent  # noqa: E402
from server import Backend  # noqa: E402


class Stub:
    """Deterministic stand-in: counts calls, never touches the network."""

    model = 'stub-unclear'

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=220):
        self.calls += 1
        return {'text': json.dumps({'intent': 'unclear', 'topics': [], 'issue_numbers': []}),
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                'finish_reason': 'stop', 'model': self.model}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def walk(suite):
    """Run one pass of a suite with the stub; report calls and reachability."""
    rows = []
    for case in suite['cases']:
        with tempfile.TemporaryDirectory() as temp:
            backend = Backend(Path(temp) / 'business.sqlite3')
            app = case.get('application_id', 'QUAL-001')
            sid = backend.call('start_session', {'application_id': app})['session_id']
            agent = Agent(backend, sid, app)
            stub = Stub()
            asyncio.run(agent.turn('开始通话', stub, False))
            if case.get('identity') == 'verified':
                backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
                agent.step = 'availability'
            for n, turn in enumerate(case['turns']):
                before = stub.calls
                try:
                    asyncio.run(agent.turn(turn['text'], stub, False))
                    rows.append({
                        'case': case['id'], 'turn': n, 'model_calls': stub.calls - before,
                        'decision': agent.last_decision,
                        'classifier_context': bool(agent.classifier_messages),
                        'asserts_decision': 'decision' in turn['expect'],
                        'step': agent.step,
                        'identity': backend.identity_status(sid),
                    })
                except Exception as exc:  # pragma: no cover - surfaced as a preflight error
                    rows.append({'case': case['id'], 'turn': n, 'error': type(exc).__name__,
                                 'model_calls': stub.calls - before})
                    break
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'data/rsi-existing/config.json')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    paths = {role: (args.config.parent / config[role]).resolve()
             for role in ('development', 'validation', 'holdout')}
    suites = {role: load_suite(path, role) for role, path in paths.items()}

    families = {r: {c['family'] for c in s['cases']} for r, s in suites.items()}
    texts = {r: {t['text'] for c in s['cases'] for t in c['turns']} for r, s in suites.items()}
    overlap = {'family': [], 'text': []}
    roles = list(paths)
    for i in range(len(roles)):
        for j in range(i):
            a, b = roles[i], roles[j]
            for f in families[a] & families[b]:
                overlap['family'].append([a, b, f])
            for t in texts[a] & texts[b]:
                overlap['text'].append([a, b, t])

    plan, per_pass = {}, {}
    for role in roles:
        rows = walk(suites[role])
        per_pass[role] = sum(r.get('model_calls', 0) for r in rows)
        plan[role] = rows

    repeat = config['repeat']
    # New gate: the candidate replays the development suite, so development is
    # scored twice. Keep this formula in sync with src/rsi_loop.run().
    projected = (per_pass['development'] * repeat * 2 + 1
                 + per_pass['validation'] * repeat * 2
                 + per_pass['holdout'] * repeat * 2
                 + per_pass['validation'])

    report = {
        'config': str(args.config),
        'suite_sha256': {r: sha(paths[r]) for r in roles},
        'engine_hash': engine_hash(),
        'cases': {r: len(suites[r]['cases']) for r in roles},
        'model_calls_per_pass': per_pass,
        'repeat': repeat,
        'projected_calls_including_proposal_and_post_release': projected,
        'max_calls': config['max_calls'],
        'projected_within_call_cap': projected <= config['max_calls'],
        'cross_split_overlap': overlap,
        'turns_asserting_decision': sum(1 for r in roles for x in plan[r] if x.get('asserts_decision')),
        'turns_without_classifier_context': [
            {'role': r, **x} for r in roles for x in plan[r]
            if x.get('asserts_decision') and not x.get('classifier_context')],
        'dry_run': plan,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if overlap['family'] or overlap['text'] or not report['projected_within_call_cap']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
