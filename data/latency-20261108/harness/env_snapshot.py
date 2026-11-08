"""环境与版本快照：源码/提示词哈希、提供方与模型标识、流式与缓存开关、机器与网络、起止时间。
只读，不修改任何配置。用法：python env_snapshot.py <输出json> [--phase before|after]
"""
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from qwen_client import load_config                      # noqa: E402

load_config()

WATCH = [
    'src/business_agent.py', 'src/voice_server.py', 'src/server.py', 'src/text_client.py',
    'src/qwen_client.py', 'src/breeze_realtime.py', 'src/cosy_audio.py', 'src/next_turn_audio.py',
    'src/streaming_reply.py', 'src/streaming_model.py', 'src/read_ahead.py', 'src/prefetch_agent.py',
    'src/audio_cache.py', 'src/turn_endpoint.py', 'src/speech_text.py', 'src/model_transport.py',
    'src/prompts/customer_service.txt',
]


def sha(path, n=16):
    p = ROOT / path
    return hashlib.sha256(p.read_bytes()).hexdigest()[:n] if p.exists() else None


def tcp_probe(host, port=443, rounds=3):
    times = []
    for _ in range(rounds):
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=5):
                times.append(round((time.monotonic() - t0) * 1000, 1))
        except OSError as exc:
            times.append(f'error:{type(exc).__name__}')
    return times


def main():
    out = Path(sys.argv[1])
    phase = sys.argv[3] if len(sys.argv) > 3 else 'snapshot'
    from evolution_store import Store, digest
    from evolution_store import BASE

    store = Store(ROOT / 'runtime/mock.sqlite3')
    active = store.active()
    version = store.version(active)

    try:
        cpu = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True, timeout=20)
        cpu_name = [x.strip() for x in cpu.stdout.splitlines() if x.strip() and 'Name' not in x]
    except Exception:
        cpu_name = [platform.processor()]

    snapshot = {
        'phase': phase,
        'captured_at': datetime.now(timezone.utc).astimezone().isoformat(),
        'epoch_ms': int(time.time() * 1000),
        'code_hashes': {p: sha(p) for p in WATCH},
        'prompt': {'active_version': active, 'content_hash': version['content_hash'][:16],
                   'guidance_len': len(version['guidance'] or ''),
                   'base_file_hash': sha('src/prompts/customer_service.txt'),
                   'base_digest_now': digest(BASE)[:16]},
        'providers': {
            'asr': {'provider': 'DashScope', 'model': 'fun-asr-realtime', 'transport': 'websocket',
                    'sample_rate': 16000, 'format': 'pcm_s16le',
                    'host': 'dashscope.aliyuncs.com (wss, /api-ws/v1/inference)'},
            'llm': {'provider': os.getenv('TEXT_PROVIDER', 'qwen'), 'model': os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'),
                    'base_url': os.getenv('DEEPSEEK_BASE_URL') or '默认', 'streaming_in_product_path': False},
            'tts': {'provider': os.getenv('TTS_PROVIDER', 'breeze'), 'model_reported_by_api': 'BreezeBlue',
                    'transport': 'websocket', 'output': 'pcm_s16le 24000', 'voice_id': os.getenv('BREEZE_VOICE_ID', '')[:12] + '…'},
        },
        'toggles': {
            'semantic_endpoint': True, 'exact_intent': True, 'audio_cache': True,
            'speculative_classification': True, 'stream_reply_path': 'eligible-only (question & len>60)',
            'read_ahead': True, 'next_turn_prefetch': True,
            'asr_vocabulary_id_set': bool(os.getenv('ASR_VOCABULARY_ID')),
        },
        'runtime': {
            'deployment': 'local single host, 127.0.0.1',
            'python': platform.python_version(),
            'os': platform.platform(),
            'cpu': cpu_name[0] if cpu_name else 'unknown',
            'cpu_count': os.cpu_count(),
            'machine': platform.machine(),
            'client_concurrency': 1,
        },
        'network': {
            'loopback_only_between_client_and_server': True,
            'tcp_connect_ms': {'dashscope.aliyuncs.com:443': tcp_probe('dashscope.aliyuncs.com'),
                               'api.breeze.blue:443': tcp_probe('api.breeze.blue')},
            'note': ('TTS/ASR/LLM 均走公网；connect 时间仅作网络条件参考，不进入延迟统计。'
                     '修正记录：before 快照误用 api.breezex.tech（DNS 无法解析，已作废），'
                     '事后核对 src/breeze_realtime.py 实际为 wss://api.breeze.blue。'),
        },
        'server_processes': {
            'shared_8766': '用户既有实例，本次测量不使用',
            'isolated_8767': '本次测量实例，独立 sqlite 库 data/latency-20261108/isolated.sqlite3',
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(snapshot, ensure_ascii=False, indent=2)[:1500])
    print('\n写入', out)


if __name__ == '__main__':
    main()
