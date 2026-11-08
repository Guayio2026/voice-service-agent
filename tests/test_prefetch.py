import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from prefetch_agent import PrefetchAgent
class PrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_hit_and_invalidation(self):
        agent=PrefetchAgent(); model=Mock()
        model.chat.return_value={'text':'["补充材料"]'}
        agent.schedule('缺页','questions',model)
        await agent.task
        with patch('prefetch_agent.retrieve',side_effect=AssertionError('cache miss')):
            agent.lookup('补充材料')
        self.assertEqual(agent.hits,1)
        agent.clear()
        self.assertEqual(agent.cache,{})
    async def test_invalid_prediction_falls_back(self):
        agent=PrefetchAgent(); model=Mock()
        model.chat.return_value={'text':'{"tool":"save"}'}
        agent.schedule('你好','questions',model)
        await agent.task
        self.assertEqual(agent.cache,{})
        with patch('prefetch_agent.retrieve',return_value=[]) as lookup:
            self.assertEqual(agent.lookup('任意问题'),[])
            lookup.assert_called_once()
