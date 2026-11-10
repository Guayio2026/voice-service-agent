"""遍历案例池，逐条小类跑标准三问，产出可核对的对照表。

设计取舍：
- 被测对象是真实的 business_agent.Agent + Backend（临时库），不是另写的 mock；
  模型输出受控（固定 intent/topics），因为这里验的是"这条小类能不能被正确说出口"，
  不是意图分类能力——意图分类由 tests/test_agent 等套件负责。
- 遍历，不随机。顺序固定为 application_id 升序，任何人重跑结果逐字一致。
- 默认不合成语音（省钱）。加 --tts 才会真的走 TTS，用于预热音频缓存并核对时长。
"""
import argparse
import asyncio
import csv
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from business_agent import Agent  # noqa: E402
from server import Backend, CASES, FEATURED_CASES  # noqa: E402

QUESTIONS = [
    ('补正事项', {'intent': 'question', 'topics': ['materials']}),
    ('为什么要补', {'intent': 'question', 'topics': ['reason']}),
    ('怎么处理', {'intent': 'question', 'topics': ['action']}),
]


class Model:
    def __init__(self, payload): self.payload = payload
    def chat(self, *a, **k): return {'text': json.dumps(self.payload, ensure_ascii=False)}


async def sweep(pool, rows, out_csv):
    with tempfile.TemporaryDirectory() as d:
        backend = Backend(Path(d) / 'db')
        for app_id in pool:
            app = CASES[app_id]
            issue = app['issues'][0]
            sid = backend.call('start_session', {'application_id': app_id})['session_id']
            backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
            agent = Agent(backend, sid, app_id)
            replies, blocked = [], False
            for _, decision in QUESTIONS:
                reply = await agent.turn('测试提问', Model(decision), True)
                replies.append(reply['text'])
                blocked = blocked or bool(reply.get('output_guard', {}).get('blocked'))
            named = all(f"「{issue['subtype_label']}」" in r for issue in app['issues'] for r in replies)
            rows.append({
                'application_id': app_id,
                'business': app.get('application_type', ''),
                'subtype_code': issue['subtype_code'],
                'subtype_label': issue['subtype_label'],
                'named': named,
                'blocked': blocked,
                'answer_len': sum(len(r) for r in replies),
                'answer': replies[0],
            })

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='', help='只跑指定前缀，如 QUAL-1')
    parser.add_argument('--sample', type=int, default=0, help='随机抽 N 条跑；配 --seed 可复现')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', default='runtime/subtype-sweep.csv')
    args = parser.parse_args()

    pool = sorted(k for k, v in CASES.items() if v.get('selectable') or k in FEATURED_CASES)
    if args.only:
        pool = [k for k in pool if k.startswith(args.only)]
    if args.sample and 0 < args.sample < len(pool):
        pool = sorted(random.Random(args.seed).sample(pool, args.sample))
    if not pool:
        raise SystemExit('没有匹配的案例')

    rows = []
    asyncio.run(sweep(pool, rows, ROOT / args.out))

    named = [r for r in rows if r['named']]
    blocked = [r for r in rows if r['blocked']]
    labels = {r['subtype_label'] for r in rows}
    print(f'遍历 {len(rows)} 条案例 / {len(labels)} 个不同小类')
    print(f'三轮回答都说出小类名称：{len(named)}/{len(rows)}')
    print(f'被输出守卫拦截：{len(blocked)}')
    print(f'平均回答字数：{sum(r["answer_len"] for r in rows) / len(rows):.0f}')
    miss = [r['application_id'] for r in rows if not r['named']]
    if miss:
        print('未说出的案例：', miss[:10])
    print(f'对照表已写入 {args.out}')


if __name__ == '__main__':
    main()
