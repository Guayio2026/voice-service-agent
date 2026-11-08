"""One command for the complete, bounded RSI loop. Calls real text models."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from rsi_loop import run
from evolution_store import Store
from qwen_client import load_config
from text_client import text_client

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--db',type=Path,default=ROOT/'runtime/mock.sqlite3')
    p.add_argument('--output',type=Path,default=ROOT/'runtime/rsi-loops')
    p.add_argument('--activate',action='store_true',help='Automatically activate only after all gates; rollback on post-release failure')
    a=p.parse_args()
    config=json.loads(a.config.read_text(encoding='utf-8'))
    for role in ('development','validation','holdout'):
        config[role]=str((a.config.parent/config[role]).resolve())
    load_config()
    result=run(Store(a.db),text_client(),config,a.output,a.activate)
    print(json.dumps({'run_id':result['run_id'],'status':result['status'],'published':result['published'],'output':str(a.output/result['run_id'])},ensure_ascii=False))
    if result['status'] in {'failed','rolled_back_after_error'}:
        raise SystemExit(1)

if __name__=='__main__':
    main()
