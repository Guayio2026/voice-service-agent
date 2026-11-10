import {API_BASE} from './mock-config.js';
const $=id=>document.getElementById(id), key='voice-mock-v1';
let s=JSON.parse(sessionStorage.getItem(key)||'null')||{}, busy=true, turn=null;
const archiveKey='voice-mock-sessions-v1';
let sessions=JSON.parse(sessionStorage.getItem(archiveKey)||'{}');
if(s.app)sessions[s.app]=s;
s=Object.values(sessions).find(item=>item.pending)||s;
function persist(){if(s.app)sessions[s.app]=s;sessionStorage.setItem(archiveKey,JSON.stringify(sessions));sessionStorage.setItem(key,JSON.stringify(s));}
function controls(){document.querySelectorAll('button,select,input').forEach(x=>x.disabled=busy);$('cases').disabled=busy||!!s.pending;$('start').disabled=busy||!!s.pending||!$('cases').value;for(const id of ['yes','no','query','confirm','save','history','handoff'])$(id).disabled=busy||!s.session;for(const id of ['save','handoff'])$(id).disabled ||= !!s.pending;$('reconcile').disabled=busy||!s.pending;}
function connection(ok){$('health').textContent=(ok?'后台可达（仅Mock，语音未接入）':'后台连接失败，请检查服务后重试')+' · 最近检查 '+new Date().toLocaleTimeString();}
function status(t){$('status').textContent=t;}
function clear(){status('');$('outcome').value='notification_completed';turn=null;$('confirmation').textContent='尚未确认';$('application').textContent='';$('issues').replaceChildren();$('records').textContent='';$('feedback').value='unknown';$('unresolved').checked=false;$('consent').checked=false;}
async function api(path,p){const c=new AbortController(),t=setTimeout(()=>c.abort(),10000);try{const r=await fetch(API_BASE+'/'+path,{method:p?'POST':'GET',headers:p?{'Content-Type':'application/json'}:{},body:p?JSON.stringify(p):undefined,signal:c.signal});const d=await r.json();connection(true);if(!r.ok){const e=new Error(d.error?.code||String(r.status));e.definite=r.status<500||d.error?.code==='WRITE_FAILED';throw e;}return d;}catch(e){if(e.definite===undefined)connection(false);throw e;}finally{clearTimeout(t);}}
async function history(){const d=await api('get_results',{session_id:s.session});s.revision=Math.max(0,...d.results.map(x=>x.result_revision));s.clock=Math.max(s.clock||0,...d.results.map(x=>x.confirmation_turn_id||0));persist();$('records').textContent=JSON.stringify(d,null,2);}
async function settle(){
  if(!s.pending)return status('没有待核对操作');
  const op=s.pending;
  const d=await api('get_operation_status',{session_id:op.payload.session_id,idempotency_key:op.payload.idempotency_key});
  let result;
  try{result=d.status==='confirmed'?d.result:await api(op.path,op.payload);}
  catch(e){if(e.definite){s.pending=null;persist();}throw e;}
  if(result.handoff_id)s.handoff=result.handoff_id;
  s.pending=null;persist();
  status(result.handoff_id?'模拟工单已登记：'+result.handoff_id+'；未接通真人':'后台已保存，记录编号：'+result.record_id+'，版本：'+result.result_revision);
  try{await history();}catch{status('后台已确认保存，但历史读取失败，请重新读取历史');}
}
async function write(path,payload){s.pending={path,payload};persist();try{const d=await api(path,payload);if(d.handoff_id)s.handoff=d.handoff_id;s.pending=null;persist();status(d.handoff_id?'模拟工单已登记：'+d.handoff_id+'；未接通真人':'后台已保存，记录编号：'+d.record_id+'，版本：'+d.result_revision);try{await history();}catch{status('后台已确认保存，但历史读取失败，请重新读取历史');}}catch(e){if(e.definite){s.pending=null;persist();throw e;}status('结果待核对；切换案例和新建会话已锁定。请核对，刷新后仍可恢复');}}
function action(id,fn){$(id).onclick=async()=>{if(busy)return;busy=true;controls();try{await fn();}catch(e){status((s.pending?'结果待核对：':'操作未成功：')+e.message);}finally{busy=false;controls();}};}
$('cases').onchange=async()=>{if(busy||s.pending){$('cases').value=s.app;return;}busy=true;controls();try{persist();s=sessions[$('cases').value]||{app:$('cases').value};persist();clear();$('identity').textContent='恢复会话时按后台当前身份读取';if(s.session)await history();}catch(e){status('切换未完成：'+e.message);}finally{busy=false;controls();}};
for(const id of ['feedback','unresolved','outcome','issues'])$(id).addEventListener('change',()=>{turn=null;$('confirmation').textContent='内容已修改，必须重新确认';});
action('start',async()=>{if(s.pending)throw Error('请先核对待定操作');const d=await api('start_session',{application_id:$('cases').value});s={app:$('cases').value,session:d.session_id,revision:0,clock:0};persist();clear();$('identity').textContent=d.identity_status;status('模拟会话已创建');});
for(const [id,value] of [['yes',true],['no',false]])action(id,async()=>{clear();$('identity').textContent='身份待核对';const d=await api('verify_identity',{session_id:s.session,is_responsible:value});$('identity').textContent=d.identity_status;await history();});
action('query',async()=>{clear();const d=await api('get_application',{session_id:s.session,application_id:s.app});$('application').textContent=JSON.stringify(d,null,2);for(const i of d.issues){const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.value=i.issue_id;l.append(c,document.createTextNode('已说明：'+i.reason));$('issues').append(l);}});
action('confirm',async()=>{s.clock=(s.clock||0)+1;turn=s.clock;persist();$('confirmation').textContent='本地模拟确认轮次 '+turn;});
action('history',history);
action('save',async()=>{if(s.pending)throw Error('请先核对待定操作');await history();const result={candidate_outcome:$('outcome').value,issues_explained:[...document.querySelectorAll('#issues input:checked')].map(x=>x.value),user_feedback:$('feedback').value,confirmation_turn_id:turn,unresolved:$('unresolved').checked};if(result.candidate_outcome==='handoff_registered')result.handoff_id=s.handoff;await write('save_call_result',{session_id:s.session,idempotency_key:crypto.randomUUID(),result_revision:s.revision+1,result});turn=null;$('confirmation').textContent='再次保存需重新确认';});
action('handoff',async()=>{if(s.pending)throw Error('请先核对待定操作');await write('create_handoff',{session_id:s.session,idempotency_key:crypto.randomUUID(),consent:$('consent').checked,reason:'user_request'});});
action('reconcile',settle);
controls();
try{const h=await api('health');$('health').textContent='实际后台连接：'+JSON.stringify(h);const d=await api('cases');const groups={};for(const c of d.cases)(groups[c.business||'其他']??=[]).push(c);const names=Object.keys(groups).sort((a,b)=>{const ak=a.split(' · ')[0],bk=b.split(' · ')[0];if(ak!==bk)return ak<bk?-1:1;return a<b?-1:1;});for(const name of names){const list=groups[name];const og=document.createElement('optgroup');og.label=name+'（'+list.length+'）';for(const c of list){const o=document.createElement('option');o.value=c.application_id;o.textContent=c.label+' · '+c.application_id;og.append(o);}$('cases').append(og);}if(s.app)$('cases').value=s.app;if(s.session){$('identity').textContent='已恢复模拟会话，历史按后台当前身份读取';await history();if(s.pending)status('存在结果待核对操作，请核对');}}catch(e){$('health').textContent='后台连接失败：'+e.message;}

busy=false;controls();
let checking=false;
async function refreshConnection(){if(busy||checking)return;checking=true;try{await api('health');}catch{}finally{checking=false;}}
setInterval(refreshConnection,5000);window.addEventListener('focus',refreshConnection);
