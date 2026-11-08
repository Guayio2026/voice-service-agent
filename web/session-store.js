import {QUALIFICATION_SCENARIOS} from './qualification-scenarios.js';
// Synthetic data only. Tab-scoped storage is not a business database or real persistence.
export const SCENARIOS=Object.fromEntries(Object.entries(QUALIFICATION_SCENARIOS).map(([key,c])=>[key,c.label]));
export const STEPS=['自报身份','核实对象','确认需求','查询依据','解释问题','确认结果','结构化回写','结束会话'];
const KEY='zhiyu.synthetic-session.v3';
let memory=null;
let historyMemory=[],undoMemory=null;
const HISTORY=KEY+'.history',UNDO=KEY+'.undo';
function readExtra(key,fallback){try{return storageAvailable?JSON.parse(sessionStorage.getItem(key)||'null')||fallback:fallback;}catch{return fallback;}}
function writeExtra(key,value){if(storageAvailable)try{sessionStorage.setItem(key,JSON.stringify(value));}catch{storageAvailable=false;document.dispatchEvent(new Event('demo-storage-unavailable'));}}
export function readHistory(){const rows=readExtra(HISTORY,historyMemory);return Array.isArray(rows)?rows.filter(x=>x?.simulated===true&&Array.isArray(x.events)&&x.result).slice(0,5):[];}
export function canUndoClear(){return Boolean(readExtra(UNDO,undoMemory));}
export function undoClear(){const backup=readExtra(UNDO,undoMemory);if(!backup)return false;historyMemory=backup.history;writeExtra(HISTORY,historyMemory);memory=backup.current;writeExtra(KEY,memory);undoMemory=null;writeExtra(UNDO,null);return true;}
export let storageAvailable=true;
try { sessionStorage.setItem(KEY+'.probe','1');sessionStorage.removeItem(KEY+'.probe'); }
catch { storageAvailable=false; }

export function freshSession(mode='trademark') {
  return {sessionId:globalThis.crypto?.randomUUID?.()||String(Date.now())+Math.random(),startedAt:new Date().toISOString(),version:3,simulated:true,mode:Object.hasOwn(SCENARIOS,mode)?mode:'trademark',status:'idle',nextAction:0,elapsedMs:0,
    result:{simulated:true,identity_status:'unverified',candidate_outcome:null,outcome:null,persistence_status:'not_attempted',record_id:null},
    transcripts:[],tools:[],steps:[],events:[],qa:'待运行'};
}
export function readSession() {
  try {
    const value=storageAvailable?JSON.parse(sessionStorage.getItem(KEY)||'null'):memory;
    if(!value||value.version!==3||value.simulated!==true||!Object.hasOwn(SCENARIOS,value.mode))return freshSession();
    if(!['idle','running','paused','ended','interrupted'].includes(value.status))return freshSession();
    if(!['events','transcripts','tools','steps'].every(key=>Array.isArray(value[key])&&value[key].length<=300))return freshSession();
    if(!value.result||value.result.simulated!==true||!Number.isFinite(value.elapsedMs)||value.elapsedMs<0||!Number.isInteger(value.nextAction)||value.nextAction<0||value.nextAction>8)return freshSession();
    const copy=JSON.parse(JSON.stringify(value));
    // A different document cannot keep the prior document's playback timer alive.
    if(copy.status==='running')copy.status='paused';
    return copy;
  } catch { return freshSession(); }
}
export function saveSession(state) {
  if(canUndoClear()){undoMemory=null;writeExtra(UNDO,null);}
  const previous=readSession();
  if(previous.events.length&&previous.sessionId!==state.sessionId){historyMemory=[previous,...readHistory()].slice(0,5);writeExtra(HISTORY,historyMemory);}
  memory=JSON.parse(JSON.stringify(state));
  if(storageAvailable)try{sessionStorage.setItem(KEY,JSON.stringify(state));}
  catch{storageAvailable=false;document.dispatchEvent(new Event('demo-storage-unavailable'));}
}
export function clearSession() {
  undoMemory={current:readSession(),history:readHistory()};writeExtra(UNDO,undoMemory);
  historyMemory=[];writeExtra(HISTORY,[]);
  memory=freshSession();
  try{sessionStorage.removeItem(KEY);}catch{storageAvailable=false;}
  return memory;
}
export function exportSession(state=readSession()) {
  if(!state.events.length)return false;
  const value={simulated:true,notice:'本标签页合成演示记录；非真实通话、非正式质检、未写入业务后台。',scenario:state.mode,status:state.status,result:state.result,events:state.events,measured_voice_latency:null};
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=`智语外呼-模拟-${state.mode}.json`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);return true;
}
export function describeResult(state) {
  if(!state.events.length)return '尚未运行模拟。先到工作台选择一个合成案例。';
  if(state.result.persistence_status==='unknown')return '模拟回写超时，登记结果待核对。未确认成功，不计入通知完成。';
  if(state.status==='paused')return '模拟已暂停，已发生的对话与工具结果已保留。返回工作台可继续。';
  if(state.status==='interrupted')return '模拟已中止。保留已发生的模拟记录，不推断用户确认。';
  if(state.result.outcome==='wrong_contact')return '模拟身份不符，已停止披露申请详情，仅保留最小异常结果。';
  if(state.result.outcome==='notification_completed')return '模拟补正通知流程完成。仅为合成演示，未产生真实业务登记。';
  return '模拟结果尚未确认。';
}
export function storageNotice() {
  const note=document.getElementById('storage-note');
  if(note)note.hidden=storageAvailable;
}
document.addEventListener('demo-storage-unavailable',storageNotice);
