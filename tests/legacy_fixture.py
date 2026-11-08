"""Legacy fault cases exist only in test processes, never in the running service."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import server,grounding
for name in ('legacy_cases.json','combined_case.json'):
    server.CASES.update({c['application_id']:c for c in json.loads((ROOT/'tests/fixtures'/name).read_text(encoding='utf-8'))})
grounding.RULES=ROOT/'tests/fixtures/rules.json'
