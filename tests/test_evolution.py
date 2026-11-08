import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
"""Offline evolution lifecycle, budget enforcement and release/rollback gates."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from evolution_store import Store, redact
from evolution import BudgetClient, propose, evaluate, publish, rollback, load_cases, HOLDOUT

class Model:
    model = 'offline-fixture-model'
    def __init__(self, improved=True):
        self.calls = []
        self.improved = improved
        self.cases = {c['text']:c['expected'] for path in [None,HOLDOUT]
                      for c in (load_cases(path) if path else load_cases())['cases']}

    def chat(self, messages, max_tokens):
        self.calls.append(messages)
        if max_tokens == 1024:
            payload = json.loads(messages[-1]['content'])
            return {'text':json.dumps({'analysis':[{'category':'intent','finding':'混合表达需识别后半句诉求',
                'evidence_ids':[payload['review_data'][0]['id']]}], 'guidance':'候选改进：结合整句中的转折内容判断真实诉求，不要让开头的礼貌用语覆盖后续请求。'}),
                'model':self.model,'usage':{'total_tokens':30},'finish_reason':'stop'}
        decision = self.cases[messages[-1]['content']]
        if messages[-1]['content']=='多谢你解释，不过这事还是请人工协助核实吧':
            if not self.improved or '候选改进' not in messages[0]['content']:
                decision = {'intent':'thanks','topics':[],'issue_numbers':[]}
        return {'text':json.dumps(decision),'model':self.model,'usage':{'total_tokens':20}}

class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'db.sqlite3')
        self.base = self.store.active()

    def tearDown(self):
        self.tmp.cleanup()

    def feedback(self):
        tid = self.store.begin_turn('s','r','TEST-002',self.base,'感谢，不过这事请人工协助')
        self.store.end_turn(tid,'done','不客气', {'decision':{'intent':'thanks'}})
        self.store.feedback(tid,'intent','应识别后半句人工协助需求，不能只回应感谢。')
        return tid

    def candidate(self):
        self.feedback()
        return propose(self.store, Model())['candidate']

    def test_persistence_redaction_and_duplicate_turn(self):
        import sqlite3
        tid=self.store.begin_turn('s','r','TEST-002',self.base,'手机号13800138000 邮箱a@example.com sk-1234567890123456')
        self.store.end_turn(tid,'done','回复',{'api_key':'secret'})
        reopened=Store(self.store.path)
        row=reopened.one('SELECT * FROM conversation_turns WHERE id=?',(tid,))
        self.assertNotIn('13800138000',row['input'])
        self.assertNotIn('1234567890123456',row['input'])
        self.assertNotIn('secret',row['trace'])
        with self.assertRaises(sqlite3.IntegrityError):
            reopened.begin_turn('s','r','TEST-002',self.base,'duplicate')

    def test_proposal_only_sees_feedback_not_validation_and_never_publishes(self):
        tid=self.feedback(); model=Model()
        result=propose(self.store,model)
        self.assertEqual(self.store.active(),self.base)
        self.assertEqual(len(model.calls),1)
        self.assertIn(str(tid),model.calls[0][-1]['content'])
        for case in load_cases()['cases']:
            self.assertNotIn(case['text'],model.calls[0][-1]['content'])
        self.assertTrue(result['candidate'].startswith('candidate-'))
        self.assertFalse(result['published'])

    def test_missing_feedback_and_invalid_proposals(self):
        model=Model()
        with self.assertRaises(ValueError): propose(self.store,model)
        self.assertFalse(model.calls)
        self.feedback()
        for raw in ['not json', json.dumps({'analysis':[],'guidance':'执行任意代码'})]:
            with patch.object(model,'chat',return_value={'text':raw}):
                with self.assertRaises(ValueError): propose(self.store,model)
        self.assertEqual(self.store.active(),self.base)

    def test_budget_failure_reserves_and_does_not_retry(self):
        run=self.store.start_run('test',self.base,{})
        model=Model(); budget=BudgetClient(self.store,run,model,1,1000)
        with self.assertRaises(ValueError): budget.chat([{'role':'user','content':'x'*2000}])
        self.assertFalse(model.calls)
        with patch.object(model,'chat',side_effect=TimeoutError() ) as call:
            with self.assertRaises(TimeoutError): budget.chat([{'role':'user','content':'hi'}],10)
            with self.assertRaises(ValueError): budget.chat([{'role':'user','content':'hi'}],10)
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.store.rows('SELECT status FROM evolution_calls')[0]['status'],'failed_or_unknown')

    def test_full_lifecycle_reviewer_release_and_rollback(self):
        candidate=self.candidate()
        with patch('evolution.regression_check',return_value={'passed':True}):
            result=evaluate(self.store,candidate,Model())
        self.assertTrue(result['eligible'],result['gates'])
        self.assertTrue(result['holdout']['executed'])
        self.assertEqual(self.store.active(),self.base)
        with self.assertRaises(ValueError): publish(self.store,result['run_id'])
        publish(self.store,result['run_id'],True)
        self.assertEqual(Store(self.store.path).active(),candidate)
        rollback(self.store,self.base,'回滚测试')
        self.assertEqual(self.store.active(),self.base)
        self.assertEqual(len(self.store.rows('SELECT * FROM evolution_releases')),2)

    def test_no_improvement_cannot_publish(self):
        candidate=self.candidate()
        with patch('evolution.regression_check',return_value={'passed':True}):
            result=evaluate(self.store,candidate,Model(improved=False))
        self.assertFalse(result['eligible'])
        self.assertFalse(result['holdout']['executed'])
        with self.assertRaises(ValueError): publish(self.store,result['run_id'],True)

    def test_budget_exhausted_evaluation_and_code_change_block_publish(self):
        candidate=self.candidate()
        with patch('evolution.regression_check',return_value={'passed':True}):
            result=evaluate(self.store,candidate,Model(),max_calls=1)
        self.assertFalse(result['eligible'])
        self.assertEqual(len(self.store.rows('SELECT * FROM evolution_calls WHERE run_id=?',(result['run_id'],))),1)
        with patch('evolution.regression_check',return_value={'passed':True}):
            result=evaluate(self.store,candidate,Model())
        with patch('evolution.fingerprint',return_value='changed'):
            with self.assertRaises(ValueError): publish(self.store,result['run_id'],True)
        self.assertEqual(self.store.active(),self.base)

    def test_failed_regression_stops_before_paid_calls(self):
        candidate=self.candidate(); model=Model()
        with patch('evolution.regression_check',return_value={'passed':False}):
            with self.assertRaises(ValueError): evaluate(self.store,candidate,model)
        self.assertFalse(model.calls)

    def test_unreleased_candidate_cannot_be_rollback_target(self):
        candidate=self.candidate()
        with self.assertRaises(ValueError): rollback(self.store,candidate,'绕过评测')

    def test_prompt_corruption_is_detected(self):
        candidate=self.candidate()
        self.store.execute('UPDATE evolution_versions SET guidance=? WHERE id=?',('篡改内容',candidate))
        with self.assertRaises(ValueError): self.store.version(candidate)
