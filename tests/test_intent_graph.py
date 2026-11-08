import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from intent_graph import INTENT_GRAPH


class IntentGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_runs_graph_and_langchain_messages(self):
        class Model:
            def chat(self, messages, max_tokens):
                assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'system', 'user']
                return {'text': json.dumps({'intent':'question','topics':['action'],'issue_numbers':[2]})}
        out = await INTENT_GRAPH.ainvoke({'prompt':'规则', 'history':[
            {'role':'user','content':'有两项'}, {'role':'assistant','content':'好的'},
            {'role':'user','content':'第二项怎么改'}], 'model':Model()})
        self.assertEqual(out['nodes'], ['prepare_messages','classify','validate'])
        self.assertEqual(out['decision']['issue_numbers'], [2])

    async def test_invalid_and_failed_model_take_fallback(self):
        class Model:
            def __init__(self, fail): self.fail = fail
            def chat(self, *args):
                if self.fail: raise TimeoutError('private provider detail')
                return {'text':'not json'}
        for fail in [False, True]:
            out = await INTENT_GRAPH.ainvoke({'prompt':'规则','history':[{'role':'user','content':'你好'}], 'model':Model(fail)})
            self.assertEqual(out['nodes'][-1], 'fallback')
            self.assertNotIn('private provider detail', str(out))
            self.assertNotIn('decision', out)
