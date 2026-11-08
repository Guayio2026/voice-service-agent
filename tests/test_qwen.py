import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, Mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from qwen_client import QwenClient, QwenError, load_config, NoRedirect

class QwenTests(unittest.TestCase):
    def client(self):
        return QwenClient('test-secret','https://dashscope.aliyuncs.com/compatible-mode/v1','qwen-plus')
    def test_configuration(self):
        with self.assertRaises(QwenError): QwenClient('', '')
        for url in ['http://dashscope.aliyuncs.com/compatible-mode/v1',
                    'https://evil.com/compatible-mode/v1',
                    'https://dashscope.aliyuncs.com.evil.com/compatible-mode/v1',
                    'https://user@dashscope.aliyuncs.com/compatible-mode/v1']:
            with self.assertRaises(QwenError): QwenClient('test',url)
        QwenClient('test','https://llm-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    def test_literal_env(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'QWEN_MODEL':'existing'},clear=True):
            p=Path(d)/'.env'; p.write_text('QWEN_MODEL=other\nDASHSCOPE_API_KEY="test-only"\nUNRELATED=ignore\n')
            load_config(p)
            self.assertEqual(os.environ['QWEN_MODEL'],'existing')
            self.assertEqual(os.environ['DASHSCOPE_API_KEY'],'test-only')
            self.assertNotIn('UNRELATED',os.environ)
    def test_success_and_limits(self):
        opener=Mock(); response=io.BytesIO(json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],'usage':{'total_tokens':3}}).encode())
        opener.open.return_value=response
        with patch('qwen_client.urllib.request.build_opener',return_value=opener):
            self.assertEqual(self.client().chat([{'role':'user','content':'hello'}])['text'],'ok')
            body=json.loads(opener.open.call_args.args[0].data)
            self.assertFalse(body['stream']); self.assertFalse(body['enable_thinking'])
            with self.assertRaises(QwenError): self.client().chat([{'role':'user','content':'x'*16001}])
            self.assertEqual(opener.open.call_count,1)
    def test_errors_redacted_no_retry(self):
        for error in [urllib.error.HTTPError('https://example',401,'test-secret',{},None),urllib.error.URLError('test-secret')]:
            opener=Mock();opener.open.side_effect=error
            with patch('qwen_client.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(QwenError) as raised:self.client().chat([{'role':'user','content':'hello'}])
                self.assertNotIn('test-secret',str(raised.exception));self.assertEqual(opener.open.call_count,1)
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://evil.com'))
    def test_malformed_response(self):
        opener=Mock();opener.open.return_value=io.BytesIO(b'{"choices":[]}')
        with patch('qwen_client.urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(QwenError,'INVALID_PROVIDER_RESPONSE'):
                self.client().chat([{'role':'user','content':'hello'}])
