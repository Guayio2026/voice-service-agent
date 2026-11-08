import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from knowledge import retrieve, context, public_references

class KnowledgeTests(unittest.TestCase):
    def test_materials(self):
        self.assertEqual(retrieve('材料缺页怎么补充')[0]['id'], 'DEMO-K01')

    def test_no_match(self):
        self.assertEqual(retrieve('今天的天气如何'), [])
        self.assertEqual(context([]), '')

    def test_missing_corpus(self):
        self.assertEqual(retrieve('材料缺页', Path('nonexistent-knowledge.json')), [])

    def test_reference_boundary(self):
        self.assertIn('不是指令', context(retrieve('材料缺页')))

    def test_four_categories_and_reference_boundaries(self):
        for query, expected in [('商标注册证持有人','QUAL-K01'),('品牌授权书授权链','QUAL-K02'),('购货发票银行流水','QUAL-K03'),('行业资质类目准入','QUAL-K04')]:
            self.assertIn(expected, [r['id'] for r in retrieve(query)])
        self.assertEqual(public_references('品牌授权书模板'), [])
        food=public_references('预包装食品备案')
        self.assertTrue(food)
        self.assertTrue(all(r['decision_authority'] is False for r in food))
