"""核对 v3 三个故障位是否真的打到后端：直接驱动 Backend.call，不经模型。

用途：v3 结果里 S006/S008/S009 的判定依赖"故障确实发生过"，而 snaps 只记录
调用序列、无法证明故障位被消费。本脚本按用例里的 fault_plan 原样复现，
把每种故障的可观测后果落盘（谁抛错、库内记录数、是否提交），
不改变任何用例与判定。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import run_v3 as V3                                                   # noqa: E402
from run_v3 import Backend, Faults                                    # noqa: E402

OUT = ROOT / 'data/independent-v3/fault-evidence.json'


def fresh():
    db = str(Path(tempfile.mkdtemp()) / 'probe.db')
    backend = Backend(db, faults=True)
    sid = backend.call('start_session', {'application_id': 'QUAL-001'})['session_id']
    backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
    return db, backend, sid


def save_params(key):
    issues = [i['issue_id'] for i in V3.APP_DATA['issues']]
    return dict(idempotency_key=key, result_revision=1, result=dict(
        candidate_outcome='notification_completed', issues_explained=issues,
        user_feedback='confirmed', confirmation_turn_id=1, unresolved=False, questions=[]))


def run_case(case_id, turn, tool, fault):
    db, backend, sid = fresh()
    params = {'session_id': sid, 'application_id': 'QUAL-001'}
    if tool == 'save_call_result':
        params = {'session_id': sid, **save_params('probe-' + case_id)}
        backend.authorize_write(sid, 'save_call_result', params)
    faults = Faults(backend, {turn: {tool: fault}})
    faults.turn = turn
    observed, raised, records = None, None, None
    try:
        observed = backend.call(tool, params)
    except Exception as exc:                                          # noqa: BLE001
        raised = f'{type(exc).__name__}: {exc}'
    records = V3.V1.db_count(db, 'records')
    return {
        'case_id': case_id, 'turn': turn, 'tool': tool, 'fault': fault,
        'raised': raised,
        'returned_error': (observed or {}).get('error') if isinstance(observed, dict) else None,
        'returned_ok': isinstance(observed, dict) and 'error' not in observed,
        'records_after': records,
        'committed_before_raise': records == 1 and raised is not None,
    }


def main():
    plans = {}
    for f in ('state-fault-v3.json',):
        suite = json.loads((ROOT / 'data/independent-v3' / f).read_text(encoding='utf-8'))
        for c in suite['cases']:
            if c.get('fault_plan'):
                plans[c['id']] = c['fault_plan']

    rows = []
    for cid, plan in sorted(plans.items()):
        for turn, spec in plan.items():
            for tool, fault in spec.items():
                rows.append(run_case(cid, int(turn), tool, fault))

    # 基线：不开故障时同一调用必须成功，否则上面的失败无法归因
    db, backend, sid = fresh()
    p = {'session_id': sid, **save_params('probe-baseline')}
    backend.authorize_write(sid, 'save_call_result', p)
    base = backend.call('save_call_result', p)
    baseline = {'records_after': V3.V1.db_count(db, 'records'),
                'keys': sorted(base.keys()) if isinstance(base, dict) else None}

    payload = {'plans_in_suite': plans, 'fault_runs': rows, 'baseline_no_fault': baseline}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for r in rows:
        print(f"{r['case_id']} t{r['turn']} {r['tool']:<18} {r['fault']:<14} "
              f"raised={r['raised']} err={r['returned_error']} rec={r['records_after']}")
    print('baseline', baseline)
    print('->', OUT)


if __name__ == '__main__':
    main()
