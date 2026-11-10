"""把四个综合案例扩成"一个客户多条同类小类"，只用同一材料家族，不跨类混合。

设计取舍：
- 只动 qualification_cases.json 里 4 个综合案例的 issues，其余字段一字不动；
  Q01/Q02 是手写的原始两条，永远保留在前，新增条目追加在后。
- 候选只从同一 material_type 的目录条目里抽，跨家族混问题不合业务事实。
- 组合是真随机：重跑一次就换一组。但只在"生成数据"这一刻随机，写进文件后固定；
  客户手上的资料不能每通电话都变，那是编事实，评测也没法复现。
- 幂等：每次都从"手写的前两条"重建，重复执行不会越加越多；内容变了自动升 seed_revision。
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/synthetic'
CASES = DATA / 'qualification_cases.json'
# 一个客户几条小类：随机 2~3 条。再多一通电话就念不完了（一条约 35~45 字）。
MIN_ISSUES, MAX_ISSUES = 2, 3
# 条数或组合变了就自动升版本号，库里才会覆盖。
SEED_REVISION = 3


def main():
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    catalog = json.loads((DATA / 'issue_catalog.json').read_text(encoding='utf-8'))
    by_material = {}
    for row in catalog:
        by_material.setdefault(row['material_type'], []).append(row)

    changed = []
    for case in cases:
        issues = case['issues']
        base = [i for i in issues if i['issue_id'] in {'Q01', 'Q02'}]
        if not base:
            continue
        family = base[0]['material_type']
        used = {i['subtype_code'] for i in base}
        material_id = base[0]['material_id']
        candidates = [row for row in by_material[family] if row['code'] not in used]
        # 跳过和已有条目"同一种毛病"的候选（如已有'采购凭证缺失'就不再要'购货发票缺失'），
        # 否则随机组合会出现两句几乎一样的话。
        used_tails = {label[-2:] for label in [i['subtype_label'] for i in base]}
        distinct = [row for row in candidates if row['label'][-2:] not in used_tails]
        total = random.randint(MIN_ISSUES, MAX_ISSUES)
        picked = random.sample(distinct, min(max(0, total - len(base)), len(distinct)))
        rebuilt = list(base)
        for n, row in enumerate(picked, len(base) + 1):
            rebuilt.append({
                'issue_id': f'Q{n:02d}',
                'material_id': material_id,
                'material_type': family,
                'subtype_code': row['code'],
                'subtype_label': row['label'],
                'reason': row['reason'],
                'action': row['action'],
                'evidence_id': row['evidence_id'],
            })
        if rebuilt != issues:
            # 内容变了就单调 +1，保证库里既有应用会被覆盖。
            case['seed_revision'] = max(case.get('seed_revision', 1), SEED_REVISION) + 1
        case['issues'] = rebuilt
        changed.append((case['application_id'], [i['subtype_label'] for i in rebuilt]))

    CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    for app_id, labels in changed:
        print(f'{app_id}（{len(labels)} 条）：' + ' → '.join(labels))


if __name__ == '__main__':
    sys.exit(main())
