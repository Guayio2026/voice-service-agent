import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys, tempfile, unittest, threading, json, urllib.request, urllib.error, http.client
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from server import Backend, Error, make_server
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'test.db'; self.b=Backend(self.path,True)
  self.sid=self.b.call('start_session',{'application_id':'TEST-001'})['session_id']
 def tearDown(self): self.tmp.cleanup()
 def call(self,n,**p): return self.b.call(n,{'session_id':self.sid,**p})
 def verified(self): self.call('verify_identity',is_responsible=True)
 def payload(self): return {'session_id':self.sid,'idempotency_key':'one','result_revision':1,'result':{'candidate_outcome':'notification_completed','issues_explained':['I01'],'user_feedback':'confirmed','confirmation_turn_id':1,'unresolved':False}}
 def test_scope(self):
  with self.assertRaises(Error): self.call('get_application',application_id='TEST-001')
  self.verified()
  with self.assertRaises(Error): self.call('get_application',application_id='TEST-002')
  self.assertTrue(self.call('get_application',application_id='TEST-001')['synthetic'])
 def test_persistence_idempotency_revision(self):
  self.verified(); p=self.payload(); r=self.b.call('save_call_result',p)
  self.assertEqual(r,self.b.call('save_call_result',p))
  self.b=Backend(self.path); self.assertEqual(len(self.call('get_results')['results']),1)
  p['result']['unresolved']=True
  with self.assertRaises(Error): self.b.call('save_call_result',p)
  p['idempotency_key']='two'
  with self.assertRaises(Error): self.b.call('save_call_result',p)
  p['result_revision']=2; p['result']['unresolved']=False
  with self.assertRaises(Error): self.b.call('save_call_result',p)
  p['result']['confirmation_turn_id']=2; self.b.call('save_call_result',p)
  self.assertEqual(len(self.call('get_results')['results']),2)
 def test_cached_result_respects_current_identity(self):
  self.verified(); p=self.payload(); original=self.b.call('save_call_result',p)
  self.call('verify_identity',is_responsible=False)
  self.b=Backend(self.path)
  allowed={'record_id','result_revision','outcome','persistence_status'}
  responses=[self.b.call('save_call_result',p),
   self.call('get_operation_status',idempotency_key='one')['result'],
   self.call('get_results')['results'][0]]
  for response in responses:
   self.assertEqual(set(response),allowed)
   self.assertEqual(response['record_id'],original['record_id'])
  self.verified()
  self.assertEqual(self.b.call('save_call_result',p),original)
  self.assertEqual(len(self.call('get_results')['results']),1)
 def test_confirmation_survives_intermediate_revision_and_restart(self):
  self.verified(); p=self.payload(); self.b.call('save_call_result',p)
  p['idempotency_key']='two'; p['result_revision']=2
  p['result'].update(candidate_outcome='unresolved',user_feedback='unknown',confirmation_turn_id=None,unresolved=True)
  self.b.call('save_call_result',p)
  self.call('verify_identity',is_responsible=False)
  self.verified(); self.b=Backend(self.path)
  p['idempotency_key']='three'; p['result_revision']=3
  p['result'].update(candidate_outcome='notification_completed',user_feedback='confirmed',confirmation_turn_id=1,unresolved=False)
  with self.assertRaises(Error) as raised:self.b.call('save_call_result',p)
  self.assertEqual(raised.exception.code,'FRESH_CONFIRMATION_REQUIRED')
  self.assertEqual(len(self.call('get_results')['results']),2)
  self.assertEqual(self.call('get_operation_status',idempotency_key='three')['status'],'unknown')
  p['result']['confirmation_turn_id']=2
  result=self.b.call('save_call_result',p)
  self.assertEqual(result,self.b.call('save_call_result',p))
  self.assertEqual(len(self.call('get_results')['results']),3)
 def test_completion(self):
  p=self.payload()
  with self.assertRaises(Error): self.b.call('save_call_result',p)
  self.verified()
  for key,value in [('issues_explained',[]),('user_feedback','unknown'),('unresolved',True),('confirmation_turn_id',None)]:
   q=json.loads(json.dumps(p));q['result'][key]=value
   with self.assertRaises(Error): self.b.call('save_call_result',q)
 def test_handoff(self):
  p={'idempotency_key':'h','consent':False,'reason':'user_request'}
  with self.assertRaises(Error): self.call('create_handoff',**p)
  p['consent']=True; r=self.call('create_handoff',**p)
  self.assertEqual(r,self.call('create_handoff',**p)); self.assertIn('handoff_id',r)
 def test_concurrent(self):
  self.verified(); p=self.payload()
  with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda _:self.b.call('save_call_result',p),range(16)))
  self.assertEqual(len({r['record_id'] for r in results}),1)
  self.assertEqual(len(self.call('get_results')['results']),1)
 def test_faults(self):
  self.verified(); p=self.payload()
  with self.assertRaises(Error): self.b.call('save_call_result',p,'write_fail')
  self.assertEqual(self.call('get_operation_status',idempotency_key='one')['status'],'unknown')
  with self.assertRaises(Error): self.b.call('get_application',{'session_id':self.sid,'application_id':'TEST-001'},'query_fail')
 def test_http_smoke(self):
  srv=make_server(self.path,0,True); t=threading.Thread(target=srv.serve_forever);t.start()
  base=f'http://127.0.0.1:{srv.server_port}'
  def request(path,data=None,headers=None):
   req=urllib.request.Request(base+path,data=data,headers=headers or {'Content-Type':'application/json'})
   try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as res:return res.status,json.load(res)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   self.assertFalse(request('/health')[1]['voice_ready'])
   self.assertEqual(request('/missing')[0],404)
   self.assertEqual(request('/start_session',b'{')[1]['error']['code'],'INVALID_JSON')
   self.assertEqual(request('/start_session',b'{"bad":1}')[0],400)
   self.verified();p=self.payload()
   with self.assertRaises((http.client.RemoteDisconnected,urllib.error.URLError)):
    request('/save_call_result',json.dumps(p).encode(),{'Content-Type':'application/json','X-Mock-Fault':'response_lost'})
   status=request('/get_operation_status',json.dumps({'session_id':self.sid,'idempotency_key':'one'}).encode())[1]
   self.assertEqual(status['status'],'confirmed')
   self.assertEqual(request('/save_call_result',json.dumps(p).encode())[1]['record_id'],status['result']['record_id'])
  finally:srv.shutdown();srv.server_close();t.join()
if __name__=='__main__':unittest.main()

