import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from server import Backend, Error
class ConfirmationTests(unittest.TestCase):
 def test_exact_content_and_idempotency(self):
  with tempfile.TemporaryDirectory() as d:
   b=Backend(Path(d)/'db'); sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
   b.control_session(sid)
   b=Backend(Path(d)/'db')
   p={'idempotency_key':'x','consent':True,'reason':'user_request'}
   with self.assertRaises(Error): b.call('create_handoff',{'session_id':sid,**p})
   b.authorize_write(sid,'create_handoff',p)
   with self.assertRaises(Error): b.call('create_handoff',{'session_id':sid,**p,'reason':'basis_unavailable'})
   r=b.call('create_handoff',{'session_id':sid,**p})
   self.assertEqual(r,b.call('create_handoff',{'session_id':sid,**p}))
   self.assertEqual(r,Backend(Path(d)/'db').call('get_operation_status',{'session_id':sid,'idempotency_key':'x'})['result'])
