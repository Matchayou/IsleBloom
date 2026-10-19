import React,{useEffect,useRef,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read,write} from './api';
import MarkdownText from './MarkdownText';

const labels={generating:'正在分析并生成计划',awaiting_confirmation:'等待你确认',generation_failed:'计划生成失败',rejected:'已拒绝',queued:'已确认，等待执行',running:'正在执行',succeeded:'计划执行完成',failed:'执行失败',blocked:'执行受阻',interrupted:'已中断，需要检查',cancelled:'已取消'};
const errorLabels={timeout:'步骤超过批准的执行时限',plan_token_budget:'剩余总 token 预算不足，已停止发送新的模型请求',evidence_invalid:'报告引用未通过校验，尚未交付',model_protocol:'模型未返回可用的结构化结果'};
const live=new Set(['generating','queued','running']);
const toolNames={read_source:'读取资料',search_source:'检索资料',search_web:'联网搜索',fetch_web:'获取网页正文'};
export default function PlanPanel({researchId,nodeId,chat,mode,draft,setDraft,onChanged}){
 const [items,setItems]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[tick,setTick]=useState(0);
 const [feedback,setFeedback]=useState({});
 const request=useRef(null),callbacks=useRef(onChanged);callbacks.current=onChanged;
 useEffect(()=>{
   const controller=new AbortController();let timer;
   read('plan_proposals',{research_id:researchId,node_id:nodeId},controller.signal).then(data=>{
     setItems(data.sort((a,b)=>b.created_at.localeCompare(a.created_at)));
     if(data.some(p=>live.has(p.status))){timer=setTimeout(()=>setTick(v=>v+1),1500);callbacks.current(researchId,nodeId)}
   }).catch(err=>{if(err.name!=='AbortError')setError(err.message)});
   return()=>{controller.abort();clearTimeout(timer)};
 },[researchId,nodeId,tick]);
 async function generate(e){
   e.preventDefault();if(busy||!draft.trim()||chat.status!=='ready')return;
   setBusy(true);setError('');
   const signature=JSON.stringify([draft.trim(),chat.data.chat.revision]);
   if(request.current?.signature!==signature)request.current={signature,body:{research_id:researchId,origin_node_id:nodeId,expected_chat_revision:chat.data.chat.revision,request_id:crypto.randomUUID(),request:draft.trim()}};
   try{await write('plan_generate',request.current.body);request.current=null;setDraft('');setTick(v=>v+1)}
   catch(err){setError(`${err.message}。可重试同一请求；确认前不会执行步骤。`)}finally{setBusy(false)}
 }
 async function act(p,action){
   if(busy)return;setBusy(true);setError('');
   try{
     const body={proposal_id:p.id,expected_revision:p.revision};
     if(action==='revise'){body.feedback=feedback[p.id];body.request_id=crypto.randomUUID()}
     await write(`plan_${action}`,body);setTick(v=>v+1);callbacks.current(researchId,nodeId);
   }catch(err){setError(err.message);setTick(v=>v+1)}finally{setBusy(false)}
 }
 return <section className="plan-panel" aria-label="研究计划">
   {mode==='plan'&&<form className="action-form" onSubmit={generate}>
     <label htmlFor={`plan-request-${nodeId}`}>先分析并生成计划</label>
     <textarea id={`plan-request-${nodeId}`} value={draft} onChange={e=>setDraft(e.target.value)} rows="2" placeholder="描述目标和约束，确认计划后才执行"/>
     <small>规划只读取已授权资料。生成会消耗模型预算；计划步骤会等你确认后执行。</small>
     <Button size="small" type="primary" htmlType="submit" disabled={busy||!draft.trim()||chat.status!=='ready'}>{busy?'正在提交…':'生成计划'}</Button>
   </form>}
   {error&&<p className="action-error" role="alert">{error}<Button size="small" onClick={()=>{setError('');setTick(v=>v+1)}}>刷新计划</Button></p>}
   {items.map(p=><article className="plan-card" key={p.id} aria-label={`计划：${labels[p.status]}`}>
     <div className="plan-heading"><strong>{labels[p.status]}</strong><small>版本 {p.revision}{p.approved_revision!==null?` · 批准版本 ${p.approved_revision}`:''}</small></div>
     <p>{p.request}</p>{p.analysis&&<MarkdownText value={p.analysis}/>}
     {!!p.assumptions.length&&<div><strong>假设</strong><ul>{p.assumptions.map((v,i)=><li key={i}>{v}</li>)}</ul></div>}
     {!!p.questions.length&&<div><strong>确认前需要澄清</strong><ul>{p.questions.map((v,i)=><li key={i}>{v}</li>)}</ul></div>}
     {p.plan&&<><ol>{p.plan.steps.map((step,i)=>{const n=p.nodes.find(n=>n.node_id===step.node_id);return <li key={step.node_id}><strong>{n.goal}</strong>{p.reused_steps?.[n.node_id]&&<p>将复用已完成的保存交付，不新增模型调用。</p>}<p>{n.deliverable}</p><small>依赖：{step.depends_on.length?step.depends_on.map(id=>p.plan.steps.findIndex(s=>s.node_id===id)+1).join('、'):'无'} · 工具：{n.allowed_tools.map(tool=>toolNames[tool]||tool).join('、')||'无'} · 最多 {step.limits.max_calls} 次工具调用 / 模型回合：{step.limits.max_turns===null?'不限':step.limits.max_turns} / {step.limits.timeout_seconds} 秒</small><details><summary>步骤指令</summary><p>{n.instructions}</p></details></li>})}</ol>
       <p className="plan-budget">总预算：{p.plan.policy.max_runs} 次运行 · 并发 {p.plan.policy.max_concurrency} · {p.plan.policy.timeout_seconds} 秒{p.plan.policy.max_total_tokens?` · ${p.plan.policy.max_total_tokens} tokens`:' · token 用量由运行账本记录'}</p>
       <details><summary>固定输入与权限</summary><ul>{p.sources?.map(({name,ref})=><li key={ref.resource_id+ref.snapshot_id}>{name} · 快照 {ref.snapshot_id} · 解析 {ref.parse_id}</li>)}</ul><p>已有结果：{p.result_ids.join('、')||'无'}。步骤仅可使用上方列出的工具。</p></details></>}
     {p.planning_result&&<small>规划用量：输入 {p.planning_result.usage.input_tokens} / 输出 {p.planning_result.usage.output_tokens} tokens</small>}
     {p.reason&&<p role="status">{p.reason}</p>}
     {p.execution_steps?.length>0&&<div className="plan-results"><strong>步骤执行与交付</strong>{p.execution_steps.map(step=><details key={step.node_id}><summary>{p.nodes.find(n=>n.node_id===step.node_id)?.goal} · {step.reused?'复用已完成交付，未新增模型调用':step.completion==='partial'?'不完整交付，已阻止后续步骤':({waiting:'等待执行',running:'运行中',succeeded:'已交付，待审核',failed:'失败',cancelled:'已取消',interrupted:'已中断'})[step.status]||step.status}</summary>{step.error&&<p role="status">{errorLabels[step.error.code]||step.error.message}（{step.error.code}）</p>}{step.usage&&<small>{step.reused?"已保存运行用量":"执行用量"}：输入 {step.usage.input_tokens} / 输出 {step.usage.output_tokens} tokens{!step.usage.complete&&' · 用量尚未完全确认，需检查运行记录'}</small>}{step.missing?.length>0&&<p role="status">交付缺项：{step.missing.join("；")}</p>}{step.limitations?.length>0&&<p>研究局限：{step.limitations.join("；")}</p>}{step.report&&<MarkdownText value={step.report}/>}</details>)}</div>}
     {['awaiting_confirmation','generation_failed','interrupted','rejected'].includes(p.status)&&p.approved_revision===null&&<div className="plan-revise"><label htmlFor={`revise-${p.id}`}>修改要求或澄清回答</label><textarea id={`revise-${p.id}`} rows="2" value={feedback[p.id]||''} onChange={e=>setFeedback({...feedback,[p.id]:e.target.value})}/><Button size="small" disabled={busy||!feedback[p.id]?.trim()} onClick={()=>act(p,'revise')}>修改计划</Button></div>}
     <div className="plan-buttons">{p.approved_revision!==null&&['blocked','failed'].includes(p.status)&&<Button size="small" disabled={busy} onClick={()=>act(p,'recover')}>创建待确认恢复方案</Button>}{p.status==='awaiting_confirmation'&&<><Button size="small" type="primary" disabled={busy||p.questions.length>0} onClick={()=>act(p,'confirm')}>确认并执行</Button><Button size="small" disabled={busy} onClick={()=>act(p,'reject')}>拒绝计划</Button></>}{live.has(p.status)&&<Button size="small" disabled={busy} onClick={()=>act(p,'cancel')}>取消计划</Button>}</div>
     {p.recovery_of&&<small>恢复方案保留已完成交付；确认后仅执行未完成步骤，上轮记录保留。</small>}{p.approved_revision!==null&&<small>确认授权执行；结果仍需审核和采用。点击 Flow 中的步骤节点查看交付。</small>}
   </article>)}
 </section>;
}
