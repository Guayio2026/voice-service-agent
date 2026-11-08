"""Streaming PCM from Bailian (DashScope) TTS over the duplex task socket.

Same transport as before; only the model and voice are parameters now, so a
cloned voice from the DashScope console plays through the existing pipeline
(24 kHz s16le PCM, identical framing to the Breeze path).
"""
import asyncio,json,uuid
import aiohttp
from qwen_client import QwenClient

DEFAULT_MODEL = 'qwen-audio-3.1-tts-flash'
DEFAULT_VOICE = 'longanyang'

async def stream(client,text,voice=DEFAULT_VOICE,model=DEFAULT_MODEL):
    config=QwenClient()
    url=config.base_url.replace('https://','wss://').replace('/compatible-mode/v1','/api-ws/v1/inference')
    task=uuid.uuid4().hex
    def command(action,payload): return {'header':{'action':action,'task_id':task,'streaming':'duplex'},'payload':payload}
    async with asyncio.timeout(45):
        async with client.ws_connect(url,headers={'Authorization':'Bearer '+config.api_key}) as ws:
            await ws.send_json(command('run-task',{'task_group':'audio','task':'tts','function':'SpeechSynthesizer',
                'model':model,'parameters':{'text_type':'PlainText','voice':voice,'format':'pcm','sample_rate':24000},'input':{}}))
            async for msg in ws:
                if msg.type==aiohttp.WSMsgType.BINARY:
                    yield msg.data
                elif msg.type==aiohttp.WSMsgType.TEXT:
                    event=json.loads(msg.data).get('header',{}).get('event')
                    if event=='task-started':
                        await ws.send_json(command('continue-task',{'input':{'text':text}}))
                        await ws.send_json(command('finish-task',{'input':{}}))
                    if event=='task-failed': raise RuntimeError('COSYVOICE_TASK_FAILED')
                    if event=='task-finished': return
                else: raise RuntimeError('COSYVOICE_DISCONNECTED')
            raise RuntimeError('COSYVOICE_INCOMPLETE')
