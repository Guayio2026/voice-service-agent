"""Create two model-bound voices from the same explicitly selected Breeze sample."""
import asyncio,base64,json,os,sys,wave
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from qwen_client import load_config,QwenClient
OUT=ROOT/'runtime/voice-comparison'

async def main():
    load_config();config=QwenClient();OUT.mkdir(exist_ok=True)
    async with aiohttp.ClientSession(trust_env=True,timeout=aiohttp.ClientTimeout(total=90)) as client:
        sample=OUT/'breeze-reference.wav'
        if not sample.exists():
            headers={'xi-api-key':os.environ['BREEZE_API_KEY']}
            async with client.get('https://api.breeze.blue/v1/voices',headers=headers,params={'voice_type':'personal','page_size':100,'page':1}) as r:
                r.raise_for_status();voices=(await r.json())['voices']
            voice=next(v['voice_id'] for v in voices if v['name']=='柔美治愈女声')
            async with client.post('https://api.breeze.blue/v1/text-to-speech/'+voice+'/stream',headers=headers,params={'output_format':'pcm'},
                json={'text':'您好，很高兴为您服务。您可以慢慢说明遇到的问题，我会认真倾听，并根据已有资料帮您梳理需要补充的内容。如果还有不清楚的地方，我们可以逐项核对。','language_code':'zh','instructions':'自然、温柔、清晰地朗读。'},allow_redirects=False) as r:
                r.raise_for_status();pcm=await r.read()
            with wave.open(str(sample),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
        uri='data:audio/wav;base64,'+base64.b64encode(sample.read_bytes()).decode()
        endpoint=config.base_url.replace('/compatible-mode/v1','/api/v1/services/audio/tts/customization')
        for model in ['cosyvoice-v3-flash','qwen-audio-3.1-tts-flash']:
            dest=OUT/(model+'-enrollment.json')
            if dest.exists(): print(model+' enrollment already saved');continue
            async with client.post(endpoint,headers={'Authorization':'Bearer '+config.api_key},json={
                'model':'voice-enrollment','input':{'action':'create_voice','target_model':model,'prefix':'roumei','audio':uri,'language_hints':['zh']}},allow_redirects=False) as r:
                data=await r.json();dest.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'model':model,'http':r.status,'output':data.get('output'),'code':data.get('code'),'message':data.get('message')},ensure_ascii=True),flush=True)

if __name__=='__main__':asyncio.run(main())
