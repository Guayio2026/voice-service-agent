"""Offline browser contract: fake audio, ASR and cloud APIs; no microphone or paid calls."""
import json
import sys
import base64
from playwright.sync_api import sync_playwright

FAKES = r"""
window.testAudio = {workers: [], sockets: [], nodes: [], stopped: 0, tracksStopped: 0};
class FakeWorker {
  constructor() { testAudio.workers.push(this); }
  postMessage(data) { if (data.type === 'init') queueMicrotask(() => this.onmessage?.({data:{type:'ready'}})); }
  terminate() {}
}
window.Worker = FakeWorker;
window.AudioContext = class {
  constructor(opts) { this.sampleRate=opts.sampleRate; this.state='running'; this.destination={}; }
  get currentTime() { return performance.now()/1000; }
  async resume() {}
  async close() { this.state='closed'; }
  audioWorklet={addModule: async()=>{}};
  createMediaStreamSource() { return {connect(){},disconnect(){}}; }
  createBuffer(_, n, rate) { return {duration:n/rate,getChannelData:()=>new Float32Array(n)}; }
  createBufferSource() { return {connect(){},disconnect(){},start(){setTimeout(()=>this.onended?.(),1000);},stop(){testAudio.stopped++; testAudio.stopAt ??= performance.now(); this.onended?.();}}; }
};
window.AudioWorkletNode = class {
  constructor() { this.port={postMessage(){}}; testAudio.nodes.push(this); setTimeout(()=>this.port.onmessage?.({data:{pcm:new ArrayBuffer(640),samples:new Float32Array(320),rms:0,rawRms:0}}),0); }
  connect() {} disconnect() {}
};
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
 const track={label:'Fake mic',stop(){testAudio.tracksStopped++;}};
 return {getTracks:()=>[track],getAudioTracks:()=>[track]};
}});
window.WebSocket = class {
  static OPEN=1;
  constructor() { this.readyState=1; this.bufferedAmount=0; this.sent=[]; testAudio.sockets.push(this); setTimeout(()=>this.onopen?.(),0); }
  send(data) { this.sent.push(data); }
  close() { this.readyState=3; this.onclose?.(); }
};
window.feedVad = async (positive=true) => {
  testAudio.feedAt = performance.now(); testAudio.stopAt = null;
  const worker=testAudio.workers.at(-1);
  for(let i=0;i<12;i++) {
    testAudio.nodes.at(-1).port.onmessage({data:{pcm:new ArrayBuffer(640),samples:new Float32Array(320),rms:.04}});
    worker.onmessage({data:{type:'probability',probability:positive?.95:.1,at:performance.now()}});
    await new Promise(r=>setTimeout(r,32));
  }
};
window.asrMessage = data => testAudio.sockets.at(-1).onmessage({data:JSON.stringify(data)});
"""

def main():
    calls = []
    generation = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script(FAKES)

        def api(route):
            nonlocal generation
            path = route.request.url.split('/api/')[1].split('?')[0]
            body = route.request.post_data_json if route.request.method == 'POST' else {}
            calls.append((path, body))
            if path == 'status':
                data = {'configured':{}, 'cases':[{'application_id':'TEST-001','label':'Test'}]}
            elif path == 'voices': data = {'voices':[{'voice_id':'fake','name':'Fake'}]}
            elif path == 'session':
                generation = 0
                data = {'session_id':'fake-session'}
            elif path == 'interrupt':
                generation += 1
                data = {'interrupted':True,'generation':generation}
            elif path == 'agent/chat':
                assert body['generation'] == generation
                data = {'request_id':body['request_id'],'text':'模拟回复','reply_ms':10,'usage':{},'grounding':{'status':'supported','checks':[{'status':'valid'}],'citations':[{'id':'TEST-R01','version':'TEST-v1','source_title':'合成测试资料','reason':'缺页','action':'补页'}]}}
            elif path == 'speech':
                if '--duplex' in sys.argv:
                    assert body.get('stream_reply') is True
                    frames = [ {'type':'text','text':'模拟流式回复。'},
                        {'type':'audio','pcm':base64.b64encode(bytes(48000)).decode()},
                        {'type':'done','metrics':{'first_pcm_ms':50,'llm_done_ms':100}} ]
                    route.fulfill(body=''.join(json.dumps(e)+'\n' for e in frames), content_type='application/x-ndjson')
                    return
                route.fulfill(body=bytes(48000),content_type='application/octet-stream')
                return
            else: data = {}
            route.fulfill(json=data)

        page.route('**/api/**', api)
        page.goto('http://127.0.0.1:8766/workbench.html')
        page.wait_for_selector('#vue-app[data-ready="true"]')
        page.click('#new-session')
        page.wait_for_function("document.querySelector('#phase').textContent === '客服正在说话'")
        assert page.evaluate('testAudio.nodes.length') == 1
        page.evaluate('feedVad()')
        page.wait_for_function('testAudio.stopped > 0')
        interrupt_ms = page.evaluate('testAudio.stopAt - testAudio.feedAt')
        assert page.evaluate('testAudio.sockets.length') == 1
        page.click('#interrupt')
        page.evaluate('testAudio.nodes = []; testAudio.sockets = []; testAudio.stopped = 0; testAudio.tracksStopped = 0')
        page.wait_for_function("!document.querySelector('#start-realtime').disabled")
        calls.clear()
        page.click('#start-realtime')
        page.wait_for_function("testAudio.nodes.length === 1")
        delays = page.evaluate("async () => { const {endpointDelay} = await import('/realtime-voice.js'); return ['是的','但是','编号是','123','怎么补材料'].map(endpointDelay); }")
        assert delays[:4] == [300,1200,1200,1200]
        assert delays[4] == 450
        page.evaluate("""async () => {
          const {RealtimeVoice, endpointDelay} = await import('/realtime-voice.js');
          const check = (ok, message) => {if (!ok) throw Error(message);};
          check(endpointDelay('我先不问材料了，') === 1200, 'comma continuation');
          check(endpointDelay('我现不问材料') === 1200, 'revised ASR continuation');
          check(endpointDelay('怎么填写编号是') === 1200, 'unfinished question');
          check(endpointDelay('怎么填写123') === 1200, 'number pause');
          check(endpointDelay('我还有一些情况要说明') === 650, 'ordinary pause');
          let starts = 0;
          const v = new RealtimeVoice({speaking:()=>true});
          v.speechStart = null;
          v.begin = () => {starts++; v.turn = {};};
          const now = performance.now();
          v.detect(.99, now - 400); check(starts === 0, 'stale frame');
          v.detect(.99, now - 96); v.detect(.1, now - 64);
          v.detect(.99, now - 32); v.detect(.99, now);
          check(starts === 0, 'isolated sound must not interrupt');
          v.detect(.99, now + 32); check(starts === 1, 'sustained speech');
          const originalFetch = window.fetch;
          try {
            let finishCount = 0, resolve;
            const end = new RealtimeVoice({session:()=> 'fake-session'});
            end.active = true; end.lastSpeech = 100;
            const turn = end.turn = {transcript:'怎么补材料',endpointAudio:[]};
            end.finish = () => { finishCount++; };
            window.fetch = () => new Promise(r => {resolve=r;});
            const pending = end.predictEnd(turn);
            end.lastSpeech = 200;
            resolve({ok:true,json:async()=>({available:true,complete:true})});
            await pending;
            check(finishCount === 0, 'resumed speech invalidates endpoint prediction');
            window.fetch = async()=>({ok:true,json:async()=>({available:true,complete:true})});
            await end.predictEnd(turn);
            check(finishCount === 1, 'matching endpoint prediction finishes turn');
            end.lastSpeech = 300; turn.transcript = '我现';
            window.fetch = () => new Promise(r => {resolve=r;});
            const stale = end.predictEnd(turn);
            turn.transcript = '我现不问材料';
            resolve({ok:true,json:async()=>({available:true,complete:true})});
            await stale;
            check(finishCount === 1, 'new ASR text invalidates old endpoint prediction');
          } finally {window.fetch = originalFetch;}
        }""")
        # Non-speech must not open ASR or cancel any business turn.
        page.evaluate('feedVad(false)')
        assert page.evaluate('testAudio.sockets.length') == 0
        page.evaluate('feedVad()')
        assert page.evaluate('testAudio.sockets.length') == 1
        assert not any(x[0] == 'interrupt' for x in calls)
        page.evaluate("asrMessage({type:'ready'})")
        assert page.evaluate('testAudio.sockets[0].sent.filter(x=>x instanceof ArrayBuffer).length') >= 10
        page.click('#finish-record')
        page.evaluate("asrMessage({type:'done',text:'你好'})")
        page.wait_for_function("document.querySelector('#phase').textContent === '客服正在说话'")
        page.evaluate('feedVad()')
        page.wait_for_timeout(150)
        assert page.evaluate('testAudio.stopped') > 0, 'barge-in must silence scheduled audio'
        assert sum(x[0] == 'interrupt' for x in calls) == 1
        assert page.evaluate('testAudio.sockets.length') == 2
        page.evaluate("asrMessage({type:'ready'})")
        page.click('#finish-record')
        page.evaluate("asrMessage({type:'done',text:'等一下，补什么材料'})")
        page.wait_for_timeout(250)
        assert len([x for x in calls if x[0] == 'agent/chat']) == 2
        page.click('#interrupt')
        assert page.evaluate('testAudio.tracksStopped') == 1
        assert page.is_disabled('#finish-record')
        assert not errors, errors
        # Re-enter then leave the voice tab: capture must be released again.
        page.click('#start-realtime')
        page.wait_for_function('testAudio.nodes.length === 2')
        page.click('#mode-demo')
        assert page.evaluate('testAudio.tracksStopped') == 2
        page.click('#mode-voice')
        assert page.locator('#continuous-on-start').count() == 0
        page.click('.sound-settings > summary')
        page.uncheck('#speak-replies')
        page.click('#new-session')
        page.wait_for_function('testAudio.nodes.length === 3')
        assert page.is_disabled('#record')
        page.wait_for_selector('#conversation details summary')
        page.locator('#conversation details summary').last.click()
        assert 'TEST-R01' in page.locator('#conversation').inner_text()
        page.screenshot(path='runtime/continuous-option.png', full_page=True)
        page.click('#interrupt')
        assert page.evaluate('testAudio.tracksStopped') == 3
        assert page.is_enabled('#record')
        assert not errors, errors
        print(json.dumps({'passed':True,'simulated_vad_to_stop_ms':round(interrupt_ms,1),'endpoint_delays_ms':delays,'checks':['non-speech','pre-roll','playback interruption','next turn','stop cleanup','tab cleanup']}))
        browser.close()

if __name__ == '__main__':
    main()
