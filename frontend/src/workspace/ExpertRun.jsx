import React,{useEffect,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read} from './api';
import TaskArtifact from './TaskArtifact';

const states={succeeded:'成功',failed:'失败',cancelled:'已取消',interrupted:'已中断',running:'运行中',saving:'保存中',started:'运行中',completed:'完成',unknown:'状态未确认'};
const seconds=value=>value==null?'未记录':`${value.toFixed(2)} s`;
function JsonBody({value}){
 let body=value;
 if(typeof value==='string'){try{body=JSON.parse(value)}catch{/* Plain text is also a valid tool input. */}}
 return <pre tabIndex={0}>{typeof body==='string'?body:JSON.stringify(body,null,2)}</pre>;
}
function ToolCall({taskId,runId,call}){
 const [open,setOpen]=useState(false),[attempt,setAttempt]=useState(0),[state,setState]=useState({status:'idle'});
 useEffect(()=>{
  if(!open)return;
  const controller=new AbortController();setState({status:'loading'});
  read('expert_run',{task_id:taskId,run_id:runId,call_id:call.call_id},controller.signal)
   .then(data=>{if(!controller.signal.aborted)setState({status:'ready',data})})
   .catch(error=>{if(!controller.signal.aborted)setState({status:'error',error:error.message})});
  return()=>controller.abort();
 },[taskId,runId,call.call_id,open,attempt]);
 return <details className="tool-call" onToggle={event=>setOpen(event.currentTarget.open)}>
  <summary>{call.name} · {states[call.status]||call.status} · {seconds(call.duration_seconds)}</summary>
  <p>调用 {call.call_id}</p>
  <p>开始：{call.started_at?new Date(call.started_at).toLocaleString():'未记录'} · 结束：{call.ended_at?new Date(call.ended_at).toLocaleString():'未记录'}</p>
  {call.error&&<p className="error">{call.error.code} · {call.error.message}</p>}
  {state.status==='loading'?<p role="status">正在读取调用输入输出…</p>:state.status==='error'?<><p className="error" role="alert">{state.error}</p><Button size="small" onClick={()=>setAttempt(value=>value+1)}>重试读取</Button></>:state.status==='ready'&&<>
   <h4>工具输入</h4>{state.data.input_recorded?<JsonBody value={state.data.input}/>:<p>此调用没有可读取的输入记录。</p>}
   <h4>工具输出</h4>{state.data.output_recorded?<JsonBody value={state.data.output}/>:<p>{state.data.confirmation==='unconfirmed'?'操作尚未确认，不能将已有内容视为已完成输出。':'此调用没有已确认的输出回执。'}</p>}
   <small>显示当时返回给 Agent 的内容；分页结果可能只是其中一页。</small>
  </>}
 </details>;
}
function RunDetail({taskId,runId}){
 const [attempt,setAttempt]=useState(0),[state,setState]=useState({status:'loading'});
 useEffect(()=>{
  const controller=new AbortController();setState({status:'loading'});
  read('expert_run',{task_id:taskId,run_id:runId},controller.signal)
   .then(data=>{if(!controller.signal.aborted)setState({status:'ready',data})})
   .catch(error=>{if(!controller.signal.aborted)setState({status:'error',error:error.message})});
  return()=>controller.abort();
 },[taskId,runId,attempt]);
 if(state.status==='error')return <><p className="error" role="alert">{state.error}</p><Button size="small" onClick={()=>setAttempt(value=>value+1)}>重试读取运行</Button></>;
 if(state.status!=='ready')return <p role="status">正在读取对应运行…</p>;
 return <RunEvidence taskId={taskId} data={state.data}/>;
}
export function RunEvidence({taskId,data,callIds=null}){
 const run=data.run,runId=data.run_id;
 const calls=callIds===null?data.tool_calls:data.tool_calls.filter(call=>callIds.includes(call.call_id));
 return <div className="expert-run-detail">
  <p>任务单 v{data.spec_version} · {states[run.status]||run.status} · {new Date(run.created_at).toLocaleString()}</p>
  <small>运行 ID：{data.run_id}</small>
  <div className="run-metrics"><span>总耗时<strong>{seconds(run.elapsed_seconds)}</strong></span><span>输入 / 输出 token<strong>{run.usage.input_tokens.toLocaleString()} / {run.usage.output_tokens.toLocaleString()}{!run.usage.complete&&'（部分已知）'}</strong></span></div>
  <details><summary>运行输入 · 开轮上下文</summary>{data.input?<><p>这是本次运行开始时保存的输入，后续工具返回见下方调用记录。</p>{data.input.messages.map((message,index)=><article key={index}><strong>{message.role}</strong><JsonBody value={message.content}/></article>)}{Object.entries(data.input.external_context).map(([key,value])=><details key={key}><summary>附带正文 {key}</summary><JsonBody value={value}/></details>)}</>:<p>这次运行没有可读取的开轮快照。</p>}</details>
  <h3>运行输出</h3>
  {data.result_id?<TaskArtifact key={data.result_id} resultId={data.result_id} expandable/>:data.output!=null?<JsonBody value={data.output}/>:<p>本次运行尚无已保存的输出。</p>}
  <h3>{callIds===null?'工具调用':'关联工具调用'}（{calls.length}）</h3>
  <p>按调用 ID 归并开始与结束通知，同名工具的不同调用分别保留。</p>
  {calls.length?calls.map(call=><ToolCall key={call.call_id} taskId={taskId} runId={runId} call={call}/>):<p>{callIds===null?'这次运行没有可读取的工具调用记录。':'此问题尚未关联具体工具调用。'}</p>}
 </div>;
}
export default function ExpertRun({activity}){
 const versions=[...new Set([activity.spec.version,...activity.runs.map(run=>run.binding.spec_version)])].sort((a,b)=>b-a);
 const [version,setVersion]=useState(versions[0]),[selection,setSelection]=useState('');
 const runs=activity.runs.filter(run=>run.binding.spec_version===version);
 const current=runs.find(run=>run.binding.id===selection)||runs.at(-1);
 return <section className="expert-run" aria-label="报告与运行详情">
  <div className="run-selectors">
   <label>报告版本<select value={version} onChange={event=>{setVersion(Number(event.target.value));setSelection('')}}>{versions.map(value=><option key={value} value={value}>{activity.runs.some(run=>run.binding.spec_version===value&&run.artifact)?`报告 v${value}`:`任务单 v${value}（无已保存报告）`}</option>)}</select></label>
   <label>对应运行<select value={current?.binding.id||''} disabled={!runs.length} onChange={event=>setSelection(event.target.value)}>{!runs.length&&<option value="">尚未执行</option>}{runs.map((run,index)=><option key={run.binding.id} value={run.binding.id}>第 {index+1} 次 · {states[run.execution_status]||run.execution_status} · {run.binding.id.slice(0,8)}</option>)}</select></label>
  </div>
  {current?<RunDetail key={current.binding.id} taskId={activity.spec.id} runId={current.binding.id}/>:<p>此任务版本尚未开始运行。</p>}
 </section>;
}
