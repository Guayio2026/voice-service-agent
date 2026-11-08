"""Paid software E2E: recorded speech -> real VAD/ASR/agent/TTS -> AudioContext.
No fake VAD, ASR, model or TTS results. Excludes physical mic/speaker hardware.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--fixture', default='materials', choices=['materials', 'revision'])
args = parser.parse_args()
INIT = r"""
window.voiceProbe = {transcripts:[], chats:[], first:null};
const originalFetch = window.fetch;
window.fetch = async (...args) => {
  const response = await originalFetch(...args), url=String(args[0]);
  if(url.endsWith('/api/speech') && voiceProbe.inputStart) voiceProbe.speechCache=response.headers.get('X-Audio-Cache');
  if(url.endsWith('/api/session')) response.clone().json().then(x=>voiceProbe.sid=x.session_id);
  if(url.endsWith('/api/agent/chat') && voiceProbe.inputStart) {
    const started=performance.now();
    response.clone().json().then(x=>voiceProbe.chats.push({received:started,reply_ms:x.reply_ms,
      preparation:x.preparation,text:x.text}));
  }
  return response;
};
const OriginalSocket = window.WebSocket;
window.WebSocket = class extends OriginalSocket {
  constructor(...args) {
    super(...args);
    if(String(args[0]).includes('/api/asr')) this.addEventListener('message', event=>{
      try {const data=JSON.parse(event.data);voiceProbe.transcripts.push({at:performance.now(),...data});}catch{}
    });
  }
};
const originalStart = AudioBufferSourceNode.prototype.start;
AudioBufferSourceNode.prototype.start = function(when,...rest) {
  if(voiceProbe.inputEnd && this.context!==voiceProbe.inputCtx && this.buffer?.sampleRate===24000) {
    const scheduled=performance.now()+Math.max(0,(when||this.context.currentTime)-this.context.currentTime)*1000;
    if(scheduled>voiceProbe.inputEnd && voiceProbe.first===null) {
      voiceProbe.first=scheduled;voiceProbe.outputLatency=this.context.outputLatency||0;
    }
  }
  return originalStart.call(this,when,...rest);
};
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
  const ctx=voiceProbe.inputCtx=new AudioContext({sampleRate:48000});await ctx.resume();
  const buffer=await ctx.decodeAudioData(await (await originalFetch('/e2e-input.wav')).arrayBuffer());
  const destination=ctx.createMediaStreamDestination(), source=ctx.createBufferSource();
  source.buffer=buffer;source.connect(destination);
  const samples=buffer.getChannelData(0);let last=0;
  // Reference end: last 20 ms block above -54 dB RMS. Keep the criterion in report.
  for(let i=0;i<samples.length;i+=960) {
    let sum=0;const n=Math.min(960,samples.length-i);
    for(let j=0;j<n;j++)sum+=samples[i+j]**2;
    if(Math.sqrt(sum/n)>.002)last=i+n;
  }
  voiceProbe.play=()=>{
    voiceProbe.inputStart=performance.now()+100;
    voiceProbe.inputEnd=voiceProbe.inputStart+last/buffer.sampleRate*1000;
    source.start(ctx.currentTime+.1);
  };
  return destination.stream;
}});
"""

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True,
                               args=['--autoplay-policy=no-user-gesture-required'])
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.add_init_script(INIT)
    page.route('**/e2e-input.wav', lambda route: route.fulfill(
        body=(ROOT/'runtime/e2e'/f'{args.fixture}.wav').read_bytes(), content_type='audio/wav'))
    report = {'fixture': args.fixture, 'scope': 'recorded input through real software pipeline; no physical hardware',
              'end_reference': 'last 20ms input block RMS > .002', 'limit_ms': 2000}
    try:
        page.goto('http://127.0.0.1:8766/workbench.html')
        page.wait_for_selector('#vue-app[data-ready="true"]')
        page.click('#new-session')
        page.wait_for_function("voiceProbe.sid && voiceProbe.play && document.querySelector('#phase').textContent==='客服正在说话'", timeout=45000)
        page.evaluate("""async()=>{
          const r=await fetch('/api/identity',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({session_id:voiceProbe.sid,is_responsible:true})});
          if(!r.ok)throw Error(await r.text());voiceProbe.play();
        }""")
        page.wait_for_function('voiceProbe.first!==null', timeout=60000)
        report.update(page.evaluate("""()=>({
          e2e_scheduled_ms:Math.round(voiceProbe.first-voiceProbe.inputEnd),
          estimated_output_latency_ms:Math.round(voiceProbe.outputLatency*1000),
          speech_cache:voiceProbe.speechCache,
          transcripts:voiceProbe.transcripts.map(x=>({...x,at:Math.round(x.at-voiceProbe.inputEnd)})),
          chats:voiceProbe.chats.map(x=>({...x,received:Math.round(x.received-voiceProbe.inputEnd)})),
          displayed_metrics:document.querySelector('#metrics').textContent
        })"""))
        report['e2e_estimated_ms'] = report['e2e_scheduled_ms'] + report['estimated_output_latency_ms']
        report['passed'] = report['e2e_estimated_ms'] <= 2000
    except Exception as e:
        report['error'] = str(e)[:1000]
        report['diagnostic'] = page.evaluate("""()=>({transcripts:voiceProbe.transcripts,chats:voiceProbe.chats,
          phase:document.querySelector('#phase')?.textContent,error:document.querySelector('#voice-error')?.textContent})""")
        report['passed'] = False
    finally:
        report['page_errors'] = errors
        try: page.click('#interrupt', timeout=2000)
        except Exception: pass
        browser.close()
    payload = json.dumps(report,ensure_ascii=False,indent=2)
    (ROOT/'runtime/e2e'/f'{args.fixture}-result.json').write_text(payload,encoding='utf8')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    (ROOT/'runtime/e2e'/f'{args.fixture}-{stamp}.json').write_text(payload,encoding='utf8')
    print(json.dumps(report,ensure_ascii=True))
