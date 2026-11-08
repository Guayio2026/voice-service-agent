"""泛化检查执行器：同一批新场景分别跑基线与候选，真实模型重复 N 次，保留全部结果。

关键点：提示词版本由 --version 指定，通过进化库 Store.version() 取出，
因此能真正加载候选版本；不会用"读默认提示词文件"冒充候选评测。

用法：
  .venv/Scripts/python.exe scripts/rsi_generalize.py --version base-6b13e03f1b116f21 --label baseline
  .venv/Scripts/python.exe scripts/rsi_generalize.py --version candidate-xxxxxxxxxxxx --label candidate
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from qwen_client import load_config                                   # noqa: E402

load_config()
os.environ['HOLDOUT_NO_ENV'] = '1'

from business_agent import Agent                                      # noqa: E402
from server import Backend                                             # noqa: E402
from evolution_store import Store, digest                              # noqa: E402
import run_holdout_v1 as V1                                            # noqa: E402

APP = 'QUAL-001'
SET_PATH = ROOT / 'data/rsi-20261108/generalization-set.json'
OUT_ROOT = ROOT / 'data/rsi-20261108'


def aff(text):
    """剥离否定语境，避免"不代表已核验"被读成"已核验"。"""
    return re.sub(r'(不代表|不等于|并非|不是|无法|不能|查不到|没有提供|尚未|未|还没有)[^。；，]{0,12}', '', text or '')


def db_count(path, table):
    return V1.db_count(path, table)


def judge(rules, ctx):
    checks = []
    reply_raw = ctx['reply'] or ''
    reply = aff(reply_raw)

    if 'identity_in' in rules:
        checks.append({'rule': 'identity_in', 'expect': rules['identity_in'], 'actual': ctx['identity'],
                       'status': 'pass' if ctx['identity'] in rules['identity_in'] else 'fail'})
    if 'not_contain' in rules:
        hit = [w for w in rules['not_contain'] if w in reply]
        checks.append({'rule': 'not_contain', 'expect': rules['not_contain'], 'actual': hit,
                       'status': 'fail' if hit else 'pass'})
    if 'contain_any' in rules:
        hit = [w for w in rules['contain_any'] if w in reply]
        checks.append({'rule': 'contain_any', 'expect': rules['contain_any'], 'actual': hit,
                       'status': 'pass' if hit else 'fail'})
    if 'no_tool' in rules:
        hit = [t for t in rules['no_tool'] if t in ctx['calls']]
        checks.append({'rule': 'no_tool', 'expect': rules['no_tool'], 'actual': ctx['calls'],
                       'status': 'fail' if hit else 'pass'})
    if 'tool_called' in rules:
        miss = [t for t in rules['tool_called'] if t not in ctx['calls']]
        checks.append({'rule': 'tool_called', 'expect': rules['tool_called'], 'actual': ctx['calls'],
                       'status': 'fail' if miss else 'pass'})
    if 'records_total' in rules:
        checks.append({'rule': 'records_total', 'expect': rules['records_total'], 'actual': ctx['records'],
                       'status': 'pass' if ctx['records'] == rules['records_total'] else 'fail'})
    if 'records_at_least' in rules:
        checks.append({'rule': 'records_at_least', 'expect': rules['records_at_least'], 'actual': ctx['records'],
                       'status': 'pass' if ctx['records'] >= rules['records_at_least'] else 'fail'})
    if 'records_at_most' in rules:
        checks.append({'rule': 'records_at_most', 'expect': rules['records_at_most'], 'actual': ctx['records'],
                       'status': 'pass' if ctx['records'] <= rules['records_at_most'] else 'fail'})
    return checks


async def run_case(case, client, prompt, version_id, attempt):
    db_path = str(Path(tempfile.mkdtemp()) / 'gen.db')
    backend = Backend(db_path)
    log, cur = [], {'t': 0}
    orig = backend.call

    def wrapped(name, p, fault=None):
        if name != 'cases':
            log.append({'turn': cur['t'], 'tool': name, 'params': dict(p or {})})
        return orig(name, p, fault)

    backend.call = wrapped
    sid = orig('start_session', {'application_id': APP})['session_id']
    pre = case['preconditions']

    if pre.get('identity') == 'verified':
        orig('verify_identity', {'session_id': sid, 'is_responsible': True})
        log.append({'turn': 0, 'tool': 'verify_identity', 'params': {'is_responsible': True}})
    elif pre.get('identity') == 'wrong_contact':
        orig('verify_identity', {'session_id': sid, 'is_responsible': False})
        log.append({'turn': 0, 'tool': 'verify_identity', 'params': {'is_responsible': False}})

    if pre.get('existing_records'):
        issues = [i['issue_id'] for i in V1.CASE['issues']]
        params = dict(idempotency_key='seed-gen-1', result_revision=1, result=dict(
            candidate_outcome='notification_completed', issues_explained=issues[:],
            user_feedback='confirmed', confirmation_turn_id=1, unresolved=False, questions=[]))
        backend.authorize_write(sid, 'save_call_result', params)
        orig('save_call_result', {'session_id': sid, **params})
        log.append({'turn': 0, 'tool': 'save_call_result', 'params': {'seed': True}})

    agent = Agent(backend, sid, APP, classifier_prompt=prompt, version_id=version_id)
    opened = await agent.turn('开始通话', client, False)
    agent.unresolved, agent.questions = False, []
    agent.issues = [i['issue_id'] for i in V1.CASE['issues']]
    agent.step = 'availability'
    if pre.get('pending'):
        agent.pending = {'outcome': pre['pending'].get('outcome', 'notification_completed'),
                         'issues': agent.issues[:], 'source_turn': 0,
                         'explanation': pre['pending'].get('explanation', '（前置）已说明材料要求'),
                         'grounding': {}}
        agent.step = 'confirmation'

    snaps, crash = [], None
    for idx, turn in enumerate(case['turns'], 1):
        cur['t'] = idx
        t0 = time.time()
        try:
            reply = (await agent.turn(turn['user_input'], client, False))['text']
        except Exception as exc:                                       # noqa: BLE001
            reply, crash = '', f'{type(exc).__name__}: {exc}'
        usage = getattr(agent, 'usage', {}) or {}
        calls = [c['tool'] for c in log if c['turn'] == idx]
        ctx = {'reply': reply or '', 'identity': backend.identity_status(sid),
               'records': db_count(db_path, 'records'), 'calls': calls}
        checks = judge(turn.get('assert') or {}, ctx)
        snaps.append({'round': idx, 'user': turn['user_input'], 'reply': reply or '',
                      'identity': ctx['identity'], 'records': ctx['records'],
                      'pending': bool(agent.pending), 'step': agent.step,
                      'calls': calls, 'checks': checks,
                      'fail': [c for c in checks if c['status'] == 'fail'],
                      'usage': {k: usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                if isinstance(usage.get(k), int)},
                      'ms': int((time.time() - t0) * 1000), 'crash': crash})
        if crash:
            break

    fails = [dict(c, round=s['round']) for s in snaps for c in s['fail']]
    verdict = 'BLOCKED' if crash else ('FAIL' if fails else 'PASS')
    return {'case_id': case['id'], 'pair': case['pair'], 'kind': case['kind'], 'attempt': attempt,
            'verdict': verdict, 'crash': crash, 'turns': snaps, 'fails': fails,
            'opening': (opened or {}).get('text', '')}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', default=None, help='提示词版本 id；缺省用当前启用版本')
    ap.add_argument('--label', default=None, help='输出子目录名，如 baseline / candidate')
    ap.add_argument('--set', default=None, help='替代用例文件路径（默认泛化集）')
    ap.add_argument('--repeat', type=int, default=3)
    ap.add_argument('--only', nargs='*', default=None)
    args = ap.parse_args()

    from text_client import text_client
    client = text_client()
    store = Store(ROOT / 'runtime/mock.sqlite3')
    version_id = args.version or store.active()
    ver = store.version(version_id)                                   # 校验 base_hash / content_hash
    prompt = ver['prompt']
    label = args.label or version_id
    out_dir = OUT_ROOT / label
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = json.loads(SET_PATH.read_text(encoding='utf-8'))
    set_path = Path(args.set) if args.set else SET_PATH
    dataset = json.loads(set_path.read_text(encoding='utf-8'))
    cases = dataset['cases']
    if args.only:
        wanted = {x.upper() for x in args.only}
        cases = [c for c in cases if c['id'].upper() in wanted]

    meta = {'executed_at': datetime.now(timezone.utc).isoformat(), 'label': label,
            'version_id': version_id, 'parent': ver['parent'],
            'prompt_hash': hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16],
            'base_prompt_hash': digest(V1.ROOT.joinpath('src/prompts/customer_service.txt').read_text(encoding='utf-8'))[:16],
            'guidance_len': len(ver['guidance'] or ''),
            'model': getattr(client, 'model', '?'), 'provider': type(client).__name__,
            'repeats': args.repeat, 'cases': len(cases),
            'set_hash': hashlib.sha256(set_path.read_bytes()).hexdigest()[:16],
            'set_path': str(set_path.resolve().relative_to(ROOT) if set_path.resolve().is_relative_to(ROOT) else set_path).replace('\\', '/'),
            'source_hashes': {Path(p).name: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()[:16]
                              for p in ('src/business_agent.py', 'src/server.py', 'src/text_client.py')},
            'dataset_fed_to_proposer': False}
    print(f'版本 {version_id} | 提示词 {meta["prompt_hash"]} | 指导语 {meta["guidance_len"]} 字 | 模型 {meta["model"]}', flush=True)

    rows = []
    for case in cases:
        for attempt in range(1, args.repeat + 1):
            r = await run_case(case, client, prompt, version_id, attempt)
            rows.append(r)
            mark = {'PASS': 'PASS', 'FAIL': 'FAIL', 'BLOCKED': 'BLOCKED'}[r['verdict']]
            print(f"  {case['id']} #{attempt} {mark}  " +
                  ('' if not r['fails'] else '; '.join(f"{f['rule']}@{f['round']}: 实际={f['actual']}" for f in r['fails'])[:150]),
                  flush=True)

    order = {'FAIL': 0, 'BLOCKED': 1, 'PASS': 2}
    summary = []
    for case in cases:
        group = [r for r in rows if r['case_id'] == case['id']]
        worst = min((r['verdict'] for r in group), key=lambda v: order[v])
        summary.append({'case_id': case['id'], 'pair': case['pair'], 'kind': case['kind'],
                        'verdicts': [r['verdict'] for r in group], 'worst': worst,
                        'fail_rules': sorted({f"{f['rule']}@{f['round']}" for r in group for f in r['fails']})})
    tokens = sum((s.get('usage') or {}).get('total_tokens', 0) or 0 for r in rows for s in r['turns'])
    lats = sorted(s['ms'] for r in rows for s in r['turns'])
    result = {'meta': meta, 'summary': summary, 'rows': rows,
              'totals': {'cases': len(cases), 'attempts': len(rows),
                         'PASS': sum(1 for s in summary if s['worst'] == 'PASS'),
                         'FAIL': sum(1 for s in summary if s['worst'] == 'FAIL'),
                         'BLOCKED': sum(1 for s in summary if s['worst'] == 'BLOCKED'),
                         'tokens': tokens,
                         'p50_ms': lats[len(lats) // 2] if lats else None,
                         'p95_ms': lats[max(0, int(len(lats) * 0.95) - 1)] if lats else None}}
    (out_dir / 'gen-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['totals'], ensure_ascii=False))
    print('写入:', out_dir / 'gen-results.json')


if __name__ == '__main__':
    asyncio.run(main())
