"""Paid synthetic browser/API test; excludes microphone and physical output."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True,
                               args=['--autoplay-policy=no-user-gesture-required'])
    page = browser.new_page()
    page.goto('http://127.0.0.1:8766/workbench.html')
    result = page.evaluate(r"""async () => {
      async function api(path, body) {
        const r = await fetch('/api/'+path, {method:body?'POST':'GET',
          headers:body?{'Content-Type':'application/json'}:{}, body:body?JSON.stringify(body):undefined});
        if (!r.ok) throw Error(await r.text()); return r.json();
      }
      const status=await api('status'), voices=await api('voices?type=personal');
      const voice=voices.voices.find(v=>v.name==='柔美治愈女声')||voices.voices[0];
      const session=await api('session',{application_id:status.cases[0].application_id});
      await api('identity',{session_id:session.session_id,is_responsible:true});
      const ctx=new AudioContext(); await ctx.resume(); const nodes=[];
      const start=performance.now(), request_id=crypto.randomUUID();
      const report={stage:'chat'};
      try {
        const reply=await api('agent/chat',{session_id:session.session_id,request_id,generation:0,
          text:'请把每项材料的问题和需要补正的具体做法逐项说明，也说一下截止时间和提交入口能不能确定。'});
        const chatMs=performance.now()-start; report.chat_ms=Math.round(chatMs);
        report.stage='speech';
        const r=await fetch('/api/speech',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({session_id:session.session_id,request_id,generation:0,voice_id:voice.voice_id,stream_reply:true})});
        if(!r.ok) throw Error(await r.text());
        if(!r.headers.get('Content-Type').includes('application/x-ndjson')) throw Error('Expected duplex reply');
        const reader=r.body.getReader(), decoder=new TextDecoder();
        let pending='', first=null, playhead=ctx.currentTime, doneSeen=false, metrics=null, overlap=false;
        while(true) {
          const next=await reader.read(); if(next.done) break;
          pending+=decoder.decode(next.value,{stream:true}); let end;
          while((end=pending.indexOf('\n'))>=0) {
            const e=JSON.parse(pending.slice(0,end)); pending=pending.slice(end+1);
            if(e.type==='error') throw Error(e.code);
            report.last_event=e.type;
            if(e.type==='timing'&&e.event==='llm_done') doneSeen=true;
            if(e.type==='done') metrics=e.metrics;
            if(e.type==='audio') {
              const bytes=Uint8Array.from(atob(e.pcm),c=>c.charCodeAt(0)), view=new DataView(bytes.buffer);
              const buffer=ctx.createBuffer(1,bytes.length/2,24000), samples=buffer.getChannelData(0);
              for(let i=0;i<samples.length;i++) samples[i]=view.getInt16(i*2,true)/32768;
              const node=ctx.createBufferSource(); node.buffer=buffer; node.connect(ctx.destination);
              playhead=Math.max(ctx.currentTime+.04,playhead); node.start(playhead); nodes.push(node);
              if(first===null) {first=performance.now()-start+(playhead-ctx.currentTime)*1000;overlap=!doneSeen;report.first_scheduled_audio_ms=Math.round(first);}
              playhead+=buffer.duration;
            }
          }
        }
        if(!metrics) throw Error('Missing completion');
        return Object.assign(report,{stage:'complete',metric:'text submission to scheduled audio; excludes ASR, endpoint and physical output',
          chat_ms:Math.round(chatMs),first_scheduled_audio_ms:Math.round(first),
          scheduled_before_llm_done_received:overlap,metrics});
      } catch(e) {report.error=String(e);return report;} finally {
        for(const node of nodes) {try{node.stop();}catch{}}
        await ctx.close(); try {await api('interrupt',{session_id:session.session_id});} catch(e) {report.cleanup_error=String(e);}
      }
    }""")
    Path('runtime/voice-comparison/browser-stream-overlap.json').write_text(
        json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result))
    browser.close()
