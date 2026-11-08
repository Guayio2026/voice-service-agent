"""Debounced read-only preparation from revisable ASR text."""
import asyncio
import re
import json
from text_client import text_client, AsyncClassifier

class ReadAhead:
    def __init__(self,service,delay=.25):
        self.service=service;self.delay=delay;self.tasks={};self.keys={}
    def cancel(self,sid):
        task=self.tasks.pop(sid,None)
        if task:task.cancel()
        self.keys.pop(sid,None)
    def update(self,sid,text):
        state=self.service.session(sid);key=(state['generation'],text)
        if self.keys.get(sid)==key:return
        self.cancel(sid);self.keys[sid]=key
        if not 4<=len(text)<=1000:return
        if re.search(r'(?:以及|我应该|需要|为什么需|然后|但是|还有|每|这次)[，,。\s]*$', text): return
        complete_question = bool(re.search(r'(?:怎样处理|怎么处理|怎么办|如何补正|能否确定|哪些材料|什么原因|[吗呢])[。？！!?\s]*$', text))
        async def prepare():
            try:
                await asyncio.sleep(0 if complete_question else self.delay)
                if self.keys.get(sid)!=key or state['lock'].locked() or state['generation']!=key[0]:return
                # A stale speculative request may still occupy the single slot.
                # Queue the newest stable text instead of silently dropping it.
                previous = state['agent'].speculation
                if previous and not previous[1].done():
                    # Final ASR often edits only punctuation. Do not cancel useful
                    # work for an equivalent read-only question already in flight.
                    if hasattr(state['agent'], 'classification_input'):
                        from business_agent import same_readonly_asr_input
                        current = (state['agent'].generation, json.dumps(state['agent'].classification_input(text),
                                   sort_keys=True, ensure_ascii=False))
                        if previous[0] == current or same_readonly_asr_input(previous[0], current):
                            return
                    if complete_question:
                        previous[1].cancel()
                        await asyncio.gather(previous[1],return_exceptions=True)
                    try:
                        if not previous[1].done(): await asyncio.shield(previous[1])
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                    if self.keys.get(sid)!=key or state['lock'].locked() or state['generation']!=key[0]:return
                model=text_client();model.http_transport=self.service.model_transport
                # speculate() permits only read-only, identity-checked preparation,
                # bounded to eight calls; final classification checks exact inputs.
                state['agent'].speculate(text,AsyncClassifier(getattr(self.service,'http',None),model))
            except asyncio.CancelledError:raise
            except Exception:pass  # Optional preparation must not break ASR.
        self.tasks[sid]=asyncio.create_task(prepare())
    async def close(self):
        tasks=list(self.tasks.values())
        for sid in list(self.tasks):self.cancel(sid)
        await asyncio.gather(*tasks,return_exceptions=True)
