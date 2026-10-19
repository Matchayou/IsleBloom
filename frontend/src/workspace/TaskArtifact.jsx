import {Button} from 'animal-island-ui';
import React,{useEffect,useState} from 'react';
import {read} from './api';
import MarkdownText from './MarkdownText';

export default function TaskArtifact({resultId,expandable=false}){
 const [opened,setOpened]=useState(!expandable);
 const [value,setValue]=useState({status:'idle'});
 useEffect(()=>{
  if(!opened)return;
  const controller=new AbortController();
  setValue({status:'loading'});
  read('task_artifact',{id:resultId},controller.signal)
   .then(data=>setValue({status:'ready',data}))
   .catch(error=>{if(error.name!=='AbortError')setValue({status:'error',error:error.message})});
  return()=>controller.abort();
 },[resultId,opened]);
 return <section className="task-artifact">
  {expandable&&<Button size="small" className="subtle-button" onClick={()=>setOpened(!opened)}>{opened?'收起完整报告':'查看完整报告'}</Button>}
  {opened&&(value.status==='loading'?<p role="status">正在读取完整报告…</p>:value.status==='error'?<p className="error" role="alert">{value.error}</p>:value.status==='ready'?<>
   <MarkdownText value={value.data.output.report}/>
   {value.data.output.candidate&&<section aria-label="代码候选">
    <h4>冻结代码候选</h4>
    <details><summary>代码版本信息</summary><p>基线 <code>{value.data.output.candidate.baseline}</code></p><p>候选 <code>{value.data.output.candidate.commit}</code></p></details>
    <details><summary>查看代码改动</summary><pre>{value.data.output.code_patch}</pre></details>
    <details><summary>对应快照的测试记录</summary>{value.data.output.candidate.tests.map(test=><div key={test.profile}>
     <p>{test.profile} · 退出码 {test.exit_code} · {test.environment_id}</p><pre>{test.log}</pre>
    </div>)}</details>
   </section>}
   <a href={`/api/task_artifact?${new URLSearchParams({id:resultId})}`} download="report.json">下载完整成果（JSON）</a>
   <p className="muted">全文已保存 · {value.data.artifact.chars} 字符</p>
  </>:null)}
 </section>;
}
