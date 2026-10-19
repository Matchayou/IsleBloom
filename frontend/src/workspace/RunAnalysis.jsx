import React,{useEffect,useMemo,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read} from './api';

const statusText={succeeded:'成功',failed:'失败',cancelled:'已取消',interrupted:'已中断',running:'运行中',saving:'保存中',started:'开始',completed:'完成'};
const seconds=value=>value==null?'未知':`${value.toFixed(2)} s`;
const tokens=run=>`${run.usage.input_tokens.toLocaleString()} / ${run.usage.output_tokens.toLocaleString()}${run.usage.complete?'':'（部分已知）'}`;
function counts(run){
 const result=new Map();
 for(const event of run.timeline)if(event.phase==='tool'&&event.status==='started')result.set(event.name,(result.get(event.name)||0)+1);
 return result;
}
function Comparison({current,baseline}){
 const a=counts(current),b=counts(baseline);
 const delta=current.usage.complete&&baseline.usage.complete?current.usage.input_tokens+current.usage.output_tokens-baseline.usage.input_tokens-baseline.usage.output_tokens:null;
 return <div className="run-comparison"><h4>相对基线的变化</h4><p>状态：{statusText[baseline.status]||baseline.status} → {statusText[current.status]||current.status} · 总 token：{delta==null?'未知':`${delta>0?'+':''}${delta}`} · 总耗时：{current.elapsed_seconds==null||baseline.elapsed_seconds==null?'未知':seconds(current.elapsed_seconds-baseline.elapsed_seconds)}</p>
 <table><caption>工具调用次数对比</caption><thead><tr><th>工具</th><th>基线</th><th>当前</th><th>变化</th></tr></thead><tbody>{[...new Set([...a.keys(),...b.keys()])].map(name=><tr key={name}><td>{name}</td><td>{b.get(name)||0}</td><td>{a.get(name)||0}</td><td>{(a.get(name)||0)-(b.get(name)||0)}</td></tr>)}</tbody></table><small>调用次数和 token 变化是执行差异；质量需要固定任务评测。</small></div>;
}
function Replay({run}){
 const [step,setStep]=useState(run.timeline.length),[playing,setPlaying]=useState(false);
 useEffect(()=>{
  if(!playing)return;
  if(step>=run.timeline.length){setPlaying(false);return;}
  const timer=setTimeout(()=>setStep(value=>value+1),700);
  return()=>clearTimeout(timer);
 },[playing,step,run.timeline.length]);
 const failed=run.timeline.findIndex(event=>event.status==='failed');
 return <><div className="run-metrics"><span>输入 / 输出 token<strong>{tokens(run)}</strong></span><span>总耗时<strong>{seconds(run.elapsed_seconds)}</strong></span><span>费用<strong>未知（未记录计费单价）</strong></span><span>未确认操作<strong>{run.unconfirmed_operations??'未知'}</strong></span></div>
 {run.failure&&<p className="error" role="status">运行未成功：{run.failure.error_code||'原因未知'} · {run.failure.origin||'来源未知'}。{run.failure.sequence?`最早记录的失败事件：#${run.failure.sequence}。`:''}需结合任务输入和结果核查根因。</p>}
 <div className="run-controls"><Button size="small" onClick={()=>{if(step>=run.timeline.length)setStep(0);setPlaying(!playing)}} disabled={!run.timeline.length}>{playing?'暂停':'播放'}</Button><Button size="small" onClick={()=>{setPlaying(false);setStep(0)}}>回到起点</Button><Button size="small" disabled={failed<0} onClick={()=>{setPlaying(false);setStep(failed+1)}}>定位首个失败事件</Button><label>回放进度 <input type="range" min="0" max={run.timeline.length} value={step} onChange={event=>{setPlaying(false);setStep(Number(event.target.value))}}/> {step} / {run.timeline.length}</label></div>
 <ol className="run-timeline" aria-label="运行事件">{run.timeline.slice(0,step).map(event=><li key={event.sequence} className={event.status==='failed'?'failed':''}><strong>#{event.sequence} {event.name||event.phase} · {statusText[event.status]||event.status}</strong><span>{event.run_id===run.run_id?'当前 agent':`子运行 ${event.run_id}`} · {new Date(event.occurred_at).toLocaleTimeString()} · 耗时 {seconds(event.duration_seconds)}{event.error_code?` · ${event.error_code}`:''}</span></li>)}</ol>
 {!run.timeline.length&&<p>这次运行没有已保存的事件。</p>}
 {run.models.length>0&&<table><caption>模型请求测量（包含辅助请求）</caption><thead><tr><th>请求</th><th>用途 / 状态</th><th>耗时</th><th>输入 / 输出 token</th></tr></thead><tbody>{run.models.map(model=><tr key={model.operation_id}><td>{model.operation_id}</td><td>{model.purpose} / {model.state}</td><td>{seconds(model.duration_seconds)}</td><td>{model.usage?`${model.usage.input_tokens} / ${model.usage.output_tokens}`:'未知'}</td></tr>)}</tbody></table>}
 </>;
}
export default function RunAnalysis({researchId}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 const [selected,setSelected]=useState(''),[baselineId,setBaselineId]=useState('');
 useEffect(()=>{
  const controller=new AbortController();setData(null);setError('');
  read('run_analysis',{research_id:researchId},controller.signal).then(value=>{if(!controller.signal.aborted)setData(value)}).catch(err=>{if(!controller.signal.aborted)setError(err.message)});
  return()=>controller.abort();
 },[researchId,revision]);
 const runs=data?.runs||[];
 const current=runs.find(run=>run.run_id===selected)||runs[0];
 const baseline=runs.find(run=>run.run_id===baselineId&&run.run_id!==current?.run_id);
 const totals=useMemo(()=>runs.reduce((sum,run)=>({input:sum.input+run.usage.input_tokens,output:sum.output+run.usage.output_tokens,complete:sum.complete&&run.usage.complete}),{input:0,output:0,complete:true}),[data]);
 const agents=useMemo(()=>{
  const grouped=new Map();
  for(const run of runs){const row=grouped.get(run.agent_id)||{id:run.agent_id,kind:run.kind,objectId:run.object_id,count:0,input:0,output:0,complete:true};row.count++;row.input+=run.usage.input_tokens;row.output+=run.usage.output_tokens;row.complete&&=run.usage.complete;grouped.set(run.agent_id,row)}
  return [...grouped.values()].sort((a,b)=>(b.input+b.output)-(a.input+a.output));
 },[data]);
 const title=run=>`${run.kind==='task'?'专家':'对话'} ${run.object_id.slice(0,8)} · ${statusText[run.status]||run.status} · ${new Date(run.created_at).toLocaleString()} · ${run.run_id.slice(0,8)}`;
 return <section className="run-analysis" aria-label="运行分析"><div className="run-analysis-heading"><h3>运行分析</h3><Button size="small" onClick={()=>setRevision(value=>value+1)}>刷新运行记录</Button></div><p>回放保存的执行事件，查看失败位置与用量，并选择基线对比。</p>
 {error?<p className="error" role="alert">{error}</p>:!data?<p role="status">正在读取运行记录…</p>:<>
 <p>{runs.length} 次可读运行 · 输入 {totals.input.toLocaleString()} / 输出 {totals.output.toLocaleString()} token{totals.complete?'':'（部分已知）'}{data.unavailable_runs?` · ${data.unavailable_runs} 次运行当前无权读取`:''}</p>
 {agents.length>0&&<table><caption>Agent 用量（按已知 token 排序）</caption><thead><tr><th>Agent</th><th>运行次数</th><th>输入 / 输出 token</th><th>测量</th></tr></thead><tbody>{agents.map(agent=><tr key={agent.id}><td title={agent.id}>{agent.kind==='task'?'专家':'对话'} {agent.id}</td><td>{agent.count}</td><td>{agent.input.toLocaleString()} / {agent.output.toLocaleString()}</td><td>{agent.complete?'完整':'部分已知'}</td></tr>)}</tbody></table>}
 {current?<><div className="run-selectors"><label>查看运行<select value={current.run_id} onChange={event=>setSelected(event.target.value)}>{runs.map(run=><option key={run.run_id} value={run.run_id}>{title(run)}</option>)}</select></label><label>对比基线<select value={baseline?.run_id||''} onChange={event=>setBaselineId(event.target.value)}><option value="">不对比</option>{runs.filter(run=>run.run_id!==current.run_id).map(run=><option key={run.run_id} value={run.run_id}>{title(run)}</option>)}</select></label></div>
 <Replay key={`${current.run_id}:${revision}`} run={current}/>{baseline&&<Comparison current={current} baseline={baseline}/>}</>:<p>执行一次对话或专家任务后，这里会显示真实运行记录。</p>}
 </>}
 </section>;
}
