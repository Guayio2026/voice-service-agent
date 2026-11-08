import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from business_agent import Agent
from server import Backend

class Model:
    def __init__(self, **decision): self.decision = decision
    def chat(self, *args): return {'text': json.dumps(self.decision)}

class MultiQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = Backend(Path(self.temp.name) / 'test.db')
        self.sid = self.backend.call('start_session', {'application_id':'TEST-001'})['session_id']
        self.backend.call('verify_identity', {'session_id':self.sid, 'is_responsible':True})
        self.agent = Agent(self.backend, self.sid, 'TEST-001')
    async def asyncTearDown(self): self.temp.cleanup()
    async def ask(self, text, **decision):
        return await self.agent.turn(text, Model(**decision), True)
    async def test_three_questions_and_unknown_cannot_complete(self):
        r = await self.ask('缺什么材料？什么时候交？保证通过吗？', intent='question', topics=['materials','deadline','guarantee'])
        self.assertEqual([q['status'] for q in r['agent']['questions']], ['answered','needs_verification','answered'])
        self.assertIn('无法保证', r['text'])
        await self.ask('问题已解决', intent='continue')
        self.assertIsNone(self.agent.pending)
        self.assertIsNone(self.agent.result)
    async def test_new_question_invalidates_confirmation(self):
        await self.ask('方便', intent='continue')
        self.assertIsNotNone(self.agent.pending)
        await self.ask('好的，但费用怎么算', intent='question', topics=['fees'])
        await self.ask('确认登记', intent='continue')
        self.assertIsNone(self.agent.result)
    async def test_handoff_persists_all_questions(self):
        await self.ask('缺什么？什么时候交？帮我转人工', intent='question', topics=['materials','deadline'], actions=['handoff'])
        r = await self.ask('确认登记', intent='continue')
        self.assertTrue(r['persisted_business_result'])
        records = self.backend.call('get_results', {'session_id':self.sid})['results']
        self.assertEqual(records[0]['outcome'], 'handoff_registered')
        self.assertEqual(len(records[0]['questions']), 2)
        self.assertEqual(records[0]['questions'][1]['status'], 'needs_verification')
    async def test_correction_invalidates_previous_answers(self):
        await self.ask('什么材料', intent='question', topics=['materials'])
        await self.ask('材料不对，期限是什么', intent='question', topics=['deadline'], actions=['correct'])
        self.assertTrue(all(q['status']=='needs_verification' for q in self.agent.questions))
        await self.ask('问题已解决', intent='continue')
        self.assertIsNone(self.agent.pending)
    async def test_answered_questions_can_finish_with_fresh_confirmation(self):
        await self.ask('材料和依据是什么', intent='question', topics=['materials','basis'])
        await self.ask('问题已解决', intent='continue')
        r = await self.ask('确认登记', intent='continue')
        self.assertTrue(r['persisted_business_result'])
        self.assertEqual(r['agent']['result']['outcome'], 'notification_completed')
    async def test_refusal_stops_without_answering_or_writing(self):
        r = await self.ask('材料是什么，算了别说了', intent='question', topics=['materials'], actions=['decline'])
        self.assertEqual(self.agent.step, 'declined')
        self.assertFalse(self.agent.tools)
        self.assertIsNone(self.agent.result)

    async def test_combined_case_preserves_unknown_basis_and_handoff(self):
        from server import CASES
        self.assertTrue(all(f'TEST-{i:03}' in CASES for i in range(1, 10)))
        sid = self.backend.call('start_session', {'application_id':'TEST-009'})['session_id']
        self.backend.call('verify_identity', {'session_id':sid,'is_responsible':True})
        agent = Agent(self.backend,sid,'TEST-009')
        r = await agent.turn('有哪些材料问题',Model(intent='question',topics=['materials']),True)
        for word in ['缺页','模糊','过期','缺失','冲突']:
            self.assertIn(word,r['text'])
        self.assertEqual(agent.questions[0]['status'],'needs_verification')
        await agent.turn('问题已解决',Model(intent='continue'),True)
        self.assertIsNone(agent.pending)
        await agent.turn('转人工',Model(intent='handoff'),True)
        r = await agent.turn('确认登记',Model(intent='continue'),True)
        self.assertTrue(r['persisted_business_result'])
        self.assertEqual(r['agent']['result']['outcome'],'handoff_registered')

    async def test_spoken_identity_requires_question_then_explicit_answer(self):
        sid = self.backend.call('start_session', {'application_id':'TEST-001'})['session_id']
        a = Agent(self.backend,sid,'TEST-001')
        r = await a.turn('开始通话',Model(intent='continue'),False)
        self.assertTrue(r['text'].startswith('您好，我是京东客服'))
        self.assertIn('并非京东官方来电',r['text'])
        self.assertNotIn('缺页',r['text'])
        await a.turn('可能吧',Model(intent='continue'),False)
        self.assertEqual(self.backend.identity_status(sid),'unverified')
        r = await a.turn('是的，我是负责人。',Model(intent='continue'),False)
        self.assertEqual(self.backend.identity_status(sid),'verified')
        self.assertIn('现在方便吗',r['text'])
    async def test_spoken_wrong_contact_stops(self):
        sid = self.backend.call('start_session', {'application_id':'TEST-001'})['session_id']
        a = Agent(self.backend,sid,'TEST-001')
        await a.turn('开始通话',Model(intent='continue'),False)
        r = await a.turn('打错了',Model(intent='continue'),False)
        self.assertEqual(self.backend.identity_status(sid),'wrong_contact')
        self.assertNotIn('缺页',r['text'])
        await a.turn('是的',Model(intent='continue'),False)
        self.assertEqual(self.backend.identity_status(sid),'wrong_contact')
