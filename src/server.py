"""Internal synthetic mock only; no production authentication or official policy."""
import json
import os
import secrets
import call_workflow
import qualification_store
from grounding import ground_application
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
CASES = {c['application_id']: c for filename in ('qualification_cases.json',)
         for c in json.loads((ROOT/'data/synthetic'/filename).read_text(encoding='utf-8'))}
OUTCOMES = {'notification_completed','wrong_contact','unverified','declined','deferred','handoff_registered','unresolved','interrupted'}
class Error(Exception):
    def __init__(self, code, status=400): self.code, self.status = code, status

def require(ok, code='INVALID_FIELDS', status=400):
    if not ok: raise Error(code, status)
def fields(obj, required, optional=()):
    require(type(obj) is dict and set(required)<=obj.keys() and obj.keys()<=set(required)|set(optional))
def string(v): return type(v) is str and 0<len(v)<=200

def visible_record(record, verified):
    """Apply current authorization to both stored records and cached replies."""
    if verified or 'record_id' not in record:
        return record
    return {k: record[k] for k in ('record_id', 'result_revision', 'outcome', 'persistence_status')}

def connect(path):
    db=sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory=sqlite3.Row
    return db

class Backend:
    def __init__(self, path, faults=False):
        self.path, self.faults = str(path), faults
        self._write_confirmations = {}

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(self.path)) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, app TEXT, identity TEXT);
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, session TEXT, revision INTEGER, payload TEXT, UNIQUE(session,revision));
            CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, session TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS operations(session TEXT, key TEXT, request TEXT, response TEXT, PRIMARY KEY(session,key));
            CREATE TABLE IF NOT EXISTS agent_profile(id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL);''')
            db.execute('INSERT OR IGNORE INTO agent_profile(id,name) VALUES(1,?)', ('小语',))
            call_workflow.migrate(db)
            qualification_store.migrate(db, CASES, json.loads((ROOT/'data/synthetic/material_catalog.json').read_text(encoding='utf-8')))

    def record_stage(self, sid, stage, event, detail=None):
        with closing(connect(self.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            require(db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone(), 'SESSION_NOT_FOUND', 404)
            call_workflow.record(db, sid, stage, event, detail)
            db.commit()

    def workflow(self, sid):
        with closing(connect(self.path)) as db:
            return call_workflow.snapshot(db, sid)
    def agent_name(self):
        with closing(connect(self.path)) as db:
            return db.execute('SELECT name FROM agent_profile WHERE id=1').fetchone()[0]
    def control_session(self, sid):
        # Reuse the operation ledger; no schema migration or parallel database.
        with closing(connect(self.path)) as db:
            db.execute("INSERT OR IGNORE INTO operations VALUES(?,?,?,?)",
                       (sid, '__agent_control__', '{}', '{}'))

    def revoke_identity(self, sid):
        with closing(connect(self.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            require(db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone(), 'SESSION_NOT_FOUND', 404)
            db.execute('UPDATE sessions SET identity=? WHERE id=?', ('unverified', sid))
            call_workflow.record(db, sid, 'identity', 'identity_revoked', {})
            db.commit()
        self._write_confirmations = {k:v for k,v in self._write_confirmations.items() if k[0] != sid}

    def latest_save_key(self, sid):
        with closing(connect(self.path)) as db:
            rows = db.execute('SELECT key,request FROM operations WHERE session=? ORDER BY rowid DESC', (sid,)).fetchall()
            for row in rows:
                if json.loads(row['request']).get('name') == 'save_call_result':
                    return row['key']
        return None

    def identity_status(self, sid):
        with closing(connect(self.path)) as db:
            row = db.execute('SELECT identity FROM sessions WHERE id=?', (sid,)).fetchone()
        require(row is not None, 'SESSION_NOT_FOUND', 404)
        return row[0]

    def authorize_write(self, sid, name, params):
        """Trusted controller only; never exposed as an HTTP/model tool.

        The controller calls this only after consuming explicit confirmation.
        Exact canonical content binds permission to this operation and revision.
        Restart loses permission safely; committed outcomes remain queryable.
        """
        self._write_confirmations[(sid, params['idempotency_key'])] = json.dumps(
            {'name': name, 'payload': {'session_id': sid, **params}}, sort_keys=True, ensure_ascii=False)

    def call(self, name, p, fault=None):
        require(fault is None or (self.faults and fault in {'query_fail','write_fail','response_lost'}), 'FAULT_DISABLED')
        if name=='cases': return {'simulated':True,'cases':[{'application_id':k,'label':v['label'],'synthetic':True} for k,v in CASES.items() if k in {'QUAL-001','QUAL-002','QUAL-003','QUAL-004'}]}
        if name=='start_session':
            fields(p,['application_id']); require(p['application_id'] in CASES)
            sid=secrets.token_urlsafe(32)
            with closing(connect(self.path)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(sid,p['application_id'],'unverified'))
                db.execute('INSERT INTO call_progress VALUES(?,?,?,?)', (sid,'introduction','new',datetime.now(timezone.utc).isoformat()))
                db.commit()
            return {'session_id':sid,'identity_status':'unverified','simulated':True,'notice':'仅本地测试，不是生产身份认证'}
        schemas={'verify_identity':(['session_id','is_responsible'],[]),'get_application':(['session_id','application_id'],[]),'get_results':(['session_id'],[]),'get_operation_status':(['session_id','idempotency_key'],[]),'save_call_result':(['session_id','idempotency_key','result_revision','result'],[]),'create_handoff':(['session_id','idempotency_key','consent','reason'],[])}
        require(name in schemas,'NOT_FOUND',404)
        fields(p,*schemas[name]); require(string(p['session_id']))
        db=connect(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            s=db.execute('SELECT * FROM sessions WHERE id=?',(p['session_id'],)).fetchone()
            require(s is not None,'SESSION_NOT_FOUND',404)
            sid=s['id']; app=qualification_store.application(db, s['app']); verified=s['identity']=='verified'
            if name=='verify_identity':
                require(type(p['is_responsible']) is bool)
                identity='verified' if p['is_responsible'] else 'wrong_contact'
                db.execute('UPDATE sessions SET identity=? WHERE id=?',(identity,sid))
                call_workflow.record(db, sid, 'identity', 'identity_checked', {'status': identity, 'simulated': True})
                self._write_confirmations = {k:v for k,v in self._write_confirmations.items() if k[0] != sid}
                result={'identity_status':identity,'simulated':True,'notice':'仅本地模拟确认'}
            elif name=='get_application':
                require(verified,'IDENTITY_REQUIRED',403); require(p['application_id']==s['app'],'APPLICATION_SCOPE',403)
                require(fault!='query_fail','QUERY_FAILED',503)
                require(app['basis_status']=='valid','BASIS_UNAVAILABLE',409)
                result=app
            elif name=='get_results':
                rows=db.execute('SELECT payload FROM records WHERE session=? ORDER BY revision',(sid,)).fetchall()
                result={'results':[json.loads(r[0]) for r in rows]}
                if not verified:
                    result={'results':[visible_record(r, verified) for r in result['results']]}
            else:
                require(string(p['idempotency_key']) and not p['idempotency_key'].startswith('__'))
                old=db.execute('SELECT * FROM operations WHERE session=? AND key=?',(sid,p['idempotency_key'])).fetchone()
                canonical=json.dumps({'name':name,'payload':p},sort_keys=True,ensure_ascii=False)
                if name=='get_operation_status':
                    result={'status':'confirmed','result':json.loads(old['response'])} if old else {'status':'unknown','result':None}
                elif old:
                    require(old['request']==canonical,'IDEMPOTENCY_CONFLICT',409)
                    result=json.loads(old['response'])
                else:
                    require(not db.execute("SELECT 1 FROM operations WHERE session=? AND key='__agent_control__'", (sid,)).fetchone() or self._write_confirmations.get((sid,p['idempotency_key'])) == canonical, 'EXPLICIT_CONFIRMATION_REQUIRED',403)
                    require(fault!='write_fail','WRITE_FAILED',503)
                    rid=secrets.token_hex(16)
                    if name=='create_handoff':
                        require(p['consent'] is True,'CONSENT_REQUIRED')
                        require(p['reason'] in ['user_request','basis_unavailable','communication_difficulty'])
                        result={'handoff_id':rid,'status':'registered','simulated':True,'notice':'仅建立模拟工单，未接通真人'}
                        db.execute('INSERT INTO handoffs VALUES(?,?,?)',(rid,sid,json.dumps({'consent':True,'reason':p['reason']})))
                    else:
                        rev=p['result_revision']; r=p['result']
                        require(type(rev) is int and rev>0)
                        fields(r,['candidate_outcome','issues_explained','user_feedback','confirmation_turn_id','unresolved'],['handoff_id','questions','registration_summary','notification_status','remediation_status'])
                        if 'notification_status' in r:
                            require(r['notification_status'] in {'unknown','communicated','received'}, 'INVALID_NOTIFICATION_STATUS')
                        if 'remediation_status' in r:
                            require(r['remediation_status'] in {'unknown','incomplete'}, 'INVALID_REMEDIATION_STATUS')
                        if 'registration_summary' in r:
                            require(verified and type(r['registration_summary']) is str and 0 < len(r['registration_summary']) <= 1500, 'INVALID_SUMMARY')
                        questions = r.get('questions', [])
                        require(type(questions) is list and len(questions) <= 300)
                        for q in questions:
                            fields(q, ['topic', 'issue_numbers', 'status', 'evidence_ids'])
                            require(q['topic'] in {'materials','reason','action','basis','deadline','channel','fees','guarantee','status','other'})
                            require(q['status'] in {'pending','answered','needs_verification'})
                            require(type(q['issue_numbers']) is list and len(q['issue_numbers']) <= 10 and all(type(n) is int and 1 <= n <= 100 for n in q['issue_numbers']))
                            require(type(q['evidence_ids']) is list and all(string(e) and e in {i['evidence_id'] for i in app['issues']} for e in q['evidence_ids']))
                        require(verified or not questions, 'MINIMAL_RESULT_REQUIRED', 403)
                        require(r['candidate_outcome'] != 'notification_completed' or all(q['status'] == 'answered' for q in questions), 'UNRESOLVED_QUESTIONS')
                        require(type(r['candidate_outcome']) is str and r['candidate_outcome'] in OUTCOMES)
                        require(type(r['issues_explained']) is list and all(string(x) for x in r['issues_explained']))
                        require(len(set(r['issues_explained']))==len(r['issues_explained']))
                        require(set(r['issues_explained'])<={x['issue_id'] for x in app['issues']})
                        require(r['user_feedback'] in ['confirmed','declined','unknown','needs_help'])
                        require(type(r['unresolved']) is bool and (r['confirmation_turn_id'] is None or type(r['confirmation_turn_id']) is int and r['confirmation_turn_id']>0))
                        previous=db.execute('SELECT payload FROM records WHERE session=? ORDER BY revision DESC LIMIT 1',(sid,)).fetchone()
                        prev=json.loads(previous[0]) if previous else None
                        require(rev==(prev['result_revision']+1 if prev else 1),'STALE_REVISION',409)
                        outcome=r['candidate_outcome']
                        if not verified:
                            require(outcome in {'wrong_contact','unverified','declined','deferred','handoff_registered','interrupted','unresolved'} and not r['issues_explained'] and r['confirmation_turn_id'] is None and r['user_feedback']!='confirmed','MINIMAL_RESULT_REQUIRED',403)
                        if outcome=='notification_completed':
                            require(ground_application(app)['grounding']['status'] == 'supported', 'BASIS_UNAVAILABLE', 409)
                            require(verified and app['basis_status']=='valid' and set(r['issues_explained'])=={x['issue_id'] for x in app['issues']} and r['user_feedback']=='confirmed' and r['confirmation_turn_id'] is not None and not r['unresolved'],'COMPLETION_CONDITIONS')
                            # Scan persisted history, not just the last revision: an
                            # unresolved revision must never reset confirmation freshness.
                            history=db.execute('SELECT payload FROM records WHERE session=?',(sid,)).fetchall()
                            last_confirmation=max((json.loads(row[0]).get('confirmation_turn_id') or 0 for row in history),default=0)
                            require(r['confirmation_turn_id']>last_confirmation,'FRESH_CONFIRMATION_REQUIRED')
                        if outcome=='handoff_registered' or 'handoff_id' in r:
                            require(string(r.get('handoff_id')) and db.execute('SELECT 1 FROM handoffs WHERE id=? AND session=?',(r['handoff_id'],sid)).fetchone() is not None,'HANDOFF_REQUIRED')
                        now=datetime.now(timezone.utc).isoformat()
                        result={**r,'session_id':sid,'application_id':s['app'] if verified else None,'identity_status':s['identity'],'rule_version':app['rule_version'] if verified else None,'record_id':rid,'result_revision':rev,'outcome':outcome,'persistence_status':'confirmed','created_at':now,'updated_at':now,'simulated':True}
                        db.execute('INSERT INTO records VALUES(?,?,?,?)',(rid,sid,rev,json.dumps(result)))
                    db.execute('INSERT INTO operations VALUES(?,?,?,?)',(sid,p['idempotency_key'],canonical,json.dumps(result)))
            db.commit()
            if name=='get_operation_status' and result['result']:
                result['result']=visible_record(result['result'],verified)
            elif name=='save_call_result':
                result=visible_record(result,verified)
            return result
        except Exception:
            db.rollback(); raise
        finally: db.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        origin=self.headers.get('Origin')
        if origin in self.server.origins:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Headers','Content-Type, X-Mock-Fault')
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.end_headers(); self.wfile.write(body)
    def handle_request(self):
        try:
            require(self.headers.get('Origin') is None or self.headers.get('Origin') in self.server.origins,'ORIGIN_DENIED',403)
            if self.command=='OPTIONS': return self.reply(200,{'simulated':True})
            if self.command=='GET':
                if self.path=='/health': return self.reply(200,{'status':'ok','stage':'mock','simulated':True,'voice_ready':False,'official_api_connected':False,'capabilities':{'backend':True,'asr':False,'tts':False,'audio':False}})
                require(self.path=='/cases','NOT_FOUND',404)
                return self.reply(200,self.server.backend.call('cases',{}))
            require(self.command=='POST','METHOD_NOT_ALLOWED',405)
            require(self.path in ['/start_session','/verify_identity','/get_application','/save_call_result','/get_results','/create_handoff','/get_operation_status'],'NOT_FOUND',404)
            require(not self.headers.get('Transfer-Encoding'),'INVALID_BODY')
            require(self.headers.get('Content-Type','').split(';')[0]=='application/json','CONTENT_TYPE',415)
            size=self.headers.get('Content-Length','')
            require(size.isdigit(),'INVALID_LENGTH'); size=int(size)
            require(0<size<=32768,'BODY_TOO_LARGE',413)
            try: p=json.loads(self.rfile.read(size))
            except (ValueError,UnicodeError): raise Error('INVALID_JSON')
            fault=self.headers.get('X-Mock-Fault')
            result=self.server.backend.call(self.path[1:],p,fault)
            if fault=='response_lost' and self.path in ['/save_call_result','/create_handoff']:
                self.close_connection=True; self.connection.shutdown(2); self.connection.close(); return
            self.reply(200,result)
        except Error as e: self.reply(e.status,{'error':{'code':e.code},'simulated':True})
        except (TypeError,ValueError): self.reply(400,{'error':{'code':'INVALID_FIELDS'}})
        except sqlite3.Error: self.reply(503,{'error':{'code':'STORAGE_ERROR'}})
    do_GET=do_POST=do_OPTIONS=do_PUT=do_DELETE=handle_request
    def setup(self):
        super().setup(); self.connection.settimeout(10)

def make_server(path,port=8765,faults=False,origins=()):
    from urllib.parse import urlparse
    for origin in origins:
        u=urlparse(origin)
        require(u.scheme=='http' and u.hostname in {'127.0.0.1','localhost'} and u.port is not None and not u.path and not u.query and not u.fragment and not u.username,'INVALID_ORIGIN')
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.backend=Backend(path,faults); server.origins=set(origins)
    return server
if __name__=='__main__':
    require(os.environ.get('APP_HOST','127.0.0.1')=='127.0.0.1','LOCAL_ONLY')
    server=make_server(os.environ.get('MOCK_DB',str(ROOT/'runtime/mock.sqlite3')),int(os.environ.get('APP_PORT','8765')),os.environ.get('MOCK_FAULTS')=='1',filter(None,os.environ.get('MOCK_ORIGINS','').split(',')))
    print(f'Internal synthetic mock: http://127.0.0.1:{server.server_port}',flush=True)
    server.serve_forever()

