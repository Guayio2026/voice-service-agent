// Project adapter; upstream RNNoise is distributed unchanged in vendor/audio.
import createRNNoise from './vendor/audio/rnnoise-sync.js';
const rn = createRNNoise();

// 63-tap low-pass FIR before 48 -> 16 kHz decimation; do not just drop samples.
const taps = Float64Array.from({length: 63}, (_, i) => {
  const x = i - 31, cutoff = 7200 / 48000;
  return (x ? Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x) : 2 * cutoff)
    * (.54 - .46 * Math.cos(2 * Math.PI * i / 62));
});
const total = taps.reduce((a, b) => a + b, 0);
for (let i = 0; i < taps.length; i++) taps[i] /= total;

class DenoiseRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    if (sampleRate !== 48000) throw Error('RNNoise requires 48000 Hz');
    this.state = rn._rnnoise_create(0);
    this.ptr = rn._malloc(480 * 4);
    this.frame = new Float32Array(480); this.count = 0;
    this.vadHistory = new Float32Array(63); this.vadOutput = new Float32Array(320);
    this.history = new Float32Array(63); this.head = 0; this.decimate = 0;
    this.output = new Float32Array(320); this.outCount = 0;
    this.rawEnergy = 0; this.rawCount = 0; this.closed = false;
    this.port.onmessage = ({data}) => { if (data === 'dispose') this.dispose(); };
  }
  dispose() {
    if (this.closed) return;
    this.closed = true; rn._rnnoise_destroy(this.state); rn._free(this.ptr);
  }
  process(inputs) {
    if (this.closed) return false;
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const x of input) {
      this.frame[this.count++] = x * 32768;
      this.rawEnergy += x * x; this.rawCount++;
      if (this.count !== 480) continue;
      rn.HEAPF32.set(this.frame, this.ptr / 4);
      rn._rnnoise_process_frame(this.state, this.ptr, this.ptr);
      for (let i = 0; i < 480; i++) {
        this.vadHistory[this.head] = this.frame[i] / 32768;
        this.history[this.head] = rn.HEAPF32[this.ptr / 4 + i] / 32768;
        this.head = (this.head + 1) % 63;
        if (++this.decimate !== 3) continue;
        this.decimate = 0;
        let value = 0, vadValue = 0;
        for (let j = 0; j < 63; j++) value += taps[j] * this.history[(this.head + 62 - j) % 63];
        for (let j = 0; j < 63; j++) vadValue += taps[j] * this.vadHistory[(this.head + 62 - j) % 63];
        this.vadOutput[this.outCount] = Math.max(-1, Math.min(1, vadValue));
        this.output[this.outCount++] = Math.max(-1, Math.min(1, value));
        if (this.outCount !== 320) continue;
        const samples = this.output, pcm = new ArrayBuffer(640), view = new DataView(pcm);
        let energy = 0;
        samples.forEach((v, k) => { energy += v * v; view.setInt16(k * 2, v < 0 ? v * 32768 : v * 32767, true); });
        const vadSamples = this.vadOutput;
        this.port.postMessage({pcm, samples, vadSamples, rms: Math.sqrt(energy / 320),
          rawRms: Math.sqrt(this.rawEnergy / this.rawCount)}, [pcm, samples.buffer, vadSamples.buffer]);
        this.vadOutput = new Float32Array(320);
        this.output = new Float32Array(320); this.outCount = 0; this.rawEnergy = 0; this.rawCount = 0;
      }
      this.count = 0;
    }
    // Audio goes to ASR only; leave speaker output silent to avoid feedback.
    return true;
  }
}
registerProcessor('denoise-recorder', DenoiseRecorder);
