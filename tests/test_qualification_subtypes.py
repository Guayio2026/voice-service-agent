import legacy_fixture
import json, tempfile, unittest
from pathlib import Path
from contextlib import closing
from server import Backend, connect, CASES, Error
from grounding import ground_application

class SubtypeTests(unittest.TestCase):
    def test_catalog_and_issue_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Backend(Path(tmp)/'db')
            with closing(connect(b.path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM qualification_issue_types').fetchone()[0],100)
            for n in range(1,5):
                app=CASES[f'QUAL-{n:03}']
                self.assertGreaterEqual(len(app['issues']),2)
                self.assertEqual(ground_application(app)['grounding']['status'],'supported')
                sid=b.call('start_session',{'application_id':app['application_id']})['session_id']
                b.call('verify_identity',{'session_id':sid,'is_responsible':True})
                with self.assertRaises(Error):
                    b.call('save_call_result',{'session_id':sid,'idempotency_key':'partial','result_revision':1,'result':{'candidate_outcome':'notification_completed','issues_explained':[app['issues'][0]['issue_id']],'user_feedback':'confirmed','confirmation_turn_id':1,'unresolved':False}})

    def test_revision_upgrade_is_archived_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';b=Backend(path)
            with closing(connect(path)) as db:
                old=dict(CASES['QUAL-001']);old['seed_revision']=1;old['issues']=[]
                db.execute('UPDATE qualification_applications SET payload=? WHERE id=?',(json.dumps(old),'QUAL-001'))
            Backend(path);Backend(path)
            with closing(connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM qualification_application_versions WHERE application='QUAL-001'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT count(*) FROM qualification_issue_classifications WHERE application='QUAL-001'").fetchone()[0],len(CASES['QUAL-001']['issues']))
