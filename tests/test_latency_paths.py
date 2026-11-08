import legacy_fixture
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from business_agent import Agent
from server import Backend
from fast_intent import fast_decision
from audio_cache import AudioCache
from next_turn_audio import plans, NextTurnAudio
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from model_transport import ModelTransport
from qwen_client import QwenError
import httpx

class CountingModel:
    def __init__(self): self.calls = 0
    def chat(self, *args):
        self.calls += 1
        return {'text': json.dumps({'intent':'question','topics':['reason']})}

class LatencyPaths(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = Backend(Path(self.temp.name)/'test.db')
        self.sid = self.backend.call('start_session', {'application_id':'TEST-001'})['session_id']
        self.backend.call('verify_identity', {'session_id':self.sid,'is_responsible':True})
        self.agent = Agent(self.backend, self.sid, 'TEST-001')

    async def asyncTearDown(self):
        self.agent.interrupt()
        self.temp.cleanup()

    async def test_fast_question_keeps_grounding_and_no_write(self):
        model = CountingModel()
        reply = await self.agent.turn('我需要补充什么材料？', model, True)
        self.assertEqual(model.calls, 0)
        self.assertTrue(reply['grounding']['citations'])
        self.assertIsNone(self.agent.result)
        self.assertFalse(any(t['tool'] in {'save_call_result','create_handoff'} for t in self.agent.tools))

    async def test_material_composition_preserves_evidence_and_scope(self):
        model=CountingModel()
        reply=await self.agent.turn('请问第一项材料为什么要补充以及如何修改',model,True)
        self.assertEqual(model.calls,0)
        self.assertEqual(self.agent.last_decision['topics'],['reason','action'])
        self.assertEqual(self.agent.last_decision['issue_numbers'],[1])
        self.assertTrue(reply['grounding']['citations'])
        self.assertIsNone(self.agent.result)
        self.assertFalse(any(t['tool'] in {'save_call_result','create_handoff'} for t in self.agent.tools))

    async def test_material_fast_route_still_requires_identity(self):
        self.backend.revoke_identity(self.sid)
        reply=await self.agent.turn('材料为什么需要补正以及如何处理',CountingModel(),False)
        self.assertNotIn('需要补正的原因是',reply['text'])
        self.assertIsNone(self.agent.result)

    async def test_combined_prefetch_matches_real_reply_and_issue_scope(self):
        before=self.agent.report()
        prepared=plans(self.agent,['reason','action'],[1],True)
        self.assertEqual(len(prepared),1)
        self.assertEqual(self.agent.report(),before)
        reply=await self.agent.turn('第一项材料为什么需要补正以及如何处理',CountingModel(),True)
        self.assertEqual(prepared[0][0],reply['text'])
        self.assertEqual(prepared[0][1],reply['grounding'])

    async def test_speculation_exact_reuse_and_changed_input(self):
        model = CountingModel()
        text = '想了解这次退回的具体原因'
        self.agent.speculate(text, model)
        await self.agent.speculation[1]
        await self.agent.turn(text, model, True)
        self.assertEqual(model.calls, 1)
        self.assertTrue(any(e.get('status') == 'reused' for e in self.agent.events))
        self.agent.speculate(text, model)
        await self.agent.speculation[1]
        await self.agent.turn('不是这个意思，请说明其他原因', model, True)
        self.assertEqual(model.calls, 3)

    async def test_cancelled_prediction_does_not_cancel_real_turn(self):
        model=CountingModel();text='想了解这次退回的具体原因'
        self.agent.speculate(text,model)
        task=self.agent.speculation[1];task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        reply=await self.agent.turn(text,model,True)
        self.assertTrue(reply['text']);self.assertEqual(model.calls,1)
        self.assertFalse(any(e.get('status')=='reused' for e in self.agent.events))

    async def test_asr_punctuation_only_reuses_readonly_question(self):
        model = CountingModel()
        text = '想了解这次退回的具体原因'
        self.agent.speculate(text, model)
        await self.agent.speculation[1]
        await self.agent.turn(text+'。', model, True)
        self.assertEqual(model.calls, 1)
        self.assertTrue(any(e.get('match') == 'readonly_asr_punctuation' for e in self.agent.events))

    def test_asr_normalization_never_merges_negation_or_authority(self):
        from business_agent import same_readonly_asr_input
        def key(text): return (0,json.dumps({'prompt':'p','history':[{'role':'user','content':text}]}))
        self.assertTrue(same_readonly_asr_input(key('我先不问材料了，我想知道提交入口能否确定'),key('我先不问材料了，我想知道提交入口能否确定。')))
        self.assertFalse(same_readonly_asr_input(key('不，想知道原因'),key('不想知道原因')))
        for before,after in [('想了解原因','不想了解原因'),('确认登记','确认登记。'),('请提交材料','请提交材料。')]:
            self.assertFalse(same_readonly_asr_input(key(before),key(after)))

    async def test_speculation_has_no_tools_and_interrupt_invalidates(self):
        model = CountingModel()
        self.agent.speculate('想了解这次退回的具体原因', model)
        await self.agent.speculation[1]
        self.assertEqual(self.agent.tools, [])
        self.agent.interrupt()
        self.assertIsNone(self.agent.speculation)

    def test_whole_utterance_only(self):
        for text in ['不要转人工', '需要补什么材料，但是不要登记', '我不是负责人', '确认登记']:
            self.assertIsNone(fast_decision(text))

    async def test_next_turn_preview_matches_without_state_changes(self):
        before = self.agent.report()
        prepared = plans(self.agent)
        self.assertEqual(self.agent.report(), before)
        self.assertEqual(len(prepared), 2)
        answer = await self.agent.turn('我需要补充什么材料', CountingModel(), True)
        self.assertEqual(prepared[0][0], answer['text'])
        self.assertEqual(prepared[0][1], answer['grounding'])
        self.assertIsNone(self.agent.result)

    async def test_background_prepares_audio_without_business_writes(self):
        async def chunks(_):
            yield b'\x00\x01'*100
        response = MagicMock(status=200)
        response.content.iter_chunked = chunks
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        service = SimpleNamespace(dialogues={self.sid:{'generation':0,'agent':self.agent}},
            audio_cache=AudioCache(),tts_lock=asyncio.Lock(),http=SimpleNamespace(post=MagicMock(return_value=context)))
        manager = NextTurnAudio(service)
        before = self.agent.report()
        manager.schedule(self.sid,'test-voice')
        await manager.tasks[self.sid]
        self.assertEqual(manager.metrics['prepared'],2)
        self.assertEqual(len(service.audio_cache.entries),2)
        self.assertEqual(self.agent.report(),before)
        await manager.foreground()
        self.assertFalse(service.tts_lock.locked())

    async def test_realtime_prefetch_cancels_before_foreground_lock_release(self):
        started=asyncio.Event();cancelled=asyncio.Event()
        async def stream(*args):
            try:
                started.set()
                await asyncio.Event().wait()
                yield b'00'
            finally:cancelled.set()
        http=SimpleNamespace(post=MagicMock())
        service=SimpleNamespace(dialogues={self.sid:{'generation':0,'agent':self.agent}},
            audio_cache=AudioCache(),tts_lock=asyncio.Lock(),http=http,
            breeze_rt=SimpleNamespace(stream=stream))
        manager=NextTurnAudio(service)
        with patch.dict(os.environ,{'TTS_PROVIDER':'breeze_realtime'}):
            manager.schedule(self.sid,'voice',['reason'],combine=True)
            await asyncio.wait_for(started.wait(),2)
            self.assertTrue(service.tts_lock.locked())
            await manager.foreground()
        self.assertTrue(cancelled.is_set());self.assertFalse(service.tts_lock.locked())
        http.post.assert_not_called();self.assertEqual(len(service.audio_cache.entries),0)

    async def test_matching_inflight_audio_is_adopted_not_restarted(self):
        first=asyncio.Event();finish=asyncio.Event();calls=[]
        async def stream(*args):
            calls.append(args)
            yield b'12';first.set()
            await finish.wait()
            yield b'34'
        service=SimpleNamespace(dialogues={self.sid:{'generation':0,'agent':self.agent}},
            audio_cache=AudioCache(),tts_lock=asyncio.Lock(),http=None,
            breeze_rt=SimpleNamespace(stream=stream))
        manager=NextTurnAudio(service)
        with patch.dict(os.environ,{'TTS_PROVIDER':'breeze_realtime'}):
            manager.schedule(self.sid,'voice',['reason','action'],combine=True)
            await asyncio.wait_for(first.wait(),2)
            key=next(iter(manager.inflight))
            self.assertIsNone(manager.take(('wrong-session',*key[1:])))
            prepared=manager.take(key)
            await manager.foreground()
            self.assertFalse(prepared['task'].cancelling())
            consumer=manager.consume(prepared)
            self.assertEqual(await anext(consumer),b'12')
            finish.set()
            self.assertEqual(b''.join([chunk async for chunk in consumer]),b'34')
        self.assertEqual(len(calls),1)
        self.assertFalse(service.tts_lock.locked())
        self.assertEqual(service.audio_cache.get(key),b'1234')

    def test_current_question_deduplicates_and_rejects_mixed_intent(self):
        manager=NextTurnAudio(SimpleNamespace(dialogues={self.sid:{'generation':0,'agent':self.agent}}))
        manager.schedule=MagicMock()
        manager.current(self.sid,'voice','补充材料在哪里提交？')
        manager.current(self.sid,'voice','补充材料在哪里提交？')
        manager.current(self.sid,'voice','补充材料在哪里提交。')
        manager.schedule.assert_called_once_with(self.sid,'voice',['channel'],issue_numbers=[],combine=True)
        manager.current(self.sid,'voice','补充材料在哪里提交，但是我不想办理')
        self.assertEqual(manager.schedule.call_count,1)
        self.assertIsNone(self.agent.result)

    def test_incomplete_topic_does_not_start_tts(self):
        manager=NextTurnAudio(SimpleNamespace(dialogues={self.sid:{'generation':0,'agent':self.agent}}))
        manager.schedule=MagicMock()
        manager.current(self.sid,'voice','我想知道截止时间')
        manager.schedule.assert_not_called()
        manager.current(self.sid,'voice','我想知道截止时间和提交入口能否确定')
        manager.schedule.assert_called_once_with(self.sid,'voice',['deadline','channel'],issue_numbers=[],combine=True)

    def test_pool_never_follows_redirect_or_retries(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(302, headers={'location':'https://other.invalid'})
        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
            transport = ModelTransport(client)
            with self.assertRaises(QwenError):
                transport('https://api.deepseek.com/chat/completions', b'{}', 'offline')
        self.assertEqual(len(calls), 1)

    def test_audio_cache_limit_expiry_and_isolation(self):
        cache = AudioCache(max_bytes=4, ttl=10)
        with patch('audio_cache.time.monotonic', return_value=0):
            cache.put(('s1','v1'), b'1234')
        self.assertIsNone(cache.get(('s2','v1')))
        with patch('audio_cache.time.monotonic', return_value=9):
            self.assertEqual(cache.get(('s1','v1')), b'1234')
            cache.put(('s1','v2'), b'56')
            self.assertIsNone(cache.get(('s1','v1')))
        with patch('audio_cache.time.monotonic', return_value=20):
            self.assertIsNone(cache.get(('s1','v2')))
        self.assertEqual(cache.size, 0)
