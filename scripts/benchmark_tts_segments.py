"""Six paid TTS calls, alternating full/first sentence; no business writes.
Non-silence is an approximate 10ms RMS threshold, not human audibility.
"""
import asyncio, json, os, sys, time, math, struct
from pathlib import Path
import aiohttp
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from qwen_client import load_config

async def main():
    load_config()
    key, voice = os.environ['BREEZE_API_KEY'], os.environ['BREEZE_VOICE_ID']
    first = '当前资料未提供提交入口，需要进一步核实。'
    full = first + '我可以为您登记模拟人工协助工单，但不会直接接通真人。只有您明确确认后才会登记。'
    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), trust_env=True) as session:
        async with session.get('https://api.breeze.blue/v1/voices', headers={'xi-api-key':key},
            params={'voice_type':'personal','page_size':100,'page':1}, allow_redirects=False) as response:
            if response.status != 200: raise RuntimeError('Voice list HTTP '+str(response.status))
            voices = (await response.json())['voices']
            if not voices: raise RuntimeError('No personal voice')
            voice = next((v['voice_id'] for v in voices if v['voice_id']==voice), voices[0]['voice_id'])
        for index, mode in enumerate(['full','short','short','full','full','short']):
            start = time.perf_counter(); first_at = None; audible_at = None; onset = None; pcm = bytearray(); scanned = 0
            async with session.post('https://api.breeze.blue/v1/text-to-speech/'+voice+'/stream',
                params={'output_format':'pcm'}, headers={'xi-api-key':key}, allow_redirects=False,
                json={'text':full if mode=='full' else first,'language_code':'zh',
                      'instructions':'语气自然、清晰、亲切，像专业客服一样交流。'}) as response:
                headers_ms = (time.perf_counter()-start)*1000
                if response.status != 200: raise RuntimeError('TTS HTTP '+str(response.status))
                async for chunk in response.content.iter_chunked(8192):
                    now = (time.perf_counter()-start)*1000
                    if chunk and first_at is None: first_at = now
                    pcm.extend(chunk)
                    if len(pcm)>8640000: raise RuntimeError('audio limit')
                    while scanned+480<=len(pcm) and audible_at is None:
                        samples = struct.unpack('<240h', pcm[scanned:scanned+480])
                        if math.sqrt(sum(x*x for x in samples)/240)/32768 >= .005:
                            audible_at, onset = now, scanned/48
                        scanned += 480
            (ROOT/f'runtime/tts-ab-{index+1}-{mode}.pcm').write_bytes(pcm)
            results.append({'round':index+1,'mode':mode,'headers_ms':round(headers_ms),
                'first_pcm_ms':round(first_at),'first_nonquiet_received_ms':round(audible_at) if audible_at else None,
                'leading_quiet_ms':onset,'duration_ms':len(pcm)/48})
            print(json.dumps(results[-1]), flush=True)
    (ROOT/'runtime/tts-segment-ab.json').write_text(json.dumps({'sample_rate':24000,'rms_threshold':.005,
        'scope':'server to provider; no ASR or browser playback; six requests, no percentile claim',
        'results':results},indent=2),encoding='utf-8')

if __name__ == '__main__': asyncio.run(main())
