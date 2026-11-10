import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from business_agent import Agent, confirmation_phrase, is_farewell
from server import Backend

class ReviewFixTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_does_not_accept_questions_or_extra_words(self):
        for text in ['确认登记？', '确认登记吗', '不确认登记', '他说确认登记', '确认登记但先别保存']:
            self.assertFalse(confirmation_phrase(text), text)
        for text in ['不要再见就挂断', '再见是什么意思', '先别挂了', '再见之前还有个问题']:
            self.assertFalse(is_farewell(text), text)

    async def test_punctuated_confirmation_writes_once(self):
        for text in ['确认登记', '确认登记。', '确认登记！', '确认登记 ', '确认 登记']:
            with self.subTest(text=text):
                sid = self.b.call('start_session', {'application_id': 'QUAL-001'})['session_id']
                a = Agent(self.b, sid, 'QUAL-001')
                await a.turn('开始通话', self.model, False)
                await a.turn('我是负责人', self.model, False)
                await a.turn('方便', self.model, True)
                await a.turn(text, self.model, True)
                await a.turn(text, self.model, True)
                # get_results now aggregates by application, so filter to this call
                # when checking that repeating the phrase writes only once.
                own = [r for r in self.b.call('get_results', {'session_id': sid})['results']
                       if r.get('session_id') == sid]
                self.assertEqual(len(own), 1)

    async def test_prefixed_farewell_closes_without_writing(self):
        for text in ['好，再见。', '谢谢，那先这样。', '行，先这样。', '我知道了，先这样吧。', '好的，服务不错，再见。', '对，挂了吧。']:
            with self.subTest(text=text):
                sid = self.b.call('start_session', {'application_id': 'QUAL-001'})['session_id']
                a = Agent(self.b, sid, 'QUAL-001')
                self.b.call('verify_identity', {'session_id': sid, 'is_responsible': True})
                a.step = 'declined'
                a.pending = {'outcome': 'declined'}
                await a.turn(text, self.model, True)
                self.assertEqual(a.step, 'closing')
                self.assertIsNone(a.pending)
                self.assertEqual(self.b.call('get_results', {'session_id': sid})['results'], [])

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = Backend(Path(self.tmp.name) / 'db')
        self.sid = self.b.call('start_session', {'application_id': 'QUAL-001'})['session_id']
        self.a = Agent(self.b, self.sid, 'QUAL-001')
        self.model = Mock()
        await self.a.turn('开始通话', self.model, False)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_model_yes_cannot_override_ambiguous_original(self):
        self.model.chat.return_value = {'text': '{"kind":"yes"}'}
        for phrase in ['不确定是不是负责人', '他说他是该商家的负责人', '是的但我不是负责人']:
            with self.subTest(phrase=phrase):
                await self.a.turn(phrase, self.model, False)
                self.assertEqual(self.b.identity_status(self.sid), 'unverified')
                self.assertEqual(self.b.call('get_results', {'session_id': self.sid})['results'], [])

    async def test_authorized_role_and_negated_responsibility_pair(self):
        self.model.chat.return_value = {'text': '{"kind":"yes"}'}
        for phrase, expected in [
            ('我不是老板，但这件事归我负责，我是店里的运营。', 'verified'),
            ('我不是老板，也不负责这个申请。', 'unverified'),
            ('他说他不是老板，但这件事归他负责。', 'unverified'),
            ('如果我负责这个申请，是不是就可以了？', 'unverified'),
        ]:
            with self.subTest(phrase=phrase):
                sid = self.b.call('start_session', {'application_id': 'QUAL-001'})['session_id']
                a = Agent(self.b, sid, 'QUAL-001')
                await a.turn('开始通话', self.model, False)
                await a.turn(phrase, self.model, False)
                self.assertEqual(self.b.identity_status(sid), expected)

    async def test_model_decline_closes_and_clears_pending(self):
        self.model.chat.return_value = {'text': '{"kind":"decline"}'}
        self.a.pending = {'outcome': 'notification_completed'}
        await self.a.turn('我不想继续讨论这件事', self.model, False)
        self.assertEqual(self.a.step, 'closing')
        self.assertEqual(self.b.workflow(self.sid)['stage'], 'closing')
        self.assertIsNone(self.a.pending)

    async def test_late_input_and_interrupt_do_not_reopen(self):
        await self.a.turn('再见', self.model, False)
        self.a.interrupt()
        for phrase in ['是的', '方便', '确认登记']:
            reply = await self.a.turn(phrase, self.model, False)
            self.assertFalse(reply['output_guard']['blocked'])
            self.assertIn('先不打扰', reply['text'])
        self.assertEqual(self.a.step, 'closing')
        self.assertEqual(self.b.identity_status(self.sid), 'unverified')
        self.model.chat.assert_not_called()
