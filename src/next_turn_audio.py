"""Read-only next-turn planner and bounded speculative audio preparation."""
import asyncio
import copy
import json
import os
import re
import aiohttp
import anyio
from grounding import ground_application, citations_current
from speech_text import speech_text
from fast_intent import fast_decision
from input_guard import inspect_input

def plans(agent, selected_topics=None, issue_numbers=None, combine=False):
    if agent.backend.identity_status(agent.sid) != 'verified' or agent.operation or agent.step in {'saved','declined'}:
        return []
    app = ground_application(agent.backend.call('get_application', {'session_id':agent.sid,'application_id':agent.app}))
    previous = agent.grounding.get('answer_topics', [])
    topics = selected_topics if selected_topics is not None else [t for t in ['materials','reason','channel','deadline'] if t not in previous][:2]
    result = []
    for group in ([topics] if combine else [[topic] for topic in topics]):
        preview = copy.copy(agent)
        preview.questions = []
        preview.events = []
        preview.grounding = copy.deepcopy(app['grounding'])
        # Render only. Never call reply(), record_stage(), turn() or write().
        preview.reply = lambda text, stage=None: {'text':text}
        answer = preview.answer_question(app, {'intent':'question','topics':group,'issue_numbers':issue_numbers or []})
        if not preview.check_output(answer['text'])['blocked']:
            result.append((answer['text'], preview.grounding))
    return result

class NextTurnAudio:
    def __init__(self, service):
        self.service = service
        self.tasks = {}
        self.retiring = set()
        self.inflight = {}
        self.calls = {}
        self.current_inputs = {}
        self.metrics = {'prepared':0,'discarded':0,'failed':0}

    def cancel(self, sid):
        if not isinstance(sid,str): return
        task = self.tasks.pop(sid, None)
        if task and not task.done():
            self.retiring.add(task)
            task.add_done_callback(self.retiring.discard)
            task.cancel()

    def current(self, sid, voice, text, final=False):
        if not voice or not isinstance(text,str) or not 1 <= len(text) <= 1000: return
        # A noun-only partial ("我想知道截止时间") may still grow into a
        # multi-topic question. Do not spend a TTS turn on that unstable prefix.
        if not final and not re.search(r'(?:是什么|有哪些|是多少|能否确定|是否明确|是否已确定|怎样处理|怎么处理|如何处理|如何修改|怎样补正|怎么补正|如何补正|为什么(?:需要|要)?补[正证])[。？！!?\s]*$|[？?]\s*$',text):
            return
        guard = inspect_input(text)
        if guard['blocked']: return
        decision = fast_decision(guard['text'])
        if not decision or decision['intent'] != 'question': return
        state = self.service.dialogues[sid]
        signature = (state['generation'],voice,json.dumps(decision,sort_keys=True))
        if self.current_inputs.get(sid) == signature: return
        self.current_inputs[sid] = signature
        self.schedule(sid,voice,decision['topics'],issue_numbers=decision.get('issue_numbers',[]),combine=True)

    def schedule(self, sid, voice, selected_topics=None, issue_numbers=None, combine=False):
        if voice.startswith('cosy:'): return  # Never send another provider's voice to Breeze.
        self.cancel(sid)
        if self.calls.get(sid,0) >= 6: return
        state = self.service.dialogues[sid]
        generation = state['generation']
        agent = state['agent']
        async def run():
            prepared = None
            try:
                for text, evidence in plans(agent, selected_topics, issue_numbers, combine):
                    key = ('verified', voice, speech_text(text), json.dumps(evidence,sort_keys=True,ensure_ascii=False))
                    if self.service.audio_cache.get(key) is not None: continue
                    if self.calls.get(sid,0)>=6 or generation!=state['generation']: return
                    # Foreground has priority: do not queue behind an active synthesis.
                    if self.service.tts_lock.locked(): return
                    self.calls[sid] = self.calls.get(sid,0)+1
                    async with self.service.tts_lock:
                        pcm=bytearray()
                        if combine:
                            prepared={'key':key,'pcm':pcm,'changed':asyncio.Event(),'done':False,
                                      'error':False,'task':asyncio.current_task(),'sid':sid}
                            self.inflight[key]=prepared
                        def received(chunk):
                            pcm.extend(chunk)
                            if len(pcm)>8_640_000: raise RuntimeError('AUDIO_LIMIT')
                            if prepared:prepared['changed'].set()
                        if os.getenv('TTS_PROVIDER') == 'breeze_realtime':
                            # Share foreground's transport and acknowledged turn.cancel.
                            # Closing an HTTP response did not confirm provider-side
                            # cancellation and could exhaust generation concurrency.
                            upstream=self.service.breeze_rt.stream(self.service.http,voice,speech_text(text))
                            try:
                                async for chunk in upstream:
                                    received(chunk)
                            finally:
                                await upstream.aclose()
                        else:
                            async with self.service.http.post('https://api.breeze.blue/v1/text-to-speech/'+voice+'/stream',
                                params={'output_format':'pcm'},headers={'xi-api-key':os.getenv('BREEZE_API_KEY','')},
                                json={'text':speech_text(text),'language_code':'zh','instructions':'语气自然、清晰、亲切，像专业客服一样交流。'},
                                allow_redirects=False,timeout=aiohttp.ClientTimeout(total=30)) as response:
                                if response.status!=200: raise RuntimeError('PREWARM_FAILED')
                                async for chunk in response.content.iter_chunked(8192):
                                    received(chunk)
                    if generation==state['generation'] and agent.backend.identity_status(sid)=='verified' and citations_current(evidence) and pcm and len(pcm)%2==0:
                        self.service.audio_cache.put(key,bytes(pcm))
                        self.metrics['prepared']+=1
                    else: self.metrics['discarded']+=1
            except asyncio.CancelledError:
                if prepared:prepared['error']=True
                raise
            except Exception:
                if prepared:prepared['error']=True
                self.metrics['failed']+=1
            finally:
                if prepared:
                    prepared['done']=True
                    prepared['changed'].set()
                    if self.inflight.get(prepared['key']) is prepared:
                        self.inflight.pop(prepared['key'],None)
        self.tasks[sid]=asyncio.create_task(run())

    def take(self,key):
        """Transfer an EXACT current answer to foreground without restarting TTS."""
        prepared=self.inflight.pop(key,None)
        if not prepared or prepared['error'] or prepared['task'].cancelling():return None
        if self.tasks.get(prepared['sid']) is prepared['task']:
            self.tasks.pop(prepared['sid'])
        return prepared

    async def consume(self,prepared):
        offset=0
        try:
            while True:
                prepared['changed'].clear()
                if prepared['error']:raise RuntimeError('PREPARED_AUDIO_FAILED')
                end=len(prepared['pcm'])
                if end>offset:
                    chunk=bytes(prepared['pcm'][offset:end]);offset=end
                    yield chunk
                if prepared['done']:break
                await prepared['changed'].wait()
        finally:
            task=prepared['task']
            if not task.done() and not task.cancelling():task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(task,return_exceptions=True)

    async def foreground(self):
        tasks=list(set(self.tasks.values()) | self.retiring)
        self.tasks.clear()
        for task in tasks:
            if not task.cancelling():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
