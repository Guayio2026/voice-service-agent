"""Local persistent worker control. No web service restart required."""
import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'runtime/auto-rsi-config.json'
STATUS=ROOT/'runtime/auto-rsi-status.json'


def running():
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(('127.0.0.1',18767))==0


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['start','status','pause','resume'])
    args=parser.parse_args()
    if args.command=='status':
        print(json.dumps({'running':running(),'config':json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else None,
            'status':json.loads(STATUS.read_text(encoding='utf-8')) if STATUS.exists() else None},ensure_ascii=False,indent=2))
        return
    config=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {
        'enabled':True,'cooldown_seconds':600,'max_batches_per_utc_day':4,
        'per_batch_max_calls':128,'per_batch_reserved_tokens':1000000,
        'notice':'Four bounded batches/day; may reject, no guaranteed gain. Only classifier guidance evolves.'}
    config['enabled']=args.command!='pause'
    config.pop('max_batches_per_utc_day',None)
    config.setdefault('max_daily_calls',512)
    config.setdefault('max_daily_reserved_tokens',4000000)
    config['notice']='Daily ceiling: 512 calls / 4M conservative reserved tokens; per batch 128 / 1M. Rejection keeps active unchanged.'
    CONFIG.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.command=='pause':
        print('Paused: current bounded batch may finish; no new batch starts.')
        return
    if running():
        print('Automatic RSI worker already running.'); return
    log=open(ROOT/'runtime/auto-rsi-worker.log','ab',buffering=0)
    proc=subprocess.Popen([sys.executable,str(ROOT/'src/auto_rsi.py')],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=log,stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    log.close()
    print(json.dumps({'started_pid':proc.pid,'config':str(CONFIG)}))


if __name__=='__main__':
    main()
