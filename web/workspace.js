const tabs=[...document.querySelectorAll('[data-view]')];
const call=document.getElementById('call-view'),report=document.getElementById('report-view');
export function show(view){call.hidden=view!=='call';report.hidden=view!=='report';tabs.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));}
tabs.forEach(b=>b.addEventListener('click',()=>show(b.dataset.view)));
const result=document.getElementById('result');
function update(){let r;try{r=JSON.parse(result.textContent)}catch{return}
let text='尚未运行模拟，请先在通话工作台选择场景。';
if(r.persistence_status==='unknown')text='模拟回写超时，登记结果待核对。未确认成功，不计入通知完成。';
else if(r.candidate_outcome==='interrupted')text='模拟已中止。保留已发生的模拟记录，不推断用户确认。';
else if(r.outcome==='wrong_contact')text='模拟身份不符，已停止披露申请详情，保存最小异常结果。';
else if(r.outcome==='notification_completed')text='模拟补正通知流程完成。仅浏览器内存演示，未产生真实业务登记。';
else if(document.getElementById('play').disabled)text='模拟正在进行，最终结果尚未确认。';
document.getElementById('report-summary').textContent=text;
}
new MutationObserver(update).observe(result,{childList:true,characterData:true,subtree:true});update();

document.querySelector('.enter-demo').addEventListener('click',()=>{show('call');document.getElementById('demo-workspace').focus({preventScroll:true});});
