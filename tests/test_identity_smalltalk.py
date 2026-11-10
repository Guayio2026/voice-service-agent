import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from business_agent import Agent
from server import Backend
class IdentitySmalltalkTests(unittest.IsolatedAsyncioTestCase):
    async def test_introduction_before_identity_then_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(Path(d)/'db')
            sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
            a=Agent(b,sid,'TEST-001'); model=Mock()
            await a.turn('开始通话',model,False)
            for question in ['你叫什么名字？','请问你是谁','你好','谢谢']:
                result=await a.turn(question,model,False)
                self.assertFalse(result['output_guard']['blocked'])
                self.assertNotIn('京东客服',result['text'])
                self.assertNotIn('虚构材料缺页',result['text'])
                self.assertEqual(b.identity_status(sid),'unverified')
            result=await a.turn('是的',model,False)
            self.assertEqual(b.identity_status(sid),'verified')
            model.chat.assert_not_called()
    async def test_wrong_contact_still_stops(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(Path(d)/'db'); sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
            a=Agent(b,sid,'TEST-001')
            b.call('verify_identity',{'session_id':sid,'is_responsible':False})
            reply=await a.turn('你叫什么名字',Mock(),False)
            self.assertIn('身份不符',reply['text'])

    async def test_spoken_identity_variants_and_ambiguous_answers(self):
        for text, expected in [('是该商家的负责人。','verified'),('我是该商家的负责人','verified'),('不是该商家的负责人','wrong_contact'),('我不是负责人','wrong_contact'),('不确定是不是负责人','unverified'),('他说他是该商家的负责人','unverified'),('是的但我不是负责人','unverified')]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as d:
                b=Backend(Path(d)/'db'); sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
                a=Agent(b,sid,'TEST-001'); model=Mock()
                await a.turn('开始通话',model,False)
                result=await a.turn(text,model,False)
                self.assertEqual(b.identity_status(sid),expected)
                if expected=='verified': self.assertIn('现在方便吗',result['text'])
                self.assertFalse(result['output_guard']['blocked'])
                if expected != 'unverified': model.chat.assert_not_called()

    async def test_natural_identity_understanding(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(Path(d)/'db'); sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
            a=Agent(b,sid,'TEST-001'); model=Mock()
            await a.turn('开始通话',model,False)
            model.chat.return_value={'text':'{"kind":"purpose"}'}
            reply=await a.turn('找我啥事啊',model,False)
            self.assertIn('资质补正',reply['text'])
            self.assertEqual(b.identity_status(sid),'unverified')
            model.chat.return_value={'text':'{"kind":"yes"}'}
            reply=await a.turn('这家店一直是我负责的',model,False)
            self.assertEqual(b.identity_status(sid),'verified')
            self.assertIn('现在方便吗',reply['text'])

    async def test_merchant_affirmation_never_calls_negative_model(self):
        for text in ['是商家。','我是商家','是我们店']:
            with tempfile.TemporaryDirectory() as d:
                b=Backend(Path(d)/'db');sid=b.call('start_session',{'application_id':'QUAL-001'})['session_id']
                a=Agent(b,sid,'QUAL-001');model=Mock();model.chat.return_value={'text':'{"kind":"no"}'}
                await a.turn('开始通话',model,False)
                reply=await a.turn(text,model,False)
                self.assertEqual(b.identity_status(sid),'verified')
                self.assertNotIn('联系对象不符',reply['text']);model.chat.assert_not_called()

    async def test_model_negative_requires_clarification(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(Path(d)/'db');sid=b.call('start_session',{'application_id':'QUAL-001'})['session_id']
            a=Agent(b,sid,'QUAL-001');model=Mock();model.chat.return_value={'text':'{"kind":"no"}'}
            await a.turn('开始通话',model,False)
            reply=await a.turn('不是老板但是我负责对接',model,False)
            self.assertEqual(b.identity_status(sid),'unverified')
            self.assertNotIn('再见',reply['text'])
