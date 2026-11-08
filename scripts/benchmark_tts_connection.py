"""Four paid syntheses, same voice/text; trace actual connection reuse, no secrets logged."""
import asyncio, json, os, sys, time
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config

async def main():
    load_config()
    headers={'xi-api-key':os.environ['BREEZE_API_KEY']}
    async with aiohttp.ClientSession(trust_env=True) as lookup:
        async with lookup.get('https://api.breeze.blue/v1/voices',headers=headers,
                params={'voice_type':'personal','page':1,'page_size':100},allow_redirects=False) as r:
            if r.status!=200: raise RuntimeError('voice lookup HTTP '+str(r.status))
            voices=(await r.json())['voices']
    voice=next((v['voice_id'] for v in voices if v['voice_id']==os.getenv('BREEZE_VOICE_ID')),voices[0]['voice_id'])
    report=[]
    for group in range(2):
        trace=aiohttp.TraceConfig()
        events={}
        for name in ['on_connection_create_start','on_connection_create_end','on_connection_reuseconn','on_dns_resolvehost_start','on_dns_resolvehost_end','on_request_headers_sent']:
            async def record(session,ctx,params,event=name): events[event]=time.perf_counter()
            getattr(trace,name).append(record)
        async with aiohttp.ClientSession(trust_env=True,trace_configs=[trace],timeout=aiohttp.ClientTimeout(total=60)) as client:
            for round_ in range(2):
                events.clear(); start=time.perf_counter(); first=None; size=0
                async with client.post('https://api.breeze.blue/v1/text-to-speech/'+voice+'/stream',
                    headers=headers,params={'output_format':'pcm'},allow_redirects=False,
                    json={'text':'当前资料未提供提交入口，需要进一步核实。','language_code':'zh',
                    'instructions':'语气自然、清晰、亲切，像专业客服一样交流。'}) as response:
                    received=time.perf_counter()
                    if response.status!=200: raise RuntimeError('TTS HTTP '+str(response.status))
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk and first is None: first=time.perf_counter()
                        size+=len(chunk)
                        if size>8640000: raise RuntimeError('audio limit')
                row={'group':group+1,'request':round_+1,'reused':'on_connection_reuseconn' in events,
                    'connect_ms':round((events.get('on_connection_create_end',start)-events.get('on_connection_create_start',start))*1000),
                    'headers_ms':round((received-start)*1000),'first_pcm_ms':round((first-start)*1000),
                    'sent_headers_to_first_pcm_ms':round((first-events.get('on_request_headers_sent',start))*1000)}
                report.append(row);print(json.dumps(row),flush=True)
    (ROOT/'runtime/tts-connection-benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__': asyncio.run(main())
