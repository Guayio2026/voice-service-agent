"""Inventory of existing test evidence: case id -> intended use (A/B/C) -> this round's suite.

Reads the already-exposed v1/v2/v3 results and the v3 attribution report, applies
fixed rules, and writes data/rsi-existing/inventory.csv. Read-only over the
original files; nothing is overwritten.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/rsi-existing/inventory.csv'

INTENT_MARKERS = {'拒绝沟通': 'decline', '人工协助工单': 'handoff', '稍后联系': 'defer', '理解清楚': 'unclear'}

MAPPED = {
    'v3:R018': ('A', 'E-dev-deny-missing', 'T1 原句进入开发集，断言 correct（否认缺项≠拒绝）'),
    'v3:R009': ('A', 'E-dev-complaint', 'T2 原句进入开发集，断言 unclear（抱怨≠拒绝）'),
    'v3:R011': ('A', 'E-dev-view-materials', 'T2 原句进入开发集，断言 question/materials'),
    'v3:R010': ('A', 'E-dev-third-party-callback', 'T3 原句进入开发集，断言 handoff（护栏，防止改坏）'),
    'v3:R003': ('A', 'E-dev-auth-number', 'T2 原句进入开发集，断言 question/other'),
    'v3:R012': ('A', '', '撤回后二次核验，需前置状态，断言口径含混，本轮不采用'),
    'v3:R015': ('A', '', '撤销登记语义，现由确定性撤销分支覆盖，不再产生模型反馈'),
    'v3:R016': ('A', '', '“先别存”现由确定性撤销分支覆盖，不再产生模型反馈'),
    'v3:C008': ('B', 'E-dev-falsify-guard', '整体 PASS，仅 T1 偶发误判；在开发集只作零写入护栏'),
    'v3:R001': ('C', 'E-val-no-disclosure', '原断言要待确认项属口径/适配混合；仅取“未核验不披露”段入验证集'),
    'v3:R005': ('C', 'E-val-no-disclosure', '身份状态机问题（源码层），提示词不可修；仅取“未核验不披露”段'),
}

# Rows the attribution report already judged as adapter/test-side/口径, or that
# only make sense with preconditions rsi_loop cannot inject.
ADAPTER_ONLY = {
    'v3:S001': '需要 delayed delivery 能力，rsi_loop 无前置/时序注入',
    'v3:S002': '需要工具回执延后',
    'v3:S003': '需要响应延后到身份变更之后',
    'v3:S005': '需要异步队列',
    'v3:S007': '需要 timeout 故障位',
    'v3:S009': '需要记录工具返回值',
    'v3:S011': '需要并发驱动',
    'v3:S012': '需要 response_override',
    'v3:C003': '归因为适配错误（把工具调用当廉洁性必要条件）',
    'v3:S006': '归因为适配错误（断言过严）',
    'v3:R001': '原断言要求待确认项，属口径/适配混合；仅取“未核验不披露”段',
}

PRODUCT_LOGIC = {
    'v3:R010': '身份撤回状态机',
    'v3:R011': '撤回话术与状态不一致',
    'v3:R012': '撤回后二次核验触发条件',
    'v3:C001': 'payload 落地失真（数据建模）',
    'v3:C004': '待确认内容改写口径未定',
    'v3:C005': '话术与库态矛盾',
}


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def v3_rows():
    results = load('data/independent-v3/results-v3-rejudged.json')
    attribution = {r['id']: r for r in load('data/independent-v3/attribution-v3.json')['rows']}
    rows = []
    for row in results['rows']:
        cid = row['case_id']
        attr = attribution.get(cid, {})
        templates = set()
        for attempt in row['attempts']:
            for snap in attempt['snaps']:
                for marker in INTENT_MARKERS:
                    if marker in snap['reply']:
                        templates.add(INTENT_MARKERS[marker])
        key = 'v3:' + cid
        if key in MAPPED:
            use, mapped, reason = MAPPED[key]
        elif row['verdict'] == 'BLOCKED' or attr.get('attribution') == 'adapter':
            use, mapped, reason = 'C', '', ADAPTER_ONLY.get(key, '适配层缺口：执行器无对应能力')
        elif row['verdict'] in ('PASS', 'PASS_PARTIAL'):
            use, mapped, reason = 'B', '', '已知通过/部分通过，保留为回归与安全资产'
        elif templates & {'decline', 'unclear', 'handoff', 'defer'}:
            use, mapped, reason = 'A', '', '产品侧意图误判可选题，但本轮未采用（断言口径含混或同族已被覆盖）'
        elif key in PRODUCT_LOGIC:
            use, mapped, reason = 'C', '', '源码层缺陷，提示词不可修：' + PRODUCT_LOGIC[key]
        else:
            use, mapped, reason = 'C', '', '无法归为单一意图错误或证据不足'
        rows.append({
            'source_file': 'data/independent-v3/results-v3-rejudged.json',
            'version': 'v3-20261108',
            'case_id': cid,
            'suite': row['suite'],
            'verdict': row['verdict'],
            'attribution': attr.get('attribution', ''),
            'intent_templates': '/'.join(sorted(templates)),
            'use': use,
            'reason': reason,
            'mapped_case': mapped,
        })
    return rows


def holdout_rows(path, version):
    data = load(path)
    rows = []
    for row in data['rows']:
        cid = row['id']
        fails = row.get('fails') or []
        if row['verdict'] in ('PASS', 'PASS_PARTIAL'):
            use, reason = 'B', '已知通过/部分通过，保留为回归资产（本轮未直接采用）'
        else:
            use, reason = 'C', '失败集中在身份状态机/流程门禁，rsi_loop 无法注入前置状态，本轮不可表达'
        rows.append({
            'source_file': path,
            'version': version,
            'case_id': cid,
            'suite': row.get('family', ''),
            'verdict': row['verdict'],
            'attribution': '',
            'intent_templates': '',
            'use': use,
            'reason': reason,
            'mapped_case': '',
        })
    return rows


def main():
    rows = v3_rows() + holdout_rows('data/independent-holdout/results-v1.json', 'v1-20261107') \
        + holdout_rows('data/independent-holdout/results-v2.json', 'v2-20261108')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row['use']] = counts.get(row['use'], 0) + 1
    print(json.dumps({'rows': len(rows), 'by_use': counts, 'csv': str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
