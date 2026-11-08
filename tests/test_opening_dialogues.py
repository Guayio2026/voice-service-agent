import legacy_fixture
import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from server import Backend
from business_agent import Agent

class OpeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_opening_interrupt_and_identity_matrix(self):
        yes=['是','是商家','我是商家','是我们店','嗯是的','我是对接人','我是店里的运营，负责这件事','不是老板但这事归我管']
        for case in ['QUAL-001','QUAL-002','QUAL-003','QUAL-004']:
            for phrase in yes:
                with self.subTest(case=case,phrase=phrase),tempfile.TemporaryDirectory() as d:
                    b=Backend(Path(d)/'db');sid=b.call('start_session',{'application_id':case})['session_id'];a=Agent(b,sid,case);model=Mock()
                    opening=await a.turn('开始通话',model,False)
                    self.assertFalse(opening['output_guard']['blocked'])
                    a.interrupt()
                    answer=await a.turn(phrase,model,False)
                    self.assertEqual(b.identity_status(sid),'verified')
                    self.assertIn('现在方便吗',answer['text'])
                    await a.turn('方便',model,True)
                    self.assertEqual(a.step,'confirmation')
                    model.chat.assert_not_called()

    async def test_purpose_busy_wrong_contact_and_correction(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(Path(d)/'db');sid=b.call('start_session',{'application_id':'QUAL-001'})['session_id'];a=Agent(b,sid,'QUAL-001');model=Mock()
            await a.turn('开始通话',model,False)
            reply=await a.turn('什么事',model,False)
            self.assertIn('模拟沟通',reply['text']);self.assertEqual(b.identity_status(sid),'unverified')
            await a.turn('你打错了',model,False)
            self.assertEqual(b.identity_status(sid),'wrong_contact')
            reply=await a.turn('不是，我刚才说错了，我是负责人',model,False)
            self.assertEqual(b.identity_status(sid),'verified')
            self.assertIn('更正',reply['text'])
            reply=await a.turn('再见',model,True)
            self.assertEqual(a.step,'closing');self.assertFalse(reply['output_guard']['blocked'])
            model.chat.assert_not_called()
