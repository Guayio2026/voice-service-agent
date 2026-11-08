"""Three uncached cloud TTS calls with existing regional DashScope credentials."""
import asyncio,json,sys,time,uuid
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config,QwenClient

async def main():
    load_config(); config=QwenClient()
    url=config.base_url.replace('https://','wss://').replace('/compatible-mode/v1','/api-ws/v1/inference')
    rows=[]
    async with aiohttp.ClientSession(trust_env=True) as client:
        for i in range(3):
            start=time.perf_counter(); first=None; audio=bytearray(); task=uuid.uuid4().hex
            async with asyncio.timeout(45):
                async with client.ws_connect(url,headers={'Authorization':'Bearer '+config.api_key}) as ws:
                    connected=time.perf_counter()
                    def command(action,payload): return {'header':{'action':action,'task_id':task,'streaming':'duplex'},'payload':payload}
                    await ws.send_json(command('run-task',{'task_group':'audio','task':'tts','function':'SpeechSynthesizer',
                        'model':'cosyvoice-v3-flash','parameters':{'text_type':'PlainText','voice':'longanyang','format':'pcm','sample_rate':24000},'input':{}}))
                    async for msg in ws:
                        if msg.type==aiohttp.WSMsgType.BINARY:
                            first=first or time.perf_counter();audio.extend(msg.data)
                            if len(audio)>8640000: raise RuntimeError('audio limit')
                        elif msg.type==aiohttp.WSMsgType.TEXT:
                            data=json.loads(msg.data);event=data.get('header',{}).get('event')
                            if event=='task-started':
                                await ws.send_json(command('continue-task',{'input':{'text':'当前资料未提供提交入口，需要进一步核实。'}}))
                                await ws.send_json(command('finish-task',{'input':{}}))
                            if event=='task-failed':
                                raise RuntimeError('Provider task failed: '+str(data.get('header',{}).get('error_code')))
                            if event=='task-finished': break
                        else: raise RuntimeError('WebSocket closed before completion')
            if first is None: raise RuntimeError('No audio')
            row={'round':i+1,'provider':'cosyvoice-v3-flash','voice':'longanyang','new_connection':True,
                'connect_ms':round((connected-start)*1000),'request_to_first_pcm_ms':round((first-start)*1000),
                'after_connect_first_pcm_ms':round((first-connected)*1000),'bytes':len(audio)}
            rows.append(row);print(json.dumps(row),flush=True)
            (ROOT/f'runtime/cosyvoice-{i+1}.pcm').write_bytes(audio)
    (ROOT/'runtime/cosyvoice-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')

if __name__=='__main__': asyncio.run(main())
