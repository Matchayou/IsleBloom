import {useEffect,useRef,useState} from 'react';
import {read,write} from './api';

const pendingKey=nodeId=>`workspace-reply:${nodeId}`;
const draftKey=nodeId=>`workspace-draft:${nodeId}`;
const stored=key=>{try{return JSON.parse(localStorage.getItem(key))}catch{return null}};
const terminal=new Set(['succeeded','failed','cancelled','interrupted','blocked','save_failed']);
const phaseText={thinking:'正在思考，等待回复正文…',researching:'正在读取资料与执行研究…',writing:'正在生成回复…'};

export default function useChatReply({researchId,nodeId,chat,onChanged,onPreview}){
 const key=pendingKey(nodeId);
 const [draft,setDraftState]=useState(()=>localStorage.getItem(draftKey(nodeId))||'');
 const [pending,setPendingState]=useState(()=>stored(key));
 const operation=useRef(pending),lock=useRef(false);
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState({message:'',error:false});
 const callbacks=useRef({onChanged,onPreview});callbacks.current={onChanged,onPreview};
 function save(op){operation.current=op;if(op)localStorage.setItem(key,JSON.stringify(op));else localStorage.removeItem(key);setPendingState(op)}
 function setDraft(value){setDraftState(value);localStorage.setItem(draftKey(nodeId),value)}
 function changed(){callbacks.current.onChanged(researchId,nodeId)}
 function complete(){save(null);callbacks.current.onPreview(null);setNotice({message:'回复已完成',error:false});changed()}

 // A refresh reconnects to the admitted command; it never submits a new model run.
 useEffect(()=>{let active=true;const op=operation.current;if(!op)return;
  (async()=>{try{
   const value=await read('chat_read',{node_id:nodeId});if(!active)return;
   const at=value.chat.messages.findIndex(m=>m.id===op.message_id);
   if(at<0){setNotice({message:'消息保存状态待确认，请重试保存。',error:true});return}
   if(value.chat.messages.slice(at+1).some(m=>m.role==='assistant')){complete();return}
   const projection=await read('projection',{research_id:researchId});if(!active)return;
   const record=projection.commands.find(c=>c.command.command_id===op.command_id);
   save({...op,revision:record?record.command.chat_revision:value.chat.revision,phase:record?'running':'saved'});
   if(!record)setNotice({message:'消息已保存，请点击重试启动回复。',error:true});
  }catch(err){if(active)setNotice({message:err.message,error:true})}})();
  return()=>{active=false};
 },[nodeId]);

 useEffect(()=>{
  if(pending?.phase!=='running')return;
  const commandId=pending.command_id;
  const stream=new EventSource(`/api/chat_stream?${new URLSearchParams({node_id:nodeId,command_id:commandId})}`);
  let active=true;
  stream.onmessage=event=>{
   if(!active||operation.current?.command_id!==commandId)return;
   const value=JSON.parse(event.data);
   if(value.status==='succeeded'){stream.close();complete();return}
   if(value.text)callbacks.current.onPreview({...value,commandId});
   else if(!terminal.has(value.status))callbacks.current.onPreview(null);
   if(terminal.has(value.status)){
    stream.close();save({...operation.current,phase:'reply_failed',execution_status:value.status});
    const message=value.reason==='timeout'?`回复超过 ${Math.round(value.timeout_seconds/60)} 分钟时限，已停止；消息已保存，可重试。`:value.status==='save_failed'?'回复保存失败，请恢复保存，避免重复执行。':`回复${({failed:'失败',cancelled:'已取消',interrupted:'已中断',blocked:'未能启动'})[value.status]||'未完成'}${value.reason?`（${value.reason}）`:''}；消息已保存。`;
    callbacks.current.onPreview(current=>current?{...current,status:value.status}:null);
    setNotice({message,error:true});changed();
   }else setNotice({message:phaseText[value.phase]||'回复执行中…',error:false});
  };
  stream.addEventListener('revoked',()=>{if(!active)return;stream.close();callbacks.current.onPreview(null);save({...operation.current,phase:'access_failed'});setNotice({message:'对话或资料授权已改变，请刷新后检查。',error:true})});
  stream.onerror=()=>{if(active){callbacks.current.onPreview(current=>current?{...current,phase:'reconnecting'}:null);setNotice({message:stream.readyState===EventSource.CLOSED?'无法连接回复，请检查网络后重试连接。':'连接中断，正在重新连接回复…',error:true});if(stream.readyState===EventSource.CLOSED)save({...operation.current,phase:'reply_unknown'})}};
  return()=>{active=false;stream.close()};
 },[pending?.command_id,pending?.phase,nodeId]);

 async function startReply(op){
  // Persist the exact revision before transport so ambiguous failures can be redelivered safely.
  save({...op,phase:'saved'});
  try{await write('chat_reply',{node_id:nodeId,expected_revision:op.revision,command_id:op.command_id});save({...op,phase:'running'});setNotice({message:'正在思考，等待回复正文…',error:false});changed()}
  catch(err){save({...op,phase:'reply_unknown'});setNotice({message:`消息已保存，回复状态待确认：${err.message}`,error:true});changed()}
 }
 async function saveMessage(op){
  try{
   const value=await write('chat_send',{node_id:nodeId,content:op.content,expected_revision:op.revision,message_id:op.message_id});
   setDraft('');localStorage.removeItem(draftKey(nodeId));changed();await startReply({...op,revision:value.revision});
  }catch(err){setNotice({message:`消息保存状态待确认：${err.message}。请重试核对，消息不会重复保存。`,error:true})}
 }
 async function send(e){e.preventDefault();if(lock.current||operation.current||!draft.trim()||chat.status!=='ready')return;
  lock.current=true;setBusy(true);setNotice({message:'正在保存消息…',error:false});callbacks.current.onPreview(null);
  const op={message_id:crypto.randomUUID(),command_id:crypto.randomUUID(),content:draft.trim(),revision:chat.data.chat.revision,phase:'sending'};save(op);
  try{await saveMessage(op)}finally{lock.current=false;setBusy(false)}
 }
 async function retry(){
  const op=operation.current;if(!op||lock.current)return;lock.current=true;setBusy(true);
  try{
   const value=await read('chat_read',{node_id:nodeId});
   const at=value.chat.messages.findIndex(m=>m.id===op.message_id);
   if(at<0){await saveMessage(op);return}
   if(value.chat.messages.slice(at+1).some(m=>m.role==='assistant')){complete();return}
   if(value.chat.messages.at(-1)?.id!==op.message_id){setNotice({message:'聊天已有更新，请刷新后检查。',error:true});return}
   const projection=await read('projection',{research_id:researchId});
   const record=projection.commands.find(c=>c.command.command_id===op.command_id);
   const node=projection.nodes.find(n=>n.node.id===nodeId);
   if(record?.status==='accepted'){
    if(node?.execution_status==='save_failed'){setNotice({message:'回复保存失败，需要恢复保存；不会重新执行模型。',error:true});return}
    save({...op,revision:record.command.chat_revision,phase:'running'});return;
   }
   // Only a confirmed terminal failure starts a new command. Unknown delivery reuses its ID.
   const next={...op,command_id:record?crypto.randomUUID():op.command_id,revision:value.chat.revision};
   callbacks.current.onPreview(null);await startReply(next);
  }catch(err){setNotice({message:err.message,error:true})}finally{lock.current=false;setBusy(false)}
 }
 const canRetry=pending&&pending.phase!=='running'&&pending.phase!=='access_failed'&&pending.execution_status!=='save_failed';
 return {draft,setDraft,busy,pending,send,retry:canRetry?retry:null,...notice};
}
