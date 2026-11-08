// A standalone decorative control: also works when previewing index.html directly.
// No audio, permission request, network, service connection or business state.
(() => {
  const stage=document.getElementById('desk-stage');
  const button=document.getElementById('object-lamp');
  const caption=document.getElementById('lamp-caption');
  if(!stage||!button||!caption)return;
  button.addEventListener('click',() => {
    const soft=stage.dataset.light!=='soft';
    stage.dataset.light=soft?'soft':'standard';
    button.setAttribute('aria-pressed',String(soft));
    caption.textContent=soft?'柔和 · 点亮':'明亮 · 调暗';
  });
})();
