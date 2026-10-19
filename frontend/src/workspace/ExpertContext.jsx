import React,{useEffect,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read} from './api';
import MarkdownText from './MarkdownText';

export default function ExpertContext({taskId}){
 const [runId,setRunId]=useState(''),[reload,setReload]=useState(0),[state,setState]=useState({status:'loading'});
 useEffect(()=>{
  const controller=new AbortController();setState({status:'loading'});
  read('expert_context',{task_id:taskId,...(runId?{run_id:runId}:{})},controller.signal)
   .then(data=>{if(!controller.signal.aborted)setState({status:'ready',data})})
   .catch(error=>{if(!controller.signal.aborted)setState({status:'error',error:error.message})});
  return()=>controller.abort();
 },[taskId,runId,reload]);
 const data=state.status==='ready'&&(!runId||state.data.run_id===runId)?state.data:null;
 const snapshot=data?.snapshot,packet=data?.handoff;
 return <section className="expert-context" aria-label="子 Agent 上下文">
  <p>查看此 Agent 已保存的开轮上下文。后续工具结果、补充消息与上下文压缩会改变每轮模型请求。</p>
  <Button size="small" onClick={()=>setReload(value=>value+1)}>刷新上下文</Button>
  {state.status==='error'?<p className="error" role="alert">{state.error}</p>:!data?<p role="status">正在读取上下文…</p>:<>
   {data.runs.length>0&&<label className="expert-context-run">选择运行<select value={data.run_id||''} onChange={event=>setRunId(event.target.value)}>{data.runs.map(run=><option key={run.run_id} value={run.run_id}>{run.kind==='review'?'评审':'执行'} · 任务单 v{run.spec_version} · {new Date(run.created_at).toLocaleString()} · {run.run_id.slice(0,8)}</option>)}</select></label>}
   {data.state==='not_started'&&<p>尚未开始运行，暂无开轮快照。下方显示已保存的交接要求。</p>}
   {data.state==='not_recorded'&&<p>这次运行没有保存可读取的上下文快照。</p>}
   {packet&&<section><h3>主助手交接</h3><h4>用户原话</h4><MarkdownText value={packet.user_instruction}/><h4>任务指令</h4><MarkdownText value={packet.manager_instruction}/><h4>交付要求</h4><MarkdownText value={packet.delivery_requirements}/>{packet.selected_messages?.length>0&&<details><summary>交接的历史消息（{packet.selected_messages.length}）</summary>{packet.selected_messages.map(message=><article key={message.id}><small>{message.role}</small><MarkdownText value={message.content}/></article>)}</details>}</section>}
   {snapshot&&<>
    <p>任务单 v{data.spec_version} · {snapshot.mode==='discussion'?'评审/讨论上下文':'执行上下文'} · 快照 {snapshot.id}</p>
    <h3>Agent 身份与角色定义</h3><p>{snapshot.agent?.id}</p>{snapshot.agent?.instructions&&<MarkdownText value={snapshot.agent.instructions}/>}
    <h3>开轮输入消息</h3>{snapshot.input.messages.map((message,index)=><article key={index} className="expert-context-message"><strong>{message.role==='user'?'用户输入':message.role==='assistant'?'助手历史':message.role}</strong><MarkdownText value={message.content}/></article>)}
    <h3>附带上下文</h3>{Object.keys(snapshot.input.external_context).length?Object.entries(snapshot.input.external_context).map(([name,body])=><details key={name}><summary>{name}</summary><pre>{body}</pre></details>):<p>没有单独保存的附带正文；已选择内容见输入消息。</p>}
    <h3>获准工具</h3><p>{snapshot.tools.length?snapshot.tools.join('、'):'无'}</p>
    <details><summary>资料、结果与上下文选择明细</summary><pre>{JSON.stringify({mounts:snapshot.mounts,result_mounts:snapshot.result_mounts,skill_catalog:snapshot.skill_catalog,memory_refs:snapshot.memory_refs,selection:snapshot.selection},null,2)}</pre></details>
   </>}
  </>}
 </section>;
}
