"""Offline lifecycle checks for WorkBuddy; no provider calls or production DB."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import rsi_loop
from evolution_store import Store


class RSILifecycleTests(unittest.TestCase):
    def exercise(self, *, improvement=True, monitor_pass=True, activate=True, feedback=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = Store(root/'sandbox.db')
        baseline = store.active()
        config = {'repeat':3}
        for role in ('development','validation','holdout'):
            path=root/(role+'.json')
            path.write_text(json.dumps({'role':role,'reviewed':True,'cases':[{
                'id':role+str(i),'family':role+str(i),'turns':[
                    {'text':role+' one','expect':{'identity':'unverified','records':0}},
                    {'text':role+' two','expect':{'identity':'unverified','records':0}}]} for i in range(2)]}),encoding='utf-8')
            config[role]=str(path)
        turn=store.begin_turn('test','0','QUAL-001',baseline,'test failure')
        store.end_turn(turn,'done','test',{'classifier_messages':[{'role':'user','content':'test failure'}]})
        store.feedback(turn,'intent','Fixture feedback for lifecycle test only')
        counts={}
        async def score(store_arg,version,client,suite,repeat,collect=False,run_id=''):
            role=suite['role'];counts[role]=counts.get(role,0)+1
            candidate=version['id']!=baseline
            passed=True
            if role=='development':
                passed = candidate and improvement
            if role=='validation':
                if counts[role]>2:
                    passed=monitor_pass
            rows=[{'case':c['id'],'attempt':a,'turn':i,'passed':passed,
                   'checks':{'identity':True,'records':True,'decision':passed}}
                  for c in suite['cases'] for a in range(repeat) for i in range(2)]
            return {'rows':rows,'feedback_ids':[turn] if collect and feedback else [],
                    'passed':passed,'correct':len(rows) if passed else 0,'total':len(rows)}
        def proposal(store_arg,client,**kwargs):
            self.assertEqual([r['id'] for r in store_arg.review_items()],[turn])
            cid=store_arg.candidate(baseline,'这是一条仅用于离线生命周期验证的候选指导语。',[])
            return {'candidate':cid}
        with patch.object(rsi_loop,'score',score), patch.object(rsi_loop,'propose',proposal), patch.object(
                rsi_loop,'usage_stats',return_value={'reported':True,'tokens':100,'p95_ms':10}):
            result=rsi_loop.run(store,object(),config,root/'reports',activate)
        self.assertTrue((root/'reports'/result['run_id']/'report.json').is_file())
        return result,store,baseline

    def test_no_feedback_is_audited_without_candidate(self):
        result,store,baseline=self.exercise(feedback=False)
        self.assertEqual(result['status'],'no_eligible_feedback')
        self.assertEqual(store.active(),baseline)
        self.assertNotIn('candidate',result)

    def test_non_improving_candidate_cannot_consume_holdout_or_publish(self):
        result,store,baseline=self.exercise(improvement=False)
        self.assertEqual(result['status'],'rejected_no_stable_gain')
        self.assertEqual(store.active(),baseline)
        self.assertEqual(store.one('SELECT COUNT(*) AS n FROM rsi_holdout_uses')['n'],0)

    def test_shadow_does_not_switch_active_version(self):
        result,store,baseline=self.exercise(activate=False)
        self.assertEqual(result['status'],'eligible_shadow')
        self.assertEqual(store.active(),baseline)
        self.assertEqual(store.one('SELECT COUNT(*) AS n FROM rsi_holdout_uses')['n'],1)

    def test_passed_candidate_is_activated(self):
        result,store,baseline=self.exercise()
        self.assertEqual(result['status'],'published')
        self.assertEqual(store.active(),result['candidate'])
        self.assertNotEqual(store.active(),baseline)
        self.assertFalse(result['validation']['observed_improvement'])
        self.assertEqual(len(result['development_comparison']['repaired_families']),2)

    def test_monitor_regression_rolls_back(self):
        result,store,baseline=self.exercise(monitor_pass=False)
        self.assertEqual(result['status'],'rolled_back')
        self.assertFalse(result['published'])
        self.assertEqual(store.active(),baseline)
        self.assertEqual([r['action'] for r in store.rows('SELECT action FROM evolution_releases ORDER BY id')],
                         ['rsi_publish','rsi_rollback'])

    def test_switch_rejects_concurrent_version_change(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            baseline=store.active()
            candidate=store.candidate(baseline,'这是一段用于并发版本保护测试的候选指导语。',[])
            with self.assertRaises(ValueError):
                rsi_loop.switch(store,'stale-version',candidate,'test','rsi_publish',rsi_loop.engine_hash())
            self.assertEqual(store.active(),baseline)

    def test_one_family_or_one_lucky_attempt_is_not_stable_gain(self):
        suite={'cases':[{'id':'a','family':'a','turns':[{},{}]}]}
        before={'rows':[{'case':'a','attempt':a,'turn':1,'passed':False,
                         'checks':{'decision':False}} for a in range(3)], 'correct':0}
        after={'rows':[dict(r,passed=True,checks={'decision':True}) for r in before['rows']], 'correct':3}
        result=rsi_loop.development_gain(before,after,suite,3)
        self.assertEqual(result['repaired_families'],['a'])
        self.assertFalse(result['gates']['stable_repairs'])
        after['rows'][1]['passed']=False
        self.assertEqual(rsi_loop.development_gain(before,after,suite,3)['repaired_families'],[])

    def test_new_failure_cannot_hide_inside_an_already_failed_turn(self):
        before={'rows':[{'case':'mixed','attempt':0,'turn':0,'passed':False,
                         'checks':{'decision':False,'contains_any':True}}], 'correct':0}
        after={'rows':[{'case':'mixed','attempt':0,'turn':0,'passed':False,
                        'checks':{'decision':True,'contains_any':False}}], 'correct':0}
        gates=rsi_loop.compare(before,after)
        self.assertFalse(gates['no_assertion_regression'])
        self.assertFalse(gates['hard_constraints'])
