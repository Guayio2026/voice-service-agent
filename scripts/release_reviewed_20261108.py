"""One-time operator-authorized release; does not relabel automatic gates."""
import hashlib
import json
import sqlite3
import sys
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from evolution_store import Store, now

TARGET = 'candidate-561a113dbc8f'
EXPECTED = '4baf10ee58d2a2f5f40b87c1c0547001e11f7cd3c3c4bc1166640509126704e1'


def main():
    source = Store(ROOT / 'runtime/rsi-existing-data-r2.sqlite3')
    production = Store(ROOT / 'runtime/mock.sqlite3')
    candidate = source.version(TARGET)
    assert candidate['content_hash'] == EXPECTED, 'Candidate differs from reviewed evidence'
    previous = production.active()
    assert previous == candidate['parent'], 'Production baseline changed or already released'
    evidence = ROOT / 'docs/rsi-diagnose-20261108.md'
    report = evidence.read_text(encoding='utf-8')
    assert TARGET in report and EXPECTED in report
    # Verify live service availability before changing the production pointer.
    with urllib.request.urlopen('http://127.0.0.1:8766/api/status', timeout=10) as response:
        assert response.status == 200
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = ROOT / 'runtime' / ('before-reviewed-release-' + stamp + '.sqlite3')
    with closing(production.connect()) as db, closing(sqlite3.connect(backup)) as destination:
        db.backup(destination)
    metadata = {'authorization':'User explicitly requested publication after Buddy diagnostics',
                'evidence':str(evidence), 'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
                'candidate_hash':EXPECTED, 'previous':previous, 'backup':str(backup),
                'automatic_gates_passed':False, 'holdout_executed':False,
                'scope':'Operator-reviewed release; previous rejected cycle remains unchanged'}
    run_id = production.start_run('operator_reviewed_release', TARGET, metadata)
    with closing(production.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        assert db.execute('SELECT active FROM evolution_state WHERE id=1').fetchone()[0] == previous
        fields = ('id','parent','base_hash','guidance','content_hash','analysis','created_at')
        db.execute('INSERT INTO evolution_versions VALUES(?,?,?,?,?,?,?)', tuple(candidate[k] for k in fields))
        db.execute('UPDATE evolution_state SET active=? WHERE id=1', (TARGET,))
        db.execute('INSERT INTO evolution_releases(previous,target,action,run_id,reason,created_at) VALUES(?,?,?,?,?,?)',
                   (previous,TARGET,'operator_publish',run_id,metadata['authorization'],now()))
        db.commit()
    try:
        assert production.version()['content_hash'] == EXPECTED
        request = urllib.request.Request('http://127.0.0.1:8766/api/session',
            data=json.dumps({'application_id':'QUAL-001'}).encode(),
            headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:8766'})
        with urllib.request.urlopen(request, timeout=10) as response:
            session = json.load(response)['session_id']
        event = production.one("SELECT payload FROM conversation_events WHERE session_id=? AND kind='session_created' ORDER BY id DESC LIMIT 1", (session,))
        assert event and json.loads(event['payload'])['version_id'] == TARGET, 'Live service did not load candidate'
        result = {**metadata,'run_id':run_id,'active':production.active(),
                  'live_session_id':session,'live_loaded_version':TARGET,'status':'published'}
        production.finish_run(run_id,'published',result)
    except Exception:
        with closing(production.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            assert db.execute('SELECT active FROM evolution_state WHERE id=1').fetchone()[0] == TARGET
            db.execute('UPDATE evolution_state SET active=? WHERE id=1',(previous,))
            db.execute('INSERT INTO evolution_releases(previous,target,action,run_id,reason,created_at) VALUES(?,?,?,?,?,?)',
                       (TARGET,previous,'operator_rollback',run_id,'Live loading verification failed',now()))
            db.commit()
        production.finish_run(run_id,'rolled_back',metadata)
        raise
    output = ROOT / 'docs' / ('release-reviewed-' + stamp + '.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'active':TARGET,'live_session_id':session,'report':str(output),'backup':str(backup)},ensure_ascii=False))


if __name__ == '__main__':
    main()
