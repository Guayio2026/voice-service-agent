import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from output_guard import inspect_output


class OutputGuardTests(unittest.TestCase):
    def test_risky_examples(self):
        for text in ['补交材料后一定审核通过。', '您的申请已经批准了。',
                     '保证100％通过审核', '保\u200b证审核通过', '这个申请包过。',
                     '已接通真人客服', '请提供身份证号',
                     '无法保证审核通过。但是补交后一定审核通过。']:
            with self.subTest(text=text):
                result = inspect_output(text, verified=True)
                self.assertTrue(result['blocked'])
                self.assertNotEqual(result['text'], text)
                self.assertFalse(inspect_output(result['text'], verified=True)['blocked'])

    def test_normal_examples(self):
        for text in ['无法保证审核通过。补充材料不等于审核通过，最终结果需要以审核结果为准。',
                     '补交材料不一定通过。',
                     '沟通结果已登记。', '已核对，模拟人工协助工单已登记，未接通真人；沟通结果尚未保存。',
                     '资料记载授权材料缺少有效期页，需要补充。',
                     '请不要提供验证码或身份证号。']:
            with self.subTest(text=text):
                self.assertFalse(inspect_output(text, verified=True)['blocked'])

    def test_identity_gate_is_allowlist_not_keyword_search(self):
        result = inspect_output('青禾生活馆缺少有效期页。', verified=False)
        self.assertTrue(result['blocked'])
        self.assertNotIn('青禾', result['text'])
        self.assertFalse(inspect_output(result['text'], verified=False)['blocked'])

    def test_guard_drops_pending_confirmation_and_never_logs_rejected_text(self):
        from business_agent import Agent
        from unittest.mock import MagicMock
        backend = MagicMock()
        backend.identity_status.return_value = 'verified'
        backend.agent_name.return_value = '小语'
        agent = Agent(backend, 'sid', 'app')
        agent.pending = {'action': 'save'}
        reply = agent.reply('您的申请已经批准了。')
        self.assertIsNone(agent.pending)
        self.assertTrue(reply['output_guard']['blocked'])
        self.assertNotIn('已经批准', str(reply))
