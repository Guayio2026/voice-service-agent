"""Explicit, small billable end-to-end probe. Never run as part of unit tests."""
import argparse
import asyncio
import json
import struct
import wave
from pathlib import Path
import aiohttp

ROOT = Path(__file__).resolve().parents[1]


async def main(base):
    async with aiohttp.ClientSession(headers={'Origin': base}, timeout=aiohttp.ClientTimeout(total=100)) as http:
        async def call(path, p=None):
            async with http.request('POST' if p is not None else 'GET', base + '/api/' + path, json=p) as r:
                data = await r.json()
                if r.status != 200:
                    raise RuntimeError(data.get('error', {}).get('code', str(r.status)))
                return data
        voices = (await call('voices'))['voices']
        if not voices:
            raise RuntimeError('NO_PERSONAL_VOICES')
        sid = (await call('session', {'application_id': 'TEST-001'}))['session_id']
        await call('identity', {'session_id': sid, 'is_responsible': True})
        reply = await call('chat', {'session_id': sid, 'text': '我需要补什么材料？请用一句话回答。', 'request_id': 'live-probe-0001'})
        print(json.dumps({'llm': reply['model'], 'usage': reply['usage'], 'reply_ms': reply['reply_ms']}, ensure_ascii=True))
        async with http.post(base + '/api/speech', json={'session_id': sid, 'request_id': reply['request_id'], 'voice_id': voices[0]['voice_id']}) as r:
            if r.status != 200:
                raise RuntimeError((await r.json())['error']['code'])
            audio = await r.read()
        assert len(audio) > 2000 and len(audio) % 2 == 0
        target = ROOT / 'runtime/qa/live-reply.wav'
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(audio)
        print(json.dumps({'tts': 'ok', 'voice_id': voices[0]['voice_id'], 'audio_seconds': round(len(audio)/48000, 2)}))
        # Resample generated synthetic speech to the browser microphone's 16 kHz input format.
        samples = struct.unpack('<' + 'h' * (len(audio)//2), audio)
        resampled = []
        for i in range(int(len(samples)*2/3)):
            pos = i*1.5; start = int(pos); f = pos-start
            resampled.append(round(samples[start]*(1-f)+samples[min(start+1,len(samples)-1)]*f))
        pcm = struct.pack('<' + 'h'*len(resampled), *resampled)
        async with http.ws_connect(base.replace('http:', 'ws:') + '/api/asr') as ws:
            await ws.send_json({'session_id': sid})
            ready = await ws.receive_json(timeout=25)
            if ready.get('type') != 'ready':
                raise RuntimeError(ready.get('code', 'ASR_NOT_READY'))
            async def upload():
                for i in range(0, len(pcm), 3200):
                    await ws.send_bytes(pcm[i:i+3200]); await asyncio.sleep(.1)
                await ws.send_json({'type': 'finish'})
            sending = asyncio.create_task(upload())
            try:
                while True:
                    event = await ws.receive_json(timeout=30)
                    if event['type'] == 'error':
                        raise RuntimeError(event['code'])
                    if event['type'] == 'done':
                        assert event['text'].strip(), 'EMPTY_TRANSCRIPT'
                        print(json.dumps({'asr': 'ok', 'recognized_characters': len(event['text']),
                                          'contains_expected_material': '材料' in event['text']}, ensure_ascii=True))
                        break
                await sending
            finally:
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true', help='One billed LLM/TTS/ASR probe')
    p.add_argument('--base', default='http://127.0.0.1:8766')
    args = p.parse_args()
    if not args.live:
        p.error('Use --live explicitly; this sends synthetic speech to cloud providers and incurs fees.')
    asyncio.run(main(args.base))
