import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from evolution_store import Store
from evolution import evaluate
from qwen_client import load_config
from text_client import text_client
proposal_text=(ROOT.parent/'rsi-proposal.log').read_text(encoding='utf-8-sig')
proposal=json.loads(proposal_text[proposal_text.index('{'):])
store=Store(ROOT/'runtime/rsi-review.sqlite3')
candidate=store.candidate(store.active(),proposal['guidance'],proposal['analysis'])
load_config()
report=evaluate(store,candidate,text_client(),max_calls=28,token_budget=250000)
(ROOT/'runtime/rsi-review-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('run_id','eligible','gates')},ensure_ascii=False))
