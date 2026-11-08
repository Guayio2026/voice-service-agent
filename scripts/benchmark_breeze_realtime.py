import asyncio,json,sys,time,os
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config
from breeze_realtime import BreezeRealtime
async def main():
    load_config(); rows=[]; rt=BreezeRealtime()
    async with aiohttp.ClientSession(trust_env=True) as client:
        async with client.get('https://api.breeze.blue/v1/voices',headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'voice_type':'personal','page_size':100,'page':1}) as r:
            r.raise_for_status(); voice=next(v['voice_id'] for v in (await r.json())['voices'] if v['name']=='柔美治愈女声')
        try:
            for mode in ['http','ws_cold','http','ws_reused','http','ws_reused']:
                start=time.perf_counter(); first=None; size=0
                if mode=='http':
                    async with client.post('https://api.breeze.blue/v1/text-to-speech/'+voice+'/stream',headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'output_format':'pcm'},json={'text':'当前资料未提供提交入口，需要进一步核实。','language_code':'zh'},timeout=aiohttp.ClientTimeout(total=45)) as r:
                        r.raise_for_status()
                        async for chunk in r.content.iter_chunked(8192):
                            if chunk: first=first or time.perf_counter(); size+=len(chunk)
                else:
                    async for chunk in rt.stream(client,voice,'当前资料未提供提交入口，需要进一步核实。'):
                        if chunk: first=first or time.perf_counter(); size+=len(chunk)
                if first is None: raise RuntimeError('No audio')
                row={'mode':mode,'first_pcm_ms':round((first-start)*1000),'bytes':size}
                rows.append(row);print(json.dumps(row),flush=True)
        finally:
            await rt.close()
            (ROOT/'runtime/voice-comparison/breeze-realtime-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
if __name__=='__main__': asyncio.run(main())
