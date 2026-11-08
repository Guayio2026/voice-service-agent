"""执行冻结版 v2 留出集 data/independent-holdout/holdout-20261108-v2.json。

与 v1 执行器的差异（口径公开）：
- 前置 operation 用真实 save_call_result 路径构造，保证"有记录也有操作台账"；
- 支持 fault_injection_model（按 kind 覆写 / 非法 JSON）与 fault_injection_asr（迟到但无时间戳）；
- 断言解释器按 v2 句式重写，未覆盖的断言显式 BLOCKED，不静默通过。
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from business_agent import Agent                                    # noqa: E402
from server import Backend                                          # noqa: E402

import run_holdout_v1 as V1                                          # noqa: E402

APP = 'QUAL-001'
SUITE = json.loads((ROOT / 'data/independent-holdout/holdout-20261108-v2.json').read_text(encoding='utf-8'))


def affirmative(reply):
    """剥离否定语境，避免"不是审核进度"被读成"宣称审核进度"。"""
    return re.sub(r'(不是|不代表|并非|不属于|不等于|无法查询|查不到|无法确认|没有提供)[^。；，]{0,14}', '', reply or '')


def evaluate(a, ctx):
    snaps, calls = ctx['snaps'], ctx['calls']
    last = snaps[-1]
    text = a.replace('【可判定】', '').replace('【需适配】', '').strip()
    all_reply = ctx['all_reply']

    def n_calls(tool, upto=None):
        return len([c for c in calls if c['tool'] == tool and (upto is None or c['turn'] <= upto)])

    def r_calls(i, tool):
        return tool in snaps[i - 1]['calls']

    def n_records():
        return ctx['records']

    if a.startswith('【需适配】'):
        return 'blocked', '冻结标注需适配：' + text[:40]

    if text in ('records 记录数 == %d' % n_records(),):
        return 'ok', f'records={n_records()}'
    m = re.fullmatch(r'(records|handoffs|identity) 记录数 == (\d+)', text)
    if m:
        want = int(m.group(2))
        got = {'records': ctx['records'], 'handoffs': ctx['handoffs'],
               'identity': n_calls('verify_identity')}[m.group(1)]
        return ('ok' if got == want else 'fail'), f'{m.group(1)} 实际 {got}'
    if text.startswith('未发生写入'):
        return ('ok' if ctx['records'] == 0 else 'fail'), f'records={ctx["records"]}'
    m = re.fullmatch(r'(\w+) 调用次数 == (\d+)', text)
    if m:
        got = n_calls(m.group(1))
        return ('ok' if got == int(m.group(2)) else 'fail'), f'{m.group(1)} 实际 {got}'
    if text == '核验总次数 == 1':
        got = n_calls('verify_identity')
        return ('ok' if got == 1 else 'fail'), f'实际 {got}'
    m = re.fullmatch(r'第 (\d+) 轮(?:无核验、无写入|无核验、无写入)', text)
    if m:
        i = int(m.group(1)); s = snaps[i - 1]
        ok = not r_calls(i, 'verify_identity') and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f'r{i} calls={s["calls"]} 新增={s["records"]-s["records_before"]}'
    if text == '前两轮无 verify_identity 调用':
        bad = [i for i in (1, 2) if r_calls(i, 'verify_identity')]
        return ('ok' if not bad else 'fail'), f'出现在第 {bad} 轮'
    m = re.fullmatch(r'第 (\d+) 轮\s*(?:identity ==|identity 仍为|后 identity ==)\s*\'?(verified|unverified|wrong_contact)\'?', text)
    if m:
        i, want = int(m.group(1)), m.group(2)
        got = snaps[i - 1]['identity']
        return ('ok' if got == want else 'fail'), f'r{i} 实际 {got}'
    m = re.fullmatch(r'第 (\d+) 轮无 verify_identity 调用且 identity 变为 \'(\w+)\'', text)
    if m:
        i, want = int(m.group(1)), m.group(2)
        ok = (not r_calls(i, 'verify_identity')) and snaps[i - 1]['identity'] == want
        return ('ok' if ok else 'fail'), f'r{i} calls={snaps[i-1]["calls"]} identity={snaps[i-1]["identity"]}'
    m = re.fullmatch(r'第 (\d+) 轮 verify_identity 调用一次', text)
    if m:
        i = int(m.group(1))
        ok = r_calls(i, 'verify_identity') and n_calls('verify_identity') == 1
        return ('ok' if ok else 'fail'), f'r{i} calls={snaps[i-1]["calls"]} 总={n_calls("verify_identity")}'
    m = re.fullmatch(r'第 (\d+) 轮核验一次', text)
    if m:
        i = int(m.group(1))
        ok = r_calls(i, 'verify_identity') and n_calls('verify_identity') == 1
        return ('ok' if ok else 'fail'), f'r{i} calls={snaps[i-1]["calls"]}'
    if text in ('identity 最终 == \'verified\'', "identity 最终 == 'verified'"):
        return ('ok' if last['identity'] == 'verified' else 'fail'), f"实际 {last['identity']}"
    if text == "identity == 'unverified'":
        return ('ok' if last['identity'] == 'unverified' else 'fail'), f"实际 {last['identity']}"
    if text == "identity == 'wrong_contact'":
        return ('ok' if last['identity'] == 'wrong_contact' else 'fail'), f"实际 {last['identity']}"
    m = re.fullmatch(r"identity 状态 == '(\w+)'", text)
    if m:
        got = last['identity']
        return ('ok' if got == m.group(1) else 'fail'), f'实际 {got}'
    if text in ('首轮核验通过', '首轮核验通过（未误杀）', '核验通过') or '首轮完成核验' in text:
        ok = last['identity'] == 'verified'
        return ('ok' if ok else 'fail'), f"实际 {last['identity']}"
    if text == "verify_identity 首轮调用且 identity == 'verified'":
        ok = r_calls(1, 'verify_identity') and snaps[0]['identity'] == 'verified'
        return ('ok' if ok else 'fail'), f"r1 calls={snaps[0]['calls']} identity={snaps[0]['identity']}"
    m = re.fullmatch(r'第 (\d+) 轮有 (get_application|get_operation_status) 调用', text)
    if m:
        i, tool = int(m.group(1)), m.group(2)
        return ('ok' if r_calls(i, tool) else 'fail'), f'r{i} 实际 {snaps[i-1]["calls"]}'
    m = re.fullmatch(r'第 (\d+) 轮有 (get_operation_status)\s*(?:调用)?\s*且无写入', text)
    if m:
        i = int(m.group(1)); s = snaps[i - 1]
        ok = r_calls(i, 'get_operation_status') and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f'r{i} calls={s["calls"]} 新增={s["records"]-s["records_before"]}'
    m = re.fullmatch(r'第 (\d+) 轮无 (get_application|save_call_result|create_handoff|verify_identity) 调用', text)
    if m:
        i, tool = int(m.group(1)), m.group(2)
        return ('ok' if not r_calls(i, tool) else 'fail'), f'r{i} 实际 {snaps[i-1]["calls"]}'
    m = re.fullmatch(r'第 (\d+) 轮无写入', text)
    if m:
        i = int(m.group(1)); s = snaps[i - 1]
        delta = s['records'] - s['records_before']
        return ('ok' if delta == 0 else 'fail'), f'r{i} 新增 {delta}'
    m = re.fullmatch(r'第 (\d+) 轮无写入且会话未结束', text)
    if m:
        i = int(m.group(1)); s = snaps[i - 1]
        ok = s['records'] == s['records_before'] and s['step'] != 'closing'
        return ('ok' if ok else 'fail'), f'r{i} 新增={s["records"]-s["records_before"]} step={s["step"]}'
    m = re.fullmatch(r'第 (\d+) 轮仍有 get_application 且无写入', text)
    if m:
        i = int(m.group(1)); s = snaps[i - 1]
        ok = r_calls(i, 'get_application') and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f'r{i} calls={s["calls"]} 新增={s["records"]-s["records_before"]}'
    if text == "session_status == 'ended'":
        return ('ok' if last['step'] == 'closing' else 'fail'), f"step={last['step']}"
    m = re.fullmatch(r'第 (\d+) 轮后 session_status == \'ended\'', text)
    if m:
        i = int(m.group(1))
        return ('ok' if snaps[i - 1]['step'] == 'closing' else 'fail'), f'r{i} step={snaps[i-1]["step"]}'
    if text in ('session 全程保持 ended', "session_status 自第 1 轮起恒为 'ended'"):
        bad = [s['round'] for s in snaps if s['step'] != 'closing']
        return ('ok' if not bad else 'fail'), f'非 ended 轮次 {bad}'
    # 回复类
    if '不含本单材料名称' in text:
        leaked = V1.hits_tokens(all_reply, V1.DISCLOSURE_TOKENS)
        return ('ok' if not leaked else 'fail'), f'出现 {leaked}'
    if '不含“必须法人”' in text or '不含“必须老板”' in text:
        hit = re.search(r'必须法人|只能法人|法人本人|必须老板|老板本人', all_reply)
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含“已登记/已保存”' in text:
        hit = re.search(r'已登记|已保存|登记成功', affirmative(all_reply))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含“登记成功/已保存”' in text:
        hit = re.search(r'登记成功|已保存|保存成功', affirmative(all_reply))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含金额与“免费”' in text:
        hit = re.search(r'\d+\s*元|￥|免费|不收费', affirmative(all_reply))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含审核结论' in text:
        hit = re.search(r'(审核|审批).{0,4}(通过|结论)|已通过|审核结果', affirmative(all_reply))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '明确区分' in text:
        # 区分检查在原文上做：否定剥离会连"审核"这个关键词一起删掉，造成假阴性。
        ok = bool(re.search(r'(登记|操作).{0,8}状态', all_reply)) and '审核' in all_reply \
             and bool(re.search(r'无法|不能|不代表|查不到|没有提供', all_reply))
        return ('ok' if ok else 'fail'), f'回复={all_reply[:90]}'
    m = re.fullmatch(r'第 (\d+) 轮 reply 说明已登记', text)
    if m:
        i = int(m.group(1))
        hit = re.search(r'已登记|登记成功|已保存', snaps[i - 1]['reply'])
        return ('ok' if hit else 'fail'), f'r{i} 回复={snaps[i-1]["reply"][:80]}'
    if text == 'reply 说明已登记':
        hit = re.search(r'已登记|登记成功|已保存', affirmative(all_reply))
        return ('ok' if hit else 'fail'), f'回复={all_reply[:90]}'
    return 'blocked', '无机械判定规则：' + text[:40]


async def run_case(sc):
    fails, blocks, transcript = [], [], []
    db_path = str(Path(tempfile.mkdtemp()) / 'test.db')
    backend = Backend(db_path, faults=True)
    sid = backend.call('start_session', {'application_id': APP})['session_id']
    pre = sc['preconditions']
    pre_identity = pre.get('identity') or 'unverified'
    if pre_identity == 'verified':
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
    elif pre_identity in ('wrong_contact', 'rejected'):
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': False})

    agent = Agent(backend, sid, APP)
    issues = [i['issue_id'] for i in V1.CASE['issues']]
    op = pre.get('operation') or {}
    if pre.get('pending_state') == 'written' or (pre.get('business_fixture') or {}).get('existing_records'):
        key = op.get('idempotency_key') or 'seed-save-1'
        params = dict(idempotency_key=key, result_revision=1, result=dict(
            candidate_outcome='notification_completed', issues_explained=issues[:],
            user_feedback='confirmed', confirmation_turn_id=1, unresolved=False, questions=[]))
        backend.authorize_write(sid, 'save_call_result', params)
        backend.call('save_call_result', {'session_id': sid, **params})

    calls, snaps, cur = [], [], {'turn': 0}
    orig = backend.call

    def traced(name, p, fault=None):
        calls.append({'turn': cur['turn'], 'tool': name})
        return orig(name, p, fault)

    backend.call = traced
    await agent.turn('开始通话', V1.ScriptedModel(), False)

    agent.unresolved = False
    agent.questions = []
    agent.issues = issues[:]
    agent.step = 'availability'
    if pre.get('pending_state') == 'awaiting_confirmation':
        agent.pending = {'outcome': 'notification_completed', 'issues': issues[:],
                         'source_turn': 0, 'explanation': '（合成）已说明材料与提交入口', 'grounding': {}}
        agent.step = 'confirmation'
    if pre.get('pending_state') == 'written':
        agent.step = 'saved'

    for idx, turn in enumerate(sc['turns'], 1):
        cur['turn'] = idx
        text = turn['user_input']
        model = V1.ScriptedModel()
        model.kind = V1.identity_kind(text)
        model.dec = V1.decision(text)
        injected = turn.get('injected_model_output')
        if injected:
            try:
                parsed = json.loads(injected)
            except Exception:
                parsed = None
                model.invalid = injected
            if isinstance(parsed, dict):
                if parsed.get('kind') in {'yes', 'no', 'unclear', 'identity', 'purpose', 'decline'}:
                    model.kind = parsed['kind']
                if 'tool' in parsed:
                    model.invalid = injected

        before = {'records': V1.db_count(db_path, 'records'), 'handoffs': V1.db_count(db_path, 'handoffs')}
        crash = None
        try:
            reply = (await agent.turn(text, model, False))['text']
        except Exception as exc:                                    # noqa: BLE001
            reply, crash = '', f'{type(exc).__name__}: {exc}'
        after = {'records': V1.db_count(db_path, 'records'), 'handoffs': V1.db_count(db_path, 'handoffs')}

        snap = {'round': idx, 'identity': backend.identity_status(sid), 'records': after['records'],
                'handoffs': after['handoffs'], 'pending': bool(agent.pending), 'step': agent.step,
                'reply': reply or '', 'records_before': before['records'],
                'handoffs_before': before['handoffs'],
                'calls': [c['tool'] for c in calls if c['turn'] == idx],
                'kind': model.kind, 'intent': model.dec['intent'], 'crash': crash}
        snaps.append(snap)
        transcript.append({'r': idx, 'user': text, 'kind': model.kind, 'intent': model.dec['intent'],
                           'calls': snap['calls'], 'step': snap['step'], 'pending': snap['pending'],
                           'records': snap['records'], 'reply': (reply or '')[:220]})
        if crash:
            fails.append({'where': f'r{idx}', 'assert': 'turn() 未抛异常', 'detail': crash})
            continue

        round_calls = snap['calls']
        for tc in turn['required_tool_calls']:
            if tc['tool'] not in round_calls:
                fails.append({'where': f"r{idx}", 'assert': f"必须调用 {tc['tool']}", 'detail': f"实际 {round_calls}"})
        for tc in turn['forbidden_tool_calls']:
            if tc['tool'] in round_calls:
                fails.append({'where': f"r{idx}", 'assert': f"禁止调用 {tc['tool']}", 'detail': f"实际 {round_calls}"})

        exp = turn.get('expected_state') or {}
        if 'identity' in exp:
            want = 'wrong_contact' if exp['identity'] == 'rejected' else exp['identity']
            if snap['identity'] != want:
                fails.append({'where': f"r{idx}", 'assert': f'identity == {want}', 'detail': f"实际 {snap['identity']}"})
        if exp.get('session_status') == 'ended' and snap['step'] != 'closing':
            fails.append({'where': f"r{idx}", 'assert': "session_status == 'ended'", 'detail': f"step={snap['step']}"})
        want_pending = exp.get('pending_state')
        if want_pending == 'awaiting_confirmation' and not snap['pending']:
            fails.append({'where': f"r{idx}", 'assert': 'pending_state == awaiting_confirmation', 'detail': 'pending 为空'})
        if want_pending == 'written' and snap['records'] < 1:
            fails.append({'where': f"r{idx}", 'assert': 'pending_state == written', 'detail': '库内无记录'})
        if exp.get('disclosure') == 'none' and snap['identity'] == 'unverified':
            leaked = V1.hits_tokens(reply, V1.DISCLOSURE_TOKENS)
            if leaked:
                fails.append({'where': f"r{idx}", 'assert': '不得披露本单材料', 'detail': f"泄露 {leaked} | {reply[:80]}"})
        for claim in turn.get('forbidden_reply_claims', []):
            pat = V1.claim_pattern(claim)
            hit = re.search(pat, reply or '') if pat else None
            if hit:
                fails.append({'where': f"r{idx}", 'assert': f"禁用表述「{claim}」", 'detail': f"命中 {hit.group(0)} | {reply[:80]}"})

    ctx = {'snaps': snaps, 'calls': calls, 'records': V1.db_count(db_path, 'records'),
           'handoffs': V1.db_count(db_path, 'handoffs'),
           'replies': [s['reply'] for s in snaps], 'all_reply': '\n'.join(s['reply'] for s in snaps)}
    for a in sc['final_assertions']:
        status, detail = evaluate(a, ctx)
        if status == 'ok':
            continue
        if status == 'blocked':
            blocks.append({'assert': a, 'reason': detail})
        else:
            fails.append({'where': 'final', 'assert': a, 'detail': detail})

    verdict = 'FAIL' if fails else ('PASS_PARTIAL' if blocks else 'PASS')
    return {'id': sc['id'], 'family': sc['semantic_family'], 'category': sc['category'],
            'verdict': verdict, 'fails': fails, 'blocked': blocks,
            'records': ctx['records'], 'handoffs': ctx['handoffs'],
            'final_step': agent.step, 'final_identity': backend.identity_status(sid),
            'transcript': transcript}


async def main():
    only = {x.upper() for x in sys.argv[1:] if x.strip()} or None
    repeat = int(os.environ.get('HOLDOUT_REPEAT', '1'))
    out_dir = Path(os.environ.get('HOLDOUT_OUT_DIR', str(ROOT / 'data/independent-holdout')))
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for sc in SUITE['cases']:
        if only and sc['id'] not in only:
            continue
        attempts = []
        for k in range(1, repeat + 1):
            try:
                r = await run_case(sc)
            except Exception as exc:                                # noqa: BLE001
                r = {'id': sc['id'], 'family': sc['semantic_family'], 'category': sc['category'],
                     'verdict': 'HARNESS_ERROR', 'fails': [], 'blocked': [],
                     'error': f'{type(exc).__name__}: {exc}', 'transcript': []}
            r['attempt'] = k
            attempts.append(r)
            print(f"{sc['id']} #{k} {r['verdict']} fails={len(r['fails'])}", flush=True)
        order = {'HARNESS_ERROR': 0, 'FAIL': 0, 'BLOCKED': 1, 'PASS_PARTIAL': 2, 'PASS': 3}
        worst = min((a['verdict'] for a in attempts), key=lambda v: order.get(v, 9))
        row = dict(attempts[0])
        row['verdict'] = worst
        row['attempts'] = attempts
        row['attempt_verdicts'] = [a['verdict'] for a in attempts]
        rows.append(row)
    c = Counter(r['verdict'] for r in rows)
    print(f"{'判定':<14}{'条数':>4}")
    for k in ('PASS', 'PASS_PARTIAL', 'FAIL', 'HARNESS_ERROR'):
        if c[k]:
            print(f'{k:<14}{c[k]:>4}')
    print('按类别:', dict(Counter((r['category'], r['verdict']) for r in rows)))
    print('FAIL 家族:', Counter(r['family'] for r in rows if r['verdict'] == 'FAIL').most_common())
    print('失败断言:', sum(len(r['fails']) for r in rows), 'BLOCKED 断言:', sum(len(r['blocked']) for r in rows))
    out = out_dir / ('results-v2.json' if not only else 'results-v2-selected.json')
    out.write_text(json.dumps({'suite': SUITE['version'], 'repeat': repeat,
                               'source_sha256': V1.source_hash(), 'rows': rows},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    asyncio.run(main())
