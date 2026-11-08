import {readSession,freshSession,saveSession,exportSession,SCENARIOS,storageAvailable} from './session-store.js';
const $=id=>document.getElementById(id);
let timer;
function announce(text){clearTimeout(timer);$('desk-announcement').hidden=false;$('desk-announcement').textContent=text;timer=setTimeout(()=>$('desk-announcement').hidden=true,7000);}
function sync(){const state=readSession();$('scenario-caption').textContent=SCENARIOS[state.mode];$('object-gyro').setAttribute('aria-disabled',String(state.status==='paused'));$('object-pen').setAttribute('aria-disabled',String(!state.events.length));$('export-caption').textContent=state.events.length?'导出模拟日志':'演示后导出';}
$('object-gyro').addEventListener('click',()=>{const state=readSession();if(state.status==='paused'){announce('有一段暂停的模拟，请先回工作台继续或结束，再切换案例。');return;}const modes=Object.keys(SCENARIOS);const mode=modes[(modes.indexOf(state.mode)+1)%modes.length];saveSession(freshSession(mode));sync();announce(`已选择“${SCENARIOS[mode]}”合成场景，点击机器人开始模拟。`);});
$('object-pen').addEventListener('click',()=>{if(exportSession())announce('已导出本标签页的模拟日志，文件包含 simulated:true 标记。');else announce('还没有模拟日志，请先点击机器人完成一次演示。');});
$('labels-toggle').addEventListener('click',()=>{const hidden=$('desk-stage').classList.toggle('labels-off');$('labels-toggle').setAttribute('aria-pressed',String(!hidden));$('labels-toggle').textContent=hidden?'显示物件提示':'隐藏物件提示';});
$('help-toggle').addEventListener('click',()=>location.assign('guide.html'));
const picture=$('desk-picture');picture.addEventListener('error',()=>{$('desk-stage').classList.add('asset-error');announce('场景图片未加载，文字入口仍可使用。');});
if(picture.complete&&!picture.naturalWidth)picture.dispatchEvent(new Event('error'));
window.addEventListener('pageshow',sync);window.addEventListener('pagehide',()=>clearTimeout(timer));
// Compatibility for previously shared single-file route links.
const legacy={workbench:'workbench.html',report:'report.html',about:'about.html',guide:'guide.html'};
const route=location.hash.replace(/^#\/?/,'');
if(legacy[route])location.replace(legacy[route]);
else if(route==='home')history.replaceState(null,'',location.pathname+location.search);
sync();
if(!storageAvailable)announce('浏览器禁止临时存储，跨页无法保留模拟记录；请在工作台演示后直接导出。');
