"""Freeze known evaluation data and register external holdouts without displaying cases."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'data/evaluation-protocol'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def code_digest():
    h = hashlib.sha256()
    for path in sorted((ROOT / 'src').rglob('*.py')):
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['freeze', 'register', 'consume'])
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    if args.action == 'freeze':
        source = ROOT / 'data/generated-100/scenarios.json'
        rows = json.loads(source.read_text(encoding='utf-8'))
        groups = {}
        # Group by behavioral contract, never random utterance-level splitting.
        for row in rows:
            signature = json.dumps({'mode': row['execution_mode'],
                'checks': sorted(set(str(a) for a in row.get('final_assertions', []))),
                'required': sorted(set(c['tool'] if 'tool' in c else c.get('name', 'unknown')
                    for t in row['turns'] for c in t.get('required_tool_calls', []) if isinstance(c, dict)))}, sort_keys=True)
            key = hashlib.sha256(signature.encode()).hexdigest()[:12]
            groups.setdefault(key, []).append(row['id'])
        manifest = {'role': 'known_regression', 'source': str(source.relative_to(ROOT)),
            'sha256': digest(source), 'count': len(rows),
            'grouping': 'candidate contract groups; manual semantic review required before any future split',
            'groups': groups, 'holdout_count': 0,
            'rule': 'All existing cases have been exposed; none may be relabeled blind.'}
        target = DEST / 'regression-manifest.json'
        if target.exists():
            raise SystemExit('Manifest already frozen; create a new version instead of overwriting.')
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Frozen {len(rows)} known cases; blind cases: 0')
    elif args.action == 'register':
        if not args.file or not args.file.is_file():
            raise SystemExit('--file must name an externally generated JSON file')
        rows = json.loads(args.file.read_text(encoding='utf-8'))
        if not isinstance(rows, list) or not rows:
            raise SystemExit('Expected a nonempty scenario array')
        if any(not isinstance(r, dict) or not r.get('semantic_family') for r in rows):
            raise SystemExit('Each external case must include semantic_family')
        known = json.loads((ROOT/'data/generated-100/scenarios.json').read_text(encoding='utf-8'))
        utterances = {t['user_input'] for r in known for t in r['turns']}
        if any(t.get('user_input') in utterances for r in rows for t in r.get('turns', [])):
            raise SystemExit('Exact known utterance overlap; independent semantic review required')
        key = digest(args.file)
        target = DEST / f'holdout-{key[:12]}.json'
        if target.exists():
            raise SystemExit('Already registered; cannot reset exposure state')
        target.write_text(json.dumps({'source': str(args.file.resolve()), 'sha256': key,
            'code_sha256': code_digest(), 'status': 'sealed_pending_independent_review',
            'count': len(rows), 'registered_at': datetime.now(timezone.utc).isoformat(),
            'limits': 'Exact overlap check only; independence and semantic overlap require external reviewer.'}, indent=2), encoding='utf-8')
        print(target)
    else:
        if not args.file or args.file.resolve().parent != DEST.resolve():
            raise SystemExit('--file must name a holdout manifest in data/evaluation-protocol')
        record = json.loads(args.file.read_text(encoding='utf-8'))
        if record.get('status') != 'sealed_pending_independent_review':
            raise SystemExit('Already exposed or not a holdout manifest')
        if digest(Path(record['source'])) != record['sha256'] or code_digest() != record['code_sha256']:
            raise SystemExit('Frozen data/code changed; do not evaluate as the registered version')
        record.update(status='exposed_regression_only', exposed_at=datetime.now(timezone.utc).isoformat())
        args.file.write_text(json.dumps(record, indent=2), encoding='utf-8')
        print('Marked exposed. Run evaluation once; future tuning uses this as regression only.')

if __name__ == '__main__':
    main()
