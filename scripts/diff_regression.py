"""逐例对比两轮 100 条回归结果。

用法：
  PREV_DIR=data/regression-20261108 CUR_DIR=data/verify-20261108-r3/full \
  .venv/Scripts/python.exe scripts/diff_regression.py
两轮目录下都按 v1/v2/v3 子目录存放 results-v*.json。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREV = Path(os.environ.get('PREV_DIR', 'data/regression-20261108'))
CUR = Path(os.environ.get('CUR_DIR', 'data/verify-20261108-r3/full'))
OUT = Path(os.environ.get('DIFF_OUT', 'data/verify-20261108-r3/diff.json'))

RANK = {'HARNESS_ERROR': 0, 'FAIL': 0, 'BLOCKED': 1, 'PASS_PARTIAL': 2, 'PASS': 3}


def load(root, tag):
    for p in (root / tag / f'results-{tag}.json', root / f'full-{tag}' / f'results-{tag}.json'):
        if p.exists():
            break
    else:
        raise FileNotFoundError(f'{tag}: {root}')
    d = json.loads(p.read_text(encoding='utf-8'))
    rows = {}
    for r in d['rows']:
        cid = r.get('id') or r.get('case_id')
        rows[cid] = {
            'verdict': r['verdict'],
            'attempts': [a['verdict'] for a in r.get('attempts', [])] or [r['verdict']],
            'fails': [f.get('assert') for f in (r.get('fails') or [])],
            'family': r.get('family'),
        }
    return d, rows


def main():
    out = {'prev': str(PREV), 'cur': str(CUR), 'suites': {}, 'changed': []}
    for tag in ('v1', 'v2', 'v3'):
        pd, p = load(PREV, tag)
        cd, c = load(CUR, tag)
        assert set(p) == set(c), f'{tag} 用例集合不一致: {set(p) ^ set(c)}'
        counts = {}
        for r in cd['rows']:
            counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
        prev_counts = {}
        for r in pd['rows']:
            prev_counts[r['verdict']] = prev_counts.get(r['verdict'], 0) + 1
        out['suites'][tag] = {'count': len(c), 'prev': prev_counts, 'cur': counts}
        for cid in sorted(c):
            a, b = p[cid], c[cid]
            if a['verdict'] != b['verdict']:
                out['changed'].append({
                    'suite': tag, 'id': cid, 'family': a.get('family'),
                    'prev': a['verdict'], 'cur': b['verdict'],
                    'prev_attempts': a['attempts'], 'cur_attempts': b['attempts'],
                    'cur_fails': b['fails'],
                    'direction': 'improved' if RANK[b['verdict']] > RANK[a['verdict']] else 'regressed',
                })
    prev_tot, cur_tot = {}, {}
    for v in out['suites'].values():
        for k, n in v['prev'].items():
            prev_tot[k] = prev_tot.get(k, 0) + n
        for k, n in v['cur'].items():
            cur_tot[k] = cur_tot.get(k, 0) + n
    out['total'] = {'prev': prev_tot, 'cur': cur_tot}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')

    for tag, v in out['suites'].items():
        print(f"{tag}: {v['count']} 条  上一轮 {v['prev']}  ->  本轮 {v['cur']}")
    print('合计 上一轮', prev_tot, '-> 本轮', cur_tot)
    imp = [x for x in out['changed'] if x['direction'] == 'improved']
    reg = [x for x in out['changed'] if x['direction'] == 'regressed']
    print(f'\n改善 {len(imp)} 条')
    for x in imp:
        print(f"  + {x['id']} {x['prev']} -> {x['cur']} {x['cur_attempts']}")
    print(f'退步 {len(reg)} 条')
    for x in reg:
        print(f"  - {x['id']} {x['prev']} -> {x['cur']} {x['cur_attempts']}")
        for f in x['cur_fails'][:4]:
            print(f"      × {f[:80]}")
    print('\n->', OUT)


if __name__ == '__main__':
    main()
