"""可行性探测：合成固定录音 → 真实 ASR（DashScope fun-asr-realtime）→ 打印最终转写与耗时。
只读调用产品接口，不改任何配置。用法：python scripts/latency_probe_voice.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qwen_client import load_config                      # noqa: E402
from breeze_realtime import BreezeRealtime               # noqa: E402

load_config()
BASE = os.getenv('LATENCY_BASE', 'http://127.0.0.1:8766')
WS_BASE = BASE.replace('http', 'ws')
ORIGIN = BASE
AUDIO_DIR = ROOT / 'data/latency-20261108/audio'
TEXT = os.getenv('PROBE_TEXT', '我需要补充什么材料？')


async def synthesize(session, text, voice):
    client = BreezeRealtime()
    await client.connect(session, voice)
    chunks = bytearray()
    async for chunk in client.stream(session, voice, text):
        chunks.extend(chunk)
    await client.close()
    return bytes(chunks)


def resample_24k_to_16k(pcm: bytes) -> bytes:
    data = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
    n_out = int(round(len(data) * 16000 / 24000))
    x_out = np.linspace(0, len(data) - 1, n_out)
    out = np.interp(x_out, np.arange(len(data)), data)
    return out.astype('<i2').tobytes()


async def asr_round(session, sid, pcm16k, frame_ms=20):
    url = WS_BASE + '/api/asr'
    frame = int(16000 * frame_ms / 1000) * 2
    t = {}
    async with session.ws_connect(url, headers={'Origin': ORIGIN}) as ws:
        await ws.send_json({'session_id': sid})
        ready = await ws.receive_json(timeout=20)
        t['ready'] = ready
        t0 = time.monotonic()
        for i in range(0, len(pcm16k), frame):
            await ws.send_bytes(pcm16k[i:i + frame])
        t_last_frame = time.monotonic()
        await ws.send_json({'type': 'finish'})
        final, finals = None, []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            msg = await ws.receive(timeout=30)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == 'transcript':
                    finals.append(data)
                    if data.get('final'):
                        final = data
                        break
                elif data.get('type') == 'error':
                    return {'error': data}
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        t_final = time.monotonic()
        return {'ready': ready, 'transcript': final, 'partial_count': len(finals),
                'send_ms': round((t_last_frame - t0) * 1000),
                'asr_finish_to_final_ms': round((t_final - t_last_frame) * 1000) if final else None,
                'pcm_ms': round(len(pcm16k) / 32)}


async def main():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    wav24 = AUDIO_DIR / 'probe_24k.pcm'
    wav16 = AUDIO_DIR / 'probe_16k.pcm'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.get(BASE + '/api/voices', headers={'Origin': ORIGIN}) as r:
            voices = await r.json()
        print('voices:', json.dumps(voices, ensure_ascii=False)[:200])
        voice = os.getenv('PROBE_VOICE') or voices['voices'][0]['voice_id']
        print('voice:', voice)

        if wav24.exists():
            pcm24 = wav24.read_bytes()
            print('复用已有合成录音', len(pcm24))
        else:
            pcm24 = await synthesize(session, TEXT, voice)
            wav24.write_bytes(pcm24)
            print('合成完成', len(pcm24), 'bytes =', round(len(pcm24) / 48), 'ms')

        pcm16 = resample_24k_to_16k(pcm24)
        wav16.write_bytes(pcm16)

        async with session.post(BASE + '/api/session', headers={'Origin': ORIGIN},
                                json={'application_id': 'QUAL-001'}) as r:
            sid = (await r.json())['session_id']
        print('sid:', sid)

        result = await asr_round(session, sid, pcm16)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
