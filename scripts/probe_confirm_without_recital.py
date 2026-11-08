"""探测：无待确认内容时连续两次"确认登记"，是否会写入、以及写入前用户是否真的听到复述。

契约要求是"先复述、再确认、然后才写入"。V023 第 1 轮回复只说"需要先向您复述待登记内容"，
并没有把内容说出来；第 2 轮"确认登记。"就直接落库。这里单独验证这个空档。
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
MAT = ['商标注册证明', '商标有效期证明', '补正通知']


async def main():
    db = str(Path(tempfile.mkdtemp()) / 'p.db')
    backend = Backend(db, faults=True)
    sid = backend.call('start_session', {'application_id': APP})['session_id']
    backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
    agent = Agent(backend, sid, APP)
    await agent.turn('开始通话', V1.ScriptedModel(), False)
    # 与执行器保持一致：开场后复位未解决标记，避免被"未解决问题"门禁挡在前面。
    agent.unresolved = False
    agent.questions = []
    agent.issues = [i['issue_id'] for i in V1.CASE['issues']]
    agent.step = 'availability'
    rows = []
    for text in ('确认登记。', '确认登记。', '确认登记。'):
        model = V1.ScriptedModel()
        model.kind = V1.identity_kind(text)
        model.dec = V1.decision(text)
        out = await agent.turn(text, model, False)
        reply = out['text']
        recap = any(t in reply for t in MAT)
        rows.append({'user': text, 'reply': reply, 'pending': bool(agent.pending),
                     'step': agent.step, 'records': V1.db_count(db, 'records'),
                     '回复含材料复述': recap})
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))


asyncio.run(main())
