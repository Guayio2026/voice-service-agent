"""Explicit live benchmark: synthetic case, two text turns and at most two paid TTS calls.
Run with system Python (Playwright installed); server must be running on --url.
Measures browser request -> first PCM, not microphone end -> physical playback.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8767')
parser.add_argument('--next-turn', action='store_true', help='Three turns, allowing playback-duration time for prediction (paid prewarming enabled)')
args = parser.parse_args()
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page()
    page.goto(args.url + '/workbench.html')
    result = page.evaluate("""async (nextTurn) => {
      async function api(path, body) {
        const r = await fetch('/api/'+path, {method:body?'POST':'GET',
          headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
        const data = await r.json(); if(!r.ok) throw Error(JSON.stringify(data)); return data;
      }
      const status = await api('status');
      const voices = await api('voices?type=personal');
      const voice = voices.voices.find(v=>v.voice_id===status.default_voice_id) || voices.voices[0];
      if(!voice) throw Error('No voice available');
      const session = await api('session', {application_id:status.cases[0].application_id});
      await api('identity', {session_id:session.session_id,is_responsible:true});
      const results = [];
      for(let i=0;i<(nextTurn?3:2);i++) {
        const start=performance.now(), request_id=crypto.randomUUID();
        const reply = await api('agent/chat', {session_id:session.session_id,
          request_id,generation:0,text:nextTurn&&i>0?'为什么要补材料？':'我需要补充什么材料？'});
        const textAt=performance.now();
        const r=await fetch('/api/speech', {method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({session_id:session.session_id,request_id,generation:0,voice_id:voice.voice_id})});
        if(!r.ok) throw Error(await r.text());
        const reader=r.body.getReader(); let first=null, bytes=0;
        while(true) {const chunk=await reader.read(); if(chunk.done) break;
          first ??= performance.now(); bytes+=chunk.value.length;}
        results.push({round:i+1,text_ms:Math.round(textAt-start),server_reply_ms:reply.reply_ms,
          scenario:!nextTurn?'repeat-test':i===0?'cold':i===1?'predicted-new-question':'repeat',
          text_to_first_pcm_ms:Math.round(first-start),tts_first_pcm_ms:Math.round(first-textAt),
          audio_cache:r.headers.get('X-Audio-Cache'),audio_bytes:bytes,
          model_tokens:reply.usage?.total_tokens||0});
        if(nextTurn && i===0) {
          const listenWait=Math.max(0,bytes/48-(performance.now()-first));
          results.at(-1).simulated_listening_wait_ms=Math.round(listenWait);
          await new Promise(r=>setTimeout(r,listenWait));
        }
      }
      await api('interrupt', {session_id:session.session_id});
      return {metric:'browser text submission to first PCM; excludes ASR/endpoint/playback',results};
    }""", args.next_turn)
    Path('runtime/next-turn-latency.json' if args.next_turn else 'runtime/live-latency.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    browser.close()
