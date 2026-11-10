"""Spoken replies carry a length budget, so copy edits cannot grow them again.

The announcement after "方便" is the longest line a caller hears; it is the one
that regressed into officialese before. These tests pin its length and keep the
stored record's official wording intact.
"""
import legacy_fixture
import tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
from server import Backend, CASES
from business_agent import Agent, spoken_issue

BUDGET = 42


class SpeechBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_issue_announcement_stays_short(self):
        cases = [k for k, c in CASES.items() if len(c.get('issues') or []) == 1]
        self.assertGreater(len(cases), 50)
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                b = Backend(Path(d) / 'db')
                sid = b.call('start_session', {'application_id': case})['session_id']
                a = Agent(b, sid, case)
                model = Mock()
                await a.turn('开始通话', model, False)
                a.interrupt()
                await a.turn('是的我是负责人', model, False)
                answer = await a.turn('方便', model, True)
                text = answer['text']
                self.assertLessEqual(len(text), BUDGET, text)
                self.assertNotIn('交由审核人员', text)
                # The archive reminder belongs to the confirmation step, not here.
                self.assertNotIn('存档', text)
                if a.pending is None:
                    # Invalid basis hands off before any requirement is spoken.
                    case_basis = CASES[case].get('basis_status', 'valid')
                    issue_basis = CASES[case]['issues'][0].get('basis_status', 'valid')
                    self.assertTrue('valid' not in {case_basis, issue_basis})
                    continue
                # Stored wording keeps the catalogue's full action text.
                self.assertIn(CASES[case]['issues'][0]['action'].rstrip('。'), a.pending['explanation'])

    def test_spoken_issue_prefers_action_when_label_overflows(self):
        issue = {'subtype_label': '分支机构与持证主体关系不明',
                 'action': '核对总分支机构关系及资质适用范围，补充对应材料或说明后交由审核人员复核'}
        text = spoken_issue(issue)
        self.assertLessEqual(len(text), BUDGET, text)
        self.assertIn('补上对应材料或说明', text)
        self.assertNotIn('交由审核人员', text)


if __name__ == '__main__':
    unittest.main()
