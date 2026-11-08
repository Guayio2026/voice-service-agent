// AudioWorklet: requested 16 kHz mono, signed little-endian PCM, 20 ms frames.
class PCMRecorder extends AudioWorkletProcessor {
  constructor() { super(); this.samples = []; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) this.samples.push(value);
    while (this.samples.length >= 320) {
      const block = this.samples.splice(0, 320), data = new ArrayBuffer(640), view = new DataView(data);
      let energy = 0;
      block.forEach((value, i) => { const x = Math.max(-1, Math.min(1, value)); view.setInt16(i * 2, x < 0 ? x * 32768 : x * 32767, true); energy += x * x; });
      this.port.postMessage({pcm: data, rms: Math.sqrt(energy / 320)}, [data]);
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
