import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from evolution import normalized
class ActionScoringTests(unittest.TestCase):
    def test_missing_action_is_not_correct(self):
        base={'intent':'question','topics':['materials']}
        self.assertNotEqual(normalized(base),normalized({**base,'actions':['handoff']}))
