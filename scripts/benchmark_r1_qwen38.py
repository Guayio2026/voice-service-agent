import asyncio,json,math,random
from datetime import datetime
from benchmark_classifier_models import ROOT,CASES,call,canonical,prepare_messages,load_config,QwenClient
async def main():
 load_config()
 out=ROOT/'data'/('r1-qwen38-'+datetime.now().strftime('%Y%m%d-%H%M%S'));out.mkdir()
 models=[QwenClient(model=m) for m in ['deepseek-r1-distill-qwen-1.5b','qwen3.8-flash']]
 prompt=(ROOT/'src/prompts/customer_service.txt').read_text(encoding='utf-8')
 messages=[prepare_messages({'prompt':prompt,'history':[{'role':'user','content':c[0]}]})['messages'] for c in CASES]
 import aiohttp
 rows=[]
 meta={'note':'R1 distill is thinking-only per Bailian docs; enable_thinking=false sent but disabling is NOT supported/verified. Qwen3.8 uses supported non-thinking mode. Same production base prompt, no dynamic business context. HTTP response completion, not TTFT or speech latency. 220 max_tokens; no retries; 15s timeout; warm connections and provider cache allowed.','cases':CASES,'messages':messages}
 (out/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
 print(str(out),flush=True)
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
  probes=[]
  for client in models:
   r=await call(http,client,messages[0]);r['model']=client.model;probes.append(r)
  (out/'probes.json').write_text(json.dumps(probes,ensure_ascii=False,indent=2),encoding='utf-8')
  rng=random.Random(20261108)
  for rep in range(3):
   order=list(range(10));rng.shuffle(order)
   for i in order:
    clients=list(models);rng.shuffle(clients)
    for client in clients:
     r=await call(http,client,messages[i]);r.update(model=client.model,case=i,repetition=rep)
     c=CASES[i];expected={'intent':c[1],'topics':c[2],'issue_numbers':c[3],'actions':c[4]}
     r['correct']=bool(r['valid_json'] and canonical(r['decision'])==canonical(expected));rows.append(r)
     with (out/'raw.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print('completed',len(rows),flush=True)
 summary=[]
 for client in models:
  g=[r for r in rows if r['model']==client.model];v=sorted(r['latency_ms'] for r in g if r['ok'])
  summary.append({'model':client.model,'n':len(g),'success':sum(r['ok'] for r in g),'valid_schema':sum(r['valid_json'] for r in g),'correct':sum(r['correct'] for r in g),'p50_ms':v[math.ceil(len(v)*.5)-1] if v else None,'p95_ms':v[math.ceil(len(v)*.95)-1] if v else None,'truncated':sum(r.get('finish_reason')=='length' for r in g),'reasoning_nonempty':sum(r.get('reasoning_chars',0)>0 for r in g)})
 (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary),flush=True)
asyncio.run(main())
