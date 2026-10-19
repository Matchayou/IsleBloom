import React,{useEffect,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read} from './api';
import {Icon} from './icons';
import Modal from './Modal';

export function deliveryUrl(projectId,file,download=false){
 return `/api/generated_file?${new URLSearchParams({project_id:projectId,result_id:file.result_id,file_id:file.file_id,...(download?{download:'1'}:{})})}`;
}
function FilePreview({projectId,file}){
 const [state,setState]=useState({status:'loading'});
 const url=deliveryUrl(projectId,file);
 useEffect(()=>{
  const controller=new AbortController();setState({status:'loading'});
  fetch(url,{signal:controller.signal}).then(async response=>{
   if(!response.ok)throw new Error(response.status===403?'访问已撤回或无权读取':`文件读取失败（HTTP ${response.status}）`);
   const content=await response.text();
   if(!controller.signal.aborted)setState({status:'ready',content});
  }).catch(error=>{if(error.name!=='AbortError')setState({status:'error',error:error.message})});
  return()=>controller.abort();
 },[url]);
 return <div className="delivery-preview"><h3>{file.name}</h3><p className="meta">{file.title} · 结果 v{file.version} · {file.bytes.toLocaleString()} 字节</p>
  <div className="delivery-actions"><a href={deliveryUrl(projectId,file,true)} download={file.name}>下载文件</a><a href={url} target="_blank" rel="noopener noreferrer">新窗口预览</a></div>
  {state.status==='loading'?<p role="status">正在读取文件…</p>:state.status==='error'?<p role="alert">{state.error}</p>:file.media_type==='text/html'?<iframe src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" title={`预览 ${file.name}`}/>:<pre>{state.content}</pre>}
 </div>;
}
export default function GeneratedFiles({projectId,researchId,revision=''}){
 const [state,setState]=useState({status:'loading',files:[]}),[selected,setSelected]=useState(null);
 useEffect(()=>{
  const controller=new AbortController();setState({status:'loading',files:[]});
  read('project_deliveries',{project_id:projectId,...(researchId?{research_id:researchId}:{})},controller.signal)
   .then(files=>{setState({status:'ready',files});setSelected(previous=>previous&&files.find(f=>f.result_id===previous.result_id&&f.file_id===previous.file_id)||null)})
   .catch(error=>{if(error.name!=='AbortError')setState({status:'error',files:[],error:error.message})});
  return()=>controller.abort();
 },[projectId,researchId,revision]);
 return <section className="generated-files" aria-label="交付文件"><p className="list-heading">交付文件 <span>{state.files.length}</span></p>
  {state.status==='loading'?<p role="status">正在读取交付文件…</p>:state.status==='error'?<p role="alert">{state.error}</p>:state.files.length===0?<p className="muted">暂无已保存的交付文件。</p>:state.files.map(file=><Button size="small" className={`file-row ${selected?.result_id===file.result_id&&selected?.file_id===file.file_id?'selected':''}`} key={`${file.result_id}:${file.file_id}`} onClick={()=>setSelected(file)}><Icon name="file" size={16}/><span>{file.name} · {file.title} · v{file.version}</span></Button>)}
  {selected&&<Modal title={selected.name} width="min(1100px, calc(100vw - 24px))" onClose={()=>setSelected(null)}><FilePreview key={`${selected.result_id}:${selected.file_id}`} projectId={projectId} file={selected}/></Modal>}
 </section>;
}
