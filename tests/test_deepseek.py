import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import io, json, os, sys, unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from text_client import DeepSeekClient, text_client
from qwen_client import QwenError
class DeepSeekTests(unittest.TestCase):
 def test_wire_protocol(self):
  opener=Mock(); opener.open.return_value=io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
  with patch('qwen_client.urllib.request.build_opener',return_value=opener):
   self.assertEqual(DeepSeekClient('offline-placeholder').chat([{'role':'user','content':'hello'}])['text'],'ok')
  req=opener.open.call_args.args[0]; body=json.loads(req.data)
  self.assertEqual(req.full_url,'https://api.deepseek.com/chat/completions')
  self.assertEqual(body['thinking'],{'type':'disabled'})
  self.assertNotIn('enable_thinking',body)
  self.assertEqual(opener.open.call_count,1)
 def test_selection_and_missing_key(self):
  with patch.dict(os.environ,{'TEXT_PROVIDER':'deepseek'},clear=True):
   with self.assertRaises(QwenError): text_client()
  with self.assertRaises(QwenError): DeepSeekClient('offline',model='unverified-model')
