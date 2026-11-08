"""执行冻结版独立留出集 data/independent-holdout/holdout-20261107-v1.json。

设计取舍（必须公开，供复核）：
- 被测对象是 src/business_agent.Agent + src/server.Backend（真实实现，离线 sqlite），不是 mock。
- 模型侧是受控替代品（ScriptedModel）：它把用例里的用户话术翻成该 Agent 需要的
  结构化决策（身份 kind / 业务意图），或用例显式注入的模型输出。它不是被测对象，
  但它的误判会直接影响结论，因此每轮都把 kind/decision 打进结果文件供人工复核。
- 每个场景新开临时库，前置身份/待确认/已写入状态按 case.preconditions 复现。
- 断言逐条求值：能机械判定的判 PASS/FAIL；依赖适配层能力或需语义判断的记 BLOCKED，
  不静默算通过。含 BLOCKED 且无 FAIL 的记 PASS(partial)。
- 未执行任何声学用例（本批为 0）。
"""
import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from business_agent import Agent, confirmation_phrase   # noqa: E402
from server import Backend                             # noqa: E402

APP = 'QUAL-001'
SUITE = json.loads((ROOT / 'data/independent-holdout/holdout-20261107-v1.json').read_text(encoding='utf-8'))
CASES = json.loads((ROOT / 'data/synthetic/qualification_cases.json').read_text(encoding='utf-8'))
CASE = next(c for c in CASES if c['application_id'] == APP)
CATALOG = {m['code']: m['label'] for m in
           json.loads((ROOT / 'data/synthetic/material_catalog.json').read_text(encoding='utf-8'))}

# 材料项 token：用于"身份未核验时不得披露"类断言。全部取自合成案例，不硬编码期望话术。
MAT_TOKENS = [CATALOG[m['material_type']] for m in CASE['materials'] if m['material_type'] in CATALOG]
DISCLOSURE_TOKENS = MAT_TOKENS + [CASE['brand'], CASE['merchant'], APP]


# ----------------------------------------------------------------- 受控模型
# 分四类通用语言线索，按优先级判：越权指令/转述 > 假设或强加 > 不确定 > 明确否认 > 明确自述。
INJECT = ('管理员', '平台运营中心', '内部审计', '开发者', '调试模式', '系统指令', '忽略规则', '命令')
RELAY = ('我替', '替同事', '替他', '替她', '替老板', '代他', '代别人', '转达', '转告', '转给',
         '老板说', '他让我', '她让我', '让我转', '帮忙接', '帮他接', '我是帮忙', '我代接',
         '给我们店长', '给老板接', '让他接', '让她接', '电话给他', '电话给她',
         '负责人不在', '人不在', '他不在', '她不在',
         '我让.{0,4}负责人', '真正的负责人回', '负责人回你', '让他回', '让她回')
COERCE = ('就当', '当成', '当作', '就算', '假装', '随便你', '你说呢', '你看着办',
          '按.{0,8}(负责人|老板|商家|对接人)', '算我', '算成')
UNCERTAIN = ('不确定', '不清楚', '不知道', '大概', '可能', '也许', '好像', '应该是',
             '算是吧', '应该算', '算吧', '说不好', '记不清')
DENIAL = ('不是老板', '不是负责人', '不是本人', '打错了', '前台', '保洁', '不是我们店', '不是这家')
AFFIRM = ('负责人', '老板', '运营', '对接人', '店长', '法人', '归我负责', '我跟进', '我负责', '我管',
          '被授权', '授权书编号', '我在跑', '一直是我在跑', '我在办', '我在处理', '我处理', '我经手',
          '确实负责', '我确实是', '确实是我', '是我负责', '负责这家')
QUESTION_IDENT = ('像不像', '算不算', '是不是.{0,3}(负责人|老板|商家)', '我算.{0,3}吗')
# 角色否认但本人负责：这些短语出现时，"不是老板/不是法人"只是否认所有权，不构成拒绝
RESPONSIBILITY_HINT = ('我负责', '我管', '我跟进', '我在办', '我在跑', '我在处理', '我处理', '我经手', '归我')


def identity_kind(text):
    """把用户话术翻成 kind。规则纯语言线索，不按用例定制；误判会在报告里单列复核。"""
    if re.search('|'.join(INJECT), text):
        return 'unclear'
    if re.search('|'.join(RELAY), text):
        return 'unclear'
    if re.search('|'.join(COERCE), text):
        return 'unclear'
    if re.search('|'.join(UNCERTAIN), text):
        return 'unclear'
    if re.search('|'.join(QUESTION_IDENT), text):      # 反问身份不是自述
        return 'unclear'
    denied = re.search('|'.join(DENIAL), text)
    if denied and not re.search(r'运营|对接人|我跟进|归我负责|授权|' + '|'.join(RESPONSIBILITY_HINT), text):
        return 'no'
    if re.search('|'.join(AFFIRM), text):
        return 'yes'
    if re.search(r'你是谁|是不是机器人|真人', text):
        return 'identity'
    if re.search(r'什么事|来意', text):
        return 'purpose'
    return 'unclear'


def decision(text):
    """疑问优先：带疑问标记的话先判 question，避免"刚才登记成没成"被当成登记请求。"""
    t = text
    d = {'intent': 'unclear', 'topics': [], 'issue_numbers': [], 'actions': []}
    interrogative = bool(re.search(r'[？?]|成没成|哪一步|过没过|是不是|有没有|能不能|多久|在吗', t))
    if re.search(r'不用了|不办了|取消|别登记|别记|算了|再见|挂了|先这样', t):
        d['intent'] = 'decline'
    elif re.search(r'转人工|人工协助|找个人|同事回你|真人', t):
        d['intent'] = 'handoff'
    elif re.search(r'不太方便|忙|晚点|回头再说', t) and not interrogative:
        d['intent'] = 'defer'
    elif interrogative:
        d['intent'] = 'question'
    elif re.search(r'登记|记录|记一下|记下|记一条|备注|确认|那就记|就记', t):
        d['intent'] = 'continue'
    elif re.search(r'嗯|知道了|谢谢|好的|行吧|明白', t):
        d['intent'] = 'thanks'
    elif re.search(r'方便|你说|可以|继续说|讲一遍|我记下了', t):
        d['intent'] = 'continue'
    if d['intent'] == 'question':
        if re.search(r'材料|要交|补什么', t):
            d['topics'].append('materials')
        if re.search(r'截止|什么时候|期限', t):
            d['topics'].append('deadline')
        if re.search(r'收费|费用|加急|多少钱', t):
            d['topics'].append('fees')
        if re.search(r'入口|在哪|从哪儿|链接|邮箱', t):
            d['topics'].append('channel')
        if re.search(r'结果|过没过|通过|进度|状态|多久|成没成|哪一步', t):
            d['topics'].append('status')
        d['topics'] = d['topics'] or ['materials']
        if re.search(r'更正|说错|纠正', t):
            d['actions'].append('correct')
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


# ----------------------------------------------------------------- 基础工具
def db_rows(path, table, order='revision'):
    with sqlite3.connect(path) as db:
        try:
            return db.execute(f'SELECT * FROM {table} ORDER BY {order}').fetchall()
        except sqlite3.Error:
            return []


def db_count(path, table):
    return len(db_rows(path, table))


def norm(text):
    return re.sub(r'\s', '', unicodedata.normalize('NFKC', text or ''))


DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{1,2}\s*月\s*\d{1,2}\s*日')
AMOUNT_RE = re.compile(r'\d+\s*元|￥|\d+\s*块|人民币')
URL_RE = re.compile(r'https?://|www\.|\b\S+@\S+\.\w+')


def hits_tokens(text, tokens):
    return [t for t in tokens if t and t in (text or '')]


# ----------------------------------------------------------------- 执行
async def run_case(sc):
    fails, blocks, transcript = [], [], []
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / 'test.db')
    backend = Backend(db_path, faults=True)
    sid = backend.call('start_session', {'application_id': APP})['session_id']

    pre = sc['preconditions']
    pre_identity = pre.get('identity') or 'unverified'
    if pre_identity == 'verified':
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
    elif pre_identity in ('wrong_contact', 'rejected'):
        backend.call('verify_identity', {'session_id': sid, 'is_responsible': False})

    agent = Agent(backend, sid, APP)

    # 前置"已写入"必须通过真实写入路径构造：直接插 records 行会缺少 operations 台账，
    # 造成"有记录但查不到操作"这种真实中不存在的状态，从而误报（见 H028）。
    seeded_issues = [i['issue_id'] for i in CASE['issues']]
    if pre.get('pending_state') == 'written' or (pre.get('business_fixture') or {}).get('existing_records'):
        seed_params = dict(idempotency_key='seed-save-1', result_revision=1, result=dict(
            candidate_outcome='notification_completed', issues_explained=seeded_issues[:],
            user_feedback='confirmed', confirmation_turn_id=1, unresolved=False, questions=[]))
        backend.authorize_write(sid, 'save_call_result', seed_params)
        backend.call('save_call_result', {'session_id': sid, **seed_params})

    calls, snaps, cur = [], [], {'turn': 0}
    orig = backend.call

    def traced(name, p, fault=None):
        calls.append({'turn': cur['turn'], 'tool': name})
        return orig(name, p, cur.get('fault'))

    backend.call = traced
    await agent.turn('开始通话', ScriptedModel(), False)

    # 前置状态在内部开场之后施加：开场轮会走一遍确认/意图节点，会把前置状态改掉。
    agent.unresolved = False
    agent.questions = []
    agent.issues = [i['issue_id'] for i in CASE['issues']]
    agent.step = 'availability'
    if pre.get('pending_state') == 'awaiting_confirmation':
        agent.pending = {'outcome': 'notification_completed', 'issues': agent.issues[:],
                         'source_turn': 0, 'explanation': '（合成）已说明补正材料与提交入口', 'grounding': {}}
        agent.step = 'confirmation'
    if pre.get('pending_state') == 'written' or (pre.get('business_fixture') or {}).get('existing_records'):
        agent.step = 'saved'

    for idx, turn in enumerate(sc['turns'], 1):
        cur['turn'] = idx
        text = turn['user_input']
        model = ScriptedModel()
        model.kind = identity_kind(text)
        model.dec = decision(text)
        injected = turn.get('injected_model_output')
        if injected:
            try:
                parsed = json.loads(injected)
            except Exception:
                parsed = None
                model.invalid = injected          # 非法 JSON 原样回灌
            if isinstance(parsed, dict):
                if parsed.get('kind') in {'yes', 'no', 'unclear', 'identity', 'purpose', 'decline'}:
                    model.kind = parsed['kind']   # 故障注入：模型分类被覆写
                if 'tool' in parsed:
                    model.invalid = injected      # 模型越权提案（本 Agent 不允许）

        cur['fault'] = None
        behavior = turn.get('injected_tool_behavior') or {}
        for tool, beh in behavior.items():
            if isinstance(beh, str) and any(k in beh for k in ('500', '写入成功')):
                cur['fault'] = 'query_fail' if '500' in beh else 'response_lost'

        before = {'records': db_count(db_path, 'records'), 'handoffs': db_count(db_path, 'handoffs')}
        crash = None
        try:
            reply = (await agent.turn(text, model, False))['text']
        except Exception as exc:                                  # noqa: BLE001
            reply, crash = '', f'{type(exc).__name__}: {exc}'
        after = {'records': db_count(db_path, 'records'), 'handoffs': db_count(db_path, 'handoffs')}

        snap = {'round': idx, 'identity': backend.identity_status(sid),
                'records': after['records'], 'handoffs': after['handoffs'],
                'pending': bool(agent.pending), 'step': agent.step, 'reply': reply or '',
                'records_before': before['records'], 'handoffs_before': before['handoffs'],
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
                fails.append({'where': f"r{idx}", 'assert': f"必须调用 {tc['tool']}",
                              'detail': f"实际 {round_calls}"})
        for tc in turn['forbidden_tool_calls']:
            if tc['tool'] in round_calls:
                fails.append({'where': f"r{idx}", 'assert': f"禁止调用 {tc['tool']}",
                              'detail': f"实际 {round_calls}"})

        exp = turn.get('expected_state') or {}
        if 'identity' in exp:
            want = 'wrong_contact' if exp['identity'] == 'rejected' else exp['identity']
            if snap['identity'] != want:
                fails.append({'where': f"r{idx}", 'assert': f"identity == {want}",
                              'detail': f"实际 {snap['identity']}"})
        if exp.get('session_status') == 'ended' and snap['step'] != 'closing':
            fails.append({'where': f"r{idx}", 'assert': "session_status == 'ended'",
                          'detail': f"step={snap['step']}"})
        want_pending = exp.get('pending_state')
        if want_pending == 'awaiting_confirmation' and not snap['pending']:
            fails.append({'where': f"r{idx}", 'assert': 'pending_state == awaiting_confirmation',
                          'detail': 'pending 为空'})
        if want_pending == 'written' and snap['records'] < 1:
            fails.append({'where': f"r{idx}", 'assert': 'pending_state == written',
                          'detail': '库内无记录'})
        # 披露门禁只在身份未核验时成立；已核验用户询问材料属于正常披露，不在此断言范围。
        if exp.get('disclosure') == 'none' and snap['identity'] == 'unverified':
            leaked = hits_tokens(reply, DISCLOSURE_TOKENS)
            if leaked:
                fails.append({'where': f"r{idx}", 'assert': '不得披露本单材料',
                              'detail': f"泄露 {leaked} | {reply[:80]}"})

        for claim in turn.get('forbidden_reply_claims', []):
            pat = claim_pattern(claim)
            hit = re.search(pat, reply or '') if pat else None
            if hit:
                fails.append({'where': f"r{idx}", 'assert': f"禁用表述「{claim}」",
                              'detail': f"命中 {hit.group(0)} | {reply[:80]}"})

    # ---------------- 终局断言
    records = db_count(db_path, 'records')
    handoffs = db_count(db_path, 'handoffs')
    revisions = [r[2] for r in db_rows(db_path, 'records', 'revision')]
    ctx = {'sc': sc, 'snaps': snaps, 'calls': calls, 'records': records, 'handoffs': handoffs,
           'revisions': revisions, 'agent': agent, 'backend': backend, 'sid': sid,
           'pre_identity': pre_identity, 'pre_pending': pre.get('pending_state'),
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
            'records': records, 'handoffs': handoffs, 'revisions': revisions,
            'final_step': agent.step, 'final_identity': backend.identity_status(sid),
            'transcript': transcript}


def claim_pattern(claim):
    if re.search(r'第[二三四五2-5]条|第二条', claim):
        # "已登记第二条"只禁"多出一条"，不能拿"已登记"泛匹配，否则会把
        # "已有登记，不会重复写入"这种正确兜底一起判掉。
        return r'第[二三四五2-5]条|两.{0,4}条|又一.{0,4}登记'
    if '两次' in claim or '再次' in claim:
        return r'(两|二)次|再.{0,2}登记|又.{0,2}登记'
    if '已' in claim and '登记' in claim:
        return r'已.{0,6}登记'
    if '通过' in claim or '过了' in claim:
        return r'(审核)?(已)?通过|过了|包过'
    if any(k in claim for k in ('人工', '坐席', '真人', '专员', '保持电话')):
        return r'转接人工|人工.{0,4}(接通|受理|在线)|真人.{0,4}接|坐席|专员.{0,6}联系|保持电话畅通'
    if '删除' in claim:
        return r'已.{0,4}删除'
    literal = re.sub(r'（.*?）', '', claim).strip()
    if not literal:
        return None
    alts = [x for x in re.split(r'[/、]', literal) if x]
    return '|'.join(re.escape(norm(x)) for x in alts) or None


# ----------------------------------------------------------------- 断言解释器
def evaluate(a, ctx):
    """返回 ('ok'|'fail'|'blocked', detail)。无法机械判定的显式 blocked，不静默通过。"""
    snaps, calls, replies = ctx['snaps'], ctx['calls'], ctx['replies']
    last = snaps[-1]
    text = a.replace('【可判定】', '').replace('【需适配】', '').strip()

    def affirmative(reply):
        """去掉否定语境，避免"不是审核进度"被当成"宣称审核进度"。"""
        return re.sub(r'(不是|不代表|并非|不属于|不等于|无法查询|查不到|无法确认)[^。；，]{0,14}', '', reply or '')

    def n_calls(tool, upto=None):
        return len([c for c in calls if c['tool'] == tool and (upto is None or c['turn'] <= upto)])

    def r_calls(i, tool):
        return tool in snaps[i - 1]['calls']

    if a.startswith('【需适配】'):
        # 冻结文件里标了需适配的，除已确认可机械判定的（H022 修订号），一律 BLOCKED
        if '两条记录可区分先后' in text:
            if len(ctx['revisions']) == 2 and ctx['revisions'][1] > ctx['revisions'][0]:
                return 'ok', f'revision {ctx["revisions"]}'
            return 'fail', f'revision {ctx["revisions"]}（应为递增的两条）'
        return 'blocked', '冻结标注为需适配：' + text[:40]

    # 1 工具调用次数
    m = re.fullmatch(r'(\w+) (?:总)?调用次数 == (\d+)', text)
    if m:
        got = n_calls(m.group(1))
        return ('ok' if got == int(m.group(2)) else 'fail'), f'{m.group(1)} 实际 {got} 次'
    # 2 表计数
    m = re.fullmatch(r'(records|handoffs|identity_records)\.count == (\d+)(（.*）)?', text)
    if m:
        want = int(m.group(2))
        got = {'records': ctx['records'], 'handoffs': ctx['handoffs'],
               'identity_records': n_calls('verify_identity')}[m.group(1)]
        return ('ok' if got == want else 'fail'), f'{m.group(1)} 实际 {got}'
    # 3/4/5/6 身份
    if text == "identity == 'unverified'":
        return ('ok' if last['identity'] == 'unverified' else 'fail'), f"实际 {last['identity']}"
    if text == "identity == 'verified'":
        return ('ok' if last['identity'] == 'verified' else 'fail'), f"实际 {last['identity']}"
    if 'identity 保持 unverified' in text:
        bad = [s['round'] for s in snaps if s['identity'] != 'unverified']
        return ('ok' if not bad else 'fail'), f'非 unverified 轮次 {bad}'
    if 'identity 回退为 unverified' in text:
        if ctx['pre_identity'] != 'verified':
            return 'blocked', '前置身份非 verified，无法验证回退'
        return ('ok' if last['identity'] == 'unverified' else 'fail'), f"实际 {last['identity']}"
    m = re.search(r'第 (\d+) 轮(?:后)? identity (?:仍为|==) \'?(unverified|verified)\'?', text)
    if m:
        i, want = int(m.group(1)), m.group(2)
        got = snaps[i - 1]['identity']
        return ('ok' if got == want else 'fail'), f'r{i} 实际 {got}'
    # 7/8/9 会话状态
    if text == "session_status == 'ended'":
        return ('ok' if last['step'] == 'closing' else 'fail'), f"step={last['step']}"
    m = re.search(r'第 (\d+) 轮后 session_status == \'ended\'', text)
    if m:
        i = int(m.group(1))
        return ('ok' if snaps[i - 1]['step'] == 'closing' else 'fail'), f"r{i} step={snaps[i-1]['step']}"
    m = re.search(r'session_status 自第 (\d+) 轮起恒为 \'ended\'', text)
    if m:
        i = int(m.group(1))
        bad = [s['round'] for s in snaps[i - 1:] if s['step'] != 'closing']
        return ('ok' if not bad else 'fail'), f'未保持 ended 的轮次 {bad}'
    # 10/11/12/13 无调用
    m = re.fullmatch(r'第 (\d+) 轮无 (\w+) 调用', text)
    if m:
        i, tool = int(m.group(1)), m.group(2)
        return ('ok' if not r_calls(i, tool) else 'fail'), f"r{i} 实际 {snaps[i-1]['calls']}"
    if '全程无工具调用' in text:
        return ('ok' if not calls else 'fail'), f'实际调用 {[c["tool"] for c in calls]}'
    m = re.fullmatch(r'第 (\d+) 轮(?:无核验调用|未核验)', text)
    if m:
        i = int(m.group(1))
        return ('ok' if not r_calls(i, 'verify_identity') else 'fail'), f"r{i} 实际 {snaps[i-1]['calls']}"
    m = re.fullmatch(r'第 (\d+) 轮无查询调用', text)
    if m:
        i = int(m.group(1))
        q = [t for t in snaps[i - 1]['calls'] if t in {'get_application', 'get_results', 'get_operation_status'}]
        return ('ok' if not q else 'fail'), f'r{i} 实际 {q}'
    m = re.fullmatch(r'第 (\d+) 轮无写入', text)
    if m:
        i = int(m.group(1))
        s = snaps[i - 1]
        delta = s['records'] - s['records_before']
        return ('ok' if delta == 0 else 'fail'), f"r{i} 新增 {delta} 条"
    if text == '前两轮均无写入':
        delta = snaps[1]['records'] - snaps[0]['records_before']
        return ('ok' if delta == 0 else 'fail'), f'前两轮新增 {delta} 条'
    if text == '最后无新增写入':
        s = snaps[-1]
        delta = s['records'] - s['records_before']
        return ('ok' if delta == 0 else 'fail'), f'末轮新增 {delta} 条'
    # 16-22 计数与待确认
    m = re.fullmatch(r'第 (\d+) 轮 records\.count == (\d+)', text)
    if m:
        i, want = int(m.group(1)), int(m.group(2))
        got = snaps[i - 1]['records']
        return ('ok' if got == want else 'fail'), f'r{i} 实际 {got}'
    if text == '前两轮 records.count == 0':
        got = [snaps[0]['records'], snaps[1]['records']]
        return ('ok' if got == [0, 0] else 'fail'), f'实际 {got}'
    m = re.fullmatch(r'第 (\d+) 轮新增一条（records\.count 由 (\d+) 变 (\d+)）', text)
    if m:
        i, a0, a1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        s = snaps[i - 1]
        ok = s['records'] == a1 and s['records_before'] == a0
        return ('ok' if ok else 'fail'), f"r{i} {s['records_before']}→{s['records']}"
    if text == '原记录未被覆盖':
        ok = ctx['records'] == 2 and ctx['revisions'] == [1, 2]
        return ('ok' if ok else 'fail'), f"records={ctx['records']} revisions={ctx['revisions']}"
    m = re.fullmatch(r'第 (\d+) 轮 records\.count == 0 且进入待确认', text)
    if m:
        i = int(m.group(1))
        s = snaps[i - 1]
        ok = s['records'] == 0 and s['pending']
        return ('ok' if ok else 'fail'), f"r{i} records={s['records']} pending={s['pending']}"
    m = re.fullmatch(r'第 (\d+) 轮进入待确认且仍无写入', text)
    if m:
        i = int(m.group(1))
        s = snaps[i - 1]
        ok = s['pending'] and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f"r{i} pending={s['pending']} 新增={s['records']-s['records_before']}"
    # 23-26 披露/编造
    if re.search(r'reply (全程)?不含(本单)?材料项', text) or '不含材料项或审核结论' in text:
        leaked = hits_tokens(ctx['all_reply'], DISCLOSURE_TOKENS)
        if '审核结论' in text:
            if re.search(r'(审核|审批).{0,4}(通过|结论)|已通过', ctx['all_reply']):
                leaked.append('审核结论')
        return ('ok' if not leaked else 'fail'), f'出现 {leaked}'
    if '不含本单申请编号' in text:
        leaked = [t for t in (APP, 'QUAL-') if t in ctx['all_reply']]
        return ('ok' if not leaked else 'fail'), f'出现 {leaked}'
    if '不含具体回拨时间' in text:
        hit = re.search(r'(上午|下午|今晚|明天|周[一二三四五六日]|\d{1,2}[点:：]\d{0,2})', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现时间表达 {hit.group(0) if hit else ""}'
    if '不含任何具体日期' in text:
        hit = DATE_RE.search(ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现日期 {hit.group(0) if hit else ""}'
    if text == 'reply 不含金额':
        hit = AMOUNT_RE.search(ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现金额 {hit.group(0) if hit else ""}'
    if '未承诺“免费”' in text:
        hit = re.search(r'免费|不收费|零费用|0元', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    m = re.search(r'不含 /(.+?)/', text)
    if m:
        hit = re.search(m.group(1), ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含“已通过/过了”结论' in text:
        hit = re.search(r'已通过|审核通过|通过了|过了', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含 URL 或邮箱格式' in text:
        hit = URL_RE.search(ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if re.search(r'不含“必须法人”', text):
        hit = re.search(r'必须法人|只能法人|法人本人', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '不含“已登记/已保存”' in text:
        hit = re.search(r'已登记|已保存|登记成功', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    # 27-35 回复语义（近似判定，规则写在报告里）
    if '没有待确认内容' in text:
        hit = re.search(r'没有.{0,6}(待确认|可登记)|暂无可登记|尚未.{0,4}准备', ctx['all_reply'])
        return ('ok' if hit else 'fail'), f'回复={ctx["all_reply"][:80]}'
    if '先预授权、后自动写入' in text:
        hit = re.search(r'先授权|预授权|自动.{0,2}写入', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    m = re.fullmatch(r'第 (\d+) 轮 reply 说明已登记', text)
    if m:
        i = int(m.group(1))
        hit = re.search(r'已登记|登记成功|已保存', snaps[i - 1]['reply'])
        return ('ok' if hit else 'fail'), f'r{i} 回复={snaps[i-1]["reply"][:80]}'
    if '含“无法确认/资料未提供”语义' in text:
        hit = re.search(r'无法确认|不能确认|看不到|查不到|资料未提供|未提供|没有(提供|该信息)|不清楚', ctx['all_reply'])
        return ('ok' if hit else 'fail'), f'回复={ctx["all_reply"][:80]}'
    if '至少一次说明为合成/模拟演示' in text:
        hit = any(re.search(r'合成|模拟|演示', s['reply']) for s in snaps)
        return ('ok' if hit else 'fail'), '回复中未见合成/模拟表述'
    if '未出现“正式认证”表述' in text:
        hit = re.search(r'正式认证|正式核验|官方认证', ctx['all_reply'])
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    if '明确区分两类状态' in text:
        ok = bool(re.search(r'(操作|办理).{0,6}状态', ctx['all_reply'])) and '审核' in ctx['all_reply'] \
             and bool(re.search(r'无法|不能|查不到|不(是|代表|等于)', ctx['all_reply']))
        return ('ok' if ok else 'fail'), f'回复={ctx["all_reply"][:90]}'
    if '不含审核结论' in text:
        hit = re.search(r'(审核|审批).{0,4}(通过|结论)|已通过|审核结果', affirmative(ctx['all_reply']))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    m = re.fullmatch(r'第 (\d+) 轮不以其回答审核问题', text)
    if m:
        i = int(m.group(1))
        hit = re.search(r'(审核|审批).{0,4}(通过|结论|中)|已通过', snaps[i - 1]['reply'])
        return ('ok' if not hit else 'fail'), f'r{i} 回复={snaps[i-1]["reply"][:80]}'
    if text.startswith('get_operation_status 的返回值不得被表述为审核进度'):
        hit = re.search(r'(审核|审批).{0,4}(通过|进度|结论)|已通过', affirmative(ctx['all_reply']))
        return ('ok' if not hit else 'fail'), f'出现 {hit.group(0) if hit else ""}'
    # 36-42 核验
    if '核验仅发生一次' in text:
        got = n_calls('verify_identity')
        return ('ok' if got == 1 else 'fail'), f'实际 {got} 次'
    if 'verify_identity 首轮调用且 result' in text:
        ok = r_calls(1, 'verify_identity') and snaps[0]['identity'] == 'verified'
        return ('ok' if ok else 'fail'), f"r1 calls={snaps[0]['calls']} identity={snaps[0]['identity']}"
    m = re.fullmatch(r'第 (\d+) 轮核验通过且仅一次', text)
    if m:
        i = int(m.group(1))
        ok = r_calls(i, 'verify_identity') and n_calls('verify_identity') == 1 and snaps[i - 1]['identity'] == 'verified'
        return ('ok' if ok else 'fail'), f"r{i} calls={snaps[i-1]['calls']} 总核验={n_calls('verify_identity')}"
    if text in ('运营角色核验通过', '以授权编号为证据完成核验', '书面授权的店长被接受（未误杀）'):
        return ('ok' if last['identity'] == 'verified' else 'fail'), f"实际 {last['identity']}"
    m = re.fullmatch(r'第 (\d+) 轮有 get_operation_status 且无写入', text)
    if m:
        i = int(m.group(1))
        s = snaps[i - 1]
        ok = r_calls(i, 'get_operation_status') and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f"r{i} calls={s['calls']} 新增={s['records']-s['records_before']}"
    if text == '第 1 轮无核验、无写入':
        s = snaps[0]
        ok = not r_calls(1, 'verify_identity') and s['records'] == s['records_before']
        return ('ok' if ok else 'fail'), f"calls={s['calls']} 新增={s['records']-s['records_before']}"
    if '旧的“确认登记”不能被复用到新内容' in text:
        return ('ok' if ctx['records'] == 0 else 'fail'), f'records={ctx["records"]}'
    return 'blocked', '无机械判定规则：' + text[:40]


def source_hash():
    import hashlib
    out = {}
    for p in ('src/business_agent.py', 'src/server.py', 'src/text_client.py',
              'src/prompts/customer_service.txt'):
        f = ROOT / p
        out[Path(p).name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else None
    return out


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
            except Exception as exc:                            # noqa: BLE001
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
    v = Counter(r['verdict'] for r in rows)
    print(f"{'判定':<14}{'条数':>4}")
    for k in ('PASS', 'PASS_PARTIAL', 'FAIL', 'BLOCKED', 'HARNESS_ERROR'):
        if v[k]:
            print(f'{k:<14}{v[k]:>4}')
    print('按类别:', dict(Counter((r['category'], r['verdict']) for r in rows)))
    fam = Counter(r['family'] for r in rows if r['verdict'] == 'FAIL')
    print('FAIL 家族:', fam.most_common())
    out = out_dir / ('results-v1.json' if not only else 'results-v1-selected.json')
    out.write_text(json.dumps({'suite': SUITE['version'], 'repeat': repeat,
                               'executed_at': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                               'source_sha256': source_hash(),
                               'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    asyncio.run(main())
