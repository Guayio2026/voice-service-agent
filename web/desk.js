import {show,navigate} from './router.js';

const $ = id => document.getElementById(id);
const stage = $('desk-stage');
const scenario = $('scenario');
const announcement = $('desk-announcement');
const scenarioNames = {normal:'正常补正', identity:'身份不符', failure:'工具失败'};
let announcementTimer;
function announce(message) {
  clearTimeout(announcementTimer);
  announcement.hidden=false;
  announcement.textContent = message;
  announcementTimer=setTimeout(()=>{announcement.hidden=true;},7000);
}
function visit(id) {
  const target = $(id);
  target.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
  target.focus({preventScroll:true});
}
function openCall({play=false}={}) {
  show('call');
  if (play && !$('play').disabled) $('play').click();
}
function syncScenario() {
  $('scenario-caption').textContent = scenarioNames[scenario.value];
  const button = $('object-gyro');
  button.setAttribute('aria-label',`陀螺：切换合成场景，当前${scenarioNames[scenario.value]}`);
  button.setAttribute('aria-disabled',String(scenario.disabled));
}
function syncExport() {
  const hasEvents = $('logs').textContent.trim().startsWith('[');
  $('object-pen').setAttribute('aria-disabled',String(!hasEvents));
  $('export-caption').textContent = hasEvents?'导出模拟日志':'演示后导出';
}
function exportLog() {
  if ($('object-pen').getAttribute('aria-disabled') === 'true') {
    announce('钢笔：还没有会话日志。先点击机器人播放一次模拟，再导出记录。');
    return;
  }
  const data = {
    simulated:true,
    notice:'浏览器合成演示记录；非真实通话、非正式质检、未写入业务后台。',
    scenario:scenario.value,
    result:JSON.parse($('result').textContent),
    events:JSON.parse($('logs').textContent),
    measured_voice_latency:null
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = `智语外呼-模拟-${scenario.value}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  announce('已导出当前模拟日志，文件中已标记 simulated: true。');
}
function about() {
  navigate('about');
}

const actions = {
  robot() { announce('机器人：正在打开合成对话演示，不采集麦克风、不播放声音。'); openCall({play:true}); },
  evidence() { show('call'); document.querySelector('.case-details').open=true; visit('case-evidence'); },
  recorder() { show('report'); },
  gyro() {
    if (scenario.disabled) { announce('模拟正在进行，请先结束模拟，再切换合成场景。'); return; }
    scenario.selectedIndex=(scenario.selectedIndex+1)%scenario.options.length;
    scenario.dispatchEvent(new Event('change',{bubbles:true}));
    syncScenario();
    announce(`陀螺：已切换为“${scenarioNames[scenario.value]}”合成场景。点击机器人或“开始模拟演示”播放。`);
  },
  lamp() {
    const soft=stage.dataset.light!=='soft';
    stage.dataset.light=soft?'soft':'standard';
    $('object-lamp').setAttribute('aria-pressed',String(soft));
    $('lamp-caption').textContent=soft?'柔和 · 点亮':'明亮 · 调暗';
    announce(`台灯：已切换${soft?'柔和':'明亮'}场景。仅调整画面明暗，保留物件本色。`);
  },
  books() { openCall(); scenario.focus({preventScroll:true}); },
  notebook:about,
  pen:exportLog,
  memo() {
    navigate('guide');
  }
};
document.querySelectorAll('[data-object]').forEach(button=>{
  button.addEventListener('click',()=>actions[button.dataset.object]());
});
document.querySelectorAll('[data-open]').forEach(link=>link.addEventListener('click',event=>{
  event.preventDefault();
  if(link.dataset.open==='report') actions.recorder();
  else if(link.dataset.open==='about') about();
  else openCall();
}));
document.querySelector('.enter-demo').addEventListener('click',event=>{event.preventDefault();openCall({play:true});});
$('labels-toggle').addEventListener('click',()=>{
  const hidden=stage.classList.toggle('labels-off');
  $('labels-toggle').setAttribute('aria-pressed',String(!hidden));
  $('labels-toggle').textContent=hidden?'显示物件提示':'隐藏物件提示';
  announce(hidden?'物件提示已收起，悬停或键盘聚焦仍会显示。':'物件名称和操作提示已显示。');
});
$('help-toggle').addEventListener('click',()=>actions.memo());
$('close-about').addEventListener('click',()=>navigate('home'));
window.addEventListener('pagehide',()=>clearTimeout(announcementTimer));
scenario.addEventListener('change',syncScenario);
new MutationObserver(syncScenario).observe(scenario,{attributes:true,attributeFilter:['disabled']});
new MutationObserver(syncExport).observe($('logs'),{childList:true,subtree:true,characterData:true});
const picture=$('desk-picture');
picture.addEventListener('error',()=>{stage.classList.add('asset-error');announce('场景图片加载失败，文字入口仍可使用。请检查 assets 文件夹是否完整。');});
if(picture.complete&&!picture.naturalWidth) picture.dispatchEvent(new Event('error'));
syncScenario();syncExport();
