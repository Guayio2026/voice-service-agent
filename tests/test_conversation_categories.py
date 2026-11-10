import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
"""Business QA, small talk, and off-topic handling; isolated DB, no paid calls."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from business_agent import Agent
from server import Backend


class Plan:
    def __init__(self, intent='question', topics=None, numbers=None):
        self.decision = {'intent': intent, 'topics': topics or [], 'issue_numbers': numbers or []}
        self.calls = []

    def chat(self, messages, max_tokens):
        self.calls.append(messages)
        return {'text': json.dumps(self.decision), 'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = Backend(Path(self.temp.name) / 'test.db')
        self.sid = self.backend.call('start_session', {'application_id': 'TEST-002'})['session_id']
        self.backend.call('verify_identity', {'session_id': self.sid, 'is_responsible': True})
        self.agent = Agent(self.backend, self.sid, 'TEST-002')

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_business_questions_work_immediately_and_follow_up(self):
        first = await self.agent.turn('需要补什么材料', Plan(topics=['materials']), True)
        self.assertIn('缺页', first['text'])
        self.assertIn('模糊', first['text'])
        model = Plan(topics=['reason'], numbers=[2])
        second = await self.agent.turn('第二项为什么要改', model, True)
        self.assertIn('模糊', second['text'])
        self.assertNotIn('缺页', second['text'])
        self.assertNotIn('方便请回复', first['text'])
        self.assertIn(first['text'], [m['content'] for m in model.calls[0]])
        third = await self.agent.turn('那该怎么改', Plan(topics=['action'], numbers=[2]), True)
        self.assertIn('清晰副本', third['text'])
        self.assertIsNone(self.agent.pending)
        self.assertEqual(self.backend.call('get_results', {'session_id': self.sid})['results'], [])
        basis = [e for e in self.agent.events if e['type'] == 'answer_basis'][-1]
        self.assertEqual(basis['evidence_ids'], ['TEST-R02'])
        self.assertEqual(third['usage']['prompt_tokens'], 12)

    async def test_unknown_policy_and_guarantee_are_separate_answers(self):
        answer = await self.agent.turn('几点截止，去哪里交，要收费吗',
            Plan(topics=['deadline', 'channel', 'fees']), True)
        for phrase in ['未提供补正截止时间', '未提供提交入口', '未提供收费信息']:
            self.assertIn(phrase, answer['text'])
        self.assertNotIn('缺页', answer['text'])
        answer = await self.agent.turn('保证审核通过吗', Plan(topics=['guarantee']), True)
        self.assertIn('无法保证', answer['text'])
        self.assertNotIn('截止', answer['text'])
        answer = await self.agent.turn('我已经补交了，受理了吗', Plan(topics=['status']), True)
        self.assertIn('无法确认', answer['text'])
        self.assertIsNone(self.agent.result)

    async def test_small_talk_is_local_and_preserves_pending_but_never_saves(self):
        await self.agent.turn('方便沟通', Plan(intent='continue'), True)
        pending = self.agent.pending.copy()
        model = Plan()
        for text, expected in [('你好！', '您好'), ('谢谢你。', '不客气'), ('你是真人吗？', '不是真人')]:
            result = await self.agent.turn(text, model, True)
            self.assertIn(expected, result['text'])
            self.assertEqual(self.agent.pending, pending)
            self.assertFalse(result['persisted_business_result'])
            self.assertEqual(result['usage'], {})
        self.assertEqual(model.calls, [])
        answer = await self.agent.turn('确认登记', model, True)
        self.assertTrue(answer['persisted_business_result'])

    async def test_unverified_social_never_exposes_business(self):
        self.backend.call('verify_identity', {'session_id': self.sid, 'is_responsible': False})
        answer = await self.agent.turn('你好', Plan(), True)
        self.assertIn('身份不符', answer['text'])
        sid = self.backend.call('start_session', {'application_id': 'TEST-002'})['session_id']
        agent = Agent(self.backend, sid, 'TEST-002')
        model = Plan()
        for text in ['你好', '谢谢', '你是真人吗']:
            answer = await agent.turn(text, model, True)
            self.assertIn('负责人', answer['text'])
            self.assertIn('模拟', answer['text'])
            self.assertNotIn('缺页', answer['text'])
        self.assertEqual(agent.tools, [])
        self.assertEqual(model.calls, [])

    async def test_mixed_thanks_and_request_is_not_swallowed(self):
        model = Plan(intent='handoff')
        answer = await self.agent.turn('谢谢，帮我转人工', model, True)
        self.assertEqual(len(model.calls), 1)
        self.assertIn('自动存', answer['text'])
        self.assertIsNone(self.agent.result)

    async def test_off_topic_keeps_business_identity_and_no_conflict(self):
        answer = await self.agent.turn('今天天气怎么样', Plan(intent='off_topic'), True)
        self.assertIn('资质补正', answer['text'])
        self.assertNotIn('差异', answer['text'])
        self.assertFalse(self.agent.unresolved)
        self.assertEqual(self.backend.identity_status(self.sid), 'verified')
        self.assertEqual(self.agent.tools, [])

    async def test_invalid_plan_or_nonexistent_material_cannot_invent_answer(self):
        model = Plan(topics=['deadline'])
        model.decision['answer'] = '明天18点截止，已登记'
        result = await self.agent.turn('这项材料的办理时限能解释一下吗', model, True)
        self.assertIn('未能可靠理解', result['text'])
        self.assertNotIn('18', result['text'])
        result = await self.agent.turn('第九项怎么改', Plan(topics=['action'], numbers=[9]), True)
        self.assertIn('没有您提到', result['text'])
        self.assertIsNone(self.agent.pending)

    async def test_refusal_is_not_reopened_by_small_talk(self):
        await self.agent.turn('不要联系我了', Plan(intent='decline'), True)
        result = await self.agent.turn('谢谢', Plan(), True)
        self.assertIn('停止', result['text'])
        self.assertNotIn('继续问', result['text'])
