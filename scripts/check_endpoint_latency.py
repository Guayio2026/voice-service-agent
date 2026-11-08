"""Local inference smoke benchmark; not a turn-taking accuracy evaluation."""
import json
import sys
import wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from turn_endpoint import load_endpoint
root = Path(__file__).resolve().parents[1]
model = load_endpoint(root)
if model is None:
    raise SystemExit('Smart Turn not available')
with wave.open(str(root/'runtime/qa/live-reply.wav'), 'rb') as wav:
    if wav.getsampwidth() != 2:
        raise SystemExit('Need 16-bit PCM fixture')
    rate, channels = wav.getframerate(), wav.getnchannels()
    audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32)
    audio = audio.reshape(-1, channels).mean(axis=1)
audio = np.interp(np.arange(int(len(audio)*16000/rate))*rate/16000, np.arange(len(audio)), audio).astype('<i2')
results = [model.predict(audio.tobytes()) for _ in range(5)]
report = {'scope':'existing synthetic TTS WAV; no human endpoint labels; inference smoke only', 'runs':results}
(root/'runtime/endpoint-latency.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
