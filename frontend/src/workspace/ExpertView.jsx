import {Select, Input, Button} from 'animal-island-ui';
import React,{useEffect,useRef,useState} from 'react';
import {read,write} from './api';
import {Icon} from './icons';
import {execution,delivery,adoption,label,kindLabel,taskTitle,taskState,taskRequest} from './model';
import {TaskControls} from './Actions';
import MarkdownText from './MarkdownText';
import Modal from './Modal';
import TaskArtifact from './TaskArtifact';
import AutomaticReviewPanel from './AutomaticReviewPanel';
import ExpertContext from './ExpertContext';
import ExpertRun from './ExpertRun';

function RerunForm({spec,onDone}){
  const [request,setRequest]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const pending=useRef(null);
  async function submit(event){
    event.preventDefault();
    if(busy||!request.trim())return;
    const mode=event.nativeEvent.submitter?.value||'after_current';
    setBusy(true);setError('');
    try{
      if(!pending.current||pending.current.request!==request.trim()||pending.current.mode!==mode)pending.current={
        command_id:crypto.randomUUID(),task_id:spec.id,expected_version:spec.version,
        request:request.trim(),mode};
      await write('expert_revise',pending.current);
      pending.current=null;
      onDone();
    }catch(error){setError(error.message)}finally{setBusy(false)}
  }
  return <form className="action-form" onSubmit={submit}>
    <p>写出完整的新任务要求。旧运行与结果会保留；可保存、排队，或请求停止旧运行后重做。</p>
    <label htmlFor="expert-requirements">新的任务要求</label>
    <textarea id="expert-requirements" autoFocus rows="5" value={request} onChange={event=>setRequest(event.target.value)} placeholder="补充要求及重做目标"/>
    <Button size="small" htmlType="submit" value="save" disabled={busy||!request.trim()}>仅保存新版本</Button>
    <Button size="small" type="primary" htmlType="submit" value="after_current" disabled={busy||!request.trim()}>{busy?'处理中…':'保存并执行'}</Button>
    <Button size="small" danger htmlType="submit" value="replace" disabled={busy||!request.trim()}>停止旧任务并重做</Button>
    {error&&<p className="error" role="alert">{error}</p>}
  </form>;
}

function ReviewForm({spec,receipt,onDone}){
  const [reviewer,setReviewer]=useState(''),[findings,setFindings]=useState({});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event){
    event.preventDefault();
    if(busy)return;
    setBusy(true);setError('');
    try{
      await write('task_review',{result_id:receipt.result_id,reviewer:reviewer.trim(),findings});
      onDone();
    }catch(cause){setError(cause.message)}finally{setBusy(false)}
  }
  return <form className="action-form" onSubmit={submit}>
    <p>请独立核对原始资料和每项交付条件，逐项记录判断与依据。</p>
    <label htmlFor={`reviewer-${receipt.result_id}`}>复核人</label>
    <Input id={`reviewer-${receipt.result_id}`} value={reviewer} onChange={event=>setReviewer(event.target.value)} required/>
    {spec.acceptance.map(item=><fieldset key={item.number}>
      <legend>{item.number}. {item.requirement}</legend>
      <label>判断<Select aria-label={`第 ${item.number} 项判断`} value={findings[item.number]?.status||''} onChange={status=>setFindings(current=>({...current,[item.number]:{...current[item.number],status}}))} options={[{key:'passed',label:'通过'},{key:'failed',label:'未通过'}]}/></label>
      <label>核对依据<textarea value={findings[item.number]?.evidence||''} onChange={event=>setFindings(current=>({...current,[item.number]:{...current[item.number],evidence:event.target.value}}))} required rows="2"/></label>
    </fieldset>)}
    <Button size="small" type="primary" htmlType="submit" disabled={busy||!reviewer.trim()||spec.acceptance.some(item=>!findings[item.number]?.status||!findings[item.number]?.evidence?.trim())}>{busy?'提交中…':'提交独立复核'}</Button>
    {error&&<p className="error" role="alert">{error}</p>}
  </form>;
}

function TaskDetails({activity,onChanged}){
  const {spec,runs}=activity;
  const [reviewing,setReviewing]=useState(null),[busy,setBusy]=useState(false),[actionError,setActionError]=useState('');
  async function adopt(resultId){
    if(busy)return;
    setBusy(true);setActionError('');
    try{await write('task_adopt',{result_id:resultId});onChanged()}
    catch(cause){setActionError(cause.message)}finally{setBusy(false)}
  }
  return <div className="expert-task-details">
    <p>{taskRequest(activity)}</p>
    <div className="task-state-strip">{[['执行',taskState(activity).execution],['交付',taskState(activity).delivery],['采用',taskState(activity).adoption]].map(([title,value])=><div key={title}><small>{title}</small><strong>{value}</strong></div>)}</div>
    {activity.collaboration_role==='reviewer'?<p>此评审 Agent 随执行任务自动工作。</p>:<TaskControls activity={activity} onChanged={onChanged}/>}
    {!!activity.information_requests?.length&&<section aria-label="向主助手索要信息">
      <h3>向主助手索要信息</h3>
      {activity.information_requests.map(item=><article key={item.id}>
        <p>任务单 v{item.spec_version} · {item.status==='waiting'?(['running','interrupted'].includes(runs.find(run=>run.binding.id===item.run_id)?.execution_status)?'等待主助手答复':'未取得答复'):item.answer?.available?'已授权补充信息':'依据已有信息继续'}</p>
        <p>{item.question}</p>
        {item.answer&&<><MarkdownText value={item.answer.answer}/>{!!item.answer.missing.length&&<p>仍缺少：{item.answer.missing.join('；')}</p>}</>}
      </article>)}
    </section>}
    <h3>结果与运行记录</h3>
    {runs.length===0?<p className="muted">{activity.collaboration_role==='reviewer'?'评审建议和版本记录见自动协作进展。':'尚未派发执行。'}</p>:runs.map(run=><article className="task-run" key={run.binding.id}>
      <div className="task-run-head"><strong>任务单 v{run.binding.spec_version} · {run.execution_phase?.phase==='finalizing'?'正在收尾':label(execution,run.execution_status)}</strong></div>
      <p>{label(delivery,run.receipt?.delivery,'未交付')} · {label(adoption,run.receipt?.adoption,'未采用')}</p>
      {run.access==='revoked'?<p className="error">访问已撤回，回执和内容不可见。</p>:<>
        {run.error&&<p className="error">{typeof run.error==='string'?run.error:run.error.message}</p>}
        {run.artifact&&<><MarkdownText value={run.artifact.brief}/>{run.artifact.brief_truncated&&<p className="muted">以上为节选，完整报告已保存。</p>}<TaskArtifact key={run.artifact.result_id} resultId={run.artifact.result_id} expandable/></>}
        {!!run.receipt?.missing?.length&&<p>缺失：{run.receipt.missing.join('；')}</p>}
        {run.binding.spec_version===spec.version&&['pending_review','partial_pending_review'].includes(run.receipt?.delivery)&&<Button size="small" className="subtle-button" onClick={()=>setReviewing(run.receipt.result_id)}>独立复核此结果</Button>}
        {run.binding.spec_version===spec.version&&run.receipt?.delivery==='passed'&&run.receipt?.adoption!=='adopted'&&<Button size="small" className="subtle-button" disabled={busy} onClick={()=>adopt(run.receipt.result_id)}>正式采用已复核结果</Button>}
        {reviewing===run.receipt?.result_id&&<ReviewForm spec={spec} receipt={run.receipt} onDone={()=>{setReviewing(null);onChanged()}}/>}
      </>}
      <details><summary>技术诊断</summary><pre>{JSON.stringify({run_id:run.binding.id,receipt:run.receipt},null,2)}</pre></details>
    </article>)}
    {actionError&&<p className="error" role="alert">{actionError}</p>}
    <details><summary>任务详细信息</summary><p>任务单 v{spec.version} · 模型 {spec.model} · 方法 {spec.method_version}</p><p>固定资料 {spec.required_refs.length} 份 · 输入结果 {spec.required_results.length} 个</p>{spec.acceptance.length>0&&<ol>{spec.acceptance.map(item=><li key={item.number}>{item.requirement}</li>)}</ol>}</details>
  </div>;
}

export default function ExpertView({displayTitle,activity,refreshKey,onChanged,onBack,onDispatch}){
  const spec=activity.spec;
  const currentRun=activity.runs.filter(run=>run.binding.spec_version===spec.version).at(-1);
  const draftKey=`expert-draft:${spec.id}`;
  const [thread,setThread]=useState({status:'loading'}),[draft,setDraft]=useState(()=>localStorage.getItem(draftKey)||'');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0),[modal,setModal]=useState(null);
  const mounted=useRef(true),pending=useRef(null),scroll=useRef(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  useEffect(()=>{
    const controller=new AbortController();
    setThread(previous=>previous.status==='ready'?previous:{status:'loading'});
    read('expert_thread',{task_id:spec.id},controller.signal)
      .then(data=>{if(!controller.signal.aborted)setThread({status:'ready',data})})
      .catch(error=>{if(!controller.signal.aborted)setThread({status:'error',error:error.message})});
    return()=>controller.abort();
  },[spec.id,spec.version,reload,refreshKey]);
  const messages=thread.status==='ready'?thread.data.messages:[];
  useEffect(()=>{if(scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight},[messages.length,busy]);
  async function send(event){
    event.preventDefault();
    if(busy||thread.status!=='ready'||!draft.trim())return;
    const content=draft.trim();
    // Keep the same request identity after an ambiguous response so retry cannot duplicate a turn.
    if(!pending.current||pending.current.content!==content)pending.current={task_id:spec.id,content,expected_revision:thread.data.revision,message_id:crypto.randomUUID()};
    setBusy(true);setError('');
    try{
      const data=await write('expert_discuss',pending.current);
      if(mounted.current){pending.current=null;setThread({status:'ready',data});setDraft('');localStorage.removeItem(draftKey)}
    }catch(error){if(mounted.current)setError(error.message)}
    finally{if(mounted.current)setBusy(false)}
  }
  return <section className="chat-view expert-view" aria-label="专家对话">
    <div className="view-heading">
      <Button size="small" className="back-link" onClick={onBack}><Icon name="back" size={16}/>返回 Flow</Button>
      <div className="heading-actions">
        {activity.collaboration_role!=='reviewer'&&<Button size="small" className="subtle-button" onClick={()=>setModal('runs')}>报告与运行</Button>}
        <Button size="small" className="subtle-button" onClick={()=>setModal('context')}>上下文</Button>
        <Button size="small" className="subtle-button" onClick={()=>setModal('details')}>任务与执行</Button>
        <details className="expert-more"><summary className="subtle-button">更多</summary><div className="expert-more-menu">
          <Button size="small" disabled={busy||activity.collaboration_role==='reviewer'} onClick={event=>{event.currentTarget.closest('details').open=false;setModal('requirements')}}>补充任务要求</Button>
          <Button size="small" onClick={event=>{event.currentTarget.closest('details').open=false;onDispatch()}}>派发子专家</Button>
        </div></details>
      </div>
    </div>
    <div className="chat-title"><span className="eyebrow">{activity.collaboration_role==='reviewer'?'评审 Agent':'执行 Agent'} · {kindLabel(spec.kind)}</span><h1>{displayTitle||taskTitle(activity)}</h1><p>分析任务：{taskState(activity).execution} · 固定资料 {spec.required_refs.length} 份</p></div>
    <div className="chat-scroll" ref={scroll} role="log" aria-label="专家聊天记录" aria-live="polite">
      <div className="expert-assignment"><small>任务目标</small><p>{taskRequest(activity)}</p></div><AutomaticReviewPanel key={spec.id} activity={activity}/>
      {thread.status==='loading'?<p className="muted">正在读取对话…</p>:thread.status==='error'?<div role="alert"><p className="error">{thread.error}</p><Button size="small" className="retry" onClick={()=>setReload(value=>value+1)}>重新读取对话</Button></div>:<>
        {currentRun?.access==='available'&&currentRun.artifact&&<article className="message assistant"><div className="message-avatar"><Icon name="bot" size={16}/></div><div className="message-content"><small>执行产物 · 任务单 v{currentRun.binding.spec_version}</small><div className="message-body"><p>{activity.automatic_review?'执行产物已保存':label(delivery,currentRun.receipt?.delivery)} · {label(adoption,currentRun.receipt?.adoption)}</p><MarkdownText value={currentRun.artifact.brief}/>{currentRun.artifact.brief_truncated&&<p className="muted">以上为节选，完整报告已保存。</p>}<TaskArtifact key={currentRun.artifact.result_id} resultId={currentRun.artifact.result_id} expandable/></div></div></article>}
        {messages.length===0&&<div className="expert-welcome"><Icon name="bot" size={26}/><h2>与这位专家开始对话</h2><p>{currentRun?.execution_status==='failed'?'最近一次执行失败，尚无有效分析结果。可先讨论分析思路，或在“任务与执行”中查看失败记录。':currentRun?.artifact?'可以追问结论、依据和局限。':'可以先讨论分析思路、资料范围或需要关注的风险。'}</p></div>}
        {messages.map(message=><article className={`message ${message.role}`} key={message.id}><div className="message-avatar">{message.sender==='manager'?'助':message.sender_session_id?'协':message.role==='user'?'你':<Icon name="bot" size={16}/>}</div><div className="message-content"><small>{message.sender==='manager'?'主助手':message.sender_session_id?'协作专家':message.role==='user'?'你':'专家'}</small><div className="message-body"><MarkdownText value={message.content}/></div></div></article>)}
      </>}
      {busy&&<p className="expert-reply-status" role="status">专家正在回复…</p>}
    </div>
    <div className="chat-compose">
      <form className="action-form reply-compose-form" onSubmit={send}>
        <label htmlFor={`expert-draft-${spec.id}`}>与这位专家对话</label>
        <textarea id={`expert-draft-${spec.id}`} value={draft} disabled={busy} onChange={event=>{setDraft(event.target.value);localStorage.setItem(draftKey,event.target.value)}} rows="3" placeholder="追问结论、讨论风险，或补充你的想法…"/>
        <Button size="small" type="primary" htmlType="submit" disabled={busy||thread.status!=='ready'||!draft.trim()}>{busy?'回复中…':'发送'}</Button>
      </form>
      {error&&<p className="error" role="alert">{error}</p>}
      <p className="expert-chat-note">对话会保留在此专家中；更新分析任务请使用“补充任务要求”。</p>
    </div>
    {modal==='context'&&<Modal title="子 Agent 上下文" width="min(1000px, calc(100vw - 24px))" onClose={()=>setModal(null)}><ExpertContext key={spec.id} taskId={spec.id}/></Modal>}
    {modal==='runs'&&<Modal title="报告版本与运行详情" width="min(1100px, calc(100vw - 24px))" onClose={()=>setModal(null)}><ExpertRun key={spec.id} activity={activity}/></Modal>}
    {modal==='details'&&<Modal title="任务与执行" onClose={()=>setModal(null)}><TaskDetails activity={activity} onChanged={onChanged}/></Modal>}
    {modal==='requirements'&&<Modal title="补充任务要求" onClose={()=>setModal(null)}><RerunForm spec={spec} onDone={()=>{setModal(null);onChanged()}}/></Modal>}
  </section>;
}
