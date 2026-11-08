"""统计：nearest-rank 分位数（P50=ceil(0.50N)、P95=ceil(0.95N)，1 基），分层与总体分开。

- 分位数只在「成功请求」上计算，并显式标注该条件；
- 失败/超时单列，不删慢样本、不补跑覆盖；
- 主端到端（说完→客户端开始播放）无时间戳，固定输出 null / 未测；
- 不计算各批次 P95 的平均数。

用法：python stats_nearest_rank.py <rounds.jsonl> [输出目录]
"""
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

METRICS = [
    ('speech_end_to_first_audio_ms',
     '【链路段】用户最后一个有效采样结束 → 首段回复音频到达客户端（同进程单调时钟；'
     '不含客户端「开始播放」最后一跳）'),
    ('asr_ms', 'ASR 完成延迟：用户说完 → 最终转写'),
    ('chat_client_ms', 'Agent 处理耗时（客户端观测：文本发出 → 回复文本可用）'),
    ('chat_server_ms', 'Agent 处理耗时（服务端自报 reply_ms）'),
    ('tts_first_audio_ms', 'TTS 首音频：/api/speech 发出 → 首段 PCM 到达客户端'),
    ('composite_asr_to_first_audio_ms', '合成指标：最终转写 → 首段音频到客户端（非端到端，不含播放）'),
    ('gap_asr_to_chat_ms', '客户端编排间隙：最终转写 → 发出 /api/chat（建连/序列化/客户端逻辑）'),
    ('gap_chat_to_speech_ms', '客户端编排间隙：回复可用 → 发出 /api/speech'),
    ('round_ms', '本轮总墙钟（含实时推送录音，仅参考）'),
]
UNMEASURED = [
    ('e2e_speech_end_to_playback_ms', '语音端到端：用户最后一个有效采样结束 → 客户端开始播放首段音频',
     '未测：本环境为无头进程，无音频播放设备与客户端播放时间戳；无真实麦克风。'
     '已实测其中的 speech_end_to_first_audio_ms（说完 → 首段音频到达客户端），'
     '仅缺客户端「开始播放」这一跳；该跳不能用 TTS 首字节替代，故主指标仍记为未测'),
    ('endpoint_detection_ms', '端点检测等待：用户说完 → 系统判定本轮结束',
     '未测：本环境由执行器自行判定轮次结束并发送 finish，网页端判定策略未驱动；'
     '仅离线单独测得端点模型单次推理耗时（见 endpoint-probe.json）'),
    ('model_first_token_ms', '模型首 Token 延迟', '未测：产品链路 /api/chat 为整段返回，未使用流式文本调用'),
    ('transport_playback_ms', '传输及播放等待：首段音频就绪 → 客户端开始播放',
     '未测：无播放设备；仅测得"首段音频到达客户端"'),
]


def nearest_rank(values, p):
    """nearest-rank：升序第 ceil(p*N) 个，1 基。"""
    data = sorted(v for v in values if v is not None)
    n = len(data)
    if n == 0:
        return None
    k = max(1, math.ceil(p * n))
    return data[k - 1]


def summarize(rows, metric):
    values = [r.get(metric) for r in rows if r.get('status') == 'ok' and r.get('correct')]
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {'n': 0, 'p50': None, 'p95': None, 'max': None}
    return {'n': len(values), 'p50': nearest_rank(values, 0.50), 'p95': nearest_rank(values, 0.95),
            'max': max(values), 'min': min(values)}


def main():
    """用法：python stats_nearest_rank.py <目录或逗号分隔文件> [输出目录]"""
    arg = sys.argv[1]
    paths = sorted(Path(arg).glob('rounds-*.jsonl')) if Path(arg).is_dir() else \
        [Path(x) for x in arg.split(',')]
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else paths[0].parent.parent
    rows, seen = [], set()
    for path in paths:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row.get('batch'), row.get('seq'))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    # 派生指标：同一进程单调时钟下的「说完 → 首段音频到达客户端」
    for row in rows:
        ue, fa = row.get('t_user_end_mono'), row.get('t_first_audio_mono')
        if isinstance(ue, (int, float)) and isinstance(fa, (int, float)) and fa >= ue:
            row['speech_end_to_first_audio_ms'] = round((fa - ue) * 1000)
        # 客户端编排间隙（由已有时间戳反推，非新增测量）
        af, cd = row.get('t_asr_final_mono'), row.get('t_chat_done_mono')
        cc, tt = row.get('chat_client_ms'), row.get('tts_first_audio_ms')
        if isinstance(af, (int, float)) and isinstance(cd, (int, float)) and isinstance(cc, int):
            row['gap_asr_to_chat_ms'] = round((cd - cc / 1000 - af) * 1000)
        if isinstance(cd, (int, float)) and isinstance(fa, (int, float)) and isinstance(tt, int):
            row['gap_chat_to_speech_ms'] = round((fa - tt / 1000 - cd) * 1000)
    path = paths[0] if len(paths) == 1 else None
    warm = [r for r in rows if r.get('warmup')]
    formal = [r for r in rows if not r.get('warmup')]

    def block(sel, label):
        ok = [r for r in sel if r['status'] == 'ok' and r.get('correct')]
        fail = [r for r in sel if r['status'] != 'ok' or not r.get('correct')]
        timeouts = [r for r in sel if r.get('timeout')]
        return {
            'label': label, 'n_total': len(sel), 'n_success': len(ok), 'n_fail': len(fail),
            'n_timeout': len(timeouts),
            'metrics': {m: summarize(sel, m) for m, _ in METRICS},
            'fail_detail': [{'seq': r['seq'], 'layer': r['layer'], 'stage': r.get('fail_stage'),
                             'error': str(r.get('error'))[:160]} for r in fail][:20],
        }

    overall = block(formal, '全部正式样本')
    layers = {}
    for key in ['L1_rule', 'L2_model', 'L3_tool', 'L4_confirm']:
        layers[key] = block([r for r in formal if r['layer'] == key], key)

    comp = {
        'n': len(formal),
        'layer_counts': Counter(r['layer'] for r in formal),
        'model_called': sum(1 for r in formal if r.get('model_called')),
        'layer_model_called': {k: sum(1 for r in formal if r['layer'] == k and r.get('model_called'))
                               for k in layers},
        'tool_calls_total': sum((r.get('tool_calls') or 0) for r in formal),
        'layer_tool_calls': {k: sum((r.get('tool_calls') or 0) for r in formal if r['layer'] == k) for k in layers},
        'tokens_total': sum((r.get('tokens') or 0) for r in formal),
        'audio_cache': Counter(str(r.get('audio_cache')) for r in formal),
        'audio_cache_hit_rate': (sum(1 for r in formal if str(r.get('audio_cache')).lower() == 'hit')
                                 / max(1, len(formal))),
        'recordings': sorted({r['recording'] for r in formal}),
        'recording_reused': '所有轮次复用同一批固定录音（8 条），音频缓存按服务端 key 判定，见 audio_cache',
        'duplicate_write': sum(1 for r in formal if r.get('duplicate_write')),
        'request_id_echo_fail': sum(1 for r in formal if r.get('request_id_echo_ok') is False),
        'empty_audio': sum(1 for r in formal if not (r.get('audio_bytes') or 0)),
        'empty_reply': sum(1 for r in formal if not (r.get('reply_chars') or 0)),
    }

    slow = sorted([r for r in formal if r['status'] == 'ok'],
                  key=lambda r: r.get('composite_asr_to_first_audio_ms') or -1, reverse=True)[:10]
    slow_rows = [{'seq': r['seq'], 'layer': r['layer'], 'recording': r['recording'],
                  'asr_ms': r.get('asr_ms'), 'chat_client_ms': r.get('chat_client_ms'),
                  'chat_server_ms': r.get('chat_server_ms'), 'tts_first_audio_ms': r.get('tts_first_audio_ms'),
                  'composite_ms': r.get('composite_asr_to_first_audio_ms'),
                  'reply_chars': r.get('reply_chars'), 'tokens': r.get('tokens'),
                  'tool_calls': r.get('tool_calls'), 'audio_bytes': r.get('audio_bytes')} for r in slow]

    report = {
        'source': [p.name for p in paths], 'n_lines': len(rows),
        'batches': sorted({str(r.get('batch')) for r in rows}),
        'warmup': block(warm, '预热（不计入分位数）'),
        'overall': overall, 'layers': layers, 'composition': comp,
        'slowest_10': slow_rows,
        'unmeasured': [{'metric': m, 'label': l, 'reason': r} for m, l, r in UNMEASURED],
        'thresholds_ms': {'asr_final': 15000, 'chat': 30000, 'first_audio': 30000, 'round': 45000},
        'method': {'percentile': 'nearest-rank, 1-based, ceil(p*N)',
                   'population': '成功且通过正确性校验的请求',
                   'no_batching': '未合并批次，未对批次 P95 取平均',
                   'no_retry_overwrite': '失败轮次保留原记录，不补跑覆盖'},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'stats.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    csv_path = out_dir / 'rounds.csv'
    keep = ['seq', 'kind', 'warmup', 'batch', 'layer', 'recording', 'request_id', 'status', 'fail_stage',
            'timeout', 'correct',
            'transcript', 'reply_chars', 'audio_bytes', 'audio_cache', 'tts_transport',
            'asr_ms', 'chat_client_ms', 'chat_server_ms', 'tts_first_audio_ms',
            'speech_end_to_first_audio_ms', 'composite_asr_to_first_audio_ms',
            'e2e_speech_end_to_playback_ms',
            't_user_end_mono', 't_asr_final_mono', 't_first_audio_mono',
            'model_called', 'tool_calls', 'tokens', 'records_total', 'duplicate_write']
    with csv_path.open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=keep, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in keep})

    for label, blk in [('预热', report['warmup']), ('总体', overall)] + [(k, layers[k]) for k in layers]:
        m = blk['metrics']
        def fmt(key):
            v = m[key]
            return f"N={v['n']} P50={v['p50']} P95={v['p95']} max={v['max']}"
        print(f"[{label}] 成功 {blk['n_success']}/{blk['n_total']} 失败 {blk['n_fail']} 超时 {blk['n_timeout']}")
        for key, _ in METRICS:
            print(f"   {key:34s} {fmt(key)}")
    print('\n主端到端：未测（无客户端播放时间戳）')
    print('写入', out_dir / 'stats.json', '与', csv_path)


if __name__ == '__main__':
    main()
