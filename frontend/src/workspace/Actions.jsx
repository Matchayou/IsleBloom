import {Select, Checkbox, Input, Button} from 'animal-island-ui';
import Modal from './Modal';
import {Icon} from './icons';
import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {read,write,refKey} from './api';
import {kindLabel} from './model';
import useChatReply from './useChatReply';
import PlanPanel from './PlanPanel';

const id=()=>crypto.randomUUID();
function Notice({message,error,onRetry}){return message&&<div className={error?'action-error':'action-notice'} role="status">{message} {onRetry&&<Button size="small" htmlType="button" onClick={onRetry}>重试</Button>}</div>}

export function UploadAction({researchId,onUploaded}){
 const [file,setFile]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function upload(e){e.preventDefault();if(!file||busy)return;if(file.size>25_000_000){setError('文件超过后端 25 MB 限制');return}setBusy(true);setError('');const target=researchId;
  try{const result=await write('upload',file,{research_id:target,name:file.name});setFile(null);e.target.reset();onUploaded(target,result);}
  catch(err){setError(err.message)}finally{setBusy(false)}
 }
 return <form className="action-form upload-form" onSubmit={upload}><label htmlFor="upload-file">上传到当前研究</label><input hidden id="upload-file" type="file" onChange={e=>setFile(e.target.files?.[0]||null)}/><Button type="dashed" icon={<Icon name="file"/>} onClick={()=>document.getElementById("upload-file").click()}>{file?file.name:"选择文件"}</Button><Button size="small" type="primary" htmlType="submit" disabled={!file||busy}>{busy?'上传与解析中…':'上传资料'}</Button><small>真实文件字节 · 最大 25 MB · 仅授予当前研究访问</small><Notice message={error} error/></form>
}

export function ChatActions({researchId,nodeId,chat,onChanged,onBranched,onReplyPreview,branchOpen=false,onBranchClose}){
 const reply=useChatReply({researchId,nodeId,chat,onChanged,onPreview:onReplyPreview});
 const {draft,setDraft,busy,pending,send}=reply;
 const [mode,setMode]=useState('execute');
 const inputRef=useRef(null);
 useLayoutEffect(()=>{const input=inputRef.current;if(input){input.style.height='auto';input.style.height=`${Math.min(input.scrollHeight,120)}px`}},[draft]);
 const [message,setMessage]=useState(''),[error,setError]=useState(false);
 const [forkId,setForkId]=useState(''),[forkGoal,setForkGoal]=useState(''),[forkBusy,setForkBusy]=useState(false);
 async function branch(e){e.preventDefault();if(forkBusy||!forkGoal.trim()||!forkId||chat.status!=='ready')return;setForkBusy(true);setMessage('');const command_id=id();try{const next=await write('chat_branch',{node_id:nodeId,fork_message_id:forkId,goal:forkGoal.trim(),expected_revision:chat.data.chat.revision,command_id});setForkId('');setForkGoal('');onBranched(researchId,next.node_id,nodeId)}catch(err){setMessage(`创建分支失败：${err.message}`);setError(true)}finally{setForkBusy(false)}}
 return <div className="chat-actions"><div className="reply-mode" aria-label="回复模式"><Button size="small" aria-pressed={mode==='execute'} disabled={busy||!!pending} onClick={()=>setMode('execute')}>执行</Button><Button size="small" aria-pressed={mode==='plan'} disabled={busy||!!pending} onClick={()=>setMode('plan')}>计划</Button></div>{mode==='execute'&&<form className="action-form reply-compose-form" onSubmit={send}><label htmlFor={`chat-draft-${nodeId}`}>发送到此节点</label><textarea ref={inputRef} id={`chat-draft-${nodeId}`} value={draft} onChange={e=>{setDraft(e.target.value)}} placeholder="输入研究问题" rows="1"/><Button size="small" type="primary" htmlType="submit" aria-label="发送并执行回复" disabled={busy||!!pending||chat.status!=='ready'||!draft.trim()}>{busy?'发送中…':'发送'}</Button></form>}<PlanPanel key={`${researchId}:${nodeId}`} researchId={researchId} nodeId={nodeId} chat={chat} mode={mode} draft={draft} setDraft={setDraft} onChanged={onChanged}/><Notice message={reply.message} error={reply.error} onRetry={busy?null:reply.retry}/><Notice message={message} error={error}/>{branchOpen&&<Modal title="从消息创建分支" onClose={onBranchClose}><form className="action-form branch-form" onSubmit={branch}><label>分支截止消息</label><Select aria-label="分支截止消息" value={forkId} onChange={setForkId} placeholder="选择截止消息" options={chat.status==='ready'?[...chat.data.chat.inherited,...chat.data.chat.messages].map(m=>({key:m.id,label:`${m.role} · ${m.content.slice(0,60)}`})):[]}/><label>分支目标</label><Input aria-label="分支目标" value={forkGoal} onChange={e=>setForkGoal(e.target.value)} placeholder="新分支目标"/><small>继承所选消息及之前的固定快照。</small><Button size="small" type="primary" htmlType="submit" disabled={forkBusy||!forkId||!forkGoal.trim()}>{forkBusy?'创建中…':'创建分支'}</Button></form></Modal>}</div>
}

export function TaskCreate({researchId,nodeId,parentTaskId,selectionId,resources,activities,onCreated}){
 const [kind,setKind]=useState('synthesize'),[request,setRequest]=useState(''),[refs,setRefs]=useState([]),[results,setResults]=useState([]),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(false);
 const available=activities.flatMap(a=>a.runs.filter(r=>r.receipt&&r.receipt.delivery==='passed'&&r.access==='available').map(r=>({id:r.receipt.result_id,title:`${kindLabel(a.spec.kind)} · ${a.spec.goal.slice(0,50)}`})));
 useEffect(()=>{setRefs([]);setResults([]);setMessage('')},[researchId,nodeId]);
 async function create(e){e.preventDefault();if(busy||!request.trim()||(!refs.length&&!results.length))return;setBusy(true);setMessage('');const task_id=id();const body={research_id:researchId,node_id:nodeId||undefined,parent_task_id:parentTaskId||undefined,kind,request:request.trim(),required_refs:resources.filter(r=>refs.includes(refKey(r.ref))).map(r=>r.ref),required_results:results,task_id};try{const spec=await write('task_create',body);setMessage('任务单已创建，正在派发执行…');try{await write('task_run',{task_id:spec.id});setMessage('任务已派发。执行、交付复核与采用状态将从后端刷新。');setError(false)}catch(err){setMessage(`任务单已创建，但执行未确认：${err.message}。请检查任务状态，避免重复收费执行。`);setError(true)}onCreated(researchId,spec.id,selectionId);setRequest('');setRefs([]);setResults([])}catch(err){setMessage(`任务创建失败：${err.message}。请刷新任务列表确认后再提交。`);setError(true)}finally{setBusy(false)}}
 return <form className="action-form task-form" onSubmit={create}><small>{nodeId?'关联当前对话':'研究级任务'}</small><label>任务种类</label><Select aria-label="任务种类" value={kind} onChange={setKind} options={['search','extract','analyze','synthesize'].map(x=>({key:x,label:kindLabel(x)}))}/><label htmlFor="task-request">任务要求</label><textarea id="task-request" rows="3" value={request} onChange={e=>setRequest(e.target.value)} required/><fieldset><legend>必需资料</legend>{resources.length===0?<small>当前研究无资料</small>:<Checkbox direction="vertical" size="small" value={refs} onChange={setRefs} options={resources.map(r=>({value:refKey(r.ref),label:`${r.resource.name} · ${r.parse.status==='ready'?'已解析':'解析中'}`}))}/>}</fieldset><fieldset><legend>必需专家结果</legend>{available.length===0?<small>暂无通过交付复核的结果</small>:<Checkbox direction="vertical" size="small" value={results} onChange={setResults} options={available.map(r=>({value:r.id,label:r.title}))}/>}</fieldset><small>至少选择一份资料或一个已交付结果。模型使用服务端配置；提交后执行真实任务，可能产生模型费用。</small><Button size="small" type="primary" htmlType="submit" disabled={busy||!request.trim()||(!refs.length&&!results.length)}>{busy?'创建与派发中…':'创建并派发'}</Button><Notice message={message} error={error}/></form>
}

export function TaskControls({activity,onChanged}){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(false);
 const running=['ENQUEUED','PENDING','RUNNING'].includes(activity.job_status)||['executing','reviewing','revising'].includes(activity.automatic_review?.status);
 const canStart=!activity.spec.pending_dependency_task_ids?.length&&!activity.runs.some(r=>r.binding.spec_version===activity.spec.version)&&!running;
 const failedSave=activity.runs.find(r=>r.execution_status==='save_failed');
 async function act(action,body){if(busy)return;setBusy(true);setMessage('');try{await write(action,body);setMessage(action==='task_stop'?'已请求停止；请等待后端状态更新。':'请求已提交；请等待后端状态更新。');setError(false);onChanged()}catch(err){setMessage(`${err.message}。请先刷新状态，再决定是否重试。`);setError(true);onChanged()}finally{setBusy(false)}}
 return <div className="task-controls">{canStart&&<Button size="small" disabled={busy} onClick={()=>act('task_run',{task_id:activity.spec.id})}>派发此任务单</Button>}{running&&<Button size="small" disabled={busy} onClick={()=>act('task_stop',{task_id:activity.spec.id,expected_version:activity.spec.version,command_id:`user-stop:${activity.spec.id}:v${activity.spec.version}`,reason:'用户请求停止'})}>请求停止</Button>}{failedSave&&<Button size="small" disabled={busy} onClick={()=>act('task_retry_save',{run_id:failedSave.binding.id})}>重试保存</Button>}<Notice message={message} error={error}/></div>
}
