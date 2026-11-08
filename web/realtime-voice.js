// Conservative lexical endpoint hints, not a semantic endpoint model.
export function endpointDelay(text = '') {
  const phrase = text.replace(/[\s，。！？、,.!?]/g, '');
  if (/[，,、：:]\s*$/.test(text)) return 1200;
  if (/(?:不|别|不要)(?:问|说|讲|看)/.test(phrase) && !/(?:想知道|想了解|请问|怎么|如何|什么时候)/.test(phrase)) return 1200;
  if (/^(是|是的|对|不是|不是的|方便|不方便|谢谢|你好|确认登记)$/.test(phrase)) return 300;
  if (/(但是|不过|然后|还有|因为|编号是|号码是|我想|就是)$/.test(phrase) || /[0-9A-Za-z]$/.test(phrase)) return 1200;
  // Only accelerate recognizable complete questions; partial clauses keep their pause budget.
  if (phrase.length >= 4 && (/^(怎么|如何|为什么|需要什么|什么时候)/.test(phrase) || /[吗呢]$/.test(phrase))) return 450;
  return 650;
}
// Continuous local capture. Only detected utterances are uploaded to ASR.
export class RealtimeVoice {
  constructor(callbacks) { this.cb = callbacks; this.active = false; this.serial = 0; }
  async start(deviceId) {
    this.active = true;
    this.endpointUnavailable = false;
    const token = ++this.serial;
    this.ring = []; this.queue = []; this.speechStart = null; this.turn = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true,
        ...(deviceId ? {deviceId: {exact: deviceId}} : {})
      }});
      if (token !== this.serial) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
      if (stream.getAudioTracks()[0]?.getSettings?.().echoCancellation === false) {
        throw Error('浏览器未启用回声消除，连续对话已停止。请启用回声消除或使用点击说话。');
      }
      stream.getTracks().forEach(t => t.onended = () => this.fail(Error('麦克风已断开。')));
      this.ctx = new AudioContext({sampleRate: 48000});
      await this.ctx.resume();
      if (token !== this.serial) return;
      if (this.ctx.sampleRate !== 48000) throw Error('实时模式需要支持 48 kHz 的 Edge 或 Chrome。');
      await this.ctx.audioWorklet.addModule('/denoise-worklet.js');
      if (token !== this.serial) return;
      const worker = this.worker = new Worker('/vad-worker.js');
      await new Promise((resolve, reject) => {
        const finish = e => { clearTimeout(this.loadTimer); this.cancelLoad = null; e ? reject(e) : resolve(); };
        this.cancelLoad = () => finish(new Error('监听已停止'));
        this.loadTimer = setTimeout(() => finish(Error('人声模型加载超时。')), 20000);
        worker.onerror = () => finish(Error('人声模型加载失败。'));
        worker.onmessage = ({data}) => finish(data.type === 'ready' ? null : Error('人声模型加载失败。'));
        worker.postMessage({type: 'init'});
      });
      if (token !== this.serial) return;
      worker.onerror = () => this.fail(Error('人声检测失败，持续监听已停止。'));
      worker.onmessage = ({data}) => {
        if (token !== this.serial) return;
        if (data.type === 'error') return this.fail(Error('人声检测失败，持续监听已停止。'));
        if (data.type === 'probability') this.detect(data.probability, data.at);
      };
      this.node = new AudioWorkletNode(this.ctx, 'denoise-recorder');
      this.input = this.ctx.createMediaStreamSource(stream);
      this.input.connect(this.node); this.node.connect(this.ctx.destination);
      this.node.onprocessorerror = () => this.fail(Error('音频处理失败，持续监听已停止。'));
      const firstFrame = new Promise((resolve, reject) => {
        this.captureReady = resolve;
        this.cancelCapture = () => reject(Error('麦克风采集已停止'));
        this.captureTimer = setTimeout(() => reject(Error('麦克风没有送来音频，请检查输入设备。')), 3000);
      });
      this.lastVad = performance.now();
      this.watchdog = setInterval(() => {
        if (performance.now() - this.lastVad > 3000) this.fail(Error('人声检测无响应，持续监听已停止。'));
      }, 1000);
      this.node.port.onmessage = ({data}) => {
        if (token !== this.serial) return;
        const now = performance.now();
        this.captureReady?.(); this.captureReady = null;
        this.cb.level(data.rawRms ?? data.rms);
        this.cb.capture?.({device: stream.getAudioTracks()[0]?.label || '当前麦克风', rawRms: data.rawRms ?? data.rms, rms: data.rms});
        this.ring.push(data.pcm); if (this.ring.length > 30) this.ring.shift(); // 600 ms pre-roll
        // Browser AEC is retained. Avoid suppressing quiet interruptions twice:
        // VAD sees pre-RNNoise samples; ASR still receives denoised PCM.
        const vadSamples = data.vadSamples ?? data.samples;
        worker.postMessage({samples: vadSamples, at: now}, [vadSamples.buffer]);
        const turn = this.turn;
        if (turn && !turn.finishing) {
          turn.endpointAudio.push(data.pcm);
          if (turn.endpointAudio.length > 400) turn.endpointAudio.shift();
          if (turn.ready) this.upload(data.pcm);
          else {
            this.queue.push(data.pcm);
            if (this.queue.length > 250) return this.fail(Error('识别连接过慢，请重新开始。'));
          }
          if (now - turn.started > 25000 || now - this.lastSpeech > endpointDelay(turn.transcript)) this.finish();
          else if (now - this.lastSpeech >= 180) this.predictEnd(turn);
        }
      };
      await firstFrame;
      clearTimeout(this.captureTimer); this.cancelCapture = null;
      if (token !== this.serial) return;
      this.cb.status('持续监听中 · 可以直接说话，播报时也可插话');
    } catch (e) { if (token === this.serial) this.fail(e); }
  }
  detect(probability, at) {
    this.lastVad = performance.now();
    if (this.lastVad - at > 300) { this.speechStart = null; return; }
    const speaking = this.cb.speaking?.() === true;
    // Hysteresis: one confident onset, then sustain moderate speech evidence.
    // Do not require every phoneme (including quiet ones) to exceed .85.
    const threshold = this.turn ? .35 : this.speechStart !== null ? .45 : speaking ? .65 : .7;
    if (probability >= threshold) {
      this.lastSpeech = at;
      if (this.speechStart === null) this.speechStart = at;
      if (!this.turn && at - this.speechStart >= (speaking ? 64 : 192)) this.begin();
    } else this.speechStart = null;
  }
  begin() {
    const turn = this.turn = {started: performance.now(), ready: false, finishing: false, endpointAudio: [...this.ring]};
    this.queue = [...this.ring]; this.ring = [];
    // Callback silences playback synchronously; cancellation acknowledgement gates new chat.
    turn.cancelled = Promise.resolve(this.cb.interrupt());
    turn.cancelled.catch(e => { if (this.turn === turn) this.fail(e); });
    const ws = turn.ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/asr`);
    ws.binaryType = 'arraybuffer';
    turn.timer = setTimeout(() => this.fail(Error('语音识别超时。')), 20000);
    ws.onopen = () => { if (this.turn === turn) ws.send(JSON.stringify({session_id: this.cb.session()})); };
    ws.onerror = () => { if (this.turn === turn) this.fail(Error('识别服务连接失败。')); };
    ws.onclose = () => { if (this.turn === turn) this.fail(Error('识别连接已断开。')); };
    ws.onmessage = async ({data: raw}) => {
      if (this.turn !== turn) return;
      try {
        const data = JSON.parse(raw);
        if (data.type === 'error') throw Error(data.code);
        if (data.type === 'ready') {
          clearTimeout(turn.timer); turn.ready = true;
          this.queue.forEach(pcm => this.upload(pcm)); this.queue = [];
          if (turn.finishing) this.finish();
        }
        if (data.type === 'transcript') { turn.transcript = data.text; this.cb.transcript(data.text); }
        if (data.type === 'done') {
          clearTimeout(turn.timer);
          await turn.cancelled;
          if (this.turn !== turn || !this.active) return;
          this.turn = null; ws.close(); this.speechStart = null; this.ring = [];
          this.cb.text(data.text, {lastSoundAt: turn.lastSoundAt, finishAt: turn.finishAt, doneAt: performance.now()});
        }
      } catch (e) { if (this.turn === turn) this.fail(e); }
    };
    this.cb.status('正在听你说话 · 停顿后自动发送');
  }
  async predictEnd(turn) {
    if (turn.endpointPending || !turn.transcript || endpointDelay(turn.transcript) >= 1200 ||
        turn.endpointAttempt === this.lastSpeech || this.endpointUnavailable) return;
    const speechAt = this.lastSpeech;
    const transcriptAt = turn.transcript;
    turn.endpointAttempt = speechAt;
    turn.endpointPending = true;
    const abort = turn.endpointAbort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 350);
    try {
      const response = await fetch('/api/endpoint', {method: 'POST',
        headers: {'Content-Type': 'application/octet-stream', 'X-Session-ID': this.cb.session()},
        body: new Blob(turn.endpointAudio, {type: 'application/octet-stream'}), signal: abort.signal});
      if (!response.ok) return;
      const result = await response.json();
      if (result.available === false) this.endpointUnavailable = true;
      // ASR can catch up while endpoint inference is in flight even without a
      // new VAD speech event. Never end on an obsolete partial transcript.
      if (turn.transcript !== transcriptAt) {
        turn.endpointAttempt = undefined;
        return;
      }
      if (this.active && this.turn === turn && !turn.finishing && speechAt === this.lastSpeech &&
          endpointDelay(turn.transcript) < 1200 && result.complete === true) this.finish();
    } catch (_) { /* Local model is optional; existing endpoint timer remains active. */ }
    finally { clearTimeout(timeout); turn.endpointPending = false; }
  }
  upload(pcm) {
    const ws = this.turn?.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > 256000) { this.fail(Error('上传音频积压。')); return; }
    ws.send(pcm);
  }
  finish() {
    const turn = this.turn;
    if (!turn || turn.sentFinish) return;
    if (!turn.finishing) { turn.finishAt = performance.now(); turn.lastSoundAt = this.lastSpeech; }
    turn.finishing = true;
    turn.endpointAbort?.abort();
    if (!turn.ready) return;
    turn.sentFinish = true;
    turn.ws.send(JSON.stringify({type: 'finish'}));
    clearTimeout(turn.timer);
    turn.timer = setTimeout(() => this.fail(Error('识别收尾超时。')), 20000);
    this.cb.status('正在确认识别结果 · 麦克风仍开启');
  }
  fail(e) { this.stop(); this.cb.error(e); }
  stop() {
    this.active = false; this.serial++;
    clearInterval(this.watchdog); clearTimeout(this.loadTimer); this.cancelLoad?.();
    clearTimeout(this.captureTimer); this.cancelCapture?.(); this.cancelCapture = null; this.captureReady = null;
    const turn = this.turn; this.turn = null;
    if (turn) { clearTimeout(turn.timer); turn.endpointAbort?.abort(); turn.ws.close(); }
    this.node?.port.postMessage('dispose'); this.node?.disconnect(); this.input?.disconnect();
    this.stream?.getTracks().forEach(t => t.stop()); this.stream = null;
    this.worker?.terminate(); this.ctx?.close().catch(() => {});
    this.node = this.input = this.worker = this.ctx = null;
    this.ring = []; this.queue = []; this.cb.level(0);
  }
}
