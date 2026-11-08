"""隔离实例启动器：独立 sqlite 库，端口 8767，配置与产品一致（不改 .env）。
用法：python serve_isolated.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))

from qwen_client import load_config                      # noqa: E402

load_config()

DB = ROOT / 'data/latency-20261108/isolated.sqlite3'
PORT = int(os.getenv('LATENCY_PORT', '8767'))

from voice_server import create_app                      # noqa: E402
import uvicorn                                            # noqa: E402

if __name__ == '__main__':
    print(f'isolated instance on 127.0.0.1:{PORT} db={DB}', flush=True)
    uvicorn.run(create_app(DB), host='127.0.0.1', port=PORT,
                access_log=False, proxy_headers=False, ws_max_size=65536)
