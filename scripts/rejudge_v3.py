"""对已落盘的 v3 结果做"补齐判定器"复判。

背景：首轮执行时判定器缺少若干组合型规则，导致 15 条断言落到"无机械判定规则"
（记为 blocked），使 10 条用例只能给 PASS_PARTIAL。这里不改任何用例文本、
不重跑模型，只用已保存的逐轮快照 + 库内记录，把"原本 blocked 且现在有规则"
的断言重新判一次，并按"最差一次定级"重算 verdict。

原始结果 results-v3.json 保持不动，复判写 results-v3-rejudged.json。
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import run_v3 as V3                                                  # noqa: E402

D = ROOT / 'data/independent-v3'
SRC = Path(os.environ.get('V3_SRC', str(D / 'results-v3.json')))
OUT = Path(os.environ.get('V3_OUT', str(D / 'results-v3-rejudged.json')))


def ctx_from(attempt):
    snaps = attempt['snaps']
    log = []
    for s in snaps:
        for name in s.get('calls') or []:
            log.append({'turn': s['round'], 'tool': name, 'params': {}, 'fault': None})
    handoffs = snaps[-1]['handoffs'] if snaps else 0
    return {'snaps': snaps, 'log': log, 'records': attempt.get('records') or [],
            'handoffs': handoffs}


def rejudge_attempt(attempt):
    ctx = ctx_from(attempt)
    changed, results = [], []
    for r in attempt['assertions']:
        status, detail = r['status'], r.get('detail', '')
        if status == 'blocked' and 'is_responsible' not in r['assert']:
            sub = {**ctx}
            if r.get('turn'):
                sub['upto'] = r['turn']
            new, nd = V3.evaluate(r['assert'], sub)
            if new != 'blocked':
                changed.append({'assert': r['assert'], 'was': 'blocked',
                                'now': new, 'detail': nd})
                status, detail = new, nd
        results.append({**r, 'status': status, 'detail': detail})
    fails = [r for r in results if r['status'] == 'fail']
    blocks = [r for r in results if r['status'] == 'blocked']
    if attempt.get('crash'):
        verdict = 'BLOCKED'
    elif fails:
        verdict = 'FAIL'
    elif len(blocks) == len(results):
        verdict = 'BLOCKED'
    elif blocks:
        verdict = 'PASS_PARTIAL'
    else:
        verdict = 'PASS'
    return verdict, results, fails, blocks, changed


RANK = {'FAIL': 3, 'BLOCKED': 2, 'PASS_PARTIAL': 1, 'PASS': 0}


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    rows, diff = [], []
    for row in src['rows']:
        orig = row['verdict']
        new_attempts = []
        for a in row['attempts']:
            v, res, fails, blocks, changed = rejudge_attempt(a)
            new_attempts.append({**a, 'verdict_orig': a['verdict'], 'verdict': v,
                                 'assertions': res, 'fails': fails, 'blocked': blocks,
                                 'rejudged': changed})
        if new_attempts:
            verdict = max((x['verdict'] for x in new_attempts), key=lambda k: RANK[k])
        else:
            verdict = orig
        rows.append({**row, 'verdict_orig': orig, 'verdict': verdict,
                     'attempts': new_attempts})
        if verdict != orig:
            diff.append({'case_id': row['case_id'], 'was': orig, 'now': verdict,
                         'why': [c['assert'][:80] for x in new_attempts
                                 for c in x.get('rejudged', []) if c['now'] == 'fail']})
    summary = {}
    for r in rows:
        summary[r['verdict']] = summary.get(r['verdict'], 0) + 1
    by_suite = {}
    for r in rows:
        d = by_suite.setdefault(r['suite'], {})
        d[r['verdict']] = d.get(r['verdict'], 0) + 1
    try:
        src_label = str(SRC.resolve().relative_to(ROOT))
    except ValueError:
        src_label = str(SRC)
    payload = {'version': 'v3-rejudged-1', 'source': src_label,
               'meta': src['meta'], 'summary': summary, 'by_suite': by_suite,
               'verdict_changes': diff, 'rows': rows}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    print('summary', summary)
    for k, v in by_suite.items():
        print(' ', k, v)
    print('verdict changed:', len(diff))
    for d in diff:
        print('  ', d['case_id'], d['was'], '->', d['now'], d['why'][:2])
    total = sum(len([c for a in r['attempts'] for c in a.get('rejudged', [])]) for r in rows)
    print('assertions upgraded from blocked:', total)
    print('->', OUT)


if __name__ == '__main__':
    main()
