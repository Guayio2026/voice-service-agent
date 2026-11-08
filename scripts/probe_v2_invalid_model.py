"""V030 补充探测：非法模型 JSON 是否真的降级为澄清。

V030 用"我是负责人。"触发注入，但这句话在 graph_gate 里走精确短语白名单，
根本不经过模型解析，注入没有生效。这里改用一句白名单外的自然话术，
直接验证"模型输出不可解析"这条路径的真实行为。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from business_agent import Agent                                    # noqa: E402
from server import Backend                                          # noqa: E402
import run_holdout_v1 as V1                                          # noqa: E402

APP = 'QUAL-001'


async def probe(text, invalid):
    db = str(Path(tempfile.mkdtemp()) / 'p.db')
    backend = Backend(db, faults=True)
    sid = backend.call('start_session', {'application_id': APP})['session_id']
    agent = Agent(backend, sid, APP)
    calls = []
    orig = backend.call
    backend.call = lambda n, p, f=None: (calls.append(n), orig(n, p, f))[1]
    await agent.turn('开始通话', V1.ScriptedModel(), False)
    calls.clear()
    model = V1.ScriptedModel()
    model.kind = V1.identity_kind(text)
    model.dec = V1.decision(text)
    model.invalid = invalid
    out = await agent.turn(text, model, False)
    return {'text': text, 'invalid': invalid, 'reply': out['text'],
            'calls': calls, 'identity': backend.identity_status(sid),
            'step': agent.step, 'records': V1.db_count(db, 'records')}


async def main():
    rows = [
        await probe('我这边就是管这事的，你把情况说一下。', '{kind: yes,,}'),
        await probe('我这边就是管这事的，你把情况说一下。', '{"kind": "yes", "extra": 1}'),
        await probe('我这边就是管这事的，你把情况说一下。', None),
    ]
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    Path(ROOT / 'data/independent-holdout/probe-invalid-model.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')


asyncio.run(main())
