"""Seed versioned synthetic qualification records without overwriting stored cases."""
import json
from pathlib import Path


def migrate(db, cases, catalog):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS qualification_issue_types(code TEXT PRIMARY KEY, material_type TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS qualification_application_versions(application TEXT, revision INTEGER, payload TEXT NOT NULL, PRIMARY KEY(application,revision));
    CREATE TABLE IF NOT EXISTS qualification_issue_classifications(application TEXT, issue TEXT, material_id TEXT NOT NULL, subtype_code TEXT NOT NULL, PRIMARY KEY(application,issue));
    CREATE TABLE IF NOT EXISTS qualification_material_types(code TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS qualification_applications(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS qualification_materials(application TEXT NOT NULL, id TEXT NOT NULL,
        material_type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(application,id));
    CREATE TABLE IF NOT EXISTS qualification_issues(application TEXT NOT NULL, id TEXT NOT NULL,
        material_type TEXT, evidence_id TEXT NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL,
        PRIMARY KEY(application,id));
    CREATE TABLE IF NOT EXISTS qualification_rejections(application TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'not_connected', written_opinion TEXT,
        external_receipt TEXT, CHECK(status != 'confirmed' OR external_receipt IS NOT NULL));
    ''')
    db.execute('BEGIN IMMEDIATE')
    try:
        issue_types = json.loads((Path(__file__).resolve().parents[1]/'data/synthetic/issue_catalog.json').read_text(encoding='utf-8'))
        types = {r['code']: r for r in issue_types}
        for row in issue_types:
            db.execute('INSERT OR REPLACE INTO qualification_issue_types VALUES(?,?,?)', (row['code'], row['material_type'], json.dumps(row, ensure_ascii=False)))
        for row in catalog:
            db.execute('INSERT OR IGNORE INTO qualification_material_types VALUES(?,?)',
                       (row['code'], json.dumps(row, ensure_ascii=False)))
        for app in cases.values():
            for issue in app['issues']:
                if 'subtype_code' in issue:
                    category = types[issue['subtype_code']]
                    if category['material_type'] != issue['material_type'] or not any(m['material_id'] == issue['material_id'] and m['material_type'] == issue['material_type'] for m in app['materials']):
                        raise ValueError('Issue classification does not match material')
            old = db.execute('SELECT payload FROM qualification_applications WHERE id=?', (app['application_id'],)).fetchone()
            if old and app.get('seed_revision', 1) > json.loads(old[0]).get('seed_revision', 1):
                db.execute('INSERT OR IGNORE INTO qualification_application_versions VALUES(?,?,?)', (app['application_id'], json.loads(old[0]).get('seed_revision', 1), old[0]))
                db.execute('DELETE FROM qualification_applications WHERE id=?', (app['application_id'],))
                for table in ('qualification_materials','qualification_issues','qualification_issue_classifications'):
                    db.execute(f'DELETE FROM {table} WHERE application=?', (app['application_id'],))
            inserted = db.execute('INSERT OR IGNORE INTO qualification_applications VALUES(?,?)',
                                 (app['application_id'], json.dumps(app, ensure_ascii=False))).rowcount
            if not inserted:
                continue
            for material in app.get('materials', []):
                db.execute('INSERT INTO qualification_materials VALUES(?,?,?,?)',
                           (app['application_id'], material['material_id'], material['material_type'], json.dumps(material, ensure_ascii=False)))
            for issue in app['issues']:
                if 'subtype_code' in issue:
                    db.execute('INSERT INTO qualification_issue_classifications VALUES(?,?,?,?)', (app['application_id'], issue['issue_id'], issue['material_id'], issue['subtype_code']))
                db.execute('INSERT INTO qualification_issues VALUES(?,?,?,?,?,?)',
                           (app['application_id'], issue['issue_id'], issue.get('material_type'), issue['evidence_id'], issue['reason'], issue['action']))
            db.execute('INSERT OR IGNORE INTO qualification_rejections(application) VALUES(?)', (app['application_id'],))
        db.commit()
    except Exception:
        db.rollback()
        raise


def application(db, app_id):
    row = db.execute('SELECT payload FROM qualification_applications WHERE id=?', (app_id,)).fetchone()
    if row is None:
        raise ValueError('Application not found')
    return json.loads(row[0])
