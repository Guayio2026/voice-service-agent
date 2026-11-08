import asyncio,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from streaming_reply import oral_events
from read_ahead import ReadAhead

class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_precedes_generator_completion(self):
        released=asyncio.Event();sent=[]
        async def generate(*a):
            yield '第一句。'
            await released.wait()
            yield '第二句。'
        async def tts(http,voice,parts):
            async for part in parts:
                sent.append(part);yield b'\0\0'
        service=SimpleNamespace(http=None,breeze_rt=SimpleNamespace(stream=tts))
        state={'agent':SimpleNamespace(check_output=lambda t:{'blocked':False})}
        turn={'input':'测试','result':{'text':'第一句。第二句。'}}
        events=oral_events(service,state,turn,'v',lambda:None)
        with patch('streaming_reply.text_client',return_value=None),patch('streaming_reply.deltas',generate),patch('streaming_reply.supported',AsyncMock(return_value=True)):
            while True:
                event=json.loads(await asyncio.wait_for(anext(events),2))
                if event['type']=='audio':break
            self.assertFalse(released.is_set());self.assertEqual(len(sent),1)
            released.set()
            rest=[json.loads(e) async for e in events]
            self.assertEqual(rest[-1]['type'],'done');self.assertEqual(len(sent),2)
    async def test_unsupported_sentence_never_reaches_tts(self):
        async def generate(*a):yield '保证通过。'
        sent=[]
        async def tts(http,voice,parts):
            async for part in parts:sent.append(part);yield b'\0\0'
        service=SimpleNamespace(http=None,breeze_rt=SimpleNamespace(stream=tts))
        state={'agent':SimpleNamespace(check_output=lambda t:{'blocked':False})}
        turn={'input':'测试','result':{'text':'不能保证通过。还需等待审核结果。'}}
        with patch('streaming_reply.text_client',return_value=None),patch('streaming_reply.deltas',generate),patch('streaming_reply.supported',AsyncMock(return_value=False)):
            events=[json.loads(e) async for e in oral_events(service,state,turn,'v',lambda:None)]
        self.assertNotIn('保证通过。',sent);self.assertEqual(events[-1]['type'],'error')
    async def test_revision_and_interrupt_cancel_preparation(self):
        agent=SimpleNamespace(speculate=Mock(),speculation=None)
        state={'generation':0,'lock':asyncio.Lock(),'agent':agent}
        service=SimpleNamespace(session=lambda sid:state,model_transport=None)
        ahead=ReadAhead(service,delay=.01)
        with patch('read_ahead.text_client',return_value=SimpleNamespace()):
            ahead.update('s','我需要材料');ahead.update('s','我不需要材料')
            await asyncio.sleep(.03)
            self.assertEqual(agent.speculate.call_count,1)
            self.assertEqual(agent.speculate.call_args.args[0],'我不需要材料')
            ahead.update('s','请取消办理');ahead.cancel('s');await asyncio.sleep(.03)
            self.assertEqual(agent.speculate.call_count,1)
        await ahead.close()
    async def test_complete_revision_cancels_obsolete_request(self):
        old=asyncio.create_task(asyncio.Event().wait())
        agent=SimpleNamespace(speculate=Mock(),speculation=('old',old))
        state={'generation':0,'lock':asyncio.Lock(),'agent':agent}
        ahead=ReadAhead(SimpleNamespace(session=lambda sid:state,model_transport=None))
        with patch('read_ahead.text_client',return_value=SimpleNamespace()):
            ahead.update('s','第二项为什么需要补正我应该怎样处理')
            await asyncio.wait_for(ahead.tasks['s'],2)
            self.assertTrue(old.cancelled())
            agent.speculate.assert_called_once()
        await ahead.close()

    async def test_final_punctuation_keeps_inflight_work(self):
        old=asyncio.create_task(asyncio.Event().wait())
        def inputs(text):return {'prompt':'p','history':[{'role':'user','content':text}]}
        key=(0,json.dumps(inputs('我想了解原因以及怎样处理'),sort_keys=True,ensure_ascii=False))
        agent=SimpleNamespace(speculate=Mock(),speculation=(key,old),classification_input=inputs,generation=0)
        state={'generation':0,'lock':asyncio.Lock(),'agent':agent}
        ahead=ReadAhead(SimpleNamespace(session=lambda sid:state,model_transport=None))
        ahead.update('s','我想了解原因，以及怎样处理。')
        await ahead.tasks['s']
        self.assertFalse(old.done());agent.speculate.assert_not_called()
        old.cancel();await asyncio.gather(old,return_exceptions=True);await ahead.close()

    async def test_latest_stable_revision_is_not_lost_behind_inflight(self):
        gate=asyncio.Event();old=asyncio.create_task(gate.wait())
        agent=SimpleNamespace(speculate=Mock(),speculation=('old',old))
        state={'generation':0,'lock':asyncio.Lock(),'agent':agent}
        ahead=ReadAhead(SimpleNamespace(session=lambda sid:state,model_transport=None),delay=.001)
        with patch('read_ahead.text_client',return_value=SimpleNamespace()):
            ahead.update('s','想了解材料原因');await asyncio.sleep(.01)
            ahead.update('s','想了解新的补正原因');await asyncio.sleep(.01)
            self.assertFalse(old.cancelled());self.assertFalse(agent.speculate.called)
            gate.set();await asyncio.sleep(.02)
            self.assertEqual(agent.speculate.call_count,1)
            self.assertEqual(agent.speculate.call_args.args[0],'想了解新的补正原因')
        await ahead.close()

if __name__=='__main__':unittest.main()
