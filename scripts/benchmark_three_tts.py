"""Three uncached cloud TTS calls with existing regional DashScope credentials."""
import asyncio,json,sys,time,uuid,wave,os
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config,QwenClient

async def main():
    load_config(); config=QwenClient()
    url=config.base_url.replace('https://','wss://').replace('/compatible-mode/v1','/api-ws/v1/inference')
    rows=[]
    async with aiohttp.ClientSession(trust_env=True,connector=aiohttp.TCPConnector(force_close=True)) as client:
        async with client.get('https://api.breeze.blue/v1/voices',headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'voice_type':'personal','page_size':100,'page':1}) as r:
            r.raise_for_status(); breeze_voice=next(v['voice_id'] for v in (await r.json())['voices'] if v['name']=='柔美治愈女声')
        for i, model in enumerate(['breeze','cosyvoice-v3-flash','qwen-audio-3.1-tts-flash','cosyvoice-v3-flash','qwen-audio-3.1-tts-flash','breeze','qwen-audio-3.1-tts-flash','breeze','cosyvoice-v3-flash']):
            if model=='breeze':
                start=time.perf_counter(); first=None; audio=bytearray()
                async with client.post('https://api.breeze.blue/v1/text-to-speech/'+breeze_voice+'/stream',
                    headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'output_format':'pcm'},
                    json={'text':'当前资料未提供提交入口，需要进一步核实。','language_code':'zh','instructions':'语气自然、清晰、亲切，像专业客服一样交流。'},
                    allow_redirects=False,timeout=aiohttp.ClientTimeout(total=45)) as r:
                    r.raise_for_status()
                    async for chunk in r.content.iter_chunked(8192):
                        if chunk and first is None: first=time.perf_counter()
                        audio.extend(chunk)
                        if len(audio)>8640000: raise RuntimeError('audio limit')
                row={'round':i+1,'provider':model,'new_connection':True,'request_to_first_pcm_ms':round((first-start)*1000),'bytes':len(audio)}
                rows.append(row);print(json.dumps(row),flush=True)
                with wave.open(str(ROOT/f'runtime/voice-comparison/alternating-{i+1}-breeze.wav'),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(audio)
                continue
            voice=json.loads((ROOT/'runtime/voice-comparison'/(model+'-enrollment.json')).read_text(encoding='utf-8'))['output']['voice_id']
            start=time.perf_counter(); first=None; audio=bytearray(); task=uuid.uuid4().hex
            async with asyncio.timeout(45):
                async with client.ws_connect(url,headers={'Authorization':'Bearer '+config.api_key}) as ws:
                    connected=time.perf_counter()
                    def command(action,payload): return {'header':{'action':action,'task_id':task,'streaming':'duplex'},'payload':payload}
                    await ws.send_json(command('run-task',{'task_group':'audio','task':'tts','function':'SpeechSynthesizer',
                        'model':model,'parameters':{'text_type':'PlainText','voice':voice,'format':'pcm','sample_rate':24000},'input':{}}))
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
            row={'round':i+1,'provider':model,'voice':voice,'new_connection':True,
                'connect_ms':round((connected-start)*1000),'request_to_first_pcm_ms':round((first-start)*1000),
                'after_connect_first_pcm_ms':round((first-connected)*1000),'bytes':len(audio)}
            rows.append(row);print(json.dumps(row),flush=True)
            with wave.open(str(ROOT/f'runtime/voice-comparison/alternating-{i+1}-{model}.wav'),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(audio)
    (ROOT/'runtime/voice-comparison/alternating-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')

if __name__=='__main__': asyncio.run(main())
