"""从问题目录批量生成合成案例池：一条小类对应一个客户案例。

设计取舍（必须公开）：
- 只做数据，不改运行逻辑。生成结果与手写的 4 个案例结构完全一致，
  下游 qualification_store / grounding / Agent 无需任何特判。
- 一个案例只承载 1 条 issue。理由是外呼场景里客户身上通常只有 1–3 条问题，
  而且 1:1 映射才能证明"目录里 100 条小类都真的说出口过"。
- 完全确定性：不用随机数，索引即顺序，任何人重跑得到同一份文件。
- reason / action 直接取目录原文。grounding 会逐字比对 rules.json，
  任何改写都会让 basis_status 掉成 conflict，变成"依据冲突"。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/synthetic'
OUT = DATA / 'qualification_cases_pool.json'

BUSINESS_BY_MATERIAL = {
    'trademark': '入驻',
    'authorization': '新增品牌',
    'purchase': '新增类目',
    'industry': '入驻',
}
BRANDS = ['青禾', '云栖', '沐野', '星野', '白鹭', '长川', '拾光', '南枝', '木言', '洛川',
          '明野', '时雨', '河图', '半山', '鹿鸣', '松间', '澜山', '砚池', '秋池', '竹里']
CATEGORIES = ['食品（演示）', '美妆（演示）', '家居（演示）', '服饰（演示）', '数码（演示）',
              '母婴（演示）', '宠物（演示）', '图书（演示）', '运动（演示）', '家清（演示）']
SOURCE_SCOPE = '审核发现与整改动作均为合成数据，场景和材料类型参照赛题视频'
# 改了生成内容就把版本 +1，否则库里既有应用会因 INSERT OR IGNORE 保持不变。
SEED_REVISION = 3


def main():
    catalog = json.loads((DATA / 'issue_catalog.json').read_text(encoding='utf-8'))
    material_catalog = json.loads((DATA / 'material_catalog.json').read_text(encoding='utf-8'))
    material_label = {m['code']: m.get('label', m['code']) for m in material_catalog}
    material_order = [m['code'] for m in material_catalog]
    subtype_label = {row['code']: row['label'] for row in catalog}

    cases, seen = [], set()
    for index, row in enumerate(catalog, 1):
        code = row['code']
        if code in seen:
            raise SystemExit(f'目录里出现重复小类编码：{code}')
        seen.add(code)
        app_id = f'QUAL-{100 + index:03d}'
        materials = [{'material_id': f'M{i:02d}', 'material_type': m,
                      'review_status': 'needs_correction' if m == row['material_type'] else 'not_reviewed',
                      'synthetic': True} for i, m in enumerate(material_order, 1)]
        material_id = next(m['material_id'] for m in materials if m['material_type'] == row['material_type'])
        cases.append({
            'application_id': app_id,
            'label': f"{material_label[row['material_type']]}·{row['label']}（演示）",
            'synthetic': True,
            'selectable': True,
            'merchant': f'演示商家{index:03d}',
            'company': f'合成示例商贸公司{index:03d}',
            'store': f'合成示例店铺{index:03d}',
            'brand': '示例品牌' + BRANDS[index % len(BRANDS)],
            'category': CATEGORIES[index % len(CATEGORIES)],
            'application_type': BUSINESS_BY_MATERIAL[row['material_type']],
            'rule_version': 'DEMO-v2',
            'basis_status': 'valid',
            'review_status': 'correction_required',
            'formal_rejection_status': 'not_connected',
            'source_scope': SOURCE_SCOPE,
            'materials': materials,
            'issues': [{
                'issue_id': 'Q01',
                'material_id': material_id,
                'material_type': row['material_type'],
                'subtype_code': code,
                'subtype_label': subtype_label[code],
                'reason': row['reason'],
                'action': row['action'],
                'evidence_id': row['evidence_id'],
            }],
            'seed_revision': SEED_REVISION,
        })

    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')

    by_business = {}
    for case in cases:
        by_business[case['application_type']] = by_business.get(case['application_type'], 0) + 1
    print(f'已写入 {OUT.relative_to(ROOT)}：{len(cases)} 个案例')
    print('按业务：', by_business)
    print('不同小类：', len({c['issues'][0]['subtype_code'] for c in cases}))
    print('每案 issue 条数：', sorted({len(c['issues']) for c in cases}),
          '（组合案例手动维护在 qualification_cases.json，排在各组第一条）')


if __name__ == '__main__':
    sys.exit(main())
