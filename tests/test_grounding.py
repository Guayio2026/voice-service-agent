import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import grounding
from server import CASES, Backend, Error
from business_agent import Agent

class GroundingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'rules.json'
        self.rows = list(grounding.load_rules().values())
        self.path.write_text(json.dumps(self.rows), encoding='utf-8')
    def tearDown(self):
        self.tmp.cleanup()
    def check(self):
        return grounding.ground_application(CASES['TEST-001'], self.path, date(2026,11,7))['grounding']
    def test_valid_has_named_synthetic_source(self):
        result = self.check()
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['citations'][0]['id'], 'TEST-R01')
        self.assertTrue(result['citations'][0]['synthetic'])
    def test_missing_corrupt_duplicate_expired_inactive_and_version(self):
        for field, value in [('valid_until','2026-01-02'),('valid_from','2027-01-01'),('status','withdrawn'),('version','other'),('action','保证通过')]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            self.path.write_text(json.dumps(rows), encoding='utf-8')
            result = self.check()
            self.assertEqual(result['status'], 'needs_verification')
            self.assertEqual(result['citations'], [])
        for value in ('broken', '[]', json.dumps([self.rows[0],self.rows[0]])):
            self.path.write_text(value, encoding='utf-8')
            self.assertEqual(self.check()['status'], 'needs_verification')
    async def test_updated_source_invalidates_confirmation(self):
        b=Backend(Path(self.tmp.name)/'db')
        sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
        b.call('verify_identity',{'session_id':sid,'is_responsible':True})
        a=Agent(b,sid,'TEST-001')
        model=Mock(); model.chat.return_value={'text':'{"intent":"continue"}'}
        with patch.object(grounding,'RULES',self.path):
            reply=await a.turn('方便',model,True)
            self.assertEqual(reply['grounding']['status'],'supported')
            self.rows[0]['status']='withdrawn'
            self.path.write_text(json.dumps(self.rows),encoding='utf-8')
            reply=await a.turn('确认登记',model,True)
            self.assertIn('原确认已失效',reply['text'])
            self.assertIsNone(a.pending)
            self.assertEqual(b.call('get_results',{'session_id':sid})['results'],[])
    async def test_no_action_given_when_rule_missing(self):
        b=Backend(Path(self.tmp.name)/'db')
        sid=b.call('start_session',{'application_id':'TEST-001'})['session_id']
        b.call('verify_identity',{'session_id':sid,'is_responsible':True})
        a=Agent(b,sid,'TEST-001'); model=Mock()
        model.chat.return_value={'text':'{"intent":"question","topics":["action"]}'}
        self.path.write_text('[]',encoding='utf-8')
        with patch.object(grounding,'RULES',self.path):
            reply=await a.turn('怎么补交',model,True)
        self.assertNotIn('补充虚构材料页',reply['text'])
        self.assertEqual(reply['grounding']['citations'],[])
        self.assertTrue(a.unresolved)
