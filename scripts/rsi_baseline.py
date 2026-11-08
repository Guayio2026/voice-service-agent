"""只读快照：记录当前启用版本、源码/提示词/测试数据哈希、模型配置（密钥脱敏）。
不修改任何产品代码，不调用云端模型。
用法：.venv/Scripts/python.exe scripts/rsi_baseline.py [输出目录]
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data/rsi-20261108'


def sha(path, n=16):
    p = Path(path)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:n] if n else hashlib.sha256(p.read_bytes()).hexdigest()


def key_fingerprint(value):
    """不输出密钥本身，只给不可逆指纹，便于确认"同一次实验没有换密钥"."""
    if not value:
        return None
    return 'sha256:' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]


def git_head():
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=20)
        branch = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=20)
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, timeout=20)
        return {'head': r.stdout.strip() or None, 'branch': branch.stdout.strip() or None,
                'dirty_files': len([x for x in dirty.stdout.splitlines() if x.strip()])}
    except Exception as exc:
        return {'error': type(exc).__name__}


def main():
    from evolution_store import Store, BASE, digest
    from qwen_client import load_config

    load_config()  # 只读取 .env 里的字面量 KEY=value，不执行任何内容
    db = ROOT / 'runtime/mock.sqlite3'
    store = Store(db)
    active = store.active()
    ver = store.version(active)

    src_files = sorted((ROOT / 'src').glob('*.py')) + sorted((ROOT / 'src/prompts').glob('*'))
    code = {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in src_files if p.is_file()}

    data_files = [
        'data/independent-holdout/holdout-20261107-v1.json',
        'data/independent-holdout/holdout-20261108-v2.json',
        'data/independent-v3/real-model-v3.json',
        'data/independent-v3/state-fault-v3.json',
        'data/independent-v3/content-consistency-v3.json',
        'data/independent-v3/manifest.json',
        'data/independent-v3/regression-revisions.json',
        'data/evolution/validation.json',
        'data/evolution/holdout.json',
    ]
    datasets = {p: sha(ROOT / p) for p in data_files}

    def count(sql):
        return store.one(sql)['n'] if store.one(sql) else None

    model = {
        'text_provider': os.getenv('TEXT_PROVIDER'),
        'deepseek_model': os.getenv('DEEPSEEK_MODEL'),
        'qwen_model': os.getenv('QWEN_MODEL'),
        'deepseek_base_url': os.getenv('DEEPSEEK_BASE_URL') or '(默认)',
        'tts_provider': os.getenv('TTS_PROVIDER'),
        'key_fingerprints': {
            'DASHSCOPE_API_KEY': key_fingerprint(os.getenv('DASHSCOPE_API_KEY')),
            'DEEPSEEK_API_KEY': key_fingerprint(os.getenv('DEEPSEEK_API_KEY')),
            'BREEZE_API_KEY': key_fingerprint(os.getenv('BREEZE_API_KEY')),
        },
    }

    snapshot = {
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'note': '只读基线；未修改产品源码、未调用云端模型、未发布任何版本',
        'git': git_head(),
        'prompt_version': {
            'active_version_id': active,
            'parent': ver['parent'],
            'base_hash': ver['base_hash'],
            'content_hash': ver['content_hash'],
            'base_prompt_digest_now': digest(BASE),
            'guidance_len': len(ver['guidance'] or ''),
            'prompt_chars': len(ver['prompt']),
        },
        'code_hashes': code,
        'dataset_hashes': datasets,
        'evolution_db': {
            'path': str(db.relative_to(ROOT)).replace('\\', '/'),
            'size_bytes': db.stat().st_size if db.exists() else None,
            'conversation_turns': count('SELECT COUNT(*) AS n FROM conversation_turns'),
            'feedback': count('SELECT COUNT(*) AS n FROM evolution_feedback'),
            'versions': count('SELECT COUNT(*) AS n FROM evolution_versions'),
            'runs': count('SELECT COUNT(*) AS n FROM evolution_runs'),
            'calls': count('SELECT COUNT(*) AS n FROM evolution_calls'),
        },
        'model': model,
        'evolution_datasets': {
            'validation_cases': len(json.loads((ROOT / 'data/evolution/validation.json').read_text(encoding='utf-8'))['cases']),
            'sealed_cases': len(json.loads((ROOT / 'data/evolution/holdout.json').read_text(encoding='utf-8'))['cases']),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / 'baseline.json'
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print('\n写入:', target)


if __name__ == '__main__':
    main()
