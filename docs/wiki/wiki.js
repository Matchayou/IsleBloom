const chapters=[...document.querySelectorAll('.chapter')];
const navLinks=[...document.querySelectorAll('.nav-link')];
const search=document.querySelector('#search');
const results=document.querySelector('#search-results');
const dialog=document.querySelector('#source-dialog');
const sources=JSON.parse(document.querySelector('#source-data').textContent);
const sidebar=document.querySelector('.sidebar');
const menu=document.querySelector('#menu');
function closeNav(){sidebar.classList.remove('open');document.body.classList.remove('nav-open');menu.setAttribute('aria-expanded','false')}
menu.addEventListener('click',()=>{const open=sidebar.classList.toggle('open');document.body.classList.toggle('nav-open',open);menu.setAttribute('aria-expanded',String(open))});
navLinks.forEach(a=>a.addEventListener('click',closeNav));
document.addEventListener('click',e=>{if(sidebar.classList.contains('open')&&!sidebar.contains(e.target)&&!menu.contains(e.target))closeNav()});
const observer=new IntersectionObserver(entries=>{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(!visible.length)return;const id=visible[0].target.id;navLinks.forEach(a=>{const selected=a.hash==='#'+id;a.classList.toggle('active',selected);if(selected)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current')})},{rootMargin:'-80px 0px -65% 0px',threshold:0});chapters.forEach(s=>observer.observe(s));
let timer;
search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{const query=search.value.trim().toLocaleLowerCase();results.replaceChildren();results.hidden=!query;if(!query)return;const matches=chapters.filter(s=>s.textContent.toLocaleLowerCase().includes(query));for(const section of matches){const link=document.createElement('a');link.href='#'+section.id;const title=document.createElement('b');title.textContent=section.querySelector('h2').textContent;const snippet=document.createElement('small');const text=section.querySelector('.details-body')?.textContent||section.textContent;const at=text.toLocaleLowerCase().indexOf(query);snippet.textContent=text.slice(Math.max(0,at-35),Math.max(0,at-35)+130).replace(/\s+/g,' ');link.append(title,snippet);link.addEventListener('click',()=>{section.querySelector('details')?.setAttribute('open','');results.hidden=true;closeNav()});results.append(link)}if(!matches.length){const p=document.createElement('p');p.textContent='没有匹配的章节。试试函数名、系统名或状态码。';results.append(p)}document.querySelector('#search-count').textContent=`找到 ${matches.length} 个相关章节`;},120)});
search.addEventListener('keydown',e=>{if(e.key==='Escape'){results.hidden=true;search.blur()}if(e.key==='Enter'){const first=results.querySelector('a');if(first){first.click();location.hash=first.hash}}});
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();search.focus()}if(e.key==='Escape')closeNav()});
document.addEventListener('click',e=>{if(!e.target.closest('.search'))results.hidden=true;const link=e.target.closest('[data-source]');if(!link)return;e.preventDefault();const path=link.dataset.source;const line=Number(link.dataset.line||1);const body=sources[path];if(body===undefined)return;document.querySelector('#source-title').textContent=path+':'+line;document.querySelector('#source-location').textContent='2026-10-12 源码快照 · 高亮定义行 · 当前页面可离线查看';const view=document.querySelector('#source-code');view.replaceChildren();body.split('\n').forEach((text,index)=>{const row=document.createElement('div');row.className='code-line'+(index+1===line?' highlight':'');const number=document.createElement('span');number.className='line-number';number.textContent=index+1;const code=document.createElement('span');code.className='line-text';code.textContent=text||' ';row.append(number,code);view.append(row)});dialog.showModal();requestAnimationFrame(()=>{const target=view.children[Math.max(0,line-5)];view.scrollTop=target?.offsetTop-view.offsetTop||0})});
document.querySelector('#source-close').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close()}});
document.querySelector('#print').addEventListener('click',()=>{document.querySelectorAll('details').forEach(d=>d.open=true);window.print()});
document.querySelector('#expand').addEventListener('click',e=>{const expand=e.target.dataset.expanded!=='true';document.querySelectorAll('.details-body').forEach(d=>d.open=expand);e.target.dataset.expanded=String(expand);e.target.textContent=expand?'收起完整讲解':'展开完整讲解'});
window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>d.open=true));
window.addEventListener('scroll',()=>{const max=document.documentElement.scrollHeight-innerHeight;document.querySelector('.reading-progress').style.transform='scaleX('+Math.min(1,Math.max(0,max?scrollY/max:0))+')'},{passive:true});

// Diagram interactions remain local: each node references the Wiki's fixed source snapshot.
const nodeData=JSON.parse(document.querySelector('#node-data').textContent);
const nodeDialog=document.querySelector('#node-dialog');
let selectedNodeTrigger=null;
function showNode(id,trigger){
 const node=nodeData[id];
 if(!node)throw new Error('Unknown Wiki node: '+id);
 if(trigger){selectedNodeTrigger?.classList.remove('node-selected');selectedNodeTrigger=trigger;trigger.classList.add('node-selected')}
 nodeDialog.dataset.activeNode=id;
 const section=node.systems.find(system=>system.number===node.chapter);
 const step=id.match(/^step-(\d+)-(\d+)$/);
 document.querySelector('#node-context').textContent=section.title+(step?' · 第 '+step[2]+' / 6 步':' · 节点详情');
 document.querySelector('#node-title').textContent=node.title;
 document.querySelector('#node-role').textContent=node.role;
 document.querySelector('#node-input').textContent=node.input;
 document.querySelector('#node-output').textContent=node.output;
 const functions=document.querySelector('#node-functions');functions.replaceChildren();
 for(const ref of node.functions){
  const card=document.createElement('div');card.className='node-function';
  const link=document.createElement('a');link.href='#source-dialog';link.className='source-link';link.dataset.source=ref.path;link.dataset.line=ref.line;link.textContent=ref.symbol+' ↗';
  const signature=document.createElement('code');signature.className='node-signature';signature.textContent=ref.signature;
  const location=document.createElement('small');location.textContent=ref.path+':'+ref.line;
  const signatureDetails=document.createElement('details');signatureDetails.className='node-signature-details';const summary=document.createElement('summary');summary.textContent='查看函数参数签名';signatureDetails.append(summary,signature);card.append(link,location,signatureDetails);functions.append(card);
 }
 const systems=document.querySelector('#node-systems');systems.replaceChildren();
 node.systems.forEach((system,index)=>{const link=document.createElement('a');link.href='#section-'+system.number;link.dataset.chapter=system.number;link.className='node-system';link.textContent=(index===0?'本系统 · ':'')+String(system.number).padStart(2,'0')+' '+system.title+' →';systems.append(link)});
 for(const [buttonId,key] of [['node-previous','previous'],['node-next','next']]){const button=document.getElementById(buttonId);button.disabled=!node[key];if(node[key])button.dataset.node=node[key];else delete button.dataset.node}
 document.querySelector('#node-position').textContent=step?'请求链路 '+step[2]+' / 6':'';
 document.querySelector('.node-footer').hidden=!step;
 document.querySelector('.node-body').scrollTop=0;
 if(!nodeDialog.open)nodeDialog.showModal();
}
document.addEventListener('click',event=>{
 const trigger=event.target.closest('button[data-node]');
 if(trigger){showNode(trigger.dataset.node,trigger.closest('#node-dialog')?null:trigger);return}
 const chapterLink=event.target.closest('#node-dialog [data-chapter]');
 if(chapterLink){event.preventDefault();const target=document.getElementById('section-'+chapterLink.dataset.chapter);if(dialog.open)dialog.close();nodeDialog.close();closeNav();target.querySelector('.details-body')?.setAttribute('open','');location.hash=target.id;target.scrollIntoView({block:'start'})}
});
document.querySelector('#node-close').addEventListener('click',()=>nodeDialog.close());
nodeDialog.addEventListener('click',event=>{if(event.target===nodeDialog){const rect=nodeDialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)nodeDialog.close()}});
