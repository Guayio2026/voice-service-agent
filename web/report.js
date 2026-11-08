import {readSession,readHistory,canUndoClear,undoClear,clearSession,exportSession,describeResult,SCENARIOS,storageNotice} from './session-store.js';
const $=id=>document.getElementById(id);
let state;
let selected="current";
function render(){
  const history=readHistory();
  const picker=$('report-history');picker.replaceChildren();
  const current=document.createElement('option');current.value='current';current.textContent='当前会话';picker.append(current);
  history.forEach((item,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=`历史 ${index+1} · ${SCENARIOS[item.mode]} · ${item.startedAt?new Date(item.startedAt).toLocaleString('zh-CN'):'此前会话'}`;picker.append(option);});
  if(selected!=='current'&&!history[Number(selected)])selected='current';picker.value=selected;
  state=selected==='current'?readSession():history[Number(selected)];
  $('report-undo').hidden=!canUndoClear();
  $('report-summary').textContent=describeResult(state);
  document.querySelector('.records-main').dataset.empty=String(state.events.length===0);
  $('report-scenario').textContent=SCENARIOS[state.mode]+' · 合成案例';
  const result=state.result;
  $('report-status').textContent={idle:'尚未开始',paused:'模拟已暂停',ended:'模拟已结束',interrupted:'模拟已中止'}[state.status]||'模拟已暂停';
  $('report-identity').textContent=result.identity_status==='demo_confirmed'?'原型确认（模拟，非真实身份验证）':'未核实';
  $('report-save').textContent={not_attempted:'尚未尝试',pending:'等待确认（模拟）',confirmed:'模拟回写已确认 · 非真实登记',unknown:'结果未知 · 待核对',failed:'模拟失败'}[result.persistence_status]||'尚未确认';
  $('report-save').dataset.status=result.persistence_status;
  $('report-record').textContent=result.record_id||'—';
  $('report-outcome').textContent={notification_completed:'通知完成（模拟，不代表审核通过）',wrong_contact:'身份不符，安全结束（模拟）'}[result.outcome]||'尚未确认完成';
  $('qa').textContent=state.qa;
  $('count-transcripts').textContent=state.transcripts.length;
  $('count-tools').textContent=state.tools.length;
  const secs=Math.floor(state.elapsedMs/1000);$('report-duration').textContent=`${String(Math.floor(secs/60)).padStart(2,'0')}:${String(secs%60).padStart(2,'0')}`;
  $('result').textContent=JSON.stringify(result,null,2);
  $('logs').textContent=state.events.length?JSON.stringify(state.events,null,2):'暂无模拟事件';
  $('report-export').disabled=!state.events.length;$('report-clear').disabled=!readSession().events.length&&!history.length;
  const list=$('report-timeline');list.replaceChildren();
  for(const item of state.events.filter(item=>{const filter=$('event-filter').value;return filter==='all'||(filter==='system'?!['transcript','tool'].includes(item.type):item.type===filter);})){const li=document.createElement('li');const number=document.createElement('span');number.textContent=String(item.sequence).padStart(2,'0');const text=document.createElement('p');text.textContent=item.text;li.append(number,text);list.append(li);}
  $('report-empty').hidden=list.children.length>0;$('report-empty').textContent=state.events.length?'此类别没有事件。':'尚无会话记录。到工作台播放一次案例后，这里会显示结果与事件证据。';
  $('report-continue').textContent=selected==='current'&&state.status==='paused'?'返回工作台继续':'打开工作台';
  $('report-continue').href='workbench.html?mode=demo';
  storageNotice();
}
$('report-export').addEventListener('click',()=>exportSession(state));
$('report-clear').addEventListener('click',()=>{clearSession();selected='current';render();$('report-feedback').textContent='当前会话与历史已清除，可撤销。';});
window.addEventListener('pageshow',render);render();

$('report-history').addEventListener('change',event=>{selected=event.target.value;render();});
$('report-undo').addEventListener('click',()=>{if(undoClear()){selected='current';render();$('report-feedback').textContent='已恢复清除前的模拟记录。';}});

$('event-filter').addEventListener('change',render);
