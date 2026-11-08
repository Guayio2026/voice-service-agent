import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from input_guard import inspect_input
from output_guard import inspect_output

class GuardContractTests(unittest.TestCase):
    def test_invalid_input(self):
        for value in (None, {}, '', ' '):
            result=inspect_input(value)
            self.assertFalse(result['safe'])
            self.assertTrue(result['replacement_phrase'])
    def test_obfuscated_phone_removed(self):
        result=inspect_input('电话１３８００１３８０００')
        self.assertTrue(result['safe'])
        self.assertTrue(result['redacted'])
        self.assertNotIn('１３８',result['text'])
    def test_output_privacy_and_stale_evidence(self):
        for text, kwargs, reason in [('电话13800138000',{},'sensitive_data_disclosure'),('材料需要补交',{'grounding':{'registry_hash':'old'}},'stale_evidence')]:
            result=inspect_output(text,verified=True,**kwargs)
            self.assertFalse(result['safe'])
            self.assertIn(reason,result['rules'])
            self.assertEqual(result['text'],result['replacement_phrase'])
            self.assertTrue(inspect_output(result['text'],verified=True)['safe'])
    def test_negation_is_not_a_promise(self):
        self.assertTrue(inspect_output('不能保证通过。',verified=True)['safe'])
        self.assertFalse(inspect_output('不能保证通过。但是肯定通过。',verified=True)['safe'])
