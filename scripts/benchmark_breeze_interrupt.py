"""Paid synthetic test: cancel after first PCM, compare next-turn first PCM."""
import asyncio,json,sys,time,os
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config
from breeze_realtime import BreezeRealtime
async def main():
    load_config();rows=[]
    async with aiohttp.ClientSession(trust_env=True) as client:
        async with client.get('https://api.breeze.blue/v1/voices',headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'voice_type':'personal','page_size':100,'page':1}) as r:
            r.raise_for_status();voice=next(v['voice_id'] for v in (await r.json())['voices'] if v['name']=='柔美治愈女声')
        for mode in ['reuse','reconnect','reconnect','reuse']:
            rt=BreezeRealtime()
            try:
                audio=rt.stream(client,voice,'我们先核实一下需要补充的材料。请您查看当前申请对应的资料清单，确认需要补充的具体内容。如果有不清楚的地方，可以继续向我提问，我会根据现有资料逐项说明。')
                await anext(audio);old=rt.ws;start=time.perf_counter()
                await audio.aclose()
                cancelled_ms=round((time.perf_counter()-start)*1000)
                retained=rt.ws is old and not old.closed
                if mode=='reconnect':await rt.close()
                start=time.perf_counter();first=None;size=0
                async for chunk in rt.stream(client,voice,'当前资料未提供提交入口，需要进一步核实。'):
                    if chunk:first=first or time.perf_counter();size+=len(chunk)
                row={'mode':mode,'cancel_ack_ms':cancelled_ms,'retained_after_cancel':retained,'next_first_pcm_ms':round((first-start)*1000),'bytes':size}
                rows.append(row);print(json.dumps(row),flush=True)
            except Exception as exc:
                row={'mode':mode,'error':type(exc).__name__}
                rows.append(row);print(json.dumps(row),flush=True)
            finally:
                await rt.close()
                (ROOT/'runtime/voice-comparison/breeze-interrupt-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    (ROOT/'runtime/voice-comparison/breeze-interrupt-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
if __name__=='__main__':asyncio.run(main())
