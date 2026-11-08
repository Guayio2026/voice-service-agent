"""Offline DSP contract: RNNoise may suppress speech; VAD retains AEC input."""
from pathlib import Path
from playwright.sync_api import sync_playwright
source=(Path(__file__).resolve().parents[1]/'web/denoise-worklet.js').read_text(encoding='utf8')
source=source.replace("import createRNNoise from './vendor/audio/rnnoise-sync.js';",'')
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page()
    result=page.evaluate('''source => {
      const packets=[];let Recorder;
      const sampleRate=48000;
      class AudioWorkletProcessor {constructor(){this.port={postMessage:m=>packets.push(m)};}}
      const registerProcessor=(name,cls)=>Recorder=cls;
      const heap=new Float32Array(1024);
      const createRNNoise=()=>({HEAPF32:heap,_rnnoise_create:()=>1,_malloc:()=>0,
        _rnnoise_process_frame:()=>heap.fill(0),_rnnoise_destroy(){},_free(){}});
      eval(source);
      const recorder=new Recorder();
      for(let k=0;k<20;k++) recorder.process([[Float32Array.from({length:128},(_,i)=>.03*Math.sin(2*Math.PI*440*(k*128+i)/48000))]]);
      if(!packets.length)throw Error('No PCM');
      if(!packets.every(p=>p.samples.length===320&&p.vadSamples.length===320&&p.pcm.byteLength===640))throw Error('Frame format');
      if(packets.some(p=>p.samples.some(v=>v!==0)))throw Error('ASR must remain denoised');
      if(!packets.some(p=>p.vadSamples.some(v=>Math.abs(v)>.01)))throw Error('VAD lost original signal');
      recorder.dispose();return {passed:true,frames:packets.length};
    }''',source)
    print(result);browser.close()
