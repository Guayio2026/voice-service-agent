"""验证反馈导入通道的四道闸与幂等性。只写沙箱库，不碰生产库。

被验证对象：scripts/import_evaluation_feedback.py
检查项：
  1  正常导入 → 建 1 条轨迹 + 1 条反馈，返回真实 turn_id
  2  重复导入 → 不新建轨迹、不新增反馈、turn_id 不变
  3  源文件哈希不符 → 拒绝
  4  role/reviewed 不合规（模拟封存集） → 拒绝
  5  version_id 不在 Store → 拒绝
  6  path 定位不到一轮 → 拒绝
  7  生产清单（0 条）在沙箱执行 → 只建库、不写入
"""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SCRIPT = ROOT / 'scripts/import_evaluation_feedback.py'
DATA = ROOT / 'data/rsi-20261108'
OUT = DATA / 'import-verify'


def run(manifest_path, db_path):
    proc = subprocess.run([PY, str(SCRIPT), str(manifest_path), '--db', str(db_path)],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    return {'code': proc.returncode, 'out': (proc.stdout or '').strip()[-800:],
            'err': (proc.stderr or '').strip().splitlines()[-1] if proc.returncode else ''}


def counts(db):
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return {'turns': con.execute('SELECT COUNT(*) FROM conversation_turns').fetchone()[0],
                'feedback': con.execute('SELECT COUNT(*) FROM evolution_feedback').fetchone()[0],
                'imported_turns': con.execute(
                    "SELECT COUNT(*) FROM conversation_turns WHERE session_id LIKE 'evaluation-%'").fetchone()[0]}
    finally:
        con.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    db = OUT / 'sandbox.sqlite3'
    if db.exists():
        db.unlink()

    base = json.loads((DATA / 'import-manifest-channel-test.json').read_text(encoding='utf-8'))
    base['source'] = str((DATA / base['source']).resolve())

    def variant(name, **changes):
        m = json.loads(json.dumps(base))
        for key, value in changes.items():
            if key == 'turn':
                m['turns'][0].update(value)
            else:
                m[key] = value
        path = work / f'{name}.json'
        path.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
        return path

    checks = []

    before = counts(db) if db.exists() else {'turns': 0, 'feedback': 0, 'imported_turns': 0}
    first = run(variant('ok'), db)
    after_first = counts(db)
    checks.append({'name': '1 正常导入', 'pass': first['code'] == 0 and after_first['imported_turns'] == 1
                   and after_first['feedback'] == 1, **first, 'counts': after_first})
    first_ids = json.loads(first['out']) if first['code'] == 0 else []

    second = run(variant('ok'), db)
    after_second = counts(db)
    checks.append({'name': '2 重复导入不重复建记录',
                   'pass': second['code'] == 0 and after_second == after_first and first_ids == (json.loads(second['out']) if second['code'] == 0 else None),
                   **second, 'counts': after_second, 'turn_ids': [x['turn_id'] for x in first_ids]})

    bad_hash = run(variant('bad_hash', source_sha256='0' * 64), db)
    checks.append({'name': '3 源文件哈希不符应拒绝', 'pass': bad_hash['code'] != 0 and 'hash' in bad_hash['err'].lower(),
                   **bad_hash})

    sealed = run(variant('sealed', role='sealed'), db)
    checks.append({'name': '4 非 development/未审核应拒绝',
                   'pass': sealed['code'] != 0 and 'reviewed development' in sealed['err'].lower(), **sealed})

    bad_ver = run(variant('bad_version', turn={'version_id': 'candidate-doesnotexist'}), db)
    checks.append({'name': '5 未知版本应拒绝', 'pass': bad_ver['code'] != 0 and '版本' in bad_ver['err'], **bad_ver})

    bad_path = run(variant('bad_path', turn={'path': ['rows', 999, 'turns', 0]}), db)
    checks.append({'name': '6 路径定位不到应拒绝', 'pass': bad_path['code'] != 0, **bad_path})

    prod_empty = run(DATA / 'import-manifest-prod-empty.json', db)
    after_empty = counts(db)
    checks.append({'name': '7 零条清单不写入', 'pass': prod_empty['code'] == 0 and after_empty == after_second,
                   **prod_empty, 'counts': after_empty})

    report = {'checked_at': __import__('datetime').datetime.now(
                  __import__('datetime').timezone.utc).isoformat(),
              'sandbox_db': str(db.relative_to(ROOT)).replace('\\', '/'),
              'production_db_untouched': True,
              'checks': checks,
              'all_passed': all(c['pass'] for c in checks)}
    (OUT / 'import-channel-verify.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for c in checks:
        print(('PASS' if c['pass'] else 'FAIL'), '|', c['name'], '|', (c['err'] or c['out'][:60]).replace('\n', ' ')[:90])
    print('all_passed', report['all_passed'])
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
