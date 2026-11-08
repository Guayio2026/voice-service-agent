"""Live synthetic overlap benchmark; no inserted generation delays, paid model/TTS."""
import asyncio,json,os,sys,time,wave,base64
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import aiohttp
from qwen_client import load_config
from breeze_realtime import BreezeRealtime
from streaming_reply import oral_events
from output_guard import inspect_output
async def main():
    load_config();rt=BreezeRealtime();metrics={};audio=bytearray();events=[]
    source='本次为合成业务演示，不是真实审核结果。资料显示营业执照缺少有效期页面，需要补充清晰完整的有效期页面。授权材料中的主体名称与申请主体不一致，需要核对并补充一致的授权材料。产品清单缺少型号信息，需要补充具体型号。提交前应核对这些材料与本次申请对应。当前资料没有提供提交入口、收费信息或补正截止时间，需要进一步核实。补充材料不等于审核通过，最终结果需要以审核结果为准。本次沟通解释不会自动提交材料，也不会自动保存沟通登记。'
    turn={'input':'请详细解释这三项材料分别为什么要补、要怎么处理，还有哪些不能直接确认的事项。','result':{'text':source}}
    state={'agent':SimpleNamespace(check_output=lambda t:inspect_output(t,verified=True))}
    async with aiohttp.ClientSession(trust_env=True) as http:
        async with http.get('https://api.breeze.blue/v1/voices',headers={'xi-api-key':os.environ['BREEZE_API_KEY']},params={'voice_type':'personal','page_size':100,'page':1}) as r:
            r.raise_for_status();voice=next(v['voice_id'] for v in (await r.json())['voices'] if v['name']=='柔美治愈女声')
        try:
            await rt.connect(http,voice)
            service=SimpleNamespace(http=http,breeze_rt=rt)
            async for raw in oral_events(service,state,turn,voice,lambda:None):
                event=json.loads(raw)
                if event['type']=='audio':audio.extend(base64.b64decode(event['pcm']))
                else:events.append(event);print(json.dumps(event,ensure_ascii=True),flush=True)
        finally:await rt.close()
    folder=ROOT/'runtime/voice-comparison';folder.mkdir(exist_ok=True)
    (folder/'stream-overlap.json').write_text(json.dumps({'events':events,'metrics':turn.get('stream_metrics'),'spoken':turn.get('spoken_text'),'bytes':len(audio)},ensure_ascii=False,indent=2),encoding='utf8')
    with wave.open(str(folder/'stream-overlap.wav'),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(audio)
if __name__=='__main__':asyncio.run(main())
