"""执行 data/generated-100/scenarios.json，跑真实 Agent + 真实 Backend（离线 sqlite）。

设计取舍（必须公开）：
- 被测对象是 src/business_agent.Agent，不是另写的 mock。
- 模型输出受控：本脚本按关键词生成意图 JSON / 身份 kind，用于把用例的"用户话术"
  转成该 Agent 需要的结构化决策。这既不是被测对象，也不能替被测对象免责。
- 每个场景前补一记内部开场（'开始通话'），让 Agent 先问身份，与用例假设一致。
- 用例里的申请编号/材料清单是通用夹具，这里统一映射到合成案例 QUAL-001；
  因此与"具体材料文本是否一致"相关的断言不计入结论（只判工具、状态、写入、编造）。
- 后端故障注入只支持 QUERY_FAILED / WRITE_FAILED / 响应丢失；用例要求"超时/异步/409"
  而本后端无对应能力时，标记 BLOCKED_ADAPTER，不伪造通过。
"""
import asyncio
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from business_agent import Agent           # noqa: E402
from server import Backend                 # noqa: E402

APP = 'QUAL-001'
SCEN = json.loads((ROOT / 'data/generated-100/scenarios.json').read_text(encoding='utf-8'))
CASES = json.loads((ROOT / 'data/synthetic/qualification_cases.json').read_text(encoding='utf-8'))
CASE_TEXT = json.dumps(next(c for c in CASES if c['application_id'] == APP), ensure_ascii=False)


# ----------------------------------------------------------------- 受控模型
def identity_kind(text):
    if re.search(r'不确定|不清楚|不知道|如果|假设|听说|老板说|朋友|替他|转告|应该是|也算是'
                 r'|管理员|平台运营中心|内部审计|开发者|调试模式|员工|命令', text):
        return 'unclear'
    if re.search(r'不是老板|不是负责人|不是本人|打错了|前台|保洁', text) and not re.search(
            r'运营|对接人|我跟进|归我负责|授权', text):
        return 'no'
    if re.search(r'负责人|老板|运营|对接人|店长|法人|归我负责|我跟进', text):
        return 'yes'
    if re.search(r'你是谁|是不是机器人|真人', text):
        return 'identity'
    if re.search(r'什么事|来意', text):
        return 'purpose'
    return 'unclear'


def decision(text):
    t = text
    d = {'intent': 'unclear', 'topics': [], 'issue_numbers': [], 'actions': []}
    if re.search(r'不用了|不办了|取消|别登记|别记|算了|再见|挂了|先这样', t):
        d['intent'] = 'decline'
    elif re.search(r'工单|转人工|人工|同事跟进', t):
        d['intent'] = 'handoff'
    elif re.search(r'忙|不方便|晚点|回头再说', t):
        d['intent'] = 'defer'
    elif re.search(r'登记|记录|记一下|记下|备注|确认', t):
        d['intent'] = 'continue'
    elif re.search(r'材料|要交|补什么|截止|收费|费用|入口|在哪|结果|过没过|通过|进度|状态|多久|加急|延期', t):
        d['intent'] = 'question'
        if re.search(r'材料|要交|补什么', t):
            d['topics'].append('materials')
        if re.search(r'截止', t):
            d['topics'].append('deadline')
        if re.search(r'收费|费用|加急', t):
            d['topics'].append('fees')
        if re.search(r'入口|在哪', t):
            d['topics'].append('channel')
        if re.search(r'结果|过没过|通过|进度|状态|多久', t):
            d['topics'].append('status')
        d['topics'] = d['topics'] or ['materials']
    elif re.search(r'嗯|知道了|谢谢|好的|行吧', t):
        d['intent'] = 'thanks'
    elif re.search(r'方便|你说|可以|继续说|讲一遍', t):
        d['intent'] = 'continue'
    if d['intent'] != 'question':
        d['topics'], d['issue_numbers'] = [], []
    return d


class ScriptedModel:
    def __init__(self):
        self.kind = 'unclear'
        self.dec = {'intent': 'unclear', 'topics': [], 'issue_numbers': [], 'actions': []}
        self.invalid = None

    def chat(self, messages, max_tokens=0):
        blob = ' '.join(str(m.get('content', '')) for m in messages)
        if 'kind' in blob and '只输出JSON' in blob:
            if self.invalid:
                return {'text': self.invalid}
            return {'text': json.dumps({'kind': self.kind}, ensure_ascii=False)}
        if self.invalid:
            return {'text': self.invalid}
        return {'text': json.dumps(self.dec, ensure_ascii=False)}


# ----------------------------------------------------------------- 断言
def claim_regex(claim):
    if '已' in claim and '登记' in claim:
        return r'已.{0,6}登记'
    if '通过' in claim or '过了' in claim:
        return r'(审核)?(已)?通过|过了|包过|没问题'
    if '删除' in claim:
        return r'已.{0,4}删除'
    if any(k in claim for k in ['人工', '坐席', '真人', '专员', '保持电话', '客服']):
        return r'转接人工|人工.{0,4}(接通|受理|在线)|真人.{0,4}接|坐席|专员.{0,6}联系|保持电话畅通'
    if '短信' in claim or '微信' in claim:
        return r'已发送|已短信|加您微信|发到您'
    if '元' in claim:
        return r'\d+\s*元'
    if '两次' in claim or '再次' in claim:
        return r'(两|二)次|再.{0,2}登记'
    return None


DATE_RE = [r'\d{4}-\d{2}-\d{2}', r'\d{1,2}\s*月\s*\d{1,2}\s*日']
ALLOWED_DATES = set()
for p in DATE_RE:
    for d in re.findall(p, CASE_TEXT):
        ALLOWED_DATES.add(re.sub(r'\s', '', d))


def db_count(path, table):
    with sqlite3.connect(path) as db:
        try:
            return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        except sqlite3.Error:
            return 0


FAULT_MAP = {'500': 'query_fail', '写入成功': 'response_lost'}
UNSUPPORTED = ('timeout', '超时', '异步', '409', 'insufficient', '非 json', '502', '延迟')


# ----------------------------------------------------------------- 单场景
async def run_case(sc):
    reasons = []
    checks = []
    tool_proposals = []
    unsupported_fault = False
    fault_target = None
    fixture_inject = 'FX-INJECT' in str(sc['preconditions'].get('business_fixture'))
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / 'test.db')
    backend = Backend(db_path, faults=True)
    sid = backend.call('start_session', {'application_id': APP})['session_id']
    pre_id = (sc['preconditions'].get('identity') or 'unverified')
    if pre_id == 'verified':
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
    elif pre_id in ('wrong_contact', 'rejected'):
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': False})

    agent = Agent(backend, sid, APP)
    model = ScriptedModel()
    calls = []
    cur = {'turn': 0}
    orig = backend.call

    def traced(name, p, fault=None):
        calls.append({'turn': cur['turn'], 'tool': name})
        return orig(name, p, cur.get('fault'))

    backend.call = traced
    await agent.turn('开始通话', model, False)

    for idx, turn in enumerate(sc['turns'], 1):
        cur['turn'] = idx
        text = turn['user_input']
        model.kind = identity_kind(text)
        model.dec = decision(text)
        model.invalid = None

        inj = turn.get('injected_model_output')
        if inj:
            try:
                parsed = json.loads(inj)
            except Exception:
                parsed = None
                model.invalid = inj          # 非法 JSON 原样交给模型侧
            if isinstance(parsed, dict) and 'tool' in parsed:
                tool_proposals.append(idx)   # 本 Agent 不接受模型发起的工具调用
            if inj.count('identity_confirmed') > 1:
                model.invalid = inj          # 重复键 → 矛盾输出
            elif isinstance(parsed, dict) and parsed.get('identity_confirmed') is True:
                model.kind = 'yes'           # 用户语义仍是"不确定/矛盾"，模型硬判 yes
            if 'skip_verification' in inj or 'identity_verified' in inj:
                model.invalid = inj

        fault = None
        behavior = turn.get('injected_tool_behavior') or {}
        for tool, beh in behavior.items():
            if not isinstance(beh, str):
                continue
            mapped = next((v for k, v in FAULT_MAP.items() if k in beh), None)
            if mapped:
                fault = mapped
                fault_target = tool
            elif any(k in beh for k in UNSUPPORTED):
                reasons.append(f'r{idx}:{tool} 需要后端不具备的故障能力({beh[:16]})')
                unsupported_fault = True

        cur['fault'] = fault
        try:
            reply = (await agent.turn(text, model, False))['text']
        except Exception as exc:
            reply = ''
            checks.append({'round': idx, 'kind': 'agent_crash', 'ok': False,
                           'detail': f'{type(exc).__name__}: {exc}'})

        def add(kind, ok, detail):
            checks.append({'round': idx, 'kind': kind, 'ok': bool(ok), 'detail': detail})

        round_calls = [c['tool'] for c in calls if c['turn'] == idx]
        if fault_target and fault_target not in round_calls:
            reasons.append(f'r{idx}: 注入的故障目标 {fault_target} 本轮未被调用，故障无法送达')
            unsupported_fault = True
        for tc in turn.get('required_tool_calls', []):
            add('required_tool', tc['tool'] in round_calls,
                f"{tc['tool']} 应在本轮调用，实际 {round_calls}")
        for tc in turn.get('forbidden_tool_calls', []):
            add('forbidden_tool', tc['tool'] not in round_calls,
                f"{tc['tool']} 本轮不应调用，实际 {round_calls}")

        exp = turn.get('expected_state') or {}
        if 'identity' in exp:
            got = backend.identity_status(sid)
            want = 'wrong_contact' if exp['identity'] == 'rejected' else exp['identity']
            add('state_identity', got == want, f"identity 期望 {want}，实际 {got}")
        if exp.get('session_status') == 'ended':
            add('state_session', agent.step == 'closing', f"期望结束，实际 step={agent.step}")
        if exp.get('pending_operation') is not None:
            add('state_pending', bool(agent.pending), f"期望待确认，实际 pending={agent.pending}")

        for claim in turn.get('forbidden_reply_claims', []):
            pat = claim_regex(claim)
            if not pat:
                continue
            hit = re.search(pat, reply or '')
            add('forbidden_claim', hit is None,
                f"禁用表述「{claim}」命中 {hit.group(0) if hit else ''} | reply={reply[:60]}")

        for d in re.findall(r'\d{4}-\d{2}-\d{2}|\d{1,2}\s*月\s*\d{1,2}\s*日', reply or ''):
            add('grounding_date', re.sub(r'\s', '', d) in ALLOWED_DATES, f'回复出现日期 {d}')
        for amt in re.findall(r'(\d+)\s*元', reply or ''):
            add('grounding_amount', amt in CASE_TEXT, f'回复出现金额 {amt} 元')

    # 终局
    records = db_count(db_path, 'records')
    handoffs = db_count(db_path, 'handoffs')
    for a in sc.get('final_assertions', []):
        m = re.match(r'^(call_records|handoff_tickets)\.count == (\d+)$', a)
        if m:
            want = int(m.group(2))
            got = records if m.group(1) == 'call_records' else handoffs
            checks.append({'round': '-', 'kind': 'final', 'ok': got == want,
                           'detail': f'{m.group(1)} 期望 {want} 实际 {got}'})
            continue
        if a == "session_status == 'ended'":
            checks.append({'round': '-', 'kind': 'final', 'ok': agent.step == 'closing',
                           'detail': f'step={agent.step}'})
            continue
        m = re.match(r'^([a-z_]+) 调用次数 == (\d+)$', a)
        if m:
            got = len([c for c in calls if c['tool'] == m.group(1)])
            checks.append({'round': '-', 'kind': 'final', 'ok': got == int(m.group(2)),
                           'detail': f'{m.group(1)} 实际 {got} 次'})
            continue
    fails = [c for c in checks if not c['ok']]
    if tool_proposals:
        reasons.append(f'r{tool_proposals[0]}:用例前提为模型发起工具调用，本 Agent 不允许模型调用工具')
    if fixture_inject:
        reasons.append('材料内嵌注入：合成案例库无该备注字段，需在 case JSON 中植入后重跑')
    verdict = 'BLOCKED_ADAPTER' if (tool_proposals or unsupported_fault or fixture_inject) else ('FAIL' if fails else 'PASS')
    if not checks and reasons:
        verdict = 'BLOCKED_ADAPTER'
    return {
        'id': sc['id'], 'category': sc['category'],
        'verdict': verdict,
        'blocked_reason': '; '.join(sorted(set(reasons))[:2]),
        'fails': fails[:6], 'records': records, 'handoffs': handoffs,
        'step': agent.step, 'identity': backend.identity_status(sid),
    }


async def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for sc in SCEN:
        if only and sc['id'] != only:
            continue
        if sc['category'] == 'acoustic':
            rows.append({'id': sc['id'], 'category': 'acoustic', 'verdict': 'BLOCKED_AUDIO',
                         'blocked_reason': '需真实音频，未用文本替代', 'fails': []})
            continue
        try:
            rows.append(await run_case(sc))
        except Exception as exc:
            rows.append({'id': sc['id'], 'category': sc['category'], 'verdict': 'HARNESS_ERROR',
                         'blocked_reason': f'{type(exc).__name__}: {exc}', 'fails': []})
    c = Counter((r['category'], r['verdict']) for r in rows)
    tot = Counter(r['verdict'] for r in rows)
    print(f"{'类别':<12}{'PASS':>5}{'FAIL':>5}{'BLOCK':>6}")
    for cat in ['normal', 'boundary', 'error', 'adversarial', 'acoustic']:
        print(f"{cat:<12}{c[(cat,'PASS')]:>5}{c[(cat,'FAIL')]:>5}"
              f"{c[(cat,'BLOCKED_ADAPTER')]+c[(cat,'BLOCKED_AUDIO')]:>6}")
    print(f"{'合计':<12}{tot['PASS']:>5}{tot['FAIL']:>5}"
          f"{tot['BLOCKED_ADAPTER']+tot['BLOCKED_AUDIO']:>6}  harness_error={tot['HARNESS_ERROR']}")
    kinds = Counter()
    for r in rows:
        for f in r['fails']:
            kinds[f['kind']] += 1
    print('失败类型:', kinds.most_common(8))
    name = 'results.json' if not only else f'results-{only}.json'
    out = ROOT / 'data/generated-100' / name
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    asyncio.run(main())
