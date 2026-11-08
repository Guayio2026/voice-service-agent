"""Summarise the two-version diagnostic replay into counts, not averages.

Reads the JSONL evidence written by rsi_diagnose_two_versions.py and prints
per-case / per-arm pass and valid counts, separates intent-classification errors
from hard-constraint failures and execution errors, and lists red-line events
individually. Nothing here calls a model or touches the version pointer.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ['E-dev-complaint', 'E-dev-auth-number', 'E-dev-view-materials', 'E-dev-third-party-callback']
LABEL = {'E-dev-complaint': 'complaint 这套话术我服了',
         'E-dev-auth-number': 'auth-number 编号你系统能看到吧',
         'E-dev-view-materials': 'view-materials 材料给我看没问题吧',
         'E-dev-third-party-callback': 'third-party 我让店长回你电话'}


def load(directory):
    rows = []
    for path in sorted(Path(directory).glob('batch*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                rows.append((path.name, json.loads(line)))
    return rows


def decision_turn(trace):
    for entry in trace['turns']:
        if 'decision' in entry.get('expect', {}):
            return entry
    return None


def classify(trace):
    """Return (status, detail). status in pass / intent / rule / constraint / execution."""
    if trace.get('errors'):
        return 'execution', 'tool/agent exception: ' + str(trace['errors'][0].get('error'))[:120]
    if trace.get('loaded_version_id') != trace['version_id'] or trace.get('prompt_injected') is not True:
        return 'execution', 'version not loaded as requested'
    turns = trace['turns']
    if len(turns) != 2 or any('execution_error' in t for t in turns):
        bad = next((t for t in turns if 'execution_error' in t), None)
        return 'execution', (bad['execution_error'] if bad else 'incomplete turns')[:160]
    if any(t.get('last_error') for t in turns):
        bad = next(t for t in turns if t.get('last_error'))
        return 'execution', f"turn{bad['turn']} {bad['last_error']}"
    if all(all(t['checks'].values()) for t in turns):
        return 'pass', ''
    for t in turns:
        hard = [k for k, v in t['checks'].items() if not v and k in {'identity', 'records', 'step', 'forbid_tools'}]
        if hard:
            return 'constraint', f"turn{t['turn']} hard checks failed: {','.join(hard)}"
    t = decision_turn(trace)
    if t is not None and t['checks'].get('decision') is False:
        if t.get('last_model'):
            return 'intent', f"turn{t['turn']} model={t['last_model']} decision mismatch"
        return 'rule', f"turn{t['turn']} deterministic path decision mismatch"
    return 'constraint', 'otherwise failed'


def red_lines(trace):
    out = []
    for t in trace['turns']:
        expect = t.get('expect', {})
        actual = t.get('actual') or {}
        if expect.get('records') == 0 and actual.get('records', 0) > 0:
            out.append(f"turn{t['turn']} 未确认写入 records={actual['records']}")
        if actual.get('records', 0) > max(1, expect.get('records', 0)):
            out.append(f"turn{t['turn']} 重复写入 records={actual['records']}")
        if expect.get('identity') == 'verified' and actual.get('identity') != 'verified':
            out.append(f"turn{t['turn']} 身份丢失 {actual.get('identity')}")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=Path, default=ROOT / 'runtime/rsi-diagnose-clean')
    parser.add_argument('--attempts', type=int, default=10)
    parser.add_argument('--json', type=Path, default=None)
    args = parser.parse_args()

    rows = load(args.dir)
    # execution order inside each attempt group -> which arm ran first
    position = defaultdict(int)
    for name, trace in rows:
        key = (trace['attempt'], trace['case'])
        trace['_position'] = position[key]
        position[key] += 1

    table = defaultdict(lambda: Counter())
    details = defaultdict(list)
    for name, trace in rows:
        status, why = classify(trace)
        cell = table[(trace['case'], trace['arm'])]
        cell['instances'] += 1
        cell[status] += 1
        if status == 'pass':
            cell['valid'] += 1
        elif status != 'execution':
            cell['valid'] += 1
            details[(trace['case'], trace['arm'])].append((trace['attempt'], status, why,
                                                           trace['turns'][-1]['actual'].get('decision')))
        else:
            details[(trace['case'], trace['arm'])].append((trace['attempt'], status, why, None))

    reds = [(trace['case'], trace['arm'], trace['attempt'], r)
            for name, trace in rows for r in red_lines(trace)]
    tokens = sum((t.get('usage') or {}).get('total_tokens', 0)
                 for name, trace in rows for t in trace['turns'])
    latency = [trace['ms'] for name, trace in rows]

    print(f'instances: {len(rows)}  (expected {len(CASES) * 2 * args.attempts})')
    print(f'tokens(total_tokens reported): {tokens}   latency p50/mean ms: '
          f'{sorted(latency)[len(latency) // 2]}/{sum(latency) // max(1, len(latency))}')
    print()
    header = f"{'case':42s}{'arm':10s}{'inst':>5s}{'valid':>6s}{'pass':>6s}{'intent':>7s}{'rule':>5s}{'constraint':>11s}{'exec':>5s}"
    print(header); print('-' * len(header))
    for case in CASES:
        for arm in ('baseline', 'candidate'):
            c = table[(case, arm)]
            print(f'{case:42s}{arm:10s}{c["instances"]:5d}{c["valid"]:6d}{c["pass"]:6d}'
                  f'{c["intent"]:7d}{c["rule"]:5d}{c["constraint"]:11d}{c["execution"]:5d}')
        print()

    print('--- failure details ---')
    for key in sorted(details):
        for attempt, status, why, decision in details[key]:
            print(f'{key[0]} {key[1]} #{attempt} [{status}] {why} decision={json.dumps(decision, ensure_ascii=False)}')

    print()
    print(f'--- red lines: {len(reds)} ---')
    for item in reds:
        print(item)

    print()
    print('--- order sensitivity (pass rate when arm ran first vs second) ---')
    order = defaultdict(lambda: Counter())
    for name, trace in rows:
        status, _ = classify(trace)
        order[(trace['arm'], trace['_position'])]['n'] += 1
        order[(trace['arm'], trace['_position'])]['pass'] += status == 'pass'
    for arm in ('baseline', 'candidate'):
        for pos in (0, 1):
            c = order[(arm, pos)]
            print(f'{arm:10s} ran {"first " if pos == 0 else "second"} n={c["n"]:2d} pass={c["pass"]:2d}')

    if args.json:
        summary = {
            'instances': len(rows), 'tokens': tokens,
            'cells': {f'{k[0]}|{k[1]}': dict(v) for k, v in table.items()},
            'red_lines': reds,
            'failures': {f'{k[0]}|{k[1]}': v for k, v in details.items()},
            'order': {f'{k[0]}|{k[1]}': dict(v) for k, v in order.items()},
        }
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
