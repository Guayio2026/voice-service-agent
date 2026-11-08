"""冷启动 / 稳态拆解 + 批次一致性（不取批次 P95 的平均数）。

口径：
- 冷启动 = 每批重启隔离实例后的第 1 轮（seq==1），共 5 轮；
- 稳态   = 其余 95 轮；
- 批次一致性只列各批 P50/P95 供对照，**不做平均**。

用法：python cold_start_breakdown.py <raw目录> <输出json>
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

METRICS = ['speech_end_to_first_audio_ms', 'asr_ms', 'chat_client_ms', 'chat_server_ms',
           'tts_first_audio_ms', 'composite_asr_to_first_audio_ms']


def nr(values, p):
    data = sorted(v for v in values if isinstance(v, (int, float)))
    if not data:
        return None
    return data[max(1, math.ceil(p * len(data))) - 1]


def summarize(rows):
    out = {}
    for m in METRICS:
        vals = [r.get(m) for r in rows if r.get('status') == 'ok' and r.get('correct')]
        out[m] = {'n': len([v for v in vals if isinstance(v, (int, float))]),
                  'p50': nr(vals, .50), 'p95': nr(vals, .95), 'max': max(vals) if vals else None}
    return out


def main():
    raw = Path(sys.argv[1])
    out = Path(sys.argv[2])
    rows = []
    for f in sorted(raw.glob('rounds-*.jsonl')):
        rows += [json.loads(l) for l in f.read_text(encoding='utf-8').splitlines() if l.strip()]
    for r in rows:                       # 派生：说完 → 首段音频到达客户端（同进程单调时钟）
        ue, fa = r.get('t_user_end_mono'), r.get('t_first_audio_mono')
        if isinstance(ue, (int, float)) and isinstance(fa, (int, float)) and fa >= ue:
            r['speech_end_to_first_audio_ms'] = round((fa - ue) * 1000)
    formal = [r for r in rows if not r.get('warmup')]

    cold = [r for r in formal if r.get('seq') == 1]
    steady = [r for r in formal if r.get('seq') != 1]

    per_batch = defaultdict(list)
    for r in formal:
        per_batch[r['batch']].append(r)

    report = {
        'note': ('冷启动=每批重启隔离实例后第 1 轮；稳态=其余轮次。'
                 '批次一致性仅列各批 P50/P95，未对批次分位数取平均。'),
        'cold_start': {'n': len(cold), 'rounds': [
            {'batch': r['batch'], 'layer': r['layer'], **{m: r.get(m) for m in METRICS}} for r in cold],
            'summary': summarize(cold)},
        'steady_state': {'n': len(steady), 'summary': summarize(steady)},
        'per_batch': {b: {'n': len(rs), 'summary': summarize(rs),
                          'first_round_composite': next((r.get('composite_asr_to_first_audio_ms')
                                                         for r in rs if r.get('seq') == 1), None)}
                      for b, rs in sorted(per_batch.items())},
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"冷启动 n={len(cold)}")
    for r in cold:
        print(f"  {r['batch']} {r['layer']:10s} asr={r.get('asr_ms')} chat={r.get('chat_client_ms')} "
              f"tts={r.get('tts_first_audio_ms')} comp={r.get('composite_asr_to_first_audio_ms')}")
    for label, blk in [('冷启动', report['cold_start']['summary']),
                       ('稳态', report['steady_state']['summary'])]:
        print(f"[{label}]")
        for m in METRICS:
            v = blk[m]
            print(f"   {m:34s} N={v['n']} P50={v['p50']} P95={v['p95']} max={v['max']}")
    print('\n批次一致性（各批独立分位数，未平均）：')
    for b, blk in report['per_batch'].items():
        s = blk['summary']['composite_asr_to_first_audio_ms']
        ch = blk['summary']['chat_client_ms']
        print(f"  {b} n={blk['n']} comp P50={s['p50']} P95={s['p95']} | chat P50={ch['p50']} P95={ch['p95']}")
    print('写入', out)


if __name__ == '__main__':
    main()
