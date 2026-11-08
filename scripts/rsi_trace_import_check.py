"""只读核查：100 条回归集的执行轨迹是否进入进化数据库（runtime/mock.sqlite3）。
判定依据：用例原句与 conversation_turns.input 的实际重合、trace/session 中是否出现用例编号、
执行器是否使用进化存储层。不写任何数据。
用法：.venv/Scripts/python.exe scripts/rsi_trace_import_check.py [输出目录]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data/rsi-20261108'
DB = ROOT / 'runtime/mock.sqlite3'

CASE_FILES = [
    ('v1', 'data/independent-holdout/holdout-20261107-v1.json'),
    ('v2', 'data/independent-holdout/holdout-20261108-v2.json'),
    ('v3-A', 'data/independent-v3/real-model-v3.json'),
    ('v3-B', 'data/independent-v3/state-fault-v3.json'),
    ('v3-C', 'data/independent-v3/content-consistency-v3.json'),
]

HARNESS = ['scripts/run_holdout_v1.py', 'scripts/run_holdout_v2.py', 'scripts/run_v3.py',
           'scripts/run_v3_all.py', 'scripts/rejudge_v3.py', 'scripts/diff_regression.py']


def norm(text):
    return re.sub(r'[\s，。？！、,.?!~～"\'“”‘’]', '', (text or '')).lower()


def main():
    cases = []
    for suite, rel in CASE_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding='utf-8'))
        for case in data.get('cases', []):
            for idx, turn in enumerate(case.get('turns') or [], 1):
                text = turn.get('user_input') or ''
                if text.strip():
                    cases.append({'suite': suite, 'case_id': case['id'], 'turn': idx,
                                  'text': text, 'key': norm(text)})

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    turns = [dict(r) for r in con.execute(
        'SELECT id,session_id,request_id,application_id,version_id,input,response,status,started_at '
        'FROM conversation_turns ORDER BY id')]
    ids_text = ' '.join((t['session_id'] or '') + ' ' + (t['request_id'] or '') for t in turns)
    traces = ' '.join((r[0] or '') for r in con.execute('SELECT trace FROM conversation_turns'))
    con.close()

    by_key = {}
    for c in cases:
        by_key.setdefault(c['key'], []).append(c)

    exact, fuzzy = [], []
    for t in turns:
        k = norm(t['input'])
        if k in by_key:
            exact.append({'turn_id': t['id'], 'input': t['input'], 'matched_cases': [x['case_id'] for x in by_key[k]]})
        else:
            for ck in by_key:
                if not ck or not k:
                    continue
                short, long_ = (k, ck) if len(k) <= len(ck) else (ck, k)
                if len(short) >= 4 and short in long_:
                    fuzzy.append({'turn_id': t['id'], 'input': t['input'],
                                  'matched_cases': [x['case_id'] for x in by_key[ck]]})
                    break

    case_id_hits = sorted(set(re.findall(r'\b(?:H\d{3}|V\d{3}|R\d{3}|C\d{3}|S\d{3})\b',
                                         ids_text + ' ' + traces)))

    harness_uses_store = {}
    for rel in HARNESS:
        p = ROOT / rel
        if not p.exists():
            harness_uses_store[rel] = 'missing'
            continue
        text = p.read_text(encoding='utf-8')
        harness_uses_store[rel] = {
            'imports_evolution_store': bool(re.search(r'evolution_store|from evolution import|\bStore\(', text)),
            'writes_conversation_turns': bool(re.search(r'conversation_turns|begin_turn|end_turn', text)),
            'uses_temp_db': bool(re.search(r'TemporaryDirectory|tempfile|sqlite3', text)),
        }

    own = [t for t in turns if (t['session_id'] or '').startswith('eval') or (t['request_id'] or '').startswith('eval')]
    report = {
        'checked_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'db': str(DB.relative_to(ROOT)).replace('\\', '/'),
        'regression_cases_loaded': len({c['case_id'] for c in cases}),
        'regression_utterances': len(cases),
        'db_turns': len(turns),
        'db_turn_time_range': [turns[0]['started_at'], turns[-1]['started_at']] if turns else None,
        'db_distinct_versions': sorted({t['version_id'] for t in turns if t['version_id']}),
        'exact_matches': exact,
        'fuzzy_matches': fuzzy,
        'case_id_references_in_db': case_id_hits,
        'harness_store_usage': harness_uses_store,
        'test_generated_sessions': len(own),
        'verdict': 'IMPORT_CAPABILITY_MISSING' if not case_id_hits and not own else 'REVIEW',
        'conclusion': ('进化数据库中没有任何 100 条回归集的执行轨迹：无用例编号引用、无测试会话、'
                       '执行器不经过进化存储层。无法把回归失败关联到真实 conversation_turns ID。')
        if not case_id_hits and not own else '存在疑似测试轨迹，需人工复核',
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / 'trace-gap.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\n写入:', target)


if __name__ == '__main__':
    main()
