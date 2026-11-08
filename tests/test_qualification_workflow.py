import legacy_fixture  # Test-only historical cases; production seeds only four qualification scenarios.
import sys
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server import Backend, Error, connect
from business_agent import Agent
from grounding import ground_application


class Model:
    def chat(self, *args):
        return {'text': '{"intent":"continue"}'}


class QualificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_stage_flow_and_real_rejection_separation(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Backend(Path(tmp)/'db')
            sid = b.call('start_session', {'application_id':'QUAL-005'})['session_id']
            a = Agent(b, sid, 'QUAL-005')
            await a.turn('开始通话', Model(), False)
            await a.turn('是的', Model(), False)
            reply = await a.turn('方便', Model(), True)
            for text in ['商标注册证', '品牌授权链', '银行流水', '食品经营许可']:
                self.assertIn(text, reply['text'])
            self.assertFalse(reply['output_guard']['blocked'])
            await a.turn('确认登记', Model(), True)
            self.assertEqual(b.workflow(sid)['stage'], 'closing')
            with closing(connect(b.path)) as db:
                stages = {r[0] for r in db.execute('SELECT stage FROM call_stage_events WHERE session=?', (sid,))}
                self.assertEqual(stages, {'introduction','identity','explanation','closing'})
                self.assertEqual(db.execute("SELECT status FROM qualification_rejections WHERE application='QUAL-005'").fetchone()[0], 'not_connected')
            self.assertEqual(b.workflow(sid)['delivery_status'], 'not_verified')

    async def test_catalog_grounding_scope_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'db'
            b = Backend(path)
            for n in range(1, 6):
                app_id = f'QUAL-{n:03}'
                sid = b.call('start_session', {'application_id':app_id})['session_id']
                with self.assertRaises(Error):
                    b.call('get_application', {'session_id':sid,'application_id':app_id})
                b.call('verify_identity', {'session_id':sid,'is_responsible':True})
                app = b.call('get_application', {'session_id':sid,'application_id':app_id})
                self.assertEqual(ground_application(app)['grounding']['status'], 'supported')
            b = Backend(path)
            with closing(connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM qualification_material_types').fetchone()[0], 4)
                self.assertEqual(db.execute("SELECT count(*) FROM qualification_applications WHERE id LIKE 'QUAL-%'").fetchone()[0], 5)

    async def test_legacy_migration_does_not_invent_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, app TEXT, identity TEXT)')
                db.execute("INSERT INTO sessions VALUES('old','TEST-001','verified')")
                db.commit()
            b = Backend(path)
            self.assertEqual(b.workflow('old')['stage'], 'unknown')
            self.assertEqual(b.workflow('old')['tracking_origin'], 'legacy')
            with closing(connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM call_stage_events').fetchone()[0], 0)
