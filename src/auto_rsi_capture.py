"""Capture only explicitly registered, already exposed regression runners.

Unknown runners and sealed evaluations are excluded by default.
"""
import json
import sys
import uuid
from pathlib import Path
from evolution_store import Store, ROOT, BASE, digest

RUNNERS = {'run_v3.py', 'run_holdout_v1.py', 'run_holdout_v2.py',
           'run_generated_100.py', 'run_generated_scenarios.py'}


def capture(agent, text, result):
    if Path(sys.argv[0]).name not in RUNNERS or not result.get('model'):
        return
    if not agent.last_decision or not agent.classifier_messages:
        return
    # Test doubles do not supply provider model usage. Never learn their errors.
    if not result.get('usage', {}).get('total_tokens', 0):
        return
    store = Store(ROOT / 'runtime/rsi-inbox.sqlite3')
    prompt_hash = digest(agent.classifier_prompt)
    version = store.one('SELECT id FROM evolution_versions WHERE content_hash=?', (prompt_hash,))
    if not version:
        production = Store(ROOT / 'runtime/mock.sqlite3')
        version = production.one('SELECT * FROM evolution_versions WHERE content_hash=?', (prompt_hash,))
        if not version:
            return
        keys = ('id','parent','base_hash','guidance','content_hash','analysis','created_at')
        store.execute('INSERT OR IGNORE INTO evolution_versions VALUES(?,?,?,?,?,?,?)',tuple(version[k] for k in keys))
    tid = store.begin_turn('regression-'+agent.sid, uuid.uuid4().hex, agent.app, version['id'], text)
    store.end_turn(tid, 'done', '', {'origin':'registered_regression', 'runner':Path(sys.argv[0]).name,
        'decision':agent.last_decision, 'classifier_messages':agent.classifier_messages,
        'model':result['model'], 'error':None, 'identity':agent.backend.identity_status(agent.sid),
        'step':agent.step, 'usage':result['usage']})
