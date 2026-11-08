import {RealtimeVoice} from './realtime-voice.js';
const $ = id => document.getElementById(id);
const audioPreferenceKey = 'voice.audio-devices.v1';
let audioPreferences = {};
try { audioPreferences = JSON.parse(localStorage.getItem(audioPreferenceKey) || '{}') || {}; } catch {}
function rememberAudioDevice(key, select) {
  audioPreferences[key] = {id: select.value, label: select.selectedOptions[0]?.textContent || ''};
  try { localStorage.setItem(audioPreferenceKey, JSON.stringify(audioPreferences)); }
  catch { error(Error('浏览器禁止保存设置，本次选择仅在当前页面有效。')); }
}
function restoreAudioDevice(key, select) {
  const saved = audioPreferences[key];
  if (!saved || typeof saved.id !== 'string') return;
  let option = [...select.options].find(o => o.value === saved.id);
  // Device IDs may change after permission changes; only accept a unique exact label.
  if (!option && saved.label) {
    const matches = [...select.options].filter(o => o.value && o.textContent === saved.label);
    if (matches.length === 1) option = matches[0];
  }
  if (option) select.value = option.value;
  else {
    select.add(new Option('未找到已保存设备，请重新选择：' + (saved.label || saved.id), saved.id));
    select.value = saved.id;
  }
}

let session = '', busy = false, recording = false, epoch = 0, socket, media, mic, source;
let context, controller, recordTimer, finalTimer, workletReady = false;
let playback = new Set(), audioUntil = 0, voiceList = [];
let recordingTiming = null;
let captureContext, vadWorker, cancelVadLoad;
const END_SILENCE_MS = 900;
let generation = 0, cancellation = Promise.resolve();
let lastCaptureDisplay = 0;
const realtime = new RealtimeVoice({
  session: () => session,
  speaking: () => playback.size > 0,
  level: value => { $('mic-level').value = Math.min(1, value * 10); },
  capture: ({device, rawRms, rms}) => {
    if (performance.now() - lastCaptureDisplay < 250) return;
    lastCaptureDisplay = performance.now();
    $('mic-status').textContent = `${device} · 持续采集中 · ${rawRms > .001 ? '有声音输入' : '输入很轻或静音，请检查麦克风'} · 降噪后${rms > .001 ? '有声音' : '较轻'}`;
    $('enhance-status').textContent = '本地降噪与人声检测运行中，开场播报也接受插话。';
  },
  status: text => { $('realtime-status').textContent = text; },
  transcript: text => { $('live-transcript').textContent = text; },
  interrupt: () => {
    const hadReply = busy;
    epoch++; controller?.abort(); stopPlayback(); busy = true; controls();
    if (!hadReply) return cancellation;
    phase('已检测到插话，正在听你说话');
    cancellation = api('interrupt', {session_id: session}).then(data => { generation = data.generation; });
    return cancellation;
  },
  text: (text, timing) => {
    $('realtime-status').textContent = '持续监听中 · 可以直接说话，播报时也可插话';
    busy = false; controls();
    $('live-transcript').textContent = text;
    if (text.trim()) send(text, timing);
    else phase('没有听清，请继续说话');
  },
  error: e => { interrupt(); error(e); }
});

const errorMessages = {
  EVIDENCE_CHANGED: '业务依据已更新，已停止旧回复播报，请重新提问核实。',
  UNSAFE_SPEECH_BLOCKED: '这段回复未通过播报前检查，已阻止语音合成，请重新提问。',
  BREEZE_KEY_MISSING: '后端缺少 BreezeBlue 密钥，请填写本地 .env 后重启。',
  BREEZE_HTTP_401: 'BreezeBlue 密钥无效或已失效，请更新本地 .env 后重启。',
  BREEZE_HTTP_402: 'BreezeBlue 积分或该密钥的额度不足。',
  BREEZE_HTTP_403: '当前账号没有使用这个音色的权限。',
  BREEZE_HTTP_429: 'BreezeBlue 正在限流，请稍后再试。',
  PROVIDER_HTTP_401: '百炼鉴权失败，请核对密钥、地域和业务空间 Base URL。',
  INVALID_BASE_URL: '请在 .env 配置百炼控制台提供的 Base URL。',
  ASR_CONNECTION_FAILED: '识别服务连接失败，请核对百炼权限、地域和网络。',
  ASR_START_FAILED: '识别任务未启动，请检查百炼模型权限或余额。',
  SPEECH_ALREADY_REQUESTED: '这一轮已经请求过语音，未重复扣费请求。',
  SELECT_AVAILABLE_VOICE: '请先选择账号下可用的客服音色。',
  TURN_OUTCOME_UNKNOWN_NO_AUTORETRY: '上次调用结果未知，程序没有自动重试。请重新发送一轮。',
  SESSION_EXPIRED: '会话已过期，请创建新会话。',
  SESSION_NOT_FOUND: '会话已失效，后端可能已重启，请重新创建测试会话。',
  API_VERSION_MISMATCH: '当前后端没有这个接口。请重启语音服务，再刷新页面并创建测试会话。',
  INVALID_API_RESPONSE: '服务返回了无法读取的响应，请检查后端运行状态。'
};
function error(e) { $('voice-error').hidden = false; $('voice-error').textContent = errorMessages[e.message] || e.message || '操作失败，请检查连接后重试。'; }
function clearError() { $('voice-error').hidden = true; }
function phase(text) { $('phase').textContent = text; }
function controls() {
  for (const id of ['new-session', 'voice-case', 'voice-source', 'refresh-voices']) $(id).disabled = busy;
  for (const id of ['verify-yes', 'verify-no', 'message', 'send', 'record']) $(id).disabled = busy || !session;
  $('voice-select').disabled = busy || !voiceList.length;
  $('speak-replies').disabled = busy;
  $('finish-record').disabled = !recording;
  $('interrupt').disabled = !busy;
  $('mic-select').disabled = busy;
  if ($('phone-output')) $('phone-output').disabled = busy || realtime.active;
  $('auto-finish').disabled = busy;
  $('enhance-audio').disabled = busy;
  $('start-realtime').disabled = busy || !session || realtime.active;
  $('interrupt').disabled = !busy && !realtime.active;
  if (realtime.active) {
    for (const id of ['new-session','voice-case','voice-source','refresh-voices','verify-yes','verify-no','record','mic-select','enhance-audio','auto-finish','message','send']) $(id).disabled = true;
  }
  $('finish-record').disabled = !recording && !realtime.turn;

}
async function responseJson(response) {
  const raw = await response.text();
  let data;
  try { data = JSON.parse(raw); }
  catch { throw Error([404, 405].includes(response.status) ? 'API_VERSION_MISMATCH' : 'INVALID_API_RESPONSE'); }
  if (!response.ok) throw Error(data.error?.code || `请求失败（${response.status}）`);
  return data;
}
async function api(path, body, signal) {
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
  const timeout = new AbortController(), timer = setTimeout(() => timeout.abort(), 40000);
  const cancel = () => timeout.abort();
  signal?.addEventListener('abort', cancel, {once: true});
  try {
    const response = await fetch('/api/' + path, {method: body ? 'POST' : 'GET',
      headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined,
      signal: timeout.signal});
    return await responseJson(response);
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}
function append(role, text) {
  $('conversation').querySelector('.voice-empty')?.remove();
  const row = document.createElement('div'), label = document.createElement('strong'), content = document.createElement('p');
  row.className = 'voice-message'; label.textContent = role; content.textContent = text; row.append(label, content);
  $('conversation').append(row); row.scrollIntoView({block: 'nearest'});
  return content;
}
async function audioContext() {
  if (!context || context.state === 'closed') { context = new AudioContext({sampleRate: 16000}); workletReady = false; }
  const sink = document.getElementById('phone-output')?.value || '';
  if (sink) {
    if (!context.setSinkId) throw Error('当前浏览器不支持选择播报输出，请使用新版 Edge 或 Chrome。');
    await context.setSinkId(sink);
  } else if (context.setSinkId && context.sinkId) await context.setSinkId('');
  await context.resume();
  return context;
}
function stopMic() {
  clearTimeout(recordTimer); recording = false;
  if (mic) { mic.port.postMessage('dispose'); mic.port.onmessage = null; mic.disconnect(); mic = null; }
  source?.disconnect(); source = null;
  media?.getTracks().forEach(t => t.stop()); media = null;
  cancelVadLoad?.(); cancelVadLoad = null;
  vadWorker?.terminate(); vadWorker = null;
  captureContext?.close().catch(() => {}); captureContext = null;
  $('mic-level').value = 0;
}
async function prepareVad(worker) {
  await new Promise((resolve, reject) => {
    const finish = fn => { clearTimeout(timer); cancelVadLoad = null; worker.onmessage = null; worker.onerror = null; fn(); };
    const timer = setTimeout(() => finish(() => reject(Error('本地人声模型加载超时。请关闭增强功能重试，或重启新版后端。'))), 20000);
    cancelVadLoad = () => finish(() => reject(new DOMException('Cancelled', 'AbortError')));
    worker.onmessage = ({data}) => finish(() => data.type === 'ready' ? resolve() : reject(Error('本地人声模型加载失败，请重启新版后端，或关闭增强功能使用基础模式。')));
    worker.onerror = () => finish(() => reject(Error('本地人声检测组件无法加载，请重启新版后端或关闭增强功能重试。')));
    worker.postMessage({type: 'init'});
  });
}
async function loadMicrophones() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const selected = $('mic-select').value;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
  $('mic-select').replaceChildren(new Option('系统默认麦克风', ''));
  devices.filter(d => d.deviceId && d.deviceId !== 'default').forEach((d, i) =>
    $('mic-select').add(new Option(d.label || `麦克风 ${i + 1}（授权后显示名称）`, d.deviceId)));
  if ([...$('mic-select').options].some(o => o.value === selected)) $('mic-select').value = selected;
  restoreAudioDevice('input', $('mic-select'));
}
function stopPlayback() {
  for (const node of playback) { try { node.stop(); } catch {} }
  playback.clear(); audioUntil = 0;
}
function interrupt() {
  realtime.stop();
  $('realtime-status').textContent = '持续监听已关闭';
  if (session) cancellation = cancellation.catch(() => {}).then(() => api('interrupt', {session_id: session})).then(data => { generation = data.generation; }).catch(e => { error(e); throw e; });
  cancellation.catch(() => {});
  epoch++; controller?.abort(); stopMic(); clearTimeout(finalTimer);
  socket?.close(); socket = null; stopPlayback(); busy = false; controls(); phase('已停止');
  $('live-transcript').textContent = '已停止本轮。已发送到云端的生成请求可能仍会计费。';
}
function voiceDetail() {
  const voice = voiceList.find(v => v.voice_id === $('voice-select').value);
  $('voice-detail').textContent = voice ? `${voice.origin === 'cloned' ? '克隆音色' : '设计音色'} · ${voice.voice_id}` : '未找到音色。请在平台保存到“我的音色”后刷新，或切换到平台音色。';
}
async function loadVoices() {
  const selected = $('voice-select').value;
  voiceList = []; $('voice-select').replaceChildren(); controls();
  const result = await api('voices?type=' + $('voice-source').value);
  voiceList = result.voices; $('voice-select').replaceChildren();
  for (const v of voiceList) { const o = document.createElement('option'); o.value = v.voice_id; o.textContent = v.name + (v.origin === 'cloned' ? ' · 克隆' : ' · 设计'); $('voice-select').append(o); }
  if (voiceList.some(v => v.voice_id === selected)) $('voice-select').value = selected;
  voiceDetail(); controls();
}
async function playReply(turn, current, timing) {
  const ctx = await audioContext();
  if (current !== epoch) return;
  phase('正在合成声音');
  const started = performance.now();
  const response = await fetch('/api/speech', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: session, request_id: turn.request_id, generation, stream_reply: true, voice_id: $('voice-select').value}), signal: controller.signal});
  if (!response.ok) await responseJson(response);
  const reader = response.body.getReader();
  const duplex = response.headers.get('Content-Type')?.includes('application/x-ndjson');
  if (duplex && turn.spokenElement) turn.spokenElement.textContent = '';
  async function* audioChunks() {
    if (!duplex) {
      if (turn.spokenElement) turn.spokenElement.textContent = turn.text;
      while (true) { const next = await reader.read(); if (next.done) return; yield next.value; }
    }
    const decoder = new TextDecoder(); let pending = '', finished = false;
    while (true) {
      const next = await reader.read();
      if (next.done) { if (!finished || pending.trim()) throw Error('流式语音未完成。'); return; }
      pending += decoder.decode(next.value, {stream:true});
      if (pending.length > 2000000) throw Error('语音数据超限。');
      let end;
      while ((end = pending.indexOf('\n')) >= 0) {
        const event = JSON.parse(pending.slice(0,end)); pending = pending.slice(end+1);
        if (current !== epoch) return;
        if (event.type === 'text' && turn.spokenElement) turn.spokenElement.textContent += event.text;
        if (event.type === 'error') throw Error(event.message || '流式语音生成停止。');
        if (event.type === 'audio') yield Uint8Array.from(atob(event.pcm), c=>c.charCodeAt(0));
        if (event.type === 'done') {
          finished = true;
          const m = event.metrics;
          $('metrics').textContent += `；口语首句音频 ${m.first_pcm_ms} ms / LLM完成 ${m.llm_done_ms} ms`;
        }
      }
    }
  }
  let carry = new Uint8Array(0), first = true, total = 0;
  try {
    for await (const value of audioChunks()) {
      if (current !== epoch) break;
      const bytes = new Uint8Array(carry.length + value.length); bytes.set(carry); bytes.set(value, carry.length);
      const count = bytes.length - bytes.length % 2; carry = bytes.slice(count);
      if (!count) continue;
      const buffer = ctx.createBuffer(1, count / 2, 24000), samples = buffer.getChannelData(0), view = new DataView(bytes.buffer);
      for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      if (current !== epoch) break;
      const node = ctx.createBufferSource(); node.buffer = buffer; node.connect(ctx.destination);
      audioUntil = Math.max(ctx.currentTime + 0.04, audioUntil);
      const scheduledAt = performance.now() + (audioUntil - ctx.currentTime) * 1000;
      node.start(audioUntil); audioUntil += buffer.duration;
      playback.add(node); node.onended = () => { playback.delete(node); node.disconnect(); };
      total += count;
      if (first) {
        first = false; phase('客服正在说话');
        $('metrics').textContent += `；语音首包 ${Math.round(performance.now() - started)} ms`;
        if (timing?.lastSoundAt != null) $('metrics').textContent += `；末次声音到首段排定播放约 ${Math.round(scheduledAt - timing.lastSoundAt)} ms（本地估算，非官方指标）`;
      }
      while (audioUntil - ctx.currentTime > 3 && current === epoch) await new Promise(r => setTimeout(r, 60));
    }
    if (current === epoch && (!total || carry.length)) throw Error('语音数据不完整，回复文字已保留。');
    while (playback.size && current === epoch) await new Promise(r => setTimeout(r, 60));
  } catch (e) { stopPlayback(); await reader.cancel().catch(() => {}); throw e;
  } finally { if (current !== epoch) await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
async function send(text, timing = null, opening = false) {
  if (busy || !session || !text.trim()) return;
  clearError(); busy = true; const current = ++epoch; controller = new AbortController(); controls();
  if (!opening) append('你', text); $('message').value = ''; phase('客服正在思考');
  try {
    await cancellation;
    if ($('speak-replies').checked) await audioContext();
    if (current !== epoch) return;
    const result = await api('agent/chat', {session_id: session, text, generation, request_id: crypto.randomUUID()}, controller.signal);
    if (current !== epoch) return;
    result.spokenElement = append('AI 客服', result.text);
    if (result.stream_reply_eligible && $('speak-replies').checked) {
      const details = document.createElement('details'), summary = document.createElement('summary'), source = document.createElement('p');
      summary.textContent = '查看本轮业务依据'; source.textContent = result.text;
      details.append(summary, source); result.spokenElement.parentElement.append(details);
    }
    if (result.grounding?.checks?.length) {
      const details = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = result.grounding.status === 'supported' ? '查看回答依据（合成资料）' : '查看依据核实情况';
      details.append(summary);
      for (const citation of result.grounding.citations || []) {
        const line = document.createElement('p');
        line.textContent = `${citation.id} · ${citation.version} · ${citation.source_title}：${citation.reason}；${citation.action}`;
        details.append(line);
      }
      const unresolved = result.grounding.checks.filter(item => item.status !== 'valid');
      if (unresolved.length) {
        const line = document.createElement('p');
        line.textContent = `${unresolved.length} 项依据尚未核实，不能据此确认全部完成。`;
        details.append(line);
      }
      $('conversation').lastElementChild.append(details);
    }
    if (result.agent) { $('identity-state').textContent = 'Agent步骤：' + result.agent.step; }
    $('metrics').textContent = `整段文字回复 ${result.reply_ms} ms；输入 ${result.usage.prompt_tokens || 0} / 输出 ${result.usage.completion_tokens || 0} Token`;
    if (timing?.finishAt != null) {
      const endpoint = timing.lastSoundAt == null ? '' : `停顿/收尾 ${Math.round(timing.finishAt - timing.lastSoundAt)} ms；`;
      $('metrics').textContent = `${endpoint}识别收尾 ${Math.round(timing.doneAt - timing.finishAt)} ms；` + $('metrics').textContent;
    }
    if ($('speak-replies').checked) {
      if (!$('voice-select').value) throw Error('没有可用音色，已保留文字回复。请刷新音色列表。');
      await playReply(result, current, timing);
    }
    if (current === epoch) phase('等待你继续');
    return current === epoch;
  } catch (e) { if (current === epoch) { stopPlayback(); error(e.name === 'AbortError' ? Error('请求超时，未自动重试。') : e); phase('本轮未完成'); } }
  finally { if (current === epoch) { busy = false; controls(); } }
}
function finishRecord() {
  if (realtime.active) { realtime.finish(); return; }
  if (!recording) return;
  if (recordingTiming) recordingTiming.finishAt = performance.now();
  stopMic(); controls(); phase('正在确认识别结果');
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'finish'}));
  finalTimer = setTimeout(() => { interrupt(); error(Error('识别结果等待超时，请重新说话。')); }, 20000);
}
async function record() {
  if (busy || !session) return;
  clearError(); busy = true; const current = ++epoch; controls(); phase('正在连接麦克风');
  recordingTiming = {lastSoundAt: null, finishAt: null, doneAt: null};
  const timing = recordingTiming;
  $('live-transcript').textContent = '';
  $('mic-status').textContent = '正在连接，请等“正在听你说话”出现后再开口。';
  try {
    const enhanced = $('enhance-audio').checked;
    const ctx = enhanced ? (captureContext = new AudioContext({sampleRate: 48000})) : await audioContext();
    await ctx.resume();
    if (current !== epoch) return;
    if (ctx.sampleRate !== (enhanced ? 48000 : 16000)) throw Error('当前浏览器不支持所需采样率，请使用新版 Edge 或 Chrome。');
    if (enhanced) {
      phase('正在加载本地降噪'); $('enhance-status').textContent = '正在加载本地降噪与人声模型，加载完成后才连接云端识别。';
      await ctx.audioWorklet.addModule('/denoise-worklet.js');
      if (current !== epoch) return;
      vadWorker = new Worker('/vad-worker.js');
      await prepareVad(vadWorker);
      if (current !== epoch) return;
      $('enhance-status').textContent = 'RNNoise 降噪 + Silero 人声检测已加载，均在本机运行。';
    } else $('enhance-status').textContent = '基础模式：使用浏览器降噪，未启用本地增强模型。';
    const deviceId = $('mic-select').value;
    const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: !enhanced, autoGainControl: true,
      ...(deviceId ? {deviceId: {exact: deviceId}} : {})}});
    if (current !== epoch) { stream.getTracks().forEach(t => t.stop()); return; }
    media = stream;
    const micName = stream.getAudioTracks()[0]?.label || '默认麦克风';
    await loadMicrophones().catch(() => {});
    if (!enhanced && !workletReady) { await ctx.audioWorklet.addModule('/mic-worklet.js'); workletReady = true; }
    if (current !== epoch) return;
    socket = new WebSocket(`ws://${location.host}/api/asr`); socket.binaryType = 'arraybuffer';
    const live = socket;
    let receivedDone = false, startedAt = performance.now(), lastVoice = startedAt;
    let hasTranscript = false, frames = 0, peak = 0, lastMeter = 0;
    let vadHealthy = true, vadAt = 0, speechFrames = 0, sawSpeech = false;
    if (enhanced) {
      const failVad = () => {
        vadHealthy = false;
        $('enhance-status').textContent = '人声检测运行失败，本轮保留降噪并关闭自动发送；请点“说完了”提交。';
        vadWorker?.terminate(); vadWorker = null;
      };
      vadWorker.onerror = failVad;
      vadWorker.onmessage = ({data}) => {
        if (current !== epoch || !recording) return;
        if (data.type === 'error') { failVad(); return; }
        if (data.type !== 'probability') return;
        vadAt = data.at;
        if (data.probability >= (sawSpeech ? .35 : .5)) {
          speechFrames++;
          if (speechFrames >= 3 || sawSpeech) { sawSpeech = true; lastVoice = data.at; timing.lastSoundAt = data.at; }
        } else speechFrames = 0;
      };
    }
    const finals = new Map();
    recordTimer = setTimeout(() => { if (current === epoch) { interrupt(); error(Error('识别连接超时，请检查配置。')); } }, 20000);
    live.onopen = () => live.send(JSON.stringify({session_id: session}));
    live.onerror = () => { if (current === epoch) { interrupt(); error(Error('ASR_CONNECTION_FAILED')); } };
    live.onclose = () => { if (!receivedDone && current === epoch) { interrupt(); error(Error('识别连接已断开，请重新开始。')); } };
    live.onmessage = async event => {
      if (current !== epoch) return;
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'error') throw Error(data.code);
        if (data.type === 'ready') {
          clearTimeout(recordTimer); startedAt = performance.now(); lastVoice = startedAt;
          source = ctx.createMediaStreamSource(media); mic = new AudioWorkletNode(ctx, enhanced ? 'denoise-recorder' : 'pcm-recorder');
          mic.onprocessorerror = () => { if (current === epoch) { interrupt(); error(Error('录音处理失败，请关闭增强功能后重试。')); } };
          source.connect(mic); mic.connect(ctx.destination); recording = true; controls(); phase('正在听你说话');
          $('mic-status').textContent = `正在使用：${micName}。请说话，观察音量条是否变化。`;
          mic.port.onmessage = ({data}) => {
            if (!recording || current !== epoch) return;
            const now = performance.now();
            frames++; peak = Math.max(peak, data.rawRms ?? data.rms);
            if (enhanced && vadHealthy && vadWorker) vadWorker.postMessage({samples: data.samples, at: now}, [data.samples.buffer]);
            if (!enhanced && data.rms > 0.008) { lastVoice = now; timing.lastSoundAt = now; }
            if (now - lastMeter > 150) {
              lastMeter = now; $('mic-level').value = Math.min(1, data.rms * 10);
              $('mic-status').textContent = `${micName} · 已采集 ${(frames * .02).toFixed(1)} 秒 · ${data.rms > .001 ? '有声音输入' : '当前声音很轻或静音'}`;
            }
            if (live.bufferedAmount > 256000) { interrupt(); error(Error('上传音频积压，请检查网络后重试。')); return; }
            live.send(data.pcm);
            // Noise alone must not end the turn before the first recognized word.
            const endpointReady = !enhanced || (vadHealthy && sawSpeech && now - vadAt < 300);
            if (($('auto-finish').checked && hasTranscript && endpointReady && now - lastVoice > END_SILENCE_MS) || now - startedAt > 25000) finishRecord();
          };
          recordTimer = setTimeout(() => { if (recording) finishRecord(); }, 26000);
        }
        if (data.type === 'transcript') {
          // Provider/network delay is not new microphone activity.
          if (data.text.trim()) hasTranscript = true;
          if (data.final) finals.set(data.sentence_id, data.text);
          $('live-transcript').textContent = [...finals.values()].join('') + (data.final ? '' : data.text);
        }
        if (data.type === 'done') {
          timing.doneAt = performance.now();
          receivedDone = true; clearTimeout(finalTimer); stopMic(); live.close(); busy = false; controls();
          $('live-transcript').textContent = data.text;
          if (data.text.trim()) await send(data.text, timing);
          else {
            phase('没有听清');
            const reason = !frames ? '浏览器没有采集到音频，请重新开始或刷新页面。'
              : peak < .001 ? '麦克风输入接近静音。请选择正确的麦克风，并检查耳机静音键和 Windows 输入音量。'
              : '已采集到声音，但识别服务未返回文字。请等“正在听你说话”后说完整一句，再点“说完了”；也可取消自动发送后重试。';
            error(Error(reason));
          }
        }
      } catch (e) { if (current === epoch) { interrupt(); error(e); } }
    };
  } catch (e) { if (current === epoch) { interrupt();
    const messages = {NotAllowedError: '麦克风权限未开启，请在浏览器地址栏允许麦克风。', NotFoundError: '没有找到麦克风，请连接麦克风后重试。', NotReadableError: '麦克风无法读取，请检查设备是否被其他软件独占。', OverconstrainedError: '所选麦克风已不可用，请切换到系统默认麦克风。'};
    error(messages[e.name] ? Error(messages[e.name]) : e);
  } }
}
async function action(fn) { if (busy) return; busy = true; controls(); clearError(); try { await cancellation; await fn(); } catch (e) { error(e); } finally { busy = false; controls(); } }
$('new-session').onclick = async () => {
  if (busy) return;
  // Unlock playback directly within the user's click, before network waits.
  $('speak-replies').checked = true;
  const playbackReady = audioContext();
  playbackReady.catch(() => {});
  let created = false;
  await action(async () => {
  await playbackReady;
  await loadVoices();
  if (!$('voice-select').value) throw Error('账号暂无可用音色，请选择音色后开始通话。');
  const data = await api('session', {application_id: $('voice-case').value}); session = data.session_id; generation = 0; cancellation = Promise.resolve();
  $('conversation').replaceChildren(); $('live-transcript').textContent = ''; $('identity-state').textContent = '身份未确认';
  $('metrics').textContent = '尚无调用记录'; phase('小语准备开场');
  created = true;
  });
  if (created) {
    await startRealtime();
    if (!realtime.active) return;
    await send('开始通话', null, true);
  }
};
for (const [id, value] of [['verify-yes', true], ['verify-no', false]]) $(id).onclick = () => action(async () => {
  await api('identity', {session_id: session, is_responsible: value}); $('conversation').replaceChildren();
  $('live-transcript').textContent = ''; $('identity-state').textContent = value ? '已模拟确认负责人身份' : '身份不符，业务资料已锁定'; phase(value ? '可以开始对话' : '身份不符');
});
$('voice-case').onchange = () => { session = ''; $('conversation').replaceChildren(); $('live-transcript').textContent = ''; $('identity-state').textContent = '案例已切换，请创建会话'; controls(); };
$('voice-source').onchange = () => action(loadVoices);
$('refresh-voices').onclick = () => action(loadVoices);
$('voice-select').onchange = voiceDetail;
$('chat-form').onsubmit = event => { event.preventDefault(); send($('message').value); };
async function startRealtime() {
  if (busy || !session || realtime.active) return;
  clearError();
  $('realtime-status').textContent = '正在开启麦克风与本地人声检测…';
  const opening = ++epoch;
  busy = true; controls();
  try { await cancellation; if (opening !== epoch) return; busy = false; const starting = realtime.start($('mic-select').value); controls(); await starting; }
  catch (e) { error(e); }
  finally { if (opening === epoch) busy = false; controls(); }
}
$('start-realtime').onclick = startRealtime;
$('mic-select').addEventListener('change', () => rememberAudioDevice('input', $('mic-select')));
$('record').onclick = record; $('finish-record').onclick = finishRecord; $('interrupt').onclick = interrupt;
window.addEventListener('pagehide', interrupt);
window.addEventListener('workspace-mode-change', event => { if (event.detail !== 'voice' && (busy || realtime.active)) interrupt(); });
loadMicrophones().catch(() => {});
navigator.mediaDevices?.addEventListener('devicechange', () => { if (!busy) loadMicrophones().catch(() => {}); });
try {
  const data = await api('status');
  $('provider-state').textContent = Object.entries(data.configured).map(([k,v]) => `${k.toUpperCase()}：${v ? '已配置' : '缺少配置'}`).join('；');
  if (data.models?.llm) $('provider-state').textContent += `；文字模型：${data.text_provider || ''} / ${data.models.llm}`;
  for (const c of data.cases) {
    const o = document.createElement('option');
    o.value = c.application_id;
    o.textContent = c.label;
    $('voice-case').append(o);
  }
  await loadVoices();
  if (voiceList.some(v => v.voice_id === data.default_voice_id)) { $('voice-select').value = data.default_voice_id; voiceDetail(); }
} catch (e) { error(e); }
controls();

// Phone Link supplies the telephone connection; audio routing remains explicit.
const phonePanel = document.createElement('details');
const phoneSummary = document.createElement('summary');
phoneSummary.textContent = '手机电话音频接入';
const phoneHelp = document.createElement('p');
phoneHelp.textContent = '先在手机连接中拨号并接通。需要两条独立虚拟音频通道：电话声音输出到 A，页面输入选择 A 的录音端；AI 播报输出到 B，手机连接的麦克风选择 B 的录音端。不要将 A、B 混为同一条通道。停止对话不会挂断手机电话。';
const phoneLabel = document.createElement('label');
phoneLabel.htmlFor = 'phone-output'; phoneLabel.textContent = 'AI 播报输出（送往电话）';
const phoneOutput = document.createElement('select'); phoneOutput.id = 'phone-output';
phoneOutput.add(new Option('系统默认输出', ''));
const phoneRefresh = document.createElement('button'); phoneRefresh.type = 'button'; phoneRefresh.textContent = '刷新音频设备';
async function refreshPhoneDevices() {
  const selected = phoneOutput.value;
  const devices = await navigator.mediaDevices.enumerateDevices();
  phoneOutput.replaceChildren(new Option('系统默认输出', ''));
  for (const d of devices.filter(d => d.kind === 'audiooutput' && d.deviceId !== 'default')) {
    phoneOutput.add(new Option(d.label || '音频输出（授权后显示名称）', d.deviceId));
  }
  if ([...phoneOutput.options].some(o => o.value === selected)) phoneOutput.value = selected;
  else if (selected) { interrupt(); error(Error('电话输出设备已断开，请重新选择后再开始。')); }
  restoreAudioDevice('output', phoneOutput);
  await loadMicrophones();
}
phoneOutput.addEventListener('change', () => rememberAudioDevice('output', phoneOutput));
phoneRefresh.onclick = () => refreshPhoneDevices().catch(error);
phonePanel.append(phoneSummary, phoneHelp, phoneLabel, phoneOutput, phoneRefresh);
$('mic-select').parentElement.after(phonePanel);
navigator.mediaDevices?.addEventListener('devicechange', () => refreshPhoneDevices().catch(error));
refreshPhoneDevices().catch(error);
