"""Render reproducible standalone latency chart and audit table."""
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

folder = Path(sys.argv[1]).resolve()
summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
rows = [json.loads(line) for line in (folder / 'raw.jsonl').read_text(encoding='utf-8').splitlines()]
probes = json.loads((folder / 'probes.json').read_text(encoding='utf-8'))
summary.sort(key=lambda r: r['p95_ms'] if r['p95_ms'] is not None else float('inf'))
plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'DejaVu Sans'], 'axes.unicode_minus': False, 'font.size': 11})
fig, ax = plt.subplots(figsize=(13, 7.2), facecolor='#f6f8fc')
ax.set_facecolor('#f6f8fc')
y = np.arange(len(summary))
labels = [(r['model'] + ('\nDeepSeek 官方' if r['provider']=='DeepSeek official' else '\n阿里云百炼')) for r in summary]
for offset, field, label, color in [(-.18, 'p50_ms', 'P50', '#2a7bba'), (.18, 'p95_ms', 'P95', '#e88c39')]:
    values = [r[field] or 0 for r in summary]
    ax.barh(y+offset, values, height=.30, label=label, color=color)
    for i, value in enumerate(values):
        ax.text(value+18, i+offset, f'{value:.0f} ms', va='center', fontsize=11)
ax.set_yticks(y, labels)
ax.invert_yaxis()
maximum = max(r['p95_ms'] or 0 for r in summary)
ax.set_xlim(0, maximum*1.4)
for i, row in enumerate(summary):
    ax.text(maximum*1.2, i, f"正确 {row['correct']}/{row['n']}\n成功 {row['http_success']}/{row['n']}", va='center', ha='center', fontsize=10)
ax.set_xlabel('请求发出 → 完整分类 JSON 返回（毫秒，越低越好）')
ax.xaxis.grid(True, alpha=.16)
ax.set_axisbelow(True)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.legend(loc='lower right', frameon=False)
fig.suptitle('语音客服：分类模型延迟实测', x=.04, ha='left', fontsize=22, weight='bold')
fig.text(.04, .90, '非思考模式 · 单并发随机交替 · 10 道题 × 3 次 / 模型 · 同一业务提示词', fontsize=11, color='#46566b')
fig.text(.04, .075, 'P95 使用 nearest-rank；仅 30 次 / 模型，属于选型初测，不能保证生产尾延迟。', fontsize=10)
fig.text(.04, .043, '不含 ASR / TTS / 实际播放；连接复用、平台前缀缓存可命中。正确率仅代表这 10 道题。', fontsize=10)
fig.subplots_adjust(left=.23, right=.97, top=.84, bottom=.18)
fig.savefig(folder / 'latency-comparison.png', dpi=180)
fig.savefig(folder / 'latency-comparison.svg')
plt.close(fig)
with (folder / 'rounds.csv').open('w', encoding='utf-8-sig', newline='') as file:
    keys = ['provider','model','case','repetition','latency_ms','http_status','ok','valid_json','correct','reasoning_chars','finish_reason']
    writer = csv.DictWriter(file, fieldnames=keys, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
lines = ['Model classification benchmark', '', 'Metric: wall-clock HTTP request start to full non-stream response; not TTFT or voice end-to-end.',
         '30 formal requests/model; 10 synthetic single-turn cases repeated three times, randomized interleaving, concurrency 1.',
         'Production base system prompt and classification reminder reused; dynamic application context/history absent.',
         'Thinking disabled, max_tokens=220, 15s timeout, no retries; successful probe per model excluded as warmup.',
         'Connection reuse and provider prompt cache allowed. Correctness exact intent + set-equivalent topics/numbers/actions.',
         'P50/P95 nearest-rank on HTTP-successful requests. Failures retained separately. No production config changed.', '',
         json.dumps(summary, ensure_ascii=False, indent=2), '', 'Unavailable probes:',
         json.dumps([{k:r.get(k) for k in ['provider','model','http_status','error_code']} for r in probes if not r['ok']], ensure_ascii=False, indent=2),
         '', 'Incorrect or invalid formal responses:',
         json.dumps([r for r in rows if not r['correct']], ensure_ascii=False, indent=2),
         '', 'Sources:', 'https://api-docs.deepseek.com/updates/',
         'https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash',
         'https://help.aliyun.com/zh/model-studio/model-list-text-generation']
(folder / 'report.txt').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
print('CHART ' + str(folder / 'latency-comparison.png'))
