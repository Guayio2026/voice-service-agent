"""Bounded multi-turn RSI: collect -> propose -> compare -> release -> monitor.

No evaluation answers are supplied to the proposer. Production tools are never
used for scoring; only the prompt version pointer can be changed by this module.
"""
import asyncio
import difflib
import hashlib
import json
import math
import tempfile
from contextlib import closing
from pathlib import Path

from business_agent import Agent
from evolution import BudgetClient, normalized, propose
from evolution_store import Store, ROOT, dumps, now
from server import Backend


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def engine_hash():
    h = hashlib.sha256()
    for p in sorted((ROOT / 'src').rglob('*')):
        if p.is_file() and p.suffix in {'.py', '.txt'}:
            h.update(str(p.relative_to(ROOT)).encode()); h.update(p.read_bytes())
    for p in sorted((ROOT / 'data/synthetic').glob('*.json')):
        h.update(p.read_bytes())
    return h.hexdigest()


CHECKS = {'identity', 'records', 'step', 'decision', 'contains_any', 'not_contains', 'forbid_tools'}


def load_suite(path, role):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('role') != role or not data.get('cases') or data.get('reviewed') is not True:
        raise ValueError(f'Expected nonempty {role} suite')
    seen = set()
    for case in data['cases']:
        if case['id'] in seen or not case.get('family') or len(case.get('turns', [])) < 2:
            raise ValueError('Unique case IDs, semantic family and multi-turn dialogues required')
        seen.add(case['id'])
        if case.get('identity', 'unverified') not in {'unverified', 'verified'}:
            raise ValueError('Unsupported initial identity')
        for turn in case['turns']:
            checks = turn.get('expect', {})
            if not checks or set(checks) - CHECKS or not isinstance(turn.get('text'), str):
                raise ValueError('Every turn needs supported assertions; no silent BLOCKED-as-PASS')
            if 'identity' not in checks or 'records' not in checks:
                raise ValueError('Every turn must assert identity and persisted record count')
            if checks['identity'] not in {'unverified','verified','wrong_contact'} or type(checks['records']) is not int or checks['records'] < 0:
                raise ValueError('Invalid identity/record assertion')
            for key in ('contains_any','not_contains','forbid_tools'):
                if key in checks and (not isinstance(checks[key],list) or not checks[key] or not all(isinstance(s,str) and s for s in checks[key])):
                    raise ValueError('Text/tool assertions must be nonempty string arrays')
            if 'decision' in checks:
                normalized(checks['decision'])
    return data


def judge(expect, actual):
    checks = {}
    for key, value in expect.items():
        if key == 'decision':
            checks[key] = actual['decision'] is not None and normalized(actual['decision']) == normalized(value)
        elif key == 'contains_any':
            checks[key] = any(s in actual['reply'] for s in value)
        elif key == 'not_contains':
            checks[key] = all(s not in actual['reply'] for s in value)
        elif key == 'forbid_tools':
            checks[key] = not set(value).intersection(actual['calls'])
        else:
            checks[key] = actual[key] == value
    return checks


async def score(store, version, client, suite, repeat, collect=False, run_id=''):
    rows, feedback_ids = [], []
    for case in suite['cases']:
        for attempt in range(repeat):
            with tempfile.TemporaryDirectory() as temp:
                backend = Backend(Path(temp) / 'business.sqlite3')
                app = case.get('application_id', 'QUAL-001')
                sid = backend.call('start_session', {'application_id': app})['session_id']
                agent = Agent(backend, sid, app, classifier_prompt=version['prompt'], version_id=version['id'])
                await agent.turn('开始通话', client, False)
                if case.get('identity') == 'verified':
                    backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
                    agent.step = 'availability'
                original, calls, tool_events = backend.call, [], []
                def traced(name, params):
                    calls.append(name)
                    event = {'tool':name,'parameters':params}
                    tool_events.append(event)
                    try:
                        result = original(name, params)
                        event['result'] = result
                        return result
                    except Exception as exc:
                        event['error'] = type(exc).__name__
                        raise
                backend.call = traced
                for n, turn in enumerate(case['turns']):
                    calls.clear()
                    tool_events.clear()
                    ledger_sid = f'rsi-{run_id}-{case["id"]}-{attempt}'
                    tid = store.begin_turn(ledger_sid, str(n), app, version['id'], turn['text']) if collect else None
                    try:
                        answer = await agent.turn(turn['text'], client, False)
                        records = original('get_results', {'session_id':sid})['results']
                        actual = {'reply':answer['text'], 'identity':backend.identity_status(sid),
                                  'records':len(records), 'step':agent.step, 'decision':agent.last_decision,
                                  'calls':calls[:], 'tool_events':tool_events[:], 'payloads':records}
                        checks = judge(turn['expect'], actual)
                        row = {'case':case['id'], 'attempt':attempt, 'turn':n, 'checks':checks,
                               'actual':actual, 'passed':all(checks.values())}
                        rows.append(row)
                        if collect:
                            trace = {'origin':'rsi_development', 'run_id':run_id, 'case_id':case['id'],
                                     'decision':agent.last_decision, 'error':agent.last_error,
                                     'classifier_messages':agent.classifier_messages, 'actual':actual, 'checks':checks}
                            store.end_turn(tid, 'done', answer['text'], trace)
                            # Only a failed classification with observed model context is
                            # eligible. Tool/state faults are not relabelled as intent errors.
                            eligible = checks.get('decision') is False and bool(agent.classifier_messages) and not agent.last_error
                            if eligible and all(v for k,v in checks.items() if k != 'decision'):
                                note = '开发集已声明的预期意图：' + json.dumps(turn['expect']['decision'], ensure_ascii=False)
                                store.feedback(tid, 'intent', note)
                                feedback_ids.append(tid)
                    except Exception as exc:
                        if tid is not None:
                            store.end_turn(tid, 'failed', '', {'origin':'rsi_development', 'error':type(exc).__name__})
                        rows.append({'case':case['id'], 'attempt':attempt, 'turn':n,
                                     'passed':False, 'error':type(exc).__name__, 'checks':{'execution':False}})
                        break
    return {'rows':rows, 'feedback_ids':feedback_ids, 'passed':all(r['passed'] for r in rows),
            'correct':sum(r['passed'] for r in rows), 'total':len(rows)}


class ScopedStore(Store):
    """Proposer sees only this cycle's selected development feedback."""
    def review_items(self, limit=8):
        selected, seen = [], set()
        for tid in self.selected_ids:
            item = self.one('SELECT t.id,t.input,t.response,t.trace,f.category,f.note FROM conversation_turns t '
                            'JOIN evolution_feedback f ON t.id=f.turn_id WHERE t.id=?', (tid,))
            if not item:
                continue
            key = (item['input'], item['note'])
            if key in seen:
                continue
            seen.add(key); selected.append(item)
            if len(selected) >= limit:
                break
        return selected


def compare(before, after):
    aligned = [(r['case'],r['attempt'],r['turn']) for r in before['rows']] == [(r['case'],r['attempt'],r['turn']) for r in after['rows']]
    hard = all(all(v for k,v in r['checks'].items() if k != 'decision') for r in after['rows'])
    return {'aligned':aligned, 'hard_constraints':hard,
            'no_regression':aligned and all(not b['passed'] or a['passed'] for b,a in zip(before['rows'],after['rows'])),
            'no_assertion_regression':aligned and all(
                all(not passed or a['checks'].get(key) is True for key,passed in b['checks'].items())
                for b,a in zip(before['rows'],after['rows'])),
            'improved':after['correct'] > before['correct']}


def development_gain(before, after, suite, repeat):
    """Require repeatable targeted repairs, not a one-turn score fluctuation.

    This is development evidence, not an independent generalization claim.
    """
    gates = compare(before, after)
    repaired = set()
    if gates['aligned'] and repeat >= 3:
        for case in suite['cases']:
            for turn in range(len(case['turns'])):
                old = [r for r in before['rows'] if r['case'] == case['id'] and r['turn'] == turn]
                new = [r for r in after['rows'] if r['case'] == case['id'] and r['turn'] == turn]
                if (len(old) == len(new) == repeat
                        and len({r['attempt'] for r in old}) == repeat
                        and sum(r['checks'].get('decision') is False for r in old) >= math.ceil(repeat * 2 / 3)
                        and all(r['passed'] and r['checks'].get('decision') is True for r in new)):
                    repaired.add(case['family'])
    return {'gates': {**gates, 'stable_repairs':len(repaired) >= 2},
            'repaired_families':sorted(repaired), 'minimum_families':2,
            'minimum_repeats':3, 'evidence_scope':'development_only'}


def usage_stats(store, run_id, first_call):
    calls = store.rows('SELECT * FROM evolution_calls WHERE run_id=? AND id>? ORDER BY id', (run_id,first_call))
    times = sorted(c['duration_ms'] for c in calls)
    tokens = 0
    reported = bool(calls)
    for c in calls:
        u = json.loads(c['usage']); count = u.get('total_tokens', u.get('prompt_tokens',0)+u.get('completion_tokens',0))
        reported = reported and count > 0 and c['status'] == 'done'
        tokens += count
    return {'tokens':tokens,'reported':reported,'p95_ms':times[math.ceil(len(times)*.95)-1] if times else 0}


def switch(store, expected, target, run_id, action, frozen):
    if engine_hash() != frozen:
        raise ValueError('Code changed during run; activation prohibited')
    store.version(target)
    with closing(store.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT active FROM evolution_state WHERE id=1').fetchone()[0] != expected:
            raise ValueError('Active version changed concurrently')
        db.execute('UPDATE evolution_state SET active=? WHERE id=1', (target,))
        db.execute('INSERT INTO evolution_releases(previous,target,action,run_id,reason,created_at) VALUES(?,?,?,?,?,?)',
                   (expected,target,action,run_id,'RSI multi-turn gates',now()))
        db.commit()


def run(store, client, config, output, activate=False):
    paths = {role:Path(config[role]).resolve() for role in ('development','validation','holdout')}
    hashes = {r:sha(p) for r,p in paths.items()}
    suites = {r:load_suite(p,r) for r,p in paths.items()}
    if any(sha(paths[r]) != hashes[r] for r in paths):
        raise ValueError('Suite changed while being loaded')
    families = [{c['family'] for c in suites[r]['cases']} for r in paths]
    texts = [{t['text'] for c in suites[r]['cases'] for t in c['turns']} for r in paths]
    if any(families[i]&families[j] or texts[i]&texts[j] for i in range(3) for j in range(i)):
        raise ValueError('Data splits share semantic families or exact utterances')
    repeat = config.get('repeat',3)
    if type(repeat) is not int or not 1 <= repeat <= 5:
        raise ValueError('repeat must be 1..5')
    baseline = store.version()
    frozen = engine_hash()
    store.execute('CREATE TABLE IF NOT EXISTS rsi_holdout_uses(hash TEXT PRIMARY KEY,run_id TEXT,created_at TEXT)')
    if store.one('SELECT hash FROM rsi_holdout_uses WHERE hash=?',(hashes['holdout'],)):
        raise ValueError('Holdout already exposed; provide a new independently reviewed holdout')
    rid = store.start_run('rsi_cycle',baseline['id'], {'hashes':hashes,'engine_hash':frozen,'activate':activate,
                         'model':getattr(client,'model','unknown'),'repeat':repeat})
    output = Path(output) / rid; output.mkdir(parents=True, exist_ok=False)
    report = {'run_id':rid,'baseline':baseline['id'],'baseline_prompt_hash':baseline['content_hash'],
              'source_hash':frozen,'dataset_hashes':hashes,'repeat':repeat,
              'model':getattr(client,'model','unknown'), 'status':'running','stages':[], 'published':False}
    def save(stage):
        report['stages'].append({'stage':stage,'at':now()})
        store.execute('UPDATE evolution_runs SET report=? WHERE id=?', (dumps(report),rid))
        (output/'report.json').write_text(dumps(report),encoding='utf-8')
        (output/'summary.txt').write_text('RSI运行 '+rid+'\n状态：'+report['status']+'\n基线：'+baseline['id']+
            '\n候选：'+report.get('candidate','未生成')+'\n阶段：'+', '.join(x['stage'] for x in report['stages'])+
            '\n发布：'+str(report['published'])+'\n门槛：'+json.dumps(report.get('gates',{}),ensure_ascii=False)+
            '\n错误：'+report.get('error','无')+'\n详见同目录report.json；生成候选后另有prompt.diff。',encoding='utf-8')
        print(stage, flush=True)
    adopted = False
    try:
        budget = BudgetClient(store,rid,client,config.get('max_calls',128),config.get('token_budget',1000000))
        save('development_started')
        development = asyncio.run(score(store,baseline,budget,suites['development'],repeat,True,rid))
        report['development'] = development
        save('development_recorded')
        if any(r['checks'].get('execution') is False for r in development['rows']):
            report['status'] = 'rejected_development'
            report['reason'] = '开发执行存在异常或预算不足，未将执行错误当作意图优化反馈。'
            return report
        if not development['feedback_ids']:
            report['reason'] = '没有经实际模型分类且其他硬断言通过的意图错误；不伪造优化反馈。'
            report['status'] = 'no_eligible_feedback'
            return report
        scoped = ScopedStore(store.path); scoped.selected_ids = development['feedback_ids']
        # Reuse the same BudgetClient; proposal has its own audit row but all
        # underlying provider calls still share this cycle's global budget.
        candidate_info = propose(scoped,budget,token_budget=config.get('proposal_token_budget',30000))
        candidate = store.version(candidate_info['candidate'])
        report['candidate'] = candidate['id']; report['proposal'] = candidate_info
        report['candidate_prompt_hash'] = candidate['content_hash']
        (output/'prompt.diff').write_text(''.join(difflib.unified_diff(baseline['prompt'].splitlines(True),candidate['prompt'].splitlines(True),fromfile=baseline['id'],tofile=candidate['id'])),encoding='utf-8')
        save('candidate_created')
        repaired = asyncio.run(score(store,candidate,budget,suites['development'],repeat))
        gain = development_gain(development,repaired,suites['development'],repeat)
        report['development_candidate'] = repaired
        report['development_comparison'] = gain
        report['gates'] = {'development_'+k:v for k,v in gain['gates'].items()}
        save('development_candidate_complete')
        if not all(report['gates'].values()):
            report['status'] = 'rejected_no_stable_gain'
            report['reason'] = '候选未证明至少两个开发问题家族稳定修复且无退步；不消费封存集。'
            return report
        def checkpoint():
            return store.one('SELECT COALESCE(MAX(id),0) AS n FROM evolution_calls WHERE run_id=?',(rid,))['n']
        start = checkpoint()
        before = asyncio.run(score(store,baseline,budget,suites['validation'],repeat))
        old_usage = usage_stats(store,rid,start)
        start = checkpoint()
        after = asyncio.run(score(store,candidate,budget,suites['validation'],repeat))
        new_usage = usage_stats(store,rid,start)
        gates = compare(before,after)
        # Validation protects unseen scenarios; a perfect baseline need not
        # improve. Actual repair evidence is required separately above.
        validation_gain = gates.pop('improved')
        gates.update(report['gates'])
        gates.update(usage_reported=old_usage['reported'] and new_usage['reported'],
                     token_limit=new_usage['tokens']<=old_usage['tokens']*1.5,
                     latency_limit=new_usage['p95_ms']<=max(3000,old_usage['p95_ms']*1.25))
        gates['frozen_before_holdout'] = engine_hash()==frozen and all(sha(paths[r])==hashes[r] for r in paths) and store.active()==baseline['id']
        report['validation'] = {'before':before,'after':after,'usage_before':old_usage,'usage_after':new_usage,
                                'observed_improvement':validation_gain}
        report['gates'] = gates
        save('validation_complete')
        if not all(gates.values()):
            report['status'] = 'rejected_validation'; return report
        store.execute('INSERT INTO rsi_holdout_uses VALUES(?,?,?)',(hashes['holdout'],rid,now()))
        start = checkpoint()
        old = asyncio.run(score(store,baseline,budget,suites['holdout'],repeat))
        old_holdout_usage = usage_stats(store,rid,start)
        start = checkpoint()
        new = asyncio.run(score(store,candidate,budget,suites['holdout'],repeat))
        new_holdout_usage = usage_stats(store,rid,start)
        report['holdout'] = {'before':old,'after':new,'usage_before':old_holdout_usage,'usage_after':new_holdout_usage}
        hg = compare(old,new)
        gates['holdout_passed'] = new['passed'] and hg['aligned'] and hg['no_regression'] and hg['no_assertion_regression']
        gates['holdout_resources'] = (old_holdout_usage['reported'] and new_holdout_usage['reported']
            and new_holdout_usage['tokens'] <= old_holdout_usage['tokens']*1.5
            and new_holdout_usage['p95_ms'] <= max(3000,old_holdout_usage['p95_ms']*1.25))
        gates['frozen'] = engine_hash()==frozen and all(sha(paths[r])==hashes[r] for r in paths) and store.active()==baseline['id']
        gates['candidate_unchanged'] = store.version(candidate['id'])['content_hash'] == candidate['content_hash']
        save('holdout_complete')
        if not all(gates.values()):
            report['status']='rejected_holdout'; return report
        if not activate:
            report['status']='eligible_shadow'; return report
        switch(store,baseline['id'],candidate['id'],rid,'rsi_publish',frozen)
        adopted = True; report['published']=True
        save('activated_for_new_sessions')
        monitor = asyncio.run(score(store,candidate,budget,suites['validation'],1))
        report['post_release'] = monitor
        if not monitor['passed']:
            switch(store,candidate['id'],baseline['id'],rid,'rsi_rollback',engine_hash())
            adopted=False; report['published']=False; report['status']='rolled_back'
        else:
            report['status']='published'
        return report
    except Exception as exc:
        report['status']='failed'; report['error']=type(exc).__name__+': '+str(exc)
        if adopted and store.active()==report.get('candidate'):
            switch(store,report['candidate'],baseline['id'],rid,'rsi_rollback',engine_hash())
            report['published']=False; report['status']='rolled_back_after_error'
        return report
    finally:
        report['calls']=store.rows('SELECT id,status,usage,duration_ms FROM evolution_calls WHERE run_id=?',(rid,))
        save('finished')
        store.finish_run(rid,report['status'],report)
