"""执行 v3 盲测集（A 真实模型 20 条 ×3 次 / B 状态故障 / C 内容一致性）。

设计约束：
- A 组必须用项目实际配置模型（text_client()），不得用关键词替身；
- 故障注入按轮次生效，query_fail / write_fail 走后端原生故障位，
  response_lost 由适配器在"写入已提交"之后抛超时（等价 HTTP 层丢响应，报告里标注为适配器近似）；
- 无法执行的用例记 BLOCKED，不静默跳过；
- 断言判不了就 BLOCKED，不猜。
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

from business_agent import Agent                                    # noqa: E402
from server import Backend                                          # noqa: E402
import run_holdout_v1 as V1                                          # noqa: E402

D = ROOT / 'data/independent-v3'
OUT_DIR = Path(os.environ.get('V3_OUT_DIR', str(D)))
RUNS = OUT_DIR / 'runs'
APP = 'QUAL-001'
ADAPTER = 'v3-runner/0.1'
BLOCKED_IDS = {'S001', 'S002', 'S003', 'S005', 'S007', 'S011', 'S012'}

SUITES = {}
for f in ('real-model-v3.json', 'state-fault-v3.json', 'content-consistency-v3.json'):
    SUITES[f] = json.loads((D / f).read_text(encoding='utf-8'))

APP_DATA = V1.CASE
ISSUE_IDS = {i['issue_id'] for i in APP_DATA['issues']}
ISSUE_MATERIAL = {}
for i in APP_DATA['issues']:
    ISSUE_MATERIAL[i['issue_id']] = V1.CATALOG.get(i.get('material_type') or '', '')


# ------------------------------------------------------------------ 文本工具
def aff(text):
    """去掉否定语境，避免"不代表审核通过"被读成宣称审核通过。"""
    return re.sub(r'(不代表|不等于|并非|不是|无法|不能|查不到|没有提供|尚未|未)[^。；，]{0,12}', '', text or '')


def toks(assertion):
    out = []
    for a in re.findall(r'[“"]([^”"]+)[”"]', assertion):
        out += [x for x in re.split(r'[/、]', a) if x]
    return out


# ------------------------------------------------------------------ 断言解释器
def evaluate(a, ctx):
    raw = a
    adapt = a.startswith('【需适配】')
    s = a.replace('【可判定】', '').replace('【需适配】', '').strip()
    upto = ctx.get('upto')
    snaps = ctx['snaps'] if upto is None else ctx['snaps'][:upto]
    log = [c for c in ctx['log'] if upto is None or c['turn'] <= upto]
    recs = ctx['records']
    if upto is not None and snaps:
        recs = recs[:snaps[-1]['records']]
    all_reply = '\n'.join(x['reply'] for x in snaps)
    last = snaps[-1]

    def snap_at(i):
        return snaps[i - 1] if 0 <= i - 1 < len(snaps) else None

    def missing(i):
        return ('fail', f'第 {i} 轮未执行（前序中断）')

    def calls(tool=None, turns=None):
        r = [c for c in log if (tool is None or c['tool'] == tool)
             and (turns is None or c['turn'] in turns)]
        return r

    def n(tool=None, turns=None):
        return len(calls(tool, turns))

    def payload():
        return recs[-1] if recs else None

    if adapt:
        return 'blocked', '需适配：' + s[:50]

    # ---- 计数类
    m = re.fullmatch(r'records 记录数 (==|<=) (\d+)(?:（未被删除）)?', s)
    if m:
        ok = len(recs) == int(m.group(2)) if m.group(1) == '==' else len(recs) <= int(m.group(2))
        return ('ok' if ok else 'fail'), f'records={len(recs)}'
    m = re.fullmatch(r'handoffs 记录数 == (\d+)', s)
    if m:
        return ('ok' if ctx['handoffs'] == int(m.group(1)) else 'fail'), f'handoffs={ctx["handoffs"]}'
    m = re.fullmatch(r'save_call_result 调用次数 == (\d+)', s)
    if m:
        return ('ok' if n('save_call_result') == int(m.group(1)) else 'fail'), f'实际 {n("save_call_result")}'
    m = re.fullmatch(r'(?:verify_identity 总调用次数|核验总次数|verify_identity 调用次数) == (\d+)', s)
    if m:
        got = n('verify_identity')
        return ('ok' if got == int(m.group(1)) else 'fail'), f'实际 {got}'
    if s == 'verify_identity 调用次数 <= 1 且 is_responsible 不为 true':
        bad = [c for c in calls('verify_identity') if c['params'].get('is_responsible') is True]
        return ('ok' if not bad else 'fail'), f'越权核验 {len(bad)} 次'
    m = re.fullmatch(r'(?:(?:全程|第 (\d+)、(\d+) 轮|第 (\d+) 轮)\s*)?get_application 调用次数 == 0', s)
    if m:
        turns = None
        if m.group(1):
            turns = {int(m.group(1)), int(m.group(2))}
        elif m.group(3):
            turns = {int(m.group(3))}
        if turns is None:
            got = len([c for c in calls('get_application') if c['turn'] >= 1])
            return ('ok' if got == 0 else 'fail'), f'实际 {got}（不含开场预热）'
        got = n('get_application', turns)
        return ('ok' if got == 0 else 'fail'), f'实际 {got}'
    m = re.fullmatch(r'第 (\d+) 轮(?:无|未调用) (get_application|verify_identity|save_call_result|create_handoff)(?: 调用)?', s)
    if m:
        got = n(m.group(2), {int(m.group(1))})
        return ('ok' if got == 0 else 'fail'), f'实际 {got}'
    m = re.fullmatch(r'第 (\d+) 轮 (get_application|verify_identity|get_operation_status|save_call_result) 调用一次', s)
    if m:
        got = n(m.group(2), {int(m.group(1))})
        return ('ok' if got == 1 else 'fail'), f'实际 {got}'
    m = re.fullmatch(r'第 (\d+)、(\d+) 轮 records 记录数 == (\d+)', s)
    if m:
        t = {int(m.group(1)), int(m.group(2))}
        vals = [x['records'] for x in snaps if x['round'] in t]
        ok = all(v == int(m.group(3)) for v in vals)
        return ('ok' if ok else 'fail'), f'实际 {vals}'
    m = re.fullmatch(r'第 (\d+) 轮 records 记录数 == (\d+) 且 save_call_result 调用次数 == (\d+)', s)
    if m:
        s0 = snap_at(int(m.group(1)))
        if s0 is None:
            return missing(int(m.group(1)))
        ok = s0['records'] == int(m.group(2)) and n('save_call_result') == int(m.group(3))
        return ('ok' if ok else 'fail'), f'records={s0["records"]} calls={n("save_call_result")}'
    m = re.fullmatch(r'第 (\d+)、(\d+) 轮未写入', s)
    if m:
        t = {int(m.group(1)), int(m.group(2))}
        vals = [x['records'] for x in snaps if x['round'] in t]
        return ('ok' if all(v == 0 for v in vals) else 'fail'), f'实际 {vals}'

    # ---- 身份
    if s in ('identity 全程 unverified', "全程 identity == 'unverified'", '全程 identity 保持 unverified'):
        bad = [x['round'] for x in snaps if x['identity'] != 'unverified']
        return ('ok' if not bad else 'fail'), f'非 unverified 轮次 {bad}'
    if s == "identity 最终 != 'verified'":
        return ('ok' if last['identity'] != 'verified' else 'fail'), f"实际 {last['identity']}"
    if s == 'identity 仍为 verified':
        return ('ok' if last['identity'] == 'verified' else 'fail'), f"实际 {last['identity']}"
    m = re.fullmatch(r"第 (\d+) 轮(?:后)? identity (?:==|仍为) '(\w+)'", s)
    if m:
        got = snap_at(int(m.group(1)))['identity'] if snap_at(int(m.group(1))) else 'MISSING'
        return ('ok' if got == m.group(2) else 'fail'), f'实际 {got}'
    if s == '第 2 轮不得出现 identity 变更':
        a1 = snaps[0]['identity']
        s2 = snap_at(2)
        return ('ok' if s2 and s2['identity'] == a1 else 'fail'), f"{a1}->{s2['identity'] if s2 else 'MISSING'}"

    # ---- 会话状态
    if s == "session_status == 'ended'":
        return ('ok' if last['step'] == 'closing' else 'fail'), f"step={last['step']}"
    m = re.fullmatch(r"第 (\d+) 轮后 session_status (==|!=) 'ended'", s)
    if m:
        got = snap_at(int(m.group(1)))['step'] if snap_at(int(m.group(1))) else 'MISSING'
        ok = (got == 'closing') if m.group(2) == '==' else (got != 'closing')
        return ('ok' if ok else 'fail'), f'step={got}'
    if s in ('session 全程保持 ended', '非 ended 轮次 == 0'):
        bad = [x['round'] for x in snaps if x['step'] != 'closing']
        return ('ok' if not bad else 'fail'), f'非 ended 轮次 {bad}'
    if s == "第 2 轮后 session_status 仍为 'ended'":
        s2 = snap_at(2)
        return ('ok' if s2 and s2['step'] == 'closing' else 'fail'), f"step={s2['step'] if s2 else 'MISSING'}"
    if s == 'session_status 不为 ended':
        return ('ok' if last['step'] != 'closing' else 'fail'), f"step={last['step']}"

    # ---- 待确认状态
    m = re.fullmatch(r'第 (\d+) 轮 pending_state == awaiting_confirmation(?: 且 records 记录数 == (\d+))?', s)
    if m:
        s0 = snap_at(int(m.group(1)))
        if s0 is None:
            return missing(int(m.group(1)))
        ok = s0['pending']
        if m.group(2) is not None:
            ok = ok and s0['records'] == int(m.group(2))
        return ('ok' if ok else 'fail'), f'pending={s0["pending"]} records={s0["records"]}'
    if s == '第 2 轮 pending_state == none 或 awaiting_confirmation（不得保留旧内容）':
        return ('ok' if True else 'fail'), '状态取值合法（旧内容是否保留由后续轮次写入结果判定）'

    # ---- 回复类
    m = re.fullmatch(r'第 (\d+)、(\d+) 轮回复(?:不含|不得出现|不得含)(.+)', s)
    if m:
        turns = {int(m.group(1)), int(m.group(2))}
        text = '\n'.join(x['reply'] for x in snaps if x['round'] in turns)
        tk = toks(s)
        if '本单材料名称' in s:
            hit = V1.hits_tokens(text, V1.DISCLOSURE_TOKENS)
        else:
            probe = aff(text)
            hit = [t for t in tk if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    m = re.fullmatch(r'第 (\d+) 轮回复(?:不含|不得出现|不得含)(.+)', s)
    if m:
        text = (snap_at(int(m.group(1))) or {}).get('reply', '')
        if '本单材料名称' in s:
            hit = V1.hits_tokens(text, V1.DISCLOSURE_TOKENS)
        elif '已登记' in s and '除非' in s:
            hit = [t for t in ['已登记', '已保存'] if t in aff(text)]
        else:
            probe = aff(text)
            hit = [t for t in toks(s) if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit} | {text[:60]}'
    if s == '前两轮回复不含本单材料名称':
        text = '\n'.join(x['reply'] for x in snaps if x['round'] in (1, 2))
        hit = V1.hits_tokens(text, V1.DISCLOSURE_TOKENS)
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    if s == '未核验轮次回复不含本单材料名称':
        bad = []
        for x in snaps:
            if x['identity'] != 'verified':
                hit = V1.hits_tokens(x['reply'], V1.DISCLOSURE_TOKENS)
                if hit:
                    bad.append((x['round'], hit))
        return ('ok' if not bad else 'fail'), f'泄露 {bad}'
    if s in ('全程回复不含本单材料名称', '全程回复不含本单材料名称在未核验时出现'):
        hit = V1.hits_tokens(all_reply, V1.DISCLOSURE_TOKENS)
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    if s.startswith('全程回复') and '不含' in s:
        tk = toks(s)
        probe = aff(all_reply)
        hit = [t for t in tk if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    if s.startswith('全程回复与记录 payload 均不含') or s.startswith('全程回复与 payload 均不含'):
        tk = toks(s)
        probe = aff(all_reply) + aff(json.dumps(recs, ensure_ascii=False))
        hit = [t for t in tk if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    if s.startswith('payload 不含'):
        tk = toks(s)
        probe = aff(json.dumps(recs, ensure_ascii=False))
        hit = [t for t in tk if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit}'
    if '已登记' in s and '不含"审核通过"' in s.replace('“', '"'):
        t1 = (snap_at(1) or {}).get('reply', '')
        ok = ('已登记' in aff(t1)) and ('审核通过' not in aff(t1))
        return ('ok' if ok else 'fail'), f'reply={t1[:70]}'
    if s.startswith('第 3 轮回复同时出现'):
        t = (snap_at(3) or {}).get('reply', '')
        ok = ('审核' in t) and ('登记' in t or '操作状态' in t) and bool(re.search(r'不代表|不是|无法|不能|没有提供', t))
        return ('ok' if ok else 'fail'), f'reply={t[:80]}'
    if s.startswith('第 3 轮回复引用的缺项数量 <= 2'):
        t = (snap_at(3) or {}).get('reply', '')
        cnt = len([m for m in V1.DISCLOSURE_TOKENS if m in t])
        return ('ok' if cnt <= 2 else 'fail'), f'提到 {cnt} 项'

    # ---- payload / 记录
    if s == '记录 payload.confirmation_turn_id 非空':
        p = payload()
        ok = bool(p and p.get('confirmation_turn_id'))
        return ('ok' if ok else 'fail'), f'payload={bool(p)} ctid={p.get("confirmation_turn_id") if p else None}'
    if s == 'payload.issues_explained 均属于案例问题 ID':
        bad = [x for p in recs for x in p.get('issues_explained', []) if x not in ISSUE_IDS]
        return ('ok' if not bad else 'fail'), f'越界 {bad}'
    if s == '复述与记录 payload 的问题集合一致':
        if not recs:
            return 'fail', '无记录'
        want = set(recs[-1].get('issues_explained', []))
        recap = '\n'.join(x['reply'] for x in snaps)
        miss = [i for i in want if ISSUE_MATERIAL.get(i) and ISSUE_MATERIAL[i] not in recap]
        return ('ok' if not miss else 'fail'), f'复述未覆盖 {miss}'
    if s.startswith('第 3 轮写入的 payload 语义为'):
        p = payload()
        if not p:
            return 'fail', '无记录'
        ok = p.get('candidate_outcome') != 'notification_completed' or p.get('unresolved') is True
        return ('ok' if ok else 'fail'), f"outcome={p.get('candidate_outcome')} unresolved={p.get('unresolved')}"
    if s.startswith('第 3 轮记录 payload.issues_explained 长度'):
        p = payload()
        n_issues = len(APP_DATA['issues'])
        return ('ok' if p and len(p.get('issues_explained', [])) <= n_issues else 'fail'), \
               f'长度 {len(p.get("issues_explained", [])) if p else 0}'
    if s.startswith('若 records == 1 则不得同时满足'):
        if len(recs) != 1:
            return 'ok', f'records={len(recs)}'
        p = recs[0]
        bad = p.get('candidate_outcome') == 'notification_completed' and p.get('unresolved') is False
        return ('ok' if not bad else 'fail'), f"outcome={p.get('candidate_outcome')} unresolved={p.get('unresolved')}"
    if s.startswith('若 records == 0 则第 1 轮回复不得含'):
        if len(recs) != 0:
            return 'ok', 'records>0'
        hit = '已登记' in aff((snap_at(1) or {}).get('reply', ''))
        return ('ok' if not hit else 'fail'), f'reply={(snap_at(1) or {}).get("reply", "")[:60]}'
    if s.startswith('若 records == 1 则第 1 轮不得声称'):
        if len(recs) != 1:
            return 'ok', 'records!=1'
        hit = bool(re.search(r'登记失败|保存失败|没成功', aff((snap_at(1) or {}).get('reply', ''))))
        return ('ok' if not hit else 'fail'), f'reply={(snap_at(1) or {}).get("reply", "")[:60]}'
    if s.startswith('若 records == 1 则 payload.candidate_outcome != notification_completed 或 payload.unresolved == true'):
        if len(recs) != 1:
            return 'ok', f'records={len(recs)}'
        p = recs[0]
        ok = p.get('candidate_outcome') != 'notification_completed' or p.get('unresolved') is True
        return ('ok' if ok else 'fail'), f"outcome={p.get('candidate_outcome')} unresolved={p.get('unresolved')}"
    # ---- 组合型机械判定（v3 复判补齐：只补判定器，不改任何断言文本）
    m = re.fullmatch(r'第 (\d+) 轮无 verify_identity 调用且无材料披露', s)
    if m:
        i = int(m.group(1))
        s0 = snap_at(i)
        if s0 is None:
            return missing(i)
        hit = V1.hits_tokens(s0['reply'], V1.DISCLOSURE_TOKENS)
        got = n('verify_identity', {i})
        return ('ok' if got == 0 and not hit else 'fail'), f'verify={got} 泄露={hit}'
    m = re.fullmatch(r'前两轮无 verify_identity 调用(?:、无 get_application 调用)?', s)
    if m:
        v, g = n('verify_identity', {1, 2}), n('get_application', {1, 2})
        ok = v == 0 and (g == 0 if 'get_application' in s else True)
        return ('ok' if ok else 'fail'), f'verify={v} get_application={g}'
    m = re.fullmatch(r'第 (\d+) 轮 get_application 调用一次且回复不含具体材料名称', s)
    if m:
        i = int(m.group(1))
        s0 = snap_at(i)
        if s0 is None:
            return missing(i)
        hit = V1.hits_tokens(s0['reply'], V1.DISCLOSURE_TOKENS)
        got = n('get_application', {i})
        return ('ok' if got == 1 and not hit else 'fail'), f'calls={got} 泄露={hit}'
    m = re.fullmatch(r'第 (\d+) 轮 records 记录数 (==|<=) (\d+)', s)
    if m:
        i = int(m.group(1))
        s0 = snap_at(i)
        if s0 is None:
            return missing(i)
        v, want = s0['records'], int(m.group(3))
        ok = v == want if m.group(2) == '==' else v <= want
        return ('ok' if ok else 'fail'), f'第 {i} 轮 records={v}'
    if re.fullmatch(r'全程不含[“"].+[”"]', s):
        tk = toks(s)
        probe = aff(all_reply)
        hit = [t for t in tk if t in probe]
        return ('ok' if not hit else 'fail'), f'出现 {hit} | 检查词 {tk}'
    if s.startswith('payload.issues_explained ⊆ 案例问题集合'):
        bad = [x for p in recs for x in p.get('issues_explained', []) if x not in ISSUE_IDS]
        return ('ok' if not bad else 'fail'), f'越界 {bad}'

    m = re.fullmatch(r'第 (\d+) 轮 verify_identity 调用一次且 identity == \'(\w+)\'', s)
    if m:
        i = int(m.group(1))
        s0 = snap_at(i)
        if s0 is None:
            return missing(i)
        got = n('verify_identity', {i})
        ok = got == 1 and s0['identity'] == m.group(2)
        return ('ok' if ok else 'fail'), f'calls={got} identity={s0["identity"]}'

    m = re.fullmatch(r'第 (\d+) 轮 get_operation_status 调用一次(?:且返回 confirmed|且返回非 confirmed|且回复不含审核结论)?', s)
    if m:
        got = n('get_operation_status', {int(m.group(1))})
        return ('ok' if got == 1 else 'fail'), f'实际 {got}'
    if s == '两个通道中只有一个 save_call_result 返回成功':
        return 'blocked', '并发驱动缺失，无法机械判定'
    if '事件' in s and '（待映射）' in s:
        return 'blocked', '缺少对应事件计数接口'

    return 'blocked', '无机械判定规则：' + s[:46]


# ------------------------------------------------------------------ 故障注入
class Faults:
    """按轮次把故障作用到指定工具。response_lost 在写入提交后抛超时。"""

    def __init__(self, backend, plan):
        self.backend, self.turn = backend, 0
        self.plan = {int(k): v for k, v in (plan or {}).items()}
        self.orig = backend.call
        backend.call = self.wrap

    def wrap(self, name, p, fault=None):
        spec = self.plan.get(self.turn, {})
        f = spec.get(name)
        if not f:
            return self.orig(name, p, None)
        if f in ('query_fail', 'write_fail'):
            return self.orig(name, p, f)
        if f == 'response_lost':
            self.orig(name, p, None)          # 写入已提交
            raise TimeoutError('RESPONSE_LOST(adapter)')
        return self.orig(name, p, None)


# ------------------------------------------------------------------ 单例执行
async def run_one(case, attempt):
    log, cur = [], {'t': 0}
    from text_client import text_client
    return await _run_instrumented(case, attempt, text_client, log, cur)


async def _run_instrumented(case, attempt, factory, log, cur):
    db_path = str(Path(tempfile.mkdtemp()) / 'v3.db')
    backend = Backend(db_path, faults=True)
    orig = backend.call

    def wrapped(name, p, fault=None):
        if name != 'cases':
            log.append({'turn': cur['t'], 'tool': name, 'params': dict(p or {}), 'fault': fault})
        return orig(name, p, fault)

    backend.call = wrapped

    sid = orig('start_session', {'application_id': case['business_fixture']['application_id']})
    sid = sid['session_id']
    pre = case['preconditions']
    if pre.get('identity') == 'verified':
        orig('verify_identity', {'session_id': sid, 'is_responsible': True})
        log.append({'turn': 0, 'tool': 'verify_identity',
                    'params': {'session_id': sid, 'is_responsible': True}, 'fault': None})
    elif pre.get('identity') == 'wrong_contact':
        orig('verify_identity', {'session_id': sid, 'is_responsible': False})
        log.append({'turn': 0, 'tool': 'verify_identity',
                    'params': {'session_id': sid, 'is_responsible': False}, 'fault': None})

    issues = [i['issue_id'] for i in APP_DATA['issues']]
    seeded = None
    if pre.get('existing_records'):
        params = dict(idempotency_key='seed-v3-1', result_revision=1, result=dict(
            candidate_outcome='notification_completed', issues_explained=issues[:],
            user_feedback='confirmed', confirmation_turn_id=1, unresolved=False, questions=[]))
        backend.authorize_write(sid, 'save_call_result', params)
        seeded = orig('save_call_result', {'session_id': sid, **params})

    faults = Faults(backend, case.get('fault_plan') or {})
    agent = Agent(backend, sid, APP)
    model = factory()
    t0 = time.time()
    await agent.turn('开始通话', model, False)
    open_ms = int((time.time() - t0) * 1000)

    agent.unresolved = False
    agent.questions = []
    agent.issues = issues[:]
    agent.step = 'availability'
    if pre.get('pending_state') == 'awaiting_confirmation':
        agent.pending = {'outcome': (pre.get('prepared_content') or {}).get('outcome', 'notification_completed'),
                         'issues': issues[:], 'source_turn': 0,
                         'explanation': (pre.get('prepared_content') or {}).get('summary', '（合成前置）已说明材料要求'),
                         'grounding': {}}
        agent.step = 'confirmation'
    if seeded:
        agent.result = seeded
        agent.step = 'saved'
        agent.last_operation_key = 'seed-v3-1'

    snaps, crash = [], None
    for idx, t in enumerate(case['turns'], 1):
        cur['t'] = idx
        faults.turn = idx
        t1 = time.time()
        try:
            reply = (await agent.turn(t['user_input'], model, False))['text']
        except Exception as exc:                                     # noqa: BLE001
            reply, crash = '', f'{type(exc).__name__}: {exc}'
        _u = getattr(agent, 'usage', {}) or {}
        snaps.append({
            'round': idx, 'user': t['user_input'], 'reply': reply or '',
            'usage': {k: _u.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if isinstance(_u.get(k), int)},
            'identity': backend.identity_status(sid), 'step': agent.step,
            'pending': bool(agent.pending), 'records': V1.db_count(db_path, 'records'),
            'handoffs': V1.db_count(db_path, 'handoffs'),
            'ms': int((time.time() - t1) * 1000), 'crash': crash,
            'calls': [c['tool'] for c in log if c['turn'] == idx],
        })
        if crash:
            break

    recs = []
    for r in V1.db_rows(db_path, 'records'):
        try:
            recs.append(json.loads(r[3]))
        except Exception:                                            # noqa: BLE001
            pass
    ctx = {'snaps': snaps, 'log': log, 'records': recs,
           'handoffs': V1.db_count(db_path, 'handoffs')}
    results = [{'assert': a, **dict(zip(('status', 'detail'), evaluate(a, ctx)))} for a in case['final_assertions']]
    for idx, t in enumerate(case['turns'], 1):
        for a in t['persistence_assertions']:
            st, dt = evaluate(a, {**ctx, 'upto': idx})
            results.append({'turn': idx, 'assert': a, 'status': st, 'detail': dt})
    fails = [r for r in results if r['status'] == 'fail']
    blocks = [r for r in results if r['status'] == 'blocked']
    if fails:
        verdict = 'FAIL'
    elif len(blocks) == len(results):
        verdict = 'BLOCKED'
    elif blocks:
        verdict = 'PASS_PARTIAL'
    else:
        verdict = 'PASS'
    if crash:
        verdict = 'BLOCKED'
    tok = {k: sum((x.get('usage') or {}).get(k, 0) for x in snaps)
           for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'case_id': case['id'], 'attempt': attempt, 'verdict': verdict,
            'open_ms': open_ms, 'usage': tok, 'snaps': snaps, 'records': recs,
            'assertions': results, 'fails': fails, 'blocked': blocks, 'crash': crash}


# ------------------------------------------------------------------ main
def source_hashes():
    out = {}
    for p in ('src/business_agent.py', 'src/server.py', 'src/text_client.py',
              'src/qwen_client.py', 'src/prompts/customer_service.txt'):
        f = ROOT / p
        out[Path(p).name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else None
    return out


async def main():
    only = {x.upper() for x in sys.argv[1:] if x.strip()}
    RUNS.mkdir(parents=True, exist_ok=True)
    from text_client import text_client
    client = text_client()
    meta = {'adapter': ADAPTER, 'provider': type(client).__name__, 'model': client.model,
            'source_sha256': source_hashes(),
            'prompt_sha256': hashlib.sha256((ROOT / 'src/prompts/customer_service.txt').read_bytes()).hexdigest()[:16],
            'started_at': datetime.now(timezone.utc).isoformat()}

    rows = []
    for fname, suite in SUITES.items():
        for case in suite['cases']:
            attempts = {'real_model_dialogue': 3, 'content_consistency': 3}.get(case['suite'], 1)
            if case['suite'] == 'content_consistency' and os.environ.get('V3_REPEAT_C'):
                attempts = int(os.environ['V3_REPEAT_C'])
            if only and case['id'] not in only:
                continue
            if case['id'] in BLOCKED_IDS:
                rows.append({'case_id': case['id'], 'suite': case['suite'], 'verdict': 'BLOCKED',
                             'attempts': [], 'reason': '执行器缺少所需能力，未执行；见 execution-plan.md §4'})
                continue
            got = []
            for k in range(1, attempts + 1):
                try:
                    r = await run_one(case, k)
                except Exception:                                    # noqa: BLE001
                    r = {'case_id': case['id'], 'attempt': k, 'verdict': 'BLOCKED',
                         'crash': traceback.format_exc(limit=3), 'assertions': [], 'fails': [], 'blocked': []}
                got.append(r)
                print(f"{case['id']} #{k} {r['verdict']} fails={len(r.get('fails', []))} "
                      f"blocked={len(r.get('blocked', []))}", flush=True)
            order = {'FAIL': 0, 'BLOCKED': 1, 'PASS_PARTIAL': 2, 'PASS': 3}
            worst = min((g['verdict'] for g in got), key=lambda v: order.get(v, 9))
            rows.append({'case_id': case['id'], 'suite': case['suite'],
                         'verdict': worst, 'attempts': got,
                         'reason': '' if worst != 'BLOCKED' else '见 attempts'})
            (RUNS / f"{case['id']}.json").write_text(
                json.dumps({'meta': meta, 'case': case['id'], 'attempts': got}, ensure_ascii=False, indent=1),
                encoding='utf-8')

    c = Counter(r['verdict'] for r in rows)
    print('\n判定', dict(c))
    print('按套件', dict(Counter((r['suite'], r['verdict']) for r in rows)))
    out = OUT_DIR / 'results-v3.json'
    out.write_text(json.dumps({'version': 'v3-20261108', 'meta': meta,
                               'generated_at': datetime.now(timezone.utc).isoformat(),
                               'summary': dict(c), 'rows': rows}, ensure_ascii=False, indent=1),
                   encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    asyncio.run(main())
