"""音频可播放性抽检（1 轮，**不计入** 100 轮延迟统计）。

流程：起隔离实例 → 建会话 → 一次 /api/chat → 取 /api/speech 的完整 PCM →
包成 WAV(24k/mono/s16) → 用 ffmpeg 实际解码 → 统计样本数与 RMS。
用法：python audio_spotcheck.py <输出目录>
"""
import asyncio
import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import aiohttp
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
BASE = 'http://127.0.0.1:8767'
FFMPEG = (Path.home() / '.workbuddy/binaries/python/envs/default/Lib/site-packages'
          / 'imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe')


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rid = 'spot-audio-001'
    async with aiohttp.ClientSession(headers={'Origin': BASE}) as s:
        for _ in range(40):
            try:
                if (await s.get(BASE + '/api/status')).status == 200:
                    break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
        voices = (await (await s.get(BASE + '/api/voices')).json())['voices']
        sid = (await (await s.post(BASE + '/api/session', json={'application_id': 'QUAL-001'})).json())['session_id']
        await s.post(BASE + '/api/identity', json={'session_id': sid, 'is_responsible': True})
        chat = await (await s.post(BASE + '/api/chat',
                                   json={'session_id': sid, 'text': '我需要补充什么材料？',
                                         'request_id': rid})).json()

        payload = {'session_id': sid, 'request_id': rid, 'voice_id': voices[0]['voice_id']}
        buf = bytearray()
        async with s.post(BASE + '/api/speech', json=payload) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
            async for chunk in r.content.iter_chunked(65536):
                buf.extend(chunk)

    pcm = bytes(buf)
    rate = int(headers.get('x-audio-sample-rate') or 24000)
    pcm_path = OUT / 'spotcheck-24k.pcm'
    pcm_path.write_bytes(pcm)

    wav_path = OUT / 'spotcheck-24k.wav'
    with wave.open(str(wav_path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)

    # 用 ffmpeg 真实解码，验证不是"长度对但内容坏"的包
    probe = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(wav_path), '-f', 'null', '-'],
                           capture_output=True, text=True, timeout=120)
    decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(wav_path),
                              '-f', 's16le', '-ac', '1', '-ar', str(rate), '-'],
                             capture_output=True, timeout=120)
    samples = np.frombuffer(decoded.stdout, dtype='<i2').astype(np.float32) / 32768
    rms = float(np.sqrt((samples ** 2).mean())) if samples.size else 0.0
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    silent_ratio = float((np.abs(samples) < 1e-4).mean()) if samples.size else 1.0

    report = {
        'note': '1 轮抽检，用于验证音频可播放性；不计入 100 轮延迟统计，不改变任何既有样本',
        'request_id': rid,
        'reply_text': chat.get('text'),
        'reply_chars': len(chat.get('text') or ''),
        'audio_cache': headers.get('x-audio-cache'),
        'tts_transport': headers.get('x-tts-transport'),
        'sample_rate': rate,
        'pcm_bytes': len(pcm),
        'pcm_even': len(pcm) % 2 == 0,
        'wav': str(wav_path),
        'ffmpeg_decode_exit': probe.returncode,
        'ffmpeg_stderr': probe.stderr.strip()[:300],
        'decoded_samples': int(samples.size),
        'decoded_seconds': round(samples.size / rate, 2),
        'rms': round(rms, 4),
        'peak': round(peak, 4),
        'near_silent_ratio': round(silent_ratio, 4),
        'playable': bool(probe.returncode == 0 and samples.size > 0 and rms > 0.01),
    }
    (OUT / 'audio-spotcheck.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
