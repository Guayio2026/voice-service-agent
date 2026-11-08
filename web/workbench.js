import {QUALIFICATION_SCENARIOS} from './qualification-scenarios.js';
import {freshSession,readSession,saveSession,exportSession,SCENARIOS,STEPS,storageNotice} from './session-store.js';
import {voiceService} from './adapter.js';
const $=id=>document.getElementById(id);
$('scenario').replaceChildren(...Object.entries(SCENARIOS).map(([value,label])=>new Option(label,value)));
let state=readSession(),timer=0,clock=0,tickStarted=0;

function elapsed(){return state.elapsedMs+(state.status==='running'?performance.now()-tickStarted:0);}
function timeLabel(){const seconds=Math.floor(elapsed()/1000);$('duration').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;}
function event(type,text){state.events.push({simulated:true,sequence:state.events.length+1,type,text});}
function step(index){if(!state.steps.includes(index))state.steps.push(index);}
function say(who,text){state.transcripts.push({who,text});event('transcript',text);}
function tool(name,status,text){const item=`[模拟] ${name} → ${status} · ${text}`;state.tools.push(item);event('tool',item);}
function render(){
  $('scenario').value=state.mode;
  document.querySelector('.session-stage').dataset.status=state.status;
  document.querySelectorAll('.flow-strip [data-step]').forEach(node=>{const index=Number(node.dataset.step);const unknown=index===6&&state.result.persistence_status==='unknown';const done=state.steps.includes(index)&&!unknown;const skipped=state.mode==='identity'&&state.steps.includes(6)&&index>=2&&index<=5;if(index===6)node.textContent=unknown?'回写待核对':'回写结果';node.classList.toggle('done',done);node.classList.toggle('warning',unknown);node.classList.toggle('skipped',skipped);node.setAttribute('aria-label',node.textContent+(unknown?'：结果未知':done?'：已演示':skipped?'：已跳过':'：待执行'));});
  $('step-counter').textContent=`${state.steps.length} / ${STEPS.length}`;
  const current=QUALIFICATION_SCENARIOS[state.mode];
  document.querySelector('.person h2').textContent=current.store;
  const title=document.querySelector('#workspace-title');if(title)title.textContent=SCENARIOS[state.mode]+' · 资质补正';
  $('case-note').textContent=current.issues.map((i,n)=>`${n+1}. ${i.subtype_label}：${i.reason}；${i.action}`).join('。');
  document.querySelector('.case-art strong').textContent=SCENARIOS[state.mode];
  const evidence=document.querySelector('#case-evidence .context-body');
  evidence.replaceChildren();
  for(const text of ['品牌：'+current.brand,'申请：'+current.application_id,...current.issues.map((i,n)=>`${n+1}. ${i.subtype_label}：${i.reason}。整改：${i.action}`),'项目合成审核记录，非官方政策；具体期限及提交渠道待核实。']){const p=document.createElement('p');p.textContent=text;evidence.append(p);}
  const locked=state.status==='running'||state.status==='paused';
  $('scenario').disabled=locked;$('play').disabled=false;$('stop').disabled=!locked;
  $('play').textContent=state.status==='running'?'暂停模拟':state.status==='paused'?'继续模拟流程':state.status==='ended'||state.status==='interrupted'?'重新播放模拟':'播放模拟流程';
  const labels={idle:'准备就绪',running:`模拟：${STEPS[state.steps.at(-1)]||'播放中'}`,paused:'模拟已暂停 · 可以继续',ended:'模拟已结束 · 未产生真实登记',interrupted:'模拟已中止 · 未判定完成'};
  $('state').textContent=labels[state.status];
  $('steps').replaceChildren(...STEPS.map((name,index)=>{const li=document.createElement('li');const done=state.steps.includes(index);const skipped=state.mode==='identity'&&state.steps.includes(6)&&index>=2&&index<=5;li.textContent=name+(done?' · 已演示':skipped?' · 身份不符，已跳过':state.status==='interrupted'?' · 已中止，未执行':' · 待执行');if(done)li.className='done';return li;}));
  const transcript=$('transcript');
  const follow=transcript.scrollHeight-transcript.scrollTop-transcript.clientHeight<48;
  const nodes=[...transcript.querySelectorAll('.bubble')];
  const reset=nodes.length>state.transcripts.length||nodes.some((node,i)=>node.dataset.text!==state.transcripts[i]?.text);
  if(reset)transcript.replaceChildren();
  if(!state.transcripts.length){
    const brief=['演示'+SCENARIOS[state.mode],QUALIFICATION_SCENARIOS[state.mode].issues[0].reason];transcript.innerHTML=`<div class="empty session-ready"><span class="ready-symbol" aria-hidden="true">↗</span><h3>${brief[0]}</h3><p>${brief[1]}</p><dl><dt>本轮案例</dt><dd>${SCENARIOS[state.mode]}</dd><dt>显示内容</dt><dd>逐句字幕、执行进度与工具记录</dd></dl></div>`; $('new-messages').hidden=true;}
  else{
    transcript.querySelector('.empty')?.remove();
    const count=transcript.querySelectorAll('.bubble').length;
    for(const item of state.transcripts.slice(count)){const node=document.createElement('div');node.className='bubble'+(item.who==='商家'?' merchant':'');node.dataset.text=item.text;const label=document.createElement('b');label.textContent=item.who+' · 模拟';const content=document.createElement('p');content.className='speech-text';content.textContent=item.text;node.append(label,content);transcript.append(node);}
    if(follow||reset){transcript.scrollTop=transcript.scrollHeight;$('new-messages').hidden=true;}
    else if(state.transcripts.length>count)$('new-messages').hidden=false;
  }
  $('tools').replaceChildren(...state.tools.map(text=>{const item=document.createElement('p');item.textContent=text;return item;}));
  if(!state.tools.length)$('tools').textContent='尚未调用';
  $('result').textContent=JSON.stringify(state.result,null,2);
  $('qa').textContent=state.qa;$('logs').textContent=state.events.length?JSON.stringify(state.events,null,2):'暂无事件';
  $('workbench-export').disabled=!state.events.length;
  timeLabel();storageNotice();
}
function actions(){
  const c=QUALIFICATION_SCENARIOS[state.mode], issues=c.issues;
  return [
    ()=>{step(0);say('AI 客服','您好，我是智语外呼的AI客服小语。这是品牌入驻资质审核的合成演示。');},
    ()=>{step(1);say('AI 客服','请问您是'+c.merchant+'的负责人吗？');say('商家','是的，我是负责人。');state.result.identity_status='demo_confirmed';},
    ()=>{step(2);say('AI 客服','现在方便沟通本次资质材料的问题吗？');say('商家','方便，请说明。');},
    ()=>{step(3);tool('get_application','模拟返回',c.application_id+' / '+issues.map(i=>i.evidence_id).join(' / '));},
    ()=>{step(4);say('AI 客服','关于您申请的'+c.brand+'，'+issues.map((i,n)=>`${n+1}、${i.reason}，请${i.action}`).join('；')+'。补充后仍需审核，不代表审核通过。');},
    ()=>{step(5);say('商家','明白了，没有其他问题，同意登记沟通结果。');Object.assign(state.result,{application_id:c.application_id,candidate_outcome:'notification_completed',issues_explained:issues.map(i=>i.issue_id),user_feedback:'明确理解并确认（模拟）'});},
    ()=>{step(6);tool('save_call_result','浏览器模拟','未写入业务后台');Object.assign(state.result,{persistence_status:'demo_only',outcome:'notification_completed',record_id:null,formal_rejection_status:'not_connected'});state.qa='已演示口头说明与结果记录；未执行京麦正式驳回。';},
    ()=>{step(7);say('AI 客服','感谢您的接听，祝您生活愉快，再见。');event('ended','模拟结束');state.elapsedMs=elapsed();state.status='ended';clearInterval(clock);}
  ];
}

function tick(){
  if(state.status!=='running')return;
  const list=actions();
  if(state.nextAction>=list.length){state.status='ended';clearInterval(clock);saveSession(state);render();return;}
  list[state.nextAction]();state.nextAction++;saveSession(state);render();
  if(state.status==='running')timer=setTimeout(tick,3300/Number($('playback-speed').value));
}
function start(){
  if(state.status==='running')return;
  if(state.status!=='paused')state=freshSession($('scenario').value);
  else event('resumed','用户继续已暂停的模拟');
  state.status='running';tickStarted=performance.now();clock=setInterval(timeLabel,250);tick();
}
function pause(){
  if(state.status!=='running')return;
  clearTimeout(timer);clearInterval(clock);state.elapsedMs=elapsed();state.status='paused';event('paused','模拟已暂停，可继续当前进度');saveSession(state);render();
}
$('play').addEventListener('click',()=>state.status==='running'?pause():start());
$('new-messages').addEventListener('click',()=>{$('transcript').scrollTop=$('transcript').scrollHeight;$('new-messages').hidden=true;});
$('playback-speed').addEventListener('change',()=>{if(state.status==='running'){clearTimeout(timer);timer=setTimeout(tick,3300/Number($('playback-speed').value));}});
$('stop').addEventListener('click',()=>{if(!['running','paused'].includes(state.status))return;clearTimeout(timer);clearInterval(clock);state.elapsedMs=elapsed();state.status='interrupted';state.result.candidate_outcome='interrupted';state.qa='模拟已中止 · 不判定完成';event('interrupted','用户结束模拟；保留已发生的模拟结果');saveSession(state);render();});
$('scenario').addEventListener('change',()=>{state=freshSession($('scenario').value);saveSession(state);render();});
$('workbench-export').addEventListener('click',()=>exportSession(state));
// Save before regular navigation as well as refresh/back/close. Never claim background playback.
document.querySelectorAll('a[href]').forEach(link=>link.addEventListener('click',event=>{if(!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&event.button===0&&link.origin===location.origin&&link.pathname!==location.pathname)pause();}));
window.addEventListener('pagehide',pause);
window.addEventListener('pageshow',event=>{if(event.persisted){state=readSession();render();}});
render();
const query=new URLSearchParams(location.search);
if(Object.hasOwn(SCENARIOS,query.get('case'))&&!['running','paused'].includes(state.status)){state=freshSession(query.get('case'));saveSession(state);render();}
if(query.get('play')==='1'){history.replaceState(null,'',location.pathname+location.hash);start();}
if(location.hash==='#case-evidence'){$('case-evidence').open=true;$('case-evidence').scrollIntoView();}
if(location.hash==='#scenario')$('scenario').focus();
console.assert(!voiceService.capabilities().tts);

if(matchMedia("(max-width:600px)").matches && location.hash!=="#case-evidence") $("case-evidence").open=false;

window.addEventListener("workspace-mode-change",event=>{if(event.detail!=="demo")pause();});
