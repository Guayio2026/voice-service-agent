// UI helpers shared only by the rewritten interior pages.
const search=document.getElementById('help-search');
if(search){
 const sections=[...document.querySelectorAll('.help-section')];
 const feedback=document.getElementById('search-feedback');
 search.addEventListener('input',()=>{
  const query=search.value.trim().toLocaleLowerCase();let count=0;
  for(const section of sections){const matched=!query||(section.textContent+' '+section.dataset.search).toLocaleLowerCase().includes(query);section.hidden=!matched;if(matched)count++;}
  feedback.hidden=!query;feedback.textContent=count?`找到 ${count} 个相关章节`:'没有找到相关内容。试试“暂停”“记录”或“声音”。';
  document.querySelector('.help-nav').hidden=!!query;
 });
 document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){event.preventDefault();search.focus();}});
}
