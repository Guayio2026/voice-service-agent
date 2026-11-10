"""案例池验收：入库、接地、播报三层都过一遍。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from business_agent import Agent  # noqa: E402
from grounding import ground_application  # noqa: E402
from server import Backend, CASES  # noqa: E402


class Model:
    def __init__(self, payload): self.payload = payload
    def chat(self, *a, **k): return {'text': json.dumps(self.payload, ensure_ascii=False)}


async def main():
    pool = [k for k, v in CASES.items() if v.get('selectable')]
    print(f'CASES 合计 {len(CASES)}，其中案例池 {len(pool)}')

    broken = [k for k, v in CASES.items() if any(ground_application(v)['grounding']['checks'][i]['status'] != 'valid'
                                                for i in range(len(v['issues'])))]
    print('接地不为 valid 的案例：', broken or '无')

    with tempfile.TemporaryDirectory() as d:
        backend = Backend(Path(d) / 'db')
        listed = backend.call('cases', {})['cases']
        print(f'/cases 返回 {len(listed)} 条（含手写 4 条）')

        spoken, failed = [], []
        for app_id in pool:
            sid = backend.call('start_session', {'application_id': app_id})['session_id']
            backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
            agent = Agent(backend, sid, app_id)
            reply = await agent.turn('请您说明一下需要补正的事项',
                                     Model({'intent': 'question', 'topics': ['materials']}), True)
            text = reply['text']
            label = CASES[app_id]['issues'][0]['subtype_label']
            if f'「{label}」' in text:
                spoken.append(label)
            else:
                failed.append((app_id, label, text[:60]))
        print(f'说出口含小类名称：{len(spoken)}/{len(pool)}，去重 {len(set(spoken))} 个')
        if failed:
            print('未说出的：', failed[:3])
        print('\n样例三条：')
        for app_id in (pool[0], pool[37], pool[99]):
            sid = backend.call('start_session', {'application_id': app_id})['session_id']
            backend.call('verify_identity', {'session_id': sid, 'is_responsible': True})
            reply = await Agent(backend, sid, app_id).turn(
                '为什么要补这个', Model({'intent': 'question', 'topics': ['reason']}), True)
            print(f"  {CASES[app_id]['label']}\n   → {reply['text']}")

asyncio.run(main())
