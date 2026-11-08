"""Import reviewed development traces with immutable provenance, not live session IDs."""
import argparse
import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from evolution_store import Store, dumps, redact, now

def main():
    p = argparse.ArgumentParser()
    p.add_argument('manifest', type=Path)
    p.add_argument('--db', type=Path, default=ROOT / 'runtime/mock.sqlite3')
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if manifest.get('role') != 'development' or manifest.get('reviewed') is not True:
        raise ValueError('Only reviewed development evidence is eligible; never import sealed validation data')
    source = (args.manifest.parent / manifest['source']).resolve()
    raw = source.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != manifest['source_sha256']:
        raise ValueError('Source hash mismatch')
    document = json.loads(raw)
    store = Store(args.db)
    prepared = []
    for item in manifest['turns']:
        node = document
        for component in item['path']:
            node = node[component]
        user = node[item['input_key']]
        reply = node[item['response_key']]
        if not isinstance(user, str) or not isinstance(reply, str):
            raise ValueError('Input/response mapping must resolve to strings')
        if item['category'] not in {'intent', 'expression'} or not isinstance(item['note'], str) or not 1 <= len(item['note']) <= 1000:
            raise ValueError('Only reviewed intent/expression feedback belongs in prompt optimization')
        store.version(item['version_id'])
        trace = {'origin': 'evaluation_import', 'source_sha256': sha,
                 'case_id': item['case_id'], 'source_path': item['path'],
                 'source_version': item['version_id'], 'source_hashes': manifest['source_hashes'],
                 'raw_evidence': node}
        # Preserve genuine classifier context when available; do not invent it.
        if item.get('trace_key'):
            trace.update({k:v for k,v in node[item['trace_key']].items()
                          if k in {'decision', 'error', 'classifier_messages'}})
        rid = hashlib.sha256(json.dumps(item['path'], ensure_ascii=False).encode()).hexdigest()
        prepared.append((item, user, reply, trace, rid))
    imported = []
    with closing(store.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        for item, user, reply, trace, rid in prepared:
            sid = 'evaluation-' + sha
            db.execute('INSERT OR IGNORE INTO conversation_turns(session_id,request_id,application_id,version_id,input,response,status,trace,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (sid,rid,'QUAL-001',item['version_id'],redact(user),redact(reply),'evaluation_import',dumps(trace),now(),now()))
            tid = db.execute('SELECT id FROM conversation_turns WHERE session_id=? AND request_id=?',(sid,rid)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO evolution_feedback VALUES(?,?,?,?)', (tid,item['category'],redact(item['note']),now()))
            imported.append({'case_id':item['case_id'],'turn_id':tid})
        db.commit()
    print(json.dumps(imported, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
