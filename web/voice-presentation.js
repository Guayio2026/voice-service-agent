// Presentation only: never creates a session or submits a question.
const conversation=document.getElementById('conversation');
const message=document.getElementById('message');
function refreshWelcome(){
  if(!conversation.children.length){const empty=document.createElement('div');empty.className='voice-empty';conversation.append(empty);}
  const empty=conversation.querySelector('.voice-empty');if(!empty)return;
  const ready=!message.disabled;
  const key=String(ready);if(empty.dataset.ready===key)return;
  empty.dataset.ready=key;
  empty.innerHTML='<div class="welcome-line"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4zM8 9h8M8 12h5"/></svg><strong>你好，我是小语。</strong></div><p>'+ (ready?'可以直接说话，也可以选一个问题开始。':'创建会话并确认身份后，可以从这些问题开始。')+'</p><div class="starter-questions"></div>';
  for(const question of ['我需要补充什么材料？','补充材料在哪里提交？','我需要人工协助。']){const button=document.createElement('button');button.type='button';button.textContent=question;button.disabled=!ready;button.addEventListener('click',()=>{message.value=question;message.dispatchEvent(new Event('input',{bubbles:true}));message.focus();});empty.querySelector('.starter-questions').append(button);}
}
const observer=new MutationObserver(refreshWelcome);
observer.observe(conversation,{childList:true});
observer.observe(message,{attributes:true,attributeFilter:['disabled']});
refreshWelcome();
