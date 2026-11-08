"""端点检测模型「单独」推理耗时探测（离线、独立进程，不与延迟批次并发）。

严格口径：本脚本只测 onnxruntime 一次 predict 的耗时，
**不是**链路里的「用户说完 → 系统判定本轮结束」等待（该等待由网页端策略驱动，本次未测）。

用法：python endpoint_probe.py <输出json>
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from turn_endpoint import load_endpoint                      # noqa: E402

AUDIO = HERE.parent / 'audio'


def nearest_rank(values, p):
    import math
    data = sorted(values)
    k = max(1, math.ceil(p * len(data)))
    return data[k - 1]


def main():
    out = Path(sys.argv[1])
    ep = load_endpoint(ROOT)
    if ep is None:
        out.write_text(json.dumps({'available': False}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('endpoint model not available')
        return

    # 用已生成的固定录音（16k pcm_s16le），截取末尾 1.6s（模型输入要求）
    samples = sorted(AUDIO.glob('*_16k.pcm'))
    pcm_list = []
    for p in samples:
        raw = p.read_bytes()
        tail = raw[-int(16000 * 1.6) * 2:]
        pcm_list.append({'tag': p.stem, 'pcm': tail})
    if not pcm_list:
        pcm_list = [{'tag': 'silence', 'pcm': b'\x00\x00' * int(16000 * 1.6)}]

    runs = []
    for rep in range(3):                       # 3 遍；第 1 遍为加载后首调用，单列
        for item in pcm_list:
            t0 = time.monotonic()
            res = ep.predict(item['pcm'])
            wall = round((time.monotonic() - t0) * 1000, 2)     # 含本进程调用开销，仅交叉核对
            runs.append({'rep': rep, 'tag': item['tag'], 'ms': res['inference_ms'], 'wall_ms': wall,
                         'complete': res.get('complete'), 'probability': round(res.get('probability'), 4)})

    warm = [r['ms'] for r in runs if r['rep'] > 0]
    report = {
        'available': True,
        'model_file': 'runtime/models/smart-turn-v3.2-cpu.onnx',
        'note': ('仅测 onnxruntime 单次推理耗时；链路中的「说完→判定结束」等待由网页端策略驱动，'
                 '本次未测（见 stats.json 的 unmeasured.endpoint_detection_ms）'),
        'cold_first_call_ms': runs[0]['ms'],
        'timing_source': 'TurnEndpoint.predict 内部 time.perf_counter；wall_ms 为外层墙钟，仅交叉核对',
        'n_warm': len(warm),
        'p50_ms': nearest_rank(warm, 0.50),
        'p95_ms': nearest_rank(warm, 0.95),
        'max_ms': max(warm),
        'runs': runs,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"n={len(warm)} p50={report['p50_ms']} p95={report['p95_ms']} max={report['max_ms']} "
          f"cold={report['cold_first_call_ms']}")
    print('写入', out)


if __name__ == '__main__':
    main()
