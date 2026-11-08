#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../../.."
PY=./.venv/Scripts/python.exe
L=data/latency-20261108

start_server () {
  rm -f "$L/isolated.sqlite3"
  LATENCY_PORT=8767 $PY "$L/harness/serve_isolated.py" >> "$L/logs/isolated-$1.log" 2>&1 &
  SRV=$!
  for _ in $(seq 1 40); do
    if curl -s --max-time 2 -H "Origin: http://127.0.0.1:8767" http://127.0.0.1:8767/api/status >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "server $1 failed to start" >&2
  return 1
}

stop_server () { kill "$SRV" 2>/dev/null; sleep 3; }

# 预热批次（不计入分位数）
start_server warmup && \
LATENCY_BATCH=warmup $PY "$L/harness/run_latency.py" --warmup 5 --rounds 0 --batch warmup >> "$L/logs/warmup.log" 2>&1
stop_server

# 5 个正式批次，每批 20 轮，批间重启隔离实例（同代码同配置，仅清空会话/缓存）
for b in 1 2 3 4 5; do
  start_server "b$b" || break
  LATENCY_BATCH="b$b" $PY "$L/harness/run_latency.py" --warmup 0 --rounds 20 --skip-check --batch "b$b" >> "$L/logs/formal-b$b.log" 2>&1
  stop_server
done
echo "ALL BATCHES DONE"
