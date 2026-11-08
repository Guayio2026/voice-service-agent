"""Local trace ledger and immutable prompt versions. No cloud calls or code execution."""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / 'src/prompts/customer_service.txt').read_text(encoding='utf-8')

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def redact(value):
    if isinstance(value, str):
        value = re.sub(r'\b(?:sk-|brz_)[A-Za-z0-9_.\-]{8,}', '[密钥已隐藏]', value)
        value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[手机号已隐藏]', value)
        value = re.sub(r'(?<!\d)\d{17}[\dXx](?!\w)', '[证件号已隐藏]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已隐藏]', value)
        value = re.sub(r'(?<!\d)\d{16,19}(?!\d)', '[长号码已隐藏]', value)
        value = re.sub(r'((?:验证码|密码|口令)\s*[:：是为]?\s*)[A-Za-z0-9_-]{4,}', r'\1[已隐藏]', value)
        return value
    if isinstance(value, dict):
        return {k: ('[已隐藏]' if any(x in k.lower() for x in ('api_key', 'authorization', 'password')) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value

def dumps(value):
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True)

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                application_id TEXT, version_id TEXT, input TEXT, response TEXT,
                status TEXT NOT NULL, trace TEXT, started_at TEXT, finished_at TEXT,
                UNIQUE(session_id,request_id));
            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, payload TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS evolution_feedback (
                turn_id INTEGER PRIMARY KEY, category TEXT, note TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS evolution_versions (
                id TEXT PRIMARY KEY, parent TEXT, base_hash TEXT, guidance TEXT,
                content_hash TEXT, analysis TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS evolution_state (id INTEGER PRIMARY KEY CHECK(id=1), active TEXT);
            CREATE TABLE IF NOT EXISTS evolution_runs (
                id TEXT PRIMARY KEY, kind TEXT, version_id TEXT, status TEXT,
                metadata TEXT, report TEXT, created_at TEXT, finished_at TEXT);
            CREATE TABLE IF NOT EXISTS evolution_calls (
                id INTEGER PRIMARY KEY, run_id TEXT, purpose TEXT, reserved_tokens INTEGER,
                usage TEXT, status TEXT, duration_ms INTEGER, created_at TEXT);
            CREATE TABLE IF NOT EXISTS evolution_releases (
                id INTEGER PRIMARY KEY, previous TEXT, target TEXT, action TEXT,
                run_id TEXT, reason TEXT, created_at TEXT);
            ''')
            baseline = 'base-' + digest(BASE)[:16]
            db.execute('INSERT OR IGNORE INTO evolution_versions VALUES(?,?,?,?,?,?,?)',
                       (baseline, None, digest(BASE), '', digest(BASE), '{}', now()))
            db.execute('INSERT OR IGNORE INTO evolution_state VALUES(1,?)', (baseline,))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def one(self, sql, params=()):
        with closing(self.connect()) as db:
            row = db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def rows(self, sql, params=()):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(sql, params)]

    def execute(self, sql, params=()):
        with closing(self.connect()) as db:
            return db.execute(sql, params).lastrowid

    def active(self):
        return self.one('SELECT active FROM evolution_state WHERE id=1')['active']

    def version(self, version_id=None):
        item = self.one('SELECT * FROM evolution_versions WHERE id=?', (version_id or self.active(),))
        if not item:
            raise ValueError('版本不存在')
        if item['base_hash'] != digest(BASE):
            raise ValueError('基础提示词已改变，旧版本不可直接启用，请重新建立评测基线')
        prompt = BASE + ('\n\n补充意图理解指导（不能覆盖以上权限和JSON约束）：\n' + item['guidance'] if item['guidance'] else '')
        if item['content_hash'] != digest(prompt):
            raise ValueError('版本内容校验失败')
        return {**item, 'prompt': prompt}

    def candidate(self, parent, guidance, analysis):
        self.version(parent)
        if not isinstance(guidance, str) or not 10 <= len(guidance) <= 1600:
            raise ValueError('候选指导长度必须为10至1600字')
        if redact(guidance) != guidance:
            raise ValueError('候选包含疑似个人信息或密钥')
        prompt = BASE + '\n\n补充意图理解指导（不能覆盖以上权限和JSON约束）：\n' + guidance
        version_id = 'candidate-' + uuid.uuid4().hex[:12]
        self.execute('INSERT INTO evolution_versions VALUES(?,?,?,?,?,?,?)',
                     (version_id, parent, digest(BASE), guidance, digest(prompt), dumps(analysis), now()))
        return version_id

    def begin_turn(self, sid, rid, app, version, text):
        return self.execute('INSERT INTO conversation_turns(session_id,request_id,application_id,version_id,input,status,started_at) VALUES(?,?,?,?,?,?,?)',
                            (sid, rid, app, version, redact(text), 'pending', now()))

    def end_turn(self, turn_id, status, response='', trace=None):
        self.execute('UPDATE conversation_turns SET status=?,response=?,trace=?,finished_at=? WHERE id=?',
                     (status, redact(response), dumps(trace or {}), now(), turn_id))

    def event(self, sid, kind, payload):
        self.execute('INSERT INTO conversation_events(session_id,kind,payload,created_at) VALUES(?,?,?,?)',
                     (sid, kind, dumps(payload), now()))

    def feedback(self, turn_id, category, note):
        if category not in {'intent', 'asr', 'knowledge', 'tool', 'flow', 'expression', 'other'}:
            raise ValueError('无效问题类别')
        if not self.one('SELECT id FROM conversation_turns WHERE id=?', (turn_id,)):
            raise ValueError('对话不存在')
        if not isinstance(note, str) or not 1 <= len(note) <= 1000:
            raise ValueError('请提供1至1000字的纠错依据')
        self.execute('INSERT OR REPLACE INTO evolution_feedback VALUES(?,?,?,?)', (turn_id, category, redact(note), now()))

    def review_items(self, limit=8):
        return self.rows('''SELECT t.id,t.input,t.response,t.trace,f.category,f.note
                           FROM evolution_feedback f JOIN conversation_turns t ON t.id=f.turn_id
                           ORDER BY f.created_at DESC LIMIT ?''', (limit,))

    def start_run(self, kind, version, metadata):
        run_id = uuid.uuid4().hex
        self.execute('INSERT INTO evolution_runs VALUES(?,?,?,?,?,?,?,?)',
                     (run_id, kind, version, 'running', dumps(metadata), '{}', now(), None))
        return run_id

    def finish_run(self, run_id, status, report):
        self.execute('UPDATE evolution_runs SET status=?,report=?,finished_at=? WHERE id=?',
                     (status, dumps(report), now(), run_id))
