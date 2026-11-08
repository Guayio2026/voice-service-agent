// Authored decorative canvas. No audio, microphone, API, or business-state input.
const canvas=document.getElementById('scene-canvas');
const ctx=canvas.getContext('2d');
const stage=document.querySelector('.concept-stage');
const toggle=document.getElementById('motion-toggle');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
let paused=reduced.matches,visible=true,raf=0,last=0,time=0,w=0,h=0,aim=0,offset=0;
const pulseButton=document.getElementById("scene-pulse");
let pointer={x:0,y:0,strength:0,target:0},ripples=[];
function draw(){
 if(!ctx)return;
 ctx.clearRect(0,0,w,h);
 const count=w<600?24:42;
 for(let i=0;i<count;i++){
  const p=i/(count-1);
  ctx.beginPath();
  for(let x=-20;x<=w+20;x+=12){
   const u=x/w;
   const curve=Math.sin(u*4.5-1.4+time*.16)*h*.22;
   const fold=Math.sin(u*7.0+time*.12+p*.55)*h*.06;
   let y=h*.68+curve+fold+(p-.5)*h*(.28+.44*u)+offset*u*65;
   const reach=Math.exp(-(((x-pointer.x)/180)**2));
   y+=(pointer.y-y)*reach*.6*pointer.strength;
   for(const r of ripples){const d=Math.hypot(x-r.x,y-r.y);const ring=d-(time-r.birth)*240;y+=Math.sin(ring/28)*Math.exp(-((ring/85)**2))*27*Math.max(0,1-(time-r.birth)/2.4);}
   if(x===-20)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.strokeStyle=i%7===0?'rgba(109,220,236,.54)':'rgba(74,145,214,.27)';
  ctx.lineWidth=i%7===0?1.4:.8;ctx.stroke();
 }
 for(const r of ripples){const age=time-r.birth;ctx.beginPath();ctx.arc(r.x,r.y,Math.max(1,age*240),0,Math.PI*2);ctx.strokeStyle=`rgba(157,233,250,${Math.max(0,.65*(1-age/2.4))})`;ctx.lineWidth=1.3;ctx.stroke();}
}
function frame(now){raf=0;if(paused||!visible||document.hidden)return;
 if(now-last>33){time+=Math.min((now-last)/1000,.06);last=now;offset+=(aim-offset)*.08;pointer.strength+=(pointer.target-pointer.strength)*.12;ripples=ripples.filter(r=>time-r.birth<2.4);draw();}
 raf=requestAnimationFrame(frame);
}
function sync(){cancelAnimationFrame(raf);raf=0;last=performance.now();
 toggle.disabled=reduced.matches;toggle.textContent=reduced.matches?'已减少动态效果':paused?'播放背景动效':'暂停背景动效';pulseButton.disabled=paused||reduced.matches;toggle.setAttribute('aria-pressed',String(!paused));
 if(!paused&&visible&&!document.hidden&&ctx)raf=requestAnimationFrame(frame);
}
function resize(){w=stage.clientWidth;h=stage.clientHeight;const ratio=Math.min(devicePixelRatio||1,1.5);canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);ctx?.setTransform(ratio,0,0,ratio,0,0);draw();}
toggle.onclick=()=>{paused=!paused;sync();};
stage.addEventListener('pointermove',e=>{if(e.pointerType==='mouse'&&!paused){const r=stage.getBoundingClientRect();aim=(e.clientY-r.top)/r.height-.5;pointer.x=e.clientX-r.left;pointer.y=e.clientY-r.top;pointer.target=1;}});
stage.addEventListener('pointerleave',()=>{aim=0;pointer.target=0;});
reduced.addEventListener('change',()=>{paused=reduced.matches;offset=aim=0;pointer.target=pointer.strength=0;ripples=[];draw();sync();});
document.addEventListener('visibilitychange',sync);
new ResizeObserver(resize).observe(stage);
new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();}).observe(stage);
window.addEventListener('pagehide',()=>cancelAnimationFrame(raf));
if(!ctx){toggle.disabled=true;toggle.textContent='静态背景';}else{resize();sync();}

function pulse(x,y){if(paused||reduced.matches||!ctx)return;ripples.push({x,y,birth:time});if(ripples.length>5)ripples.shift();}
stage.addEventListener('click',e=>{if(e.target.closest('a,button'))return;const r=stage.getBoundingClientRect();pulse(e.clientX-r.left,e.clientY-r.top);});
pulseButton.addEventListener('click',()=>pulse(w*.74,h*.55));
