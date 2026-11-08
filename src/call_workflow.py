"""Four communication stages; records describe prepared replies, not audio delivery."""
import json
from datetime import datetime, timezone

STAGES = (
    ('introduction', '自报身份'),
    ('identity', '确认对方身份'),
    ('explanation', '说明驳回原因与整改动作'),
    ('closing', '礼貌结束'),
)


def migrate(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS call_stage_definitions(
        code TEXT PRIMARY KEY, position INTEGER UNIQUE NOT NULL, label TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS call_progress(
        session TEXT PRIMARY KEY REFERENCES sessions(id), stage TEXT NOT NULL,
        tracking_origin TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS call_stage_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL REFERENCES sessions(id),
        stage TEXT NOT NULL REFERENCES call_stage_definitions(code),
        event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS call_stage_events_session ON call_stage_events(session,id);
    ''')
    db.executemany('INSERT OR IGNORE INTO call_stage_definitions VALUES(?,?,?)',
                   [(code, i, label) for i, (code, label) in enumerate(STAGES, 1)])
    # Existing sessions have no trustworthy stage history. Never invent completion.
    db.execute("INSERT OR IGNORE INTO call_progress SELECT id,'unknown','legacy',? FROM sessions",
               (datetime.now(timezone.utc).isoformat(),))


def record(db, sid, stage, event, detail=None):
    if stage not in dict(STAGES):
        raise ValueError('Unknown call stage')
    now = datetime.now(timezone.utc).isoformat()
    db.execute('INSERT INTO call_stage_events(session,stage,event,detail,created_at) VALUES(?,?,?,?,?)',
               (sid, stage, event, json.dumps(detail or {}, ensure_ascii=False), now))
    db.execute('UPDATE call_progress SET stage=?,updated_at=? WHERE session=?', (stage, now, sid))


def snapshot(db, sid):
    row = db.execute('SELECT stage,tracking_origin FROM call_progress WHERE session=?', (sid,)).fetchone()
    return {'stage': row['stage'], 'tracking_origin': row['tracking_origin'],
            'stages': [{'code': code, 'label': label} for code, label in STAGES],
            'delivery_status': 'not_verified', 'formal_rejection_status': 'not_connected'}
