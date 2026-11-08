// Page routes retain the same in-memory mock session; no backend or storage is used.
const $=id=>document.getElementById(id);
const routes=new Set(['home','workbench','report','about','guide']);
const titles={home:'蓝色桌面',workbench:'模拟工作台',report:'会话报告',about:'项目介绍',guide:'操作指南'};
let currentRoute;

export function renderRoute() {
  const requested=location.hash.replace(/^#\/?/,'')||'home';
  const route=routes.has(requested)?requested:'home';
  const working=route==='workbench'||route==='report';
  $('home').hidden=route!=='home';
  $('workspace-shell').hidden=!working;
  $('about-page').hidden=route!=='about';
  $('guide-page').hidden=route!=='guide';
  $('subpage-header').hidden=route==='home';
  $('subpage-footer').hidden=route==='home';
  $('call-view').hidden=route!=='workbench';
  $('report-view').hidden=route!=='report';
  $('workspace-title').textContent=titles[route];
  $('workspace-description').textContent=route==='report'?'查看当前模拟会话的处理结果与证据。':'从一次补正通知，看到对话、依据与处理结果。';
  document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===(route==='report'?'report':'call'))));
  document.querySelectorAll('[data-route]').forEach(link=>{
    if(link.dataset.route===route) link.setAttribute('aria-current','page');
    else link.removeAttribute('aria-current');
  });
  document.title=`${titles[route]} · 智语外呼`;
  document.body.dataset.route=route;
  if(currentRoute!==route) {
    window.scrollTo({top:0,behavior:'instant'});
    if(currentRoute!==undefined) {
      const heading=route==='home'?$('desk-title'):working?$('workspace-title'):route==='about'?$('about-title'):$('guide-title');
      heading.focus({preventScroll:true});
    }
  }
  currentRoute=route;
}

export function navigate(route) {
  if(!routes.has(route)) route='home';
  if(location.hash!==`#/${route}`) history.pushState(null,'',`#/${route}`);
  renderRoute();
}
export function show(view) { navigate(view==='report'?'report':'workbench'); }
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>show(button.dataset.view)));
document.querySelectorAll('a[data-route]').forEach(link=>link.addEventListener('click',event=>{
  if(event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();navigate(link.dataset.route);
}));
window.addEventListener('popstate',renderRoute);
window.addEventListener('hashchange',renderRoute);

function updateReport(){
  let result;try{result=JSON.parse($('result').textContent)}catch{return}
  let text='尚未运行模拟，请先在通话工作台选择场景。';
  if(result.persistence_status==='unknown')text='模拟回写超时，登记结果待核对。未确认成功，不计入通知完成。';
  else if(result.candidate_outcome==='interrupted')text='模拟已中止。保留已发生的模拟记录，不推断用户确认。';
  else if(result.outcome==='wrong_contact')text='模拟身份不符，已停止披露申请详情，保存最小异常结果。';
  else if(result.outcome==='notification_completed')text='模拟补正通知流程完成。仅浏览器内存演示，未产生真实业务登记。';
  else if($('play').disabled)text='模拟正在进行，最终结果尚未确认。';
  $('report-summary').textContent=text;
}
new MutationObserver(updateReport).observe($('result'),{childList:true,characterData:true,subtree:true});
updateReport();renderRoute();
