"""语音链路延迟测量执行器（单并发、固定录音、单调时钟、每轮 request_id 关联）。

测量口径（严格区分「实测」与「未测」）：
  t_user_end      = 最后一段 20ms PCM 音频帧写入 ASR 通道的时刻（按实时节奏发送，抖动 ≤1 帧=20ms）
  t_asr_final     = 收到 final=true 转写的时刻
  t_chat_done     = /api/chat 返回时刻（回复文本可用）
  t_first_audio   = /api/speech 收到第一块 PCM 的时刻（“客户端收到首段音频”，非“开始播放”）
主端到端（说完 → 客户端开始播放）本环境无播放时间戳，**不计算**，字段固定为 null。

用法：
  python run_latency.py --warmup 5 --rounds 100
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from qwen_client import load_config                       # noqa: E402
from breeze_realtime import BreezeRealtime                # noqa: E402

load_config()

BATCH = os.getenv('LATENCY_BATCH', 'b1')
BASE = os.getenv('LATENCY_BASE', 'http://127.0.0.1:8767')
WS_BASE = BASE.replace('http', 'ws')
ORIGIN = BASE
OUT_DIR = HERE.parent
AUDIO = OUT_DIR / 'audio'
RAW = OUT_DIR / 'raw'
DB = OUT_DIR / 'isolated.sqlite3'

TIMEOUT = {'asr_final': 15.0, 'chat': 30.0, 'first_audio': 30.0, 'round': 45.0}
FRAME_MS = 20

LAYERS = {
    'L1_rule': {'name': '本地规则快速回复', 'identity': False, 'pending': False,
                'texts': ['你是谁？', '谢谢。']},
    'L2_model': {'name': '真实模型普通问答', 'identity': True, 'pending': False,
                 'texts': ['这次为什么要补正材料？', '我需要补充什么材料？']},
    'L3_tool': {'name': '业务工具查询', 'identity': True, 'pending': False,
                'texts': ['帮我查一下这次申请还差什么材料。', '提交入口和截止时间能确定吗？']},
    'L4_confirm': {'name': '明确确认后的登记', 'identity': True, 'pending': True,
                   'setup': ['我需要补充什么材料？', '确认登记。'],
                   'texts': ['确认登记。', '行，我确认登记。']},
}


class SessionPool:
    """分层复用会话：单会话上限 30 轮（产品限制），临近上限即换新会话。"""

    def __init__(self):
        self.sessions = {}

    async def acquire(self, session, layer_key, need_identity, turns):
        state = self.sessions.get(layer_key)
        if state is None or state['turns'] + turns > 25:
            sid = await created_session(session)
            if need_identity:
                await verify_identity(session, sid)
            state = {'sid': sid, 'turns': 0}
            self.sessions[layer_key] = state
        state['turns'] += turns
        return state['sid']


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def db_rows(sql, params=()):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def resample_24k_to_16k(pcm: bytes) -> bytes:
    data = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
    n_out = int(round(len(data) * 16000 / 24000))
    out = np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data)
    return out.astype('<i2').tobytes()


async def synth(session, text, voice, path24, attempts=3):
    last = None
    for attempt in range(1, attempts + 1):
        client = BreezeRealtime()
        try:
            await client.connect(session, voice)
            buf = bytearray()
            async for chunk in client.stream(session, voice, text):
                buf.extend(chunk)
            await client.close()
            if not buf:
                raise RuntimeError('EMPTY_TTS_AUDIO')
            path24.write_bytes(bytes(buf))
            return bytes(buf)
        except Exception as exc:                                     # noqa: BLE001
            last = exc
            try:
                await client.close()
            except Exception:
                pass
            await asyncio.sleep(2 * attempt)
    raise RuntimeError(f'SYNTH_FAILED_AFTER_{attempts}: {type(last).__name__}: {last}')


async def prepare_recordings(session, voice):
    """8 条固定录音：每层 2 条；已存在则复用（不重新合成）。"""
    AUDIO.mkdir(parents=True, exist_ok=True)
    items = []
    for key, layer in LAYERS.items():
        for idx, text in enumerate(layer['texts']):
            tag = f'{key}_{idx}'
            p24 = AUDIO / f'{tag}_24k.pcm'
            p16 = AUDIO / f'{tag}_16k.pcm'
            if p24.exists():
                pcm24 = p24.read_bytes()
                reused = True
            else:
                pcm24 = await synth(session, text, voice, p24)
                reused = False
            p16.write_bytes(resample_24k_to_16k(pcm24))
            items.append({'layer': key, 'tag': tag, 'text': text, 'path16': p16,
                          'audio_ms': round(len(pcm24) / 48), 'synth_reused': reused})
    return items


async def created_session(session):
    async with session.post(BASE + '/api/session', json={'application_id': 'QUAL-001'}) as r:
        return (await r.json())['session_id']


async def verify_identity(session, sid):
    async with session.post(BASE + '/api/identity', json={'session_id': sid, 'is_responsible': True}) as r:
        return await r.json()


async def chat(session, sid, text, rid):
    t0 = time.monotonic()
    async with session.post(BASE + '/api/chat', json={'session_id': sid, 'text': text, 'request_id': rid}) as r:
        body = await r.json()
    return {'t0': t0, 't1': time.monotonic(), 'status': r.status, 'body': body}


async def speech(session, sid, rid, voice, drain_cap=8.0):
    t0 = time.monotonic()
    payload = {'session_id': sid, 'request_id': rid, 'voice_id': voice}
    out = {'t_send': t0, 'first': None, 'bytes': 0, 'done': None, 'headers': {}, 'status': None,
           'truncated': True, 'error': None}
    async with session.post(BASE + '/api/speech', json=payload) as r:
        out['status'] = r.status
        out['headers'] = {k: v for k, v in r.headers.items()
                          if k.lower() in ('x-audio-cache', 'x-audio-sample-rate', 'x-audio-format', 'x-tts-transport')}
        if r.status != 200:
            out['error'] = (await r.text())[:300]
            return out
        try:
            while True:
                chunk = await r.content.read(65536)
                if not chunk:
                    out['truncated'] = False
                    break
                if out['first'] is None:
                    out['first'] = time.monotonic()
                out['bytes'] += len(chunk)
                if time.monotonic() - out['t_send'] > drain_cap:
                    out['truncated'] = True
                    break
        except asyncio.TimeoutError:
            out['truncated'] = True
        out['done'] = time.monotonic()
    return out


async def asr_round(session, sid, pcm16, pace=True):
    frame = int(16000 * FRAME_MS / 1000) * 2
    out = {'ready': None, 'transcript': None, 't_stream_start': None, 't_user_end': None,
           't_final': None, 'partials': 0, 'error': None, 't_ready': None}
    async with session.ws_connect(WS_BASE + '/api/asr', headers={'Origin': ORIGIN}) as ws:
        await ws.send_json({'session_id': sid})
        out['t_ready'] = time.monotonic()
        hello = await ws.receive_json(timeout=15)
        out['ready'] = hello
        if hello.get('type') != 'ready':
            out['error'] = f'ASR_NOT_READY:{hello}'
            return out
        t0 = time.monotonic()
        out['t_stream_start'] = t0
        pace_start = t0
        for i, off in enumerate(range(0, len(pcm16), frame)):
            await ws.send_bytes(pcm16[off:off + frame])
            if pace:
                delay = pace_start + (i + 1) * FRAME_MS / 1000 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
        out['t_user_end'] = time.monotonic()
        await ws.send_json({'type': 'finish'})
        deadline = time.monotonic() + TIMEOUT['asr_final']
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(ws.receive(), timeout=TIMEOUT['asr_final'])
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == 'transcript':
                    out['partials'] += 1
                    if data.get('final'):
                        out['transcript'] = data.get('text', '')
                        out['t_final'] = time.monotonic()
                        break
                elif data.get('type') == 'error':
                    out['error'] = f"ASR_ERROR:{data.get('code')}"
                    break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                out['error'] = 'ASR_CLOSED_BEFORE_FINAL'
                break
    if out['error'] is None and out['t_final'] is None:
        out['error'] = 'ASR_TIMEOUT'
    return out


async def one_round(session, rec, voice, seq, pool, warmup=False, kind='formal'):
    row = {'seq': seq, 'kind': kind, 'warmup': warmup, 'batch': BATCH, 'layer': rec['layer'],
           'layer_name': LAYERS[rec['layer']]['name'], 'recording': rec['tag'],
           'expected_text': rec['text'], 'recording_ms': rec['audio_ms'],
           'recording_synth_reused': rec['synth_reused'],
           'started_at': now_iso(), 'request_id': None,
           't_user_end_mono': None, 't_asr_final_mono': None, 't_chat_done_mono': None,
           't_first_audio_mono': None,
           'asr_ms': None, 'chat_client_ms': None, 'chat_server_ms': None,
           'tts_first_audio_ms': None, 'audio_total_ms': None,
           'composite_asr_to_first_audio_ms': None,
           'e2e_speech_end_to_playback_ms': None,
           'transcript': None, 'reply': None, 'reply_chars': None, 'audio_bytes': None,
           'audio_cache': None, 'tts_transport': None, 'sample_rate': None,
           'model_called': None, 'tool_calls': None, 'tokens': None,
           'records_total': None, 'status': 'ok', 'fail_stage': None, 'error': None,
           'timeout': False, 'correct': False, 'notes': []}
    layer = LAYERS[rec['layer']]
    rid = f'lat-{kind[:1]}-{seq:03d}-{int(time.time() * 1000) % 100000}'
    row['request_id'] = rid
    round_start = time.monotonic()
    try:
        sid = await created_session(session)
        if layer['identity']:
            await verify_identity(session, sid)
        row['session_id'] = sid
        row['session_reused'] = False
        row['records_before'] = db_rows('SELECT COUNT(*) AS n FROM records WHERE session=?', (sid,))[0]['n']
        if layer['pending']:
            setups = []
            for j, stext in enumerate(layer.get('setup') or []):
                st = await chat(session, sid, stext, f'{rid}-setup{j}')
                setups.append({'text': stext, 'status': st['status'], 'reply_ms': st['body'].get('reply_ms')})
            row['setup'] = setups
            row['setup_status'] = setups[-1]['status'] if setups else None
            row['setup_reply_ms'] = setups[-1]['reply_ms'] if setups else None
            row['notes'].append('前置：复述材料并建立待确认项（不计入本轮）')

        a = await asr_round(session, sid, rec['path16'].read_bytes())
        row['asr_partials'] = a['partials']
        if a['error']:
            row.update(status='fail', fail_stage='asr', error=a['error'])
            if a.get('t_user_end'):
                row['t_user_end_mono'] = a['t_user_end']
            raise RuntimeError(a['error'])
        row['transcript'] = a['transcript']
        row['t_user_end_mono'] = a['t_user_end']
        row['t_asr_final_mono'] = a['t_final']
        row['asr_ms'] = round((a['t_final'] - a['t_user_end']) * 1000)

        text = (a['transcript'] or '').strip()
        if not text:
            row.update(status='fail', fail_stage='asr', error='EMPTY_TRANSCRIPT')
            raise RuntimeError('EMPTY_TRANSCRIPT')

        c = await chat(session, sid, text, rid)
        row['t_chat_done_mono'] = c['t1']
        row['chat_client_ms'] = round((c['t1'] - c['t0']) * 1000)
        if c['status'] != 200:
            row.update(status='fail', fail_stage='chat', error=json.dumps(c['body'], ensure_ascii=False)[:300])
            raise RuntimeError('CHAT_FAILED')
        body = c['body']
        row['chat_server_ms'] = body.get('reply_ms')
        row['reply'] = body.get('text')
        row['reply_chars'] = len(body.get('text') or '')
        row['request_id_echo_ok'] = body.get('request_id') == rid
        row['stream_reply_eligible'] = body.get('stream_reply_eligible')

        sp = await speech(session, sid, rid, voice)
        row['t_first_audio_mono'] = sp['first']
        row['audio_bytes'] = sp['bytes']
        row['audio_cache'] = sp['headers'].get('x-audio-cache') or sp['headers'].get('X-Audio-Cache')
        row['tts_transport'] = sp['headers'].get('x-tts-transport') or sp['headers'].get('X-TTS-Transport')
        row['sample_rate'] = sp['headers'].get('x-audio-sample-rate') or sp['headers'].get('X-Audio-Sample-Rate')
        row['audio_truncated'] = sp['truncated']
        if sp['status'] != 200 or sp['first'] is None:
            row.update(status='fail', fail_stage='tts',
                       error=(sp['error'] or 'NO_AUDIO_BYTES')[:300])
            raise RuntimeError('TTS_FAILED')
        row['tts_first_audio_ms'] = round((sp['first'] - sp['t_send']) * 1000)
        row['audio_total_ms'] = round((sp['done'] - sp['t_send']) * 1000)
        row['composite_asr_to_first_audio_ms'] = round((sp['first'] - a['t_final']) * 1000)

        detail = db_rows('SELECT trace, version_id FROM conversation_turns WHERE request_id=?', (rid,))
        if detail:
            trace = json.loads(detail[0]['trace'] or '{}')
            usage = trace.get('usage') or {}
            tools = trace.get('tools') or []
            row['model_called'] = bool(usage) or bool(trace.get('model'))
            row['tool_calls'] = len(tools)
            row['tool_names'] = [t.get('name') or t.get('tool') for t in tools]
            row['tokens'] = usage.get('total_tokens')
            row['prompt_version'] = detail[0]['version_id']
            row['decision'] = trace.get('decision')
        row['records_total'] = db_rows('SELECT COUNT(*) AS n FROM records WHERE session=?', (sid,))[0]['n']

        row['correct'] = bool(row['reply'] and row['reply_chars'] > 0
                              and row['request_id_echo_ok'] is not False
                              and (row['audio_bytes'] or 0) > 0 and (row['audio_bytes'] or 0) % 2 == 0)
        if rec['layer'] == 'L4_confirm':
            delta = row['records_total'] - row['records_before']
            row['records_delta'] = delta
            row['duplicate_write'] = delta != 1
            if row['duplicate_write']:
                row['correct'] = False
                row['notes'].append(f'本轮登记增量应为 1，实际 {delta}')
        if not row['correct']:
            row['status'] = 'fail'
            row['fail_stage'] = 'correctness'
        if time.monotonic() - round_start > TIMEOUT['round']:
            row['timeout'] = True
            row['notes'].append('整轮超过阈值')
    except Exception as exc:                                        # noqa: BLE001
        if row['status'] == 'ok':
            row['status'] = 'fail'
            row['error'] = row['error'] or f'{type(exc).__name__}: {exc}'
        if 'Timeout' in type(exc).__name__ or 'TIMEOUT' in str(exc):
            row['timeout'] = True
    row['finished_at'] = now_iso()
    row['round_ms'] = round((time.monotonic() - round_start) * 1000)
    return row


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--warmup', type=int, default=5)
    ap.add_argument('--rounds', type=int, default=100)
    ap.add_argument('--voice', default=None)
    ap.add_argument('--skip-check', action='store_true')
    ap.add_argument('--batch', default='b1')
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    jsonl = RAW / f'rounds-{args.batch}-{ts}.jsonl'

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90),
                                     headers={'Origin': ORIGIN}) as session:
        for _ in range(30):
            try:
                async with session.get(BASE + '/api/status') as r:
                    if r.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(2)
        async with session.get(BASE + '/api/voices') as r:
            voices = (await r.json())['voices']
        voice = args.voice or voices[0]['voice_id']
        print(f'voice={voice} base={BASE}', flush=True)

        recs = await prepare_recordings(session, voice)
        print('recordings:', [(r['tag'], r['audio_ms']) for r in recs], flush=True)

        # 录音自查：一次 ASR，确认转写稳定（不计入统计）
        check = []
        if args.skip_check and (OUT_DIR / 'recording-check.json').exists():
            check = json.loads((OUT_DIR / 'recording-check.json').read_text(encoding='utf-8'))
            print('recording check: 复用已有结果', len(check), flush=True)
        for rec in ([] if args.skip_check else recs):
            sid = await created_session(session)
            a = await asr_round(session, sid, rec['path16'].read_bytes())
            check.append({'tag': rec['tag'], 'expected': rec['text'], 'asr': a['transcript'],
                          'match': (a['transcript'] or '').strip('。？， ') == rec['text'].strip('。？， '),
                          'asr_ms': round((a['t_final'] - a['t_user_end']) * 1000) if a['t_final'] else None,
                          'error': a['error']})
        if not args.skip_check:
            (OUT_DIR / 'recording-check.json').write_text(
                json.dumps(check, ensure_ascii=False, indent=2), encoding='utf-8')
            print('recording check:', json.dumps(check, ensure_ascii=False)[:600], flush=True)

        pool = SessionPool()
        plan = []
        seq = 0
        for i in range(args.warmup):
            seq += 1
            plan.append(('warmup', recs[i % len(recs)]))
        cycles = args.rounds // len(LAYERS)
        order = list(LAYERS)
        for i in range(cycles):
            for key in order:
                seq += 1
                candidates = [r for r in recs if r['layer'] == key]
                plan.append(('formal', candidates[i % len(candidates)]))

        with jsonl.open('w', encoding='utf-8') as fh:
            for idx, (kind, rec) in enumerate(plan, 1):
                row = await one_round(session, rec, voice, idx, pool, warmup=(kind == 'warmup'), kind=kind)
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
                fh.flush()
                flag = 'OK' if row['status'] == 'ok' and row['correct'] else 'FAIL'
                if kind == 'warmup':
                    print(f"[warmup {idx}] {rec['layer']} {flag} asr={row['asr_ms']} chat={row['chat_client_ms']} "
                          f"tts={row['tts_first_audio_ms']} comp={row['composite_asr_to_first_audio_ms']}", flush=True)
                else:
                    print(f"[{idx}/{len(plan)}] {rec['layer']} {flag} asr={row['asr_ms']} chat={row['chat_client_ms']} "
                          f"tts={row['tts_first_audio_ms']} comp={row['composite_asr_to_first_audio_ms']}"
                          f"{' ERR=' + str(row['error'])[:80] if row['error'] else ''}", flush=True)
    print('JSONL:', jsonl, flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
