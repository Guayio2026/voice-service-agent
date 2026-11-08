"""Fault-injection probes; results are evidence, not production-model accuracy."""
import asyncio, json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from server import Backend
from business_agent import Agent
class Model:
    def __init__(self,kind): self.kind=kind
    def chat(self,*args,**kwargs): return {'text':json.dumps({'kind':self.kind})}
async def main():
    results=[]
    cases=[('ambiguous_model_yes','不确定是不是负责人','yes','unverified'),
           ('reported_model_yes','他说他是该商家的负责人','yes','unverified'),
           ('contradiction_model_yes','是的但我不是负责人','yes','unverified'),
           ('model_decline','我不想继续讨论这件事','decline','closing'),
           ('closing_followup','再见','unclear','closing')]
    for name,text,kind,expected in cases:
        with tempfile.TemporaryDirectory() as tmp:
            b=Backend(Path(tmp)/'db');sid=b.call('start_session',{'application_id':'QUAL-001'})['session_id'];a=Agent(b,sid,'QUAL-001')
            await a.turn('开始通话',Model('unclear'),False)
            reply=await a.turn(text,Model(kind),False)
            first={'identity':b.identity_status(sid),'step':a.step,'text':reply['text']}
            if name=='closing_followup':
                reply=await a.turn('是的',Model('unclear'),False)
            actual=a.step if expected=='closing' else b.identity_status(sid)
            results.append({'id':name,'input':text,'injected_model_kind':kind,'expected':expected,'actual':actual,'status':'passed' if expected==actual else 'failed','first_reply':first,'last_reply':reply['text']})
    path=ROOT/'data/generated-evaluation/probes.json'
    path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))
asyncio.run(main())
