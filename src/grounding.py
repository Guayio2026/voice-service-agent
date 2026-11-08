"""Deterministic evidence validation for synthetic business facts; not similarity confidence."""
import copy
import hashlib
import json
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
RULES = Path(__file__).resolve().parents[1] / 'data/synthetic/rules.json'

def load_rules(path=None):
    try:
        rows = json.loads(Path(path or RULES).read_text(encoding='utf-8'))
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError()
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ('id','version','status','source','source_title','reason','action','valid_from')):
                raise ValueError()
            if row.get('synthetic') is not True or row['id'] in result:
                raise ValueError()
            date.fromisoformat(row['valid_from'])
            if row.get('valid_until') is not None:
                date.fromisoformat(row['valid_until'])
            result[row['id']] = row
        return result
    except (OSError, ValueError, TypeError):
        return {}

def fingerprint(rules):
    return hashlib.sha256(json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def ground_application(application, path=None, today=None):
    app = copy.deepcopy(application)
    rules = load_rules(path)
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    citations, checks = [], []
    for issue in app['issues']:
        rule = rules.get(issue.get('evidence_id'))
        status = app.get('basis_status', 'missing')
        if status == 'valid':
            status = issue.get('basis_status', 'valid')
        if status == 'valid':
            if not rule:
                status = 'missing'
            elif rule['status'] != 'active':
                status = 'inactive'
            elif rule['version'] != app.get('rule_version'):
                status = 'version_mismatch'
            elif today < date.fromisoformat(rule['valid_from']) or (rule.get('valid_until') and today > date.fromisoformat(rule['valid_until'])):
                status = 'expired_or_not_effective'
            elif any(issue.get(k) != rule[k] for k in ('reason','action')) or app.get('synthetic') is not True:
                status = 'conflict'
        issue['basis_status'] = status
        checks.append({'issue_id': issue['issue_id'], 'evidence_id': issue.get('evidence_id'), 'status': status})
        if status == 'valid':
            citations.append({k:rule[k] for k in ('id','version','source','source_title','synthetic','reason','action')})
        else:
            # Do not echo potentially corrupted source text into a fallback.
            issue['reason'] = {'missing': '该项材料的依据缺失', 'conflict': '该项材料的依据存在冲突', 'expired_or_not_effective': '该项材料的依据已过期或尚未生效', 'inactive': '该项材料的依据已停用', 'version_mismatch': '该项材料的依据版本不一致'}.get(status, '该项材料的依据需要核实')
            issue['action'] = '暂不提供补正要求'
    app['grounding'] = {'status': 'supported' if checks and all(c['status']=='valid' for c in checks) else 'needs_verification',
                        'checks': checks, 'citations': citations, 'registry_hash': fingerprint(rules)}
    return app

def citations_current(grounding):
    rules = load_rules()
    if grounding.get('registry_hash') != fingerprint(rules):
        return False
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for citation in grounding.get('citations', []):
        rule = rules.get(citation['id'])
        if not rule or rule['status'] != 'active' or today < date.fromisoformat(rule['valid_from']) or (rule.get('valid_until') and today > date.fromisoformat(rule['valid_until'])):
            return False
    return True
