// Local Silero VAD adapter, 16 kHz, 512-sample frames + 64-sample context.
importScripts('/vendor/audio/ort.wasm.min.js');
let session, state, context = new Float32Array(64), pending = [], running = false, failed = false;
const sr = new ort.Tensor('int64', BigInt64Array.from([16000n]), []);
async function initialize() {
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = '/vendor/audio/';
  session = await ort.InferenceSession.create('/vendor/audio/silero_vad.onnx', {executionProviders: ['wasm']});
  state = new ort.Tensor('float32', new Float32Array(256), [2, 1, 128]);
  postMessage({type: 'ready'});
}
function fail() { failed = true; pending = []; postMessage({type: 'error'}); }
onmessage = ({data}) => {
  if (data.type === 'init') { initialize().catch(fail); return; }
  if (failed || !session) return;
  // Never accumulate unbounded inference work on slow machines.
  if (pending.length >= 16) { fail(); return; }
  pending.push(data);
  drain().catch(fail);
};
let buffer = [];
async function drain() {
  if (running) return;
  running = true;
  try {
    while (pending.length && !failed) {
      const {samples, at} = pending.shift();
      buffer.push(...samples);
      while (buffer.length >= 512) {
        const input = new Float32Array(576);
        input.set(context); input.set(buffer.splice(0, 512), 64);
        const tensor = new ort.Tensor('float32', input, [1, 576]);
        const result = await session.run({input: tensor, state, sr});
        state.dispose(); tensor.dispose();
        state = result[session.outputNames[1]];
        const probability = result[session.outputNames[0]].data[0];
        result[session.outputNames[0]].dispose();
        context.set(input.subarray(512));
        postMessage({type: 'probability', probability, at});
      }
    }
  } finally { running = false; }
}
