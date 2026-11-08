"""Two small paid text calls with synthetic feedback in an isolated temporary DB."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from evolution_store import Store
from evolution import propose, BudgetClient, normalized
from qwen_client import load_config
from text_client import text_client

def main():
    load_config()
    client = text_client()
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory)/'smoke.sqlite3')
        base = store.active()
        tid = store.begin_turn('synthetic-smoke','synthetic-001','TEST-002',base,'谢谢，但还是请人工协助核实')
        store.end_turn(tid,'done','不客气',{'decision':{'intent':'thanks'},'fixture':True})
        store.feedback(tid,'intent','这是人工构造的错误样例，不是线上实测：回复应识别转人工请求，不能只按谢谢分类。')
        proposed = propose(store,client,max_calls=1,token_budget=30000)
        version=store.version(proposed['candidate'])
        run=store.start_run('smoke',version['id'],{'synthetic':True})
        budget=BudgetClient(store,run,client,max_calls=1,token_budget=15000)
        answer=budget.chat([{'role':'system','content':version['prompt']},
                           {'role':'user','content':'多谢解释，我想请人工再帮忙确认一下材料问题。'}],220)
        decision=json.loads(answer['text'])
        passed=normalized(decision)[0]=='handoff'
        assert passed, 'Smoke classification mismatch'
        assert store.active()==base
        calls=store.rows('SELECT usage,status FROM evolution_calls')
        report={'scope':'two real text API calls, synthetic fixture, NOT an improvement benchmark',
                'model':getattr(client,'model','unknown'),'proposal_schema_valid':True,
                'candidate_classification':decision,'active_version_unchanged':True,
                'calls':len(calls),'usage':[json.loads(c['usage']) for c in calls],
                'production_database_modified':False,'published':False}
        output=ROOT/'runtime/qa/evolution-live-smoke.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
