import {Button, Card} from 'animal-island-ui';
import React,{useMemo,useState} from 'react';
import {ReactFlow,Background,Handle,Position,MarkerType,useReactFlow} from '@xyflow/react';
import {Icon} from './icons';
import {nodeTitle,taskTitle,taskState,label,execution,kindLabel,collaborationState} from './model';
import Modal from './Modal';
import ReviewAdvice from './ReviewAdvice';

function FlowNode({data}){return <Card hoverable pattern={data.tone==='running'?'app-teal':'default'} className={`agent-node ${data.tone}`}><Handle type="target" position={Position.Left}/><span className="node-icon"><Icon name={data.icon} size={24}/></span><div className="flow-node-copy"><span className="flow-role">{data.role}</span><strong>{data.title}</strong><p>{data.subtitle}</p>{data.cooperation&&<small>{data.cooperation}</small>}</div><Handle type="source" position={Position.Right}/>{data.feedbackTarget&&<Handle id="feedback-in" type="target" position={Position.Bottom}/>} {data.feedbackSource&&<Handle id="feedback-out" type="source" position={Position.Bottom}/>}</Card>}
function FlowControls(){
 const {zoomIn,zoomOut,fitView}=useReactFlow();
 return <div className="flow-controls nodrag nopan"><Button size="small" aria-label="放大" onClick={()=>zoomIn()} icon={<Icon name="zoomIn"/>}/><Button size="small" aria-label="缩小" onClick={()=>zoomOut()} icon={<Icon name="zoomOut"/>}/><Button size="small" aria-label="适应画布" onClick={()=>fitView({padding:.2,maxZoom:1})} icon={<Icon name="expand"/>}/></div>;
}
const nodeTypes={item:FlowNode};
const positions=new Map();
export default function FlowCanvas({researchId,projection,activities,placements=[],onSelect}){
 const [large,setLarge]=useState(false),[selectedEdge,setSelectedEdge]=useState(null),[selectedRound,setSelectedRound]=useState(null);
 const graph=useMemo(()=>{
  const nodeMap=new Map(projection.nodes.map(p=>[p.node.id,p]));
  const depth=id=>{let n=nodeMap.get(id),d=0,seen=new Set([id]);while(n?.node.display_parent_id&&nodeMap.has(n.node.display_parent_id)&&!seen.has(n.node.display_parent_id)){d++;seen.add(n.node.display_parent_id);n=nodeMap.get(n.node.display_parent_id);}return d;};
  const rows=new Map(); const nodes=[]; const edges=[];
  for(const p of projection.nodes){const d=depth(p.node.id),y=(rows.get(d)||0)*140;rows.set(d,(rows.get(d)||0)+1);const key=`node:${p.node.id}`;nodes.push({id:key,type:'item',position:positions.get(`${researchId}:${key}`)||{x:d*310,y},data:{role:'协调 Agent',title:placements.find(value=>value.kind==='node'&&value.object_id===p.node.id)?.title||nodeTitle(p),subtitle:`节点 · ${label(execution,p.execution_status)} · ${p.node.selection==='excluded'?'已排除':'已选择'}`,icon:'bot',tone:p.execution_status==='running'?'running':p.execution_status==='failed'?'failed':'waiting'}});}
  for(const e of projection.display_edges){if(nodeMap.has(e.source)&&nodeMap.has(e.target))edges.push({id:`branch:${e.source}:${e.target}`,source:`node:${e.source}`,target:`node:${e.target}`,type:'smoothstep',label:'展示分支',style:{stroke:'var(--animal-text-color-secondary)',strokeDasharray:'5 4'}});}
  const taskMap=new Map(activities.map(a=>[a.spec.id,a]));
  const taskDepth=(a,seen=new Set())=>{const parent=taskMap.get(a.spec.parent_task_id);if(parent&&!seen.has(parent.spec.id))return taskDepth(parent,new Set([...seen,a.spec.id]))+1;const owner=a.spec.node_id&&nodeMap.get(a.spec.node_id);return owner?depth(owner.node.id)+1:0;};
  for(const a of activities){const owner=a.spec.node_id&&nodeMap.get(a.spec.node_id);const d=a.collaboration_role==='reviewer'&&a.automatic_review&&taskMap.has(a.automatic_review.task_id)?taskDepth(taskMap.get(a.automatic_review.task_id))+1:taskDepth(a);const y=(rows.get(d)||0)*140;rows.set(d,(rows.get(d)||0)+1);const key=`task:${a.spec.id}`;nodes.push({id:key,type:'item',position:positions.get(`${researchId}:${key}`)||{x:d*310,y},data:{feedbackTarget:a.collaboration_role!=='reviewer'&&a.automatic_review?.rounds.some(round=>round.revision_id),feedbackSource:a.collaboration_role==='reviewer'&&a.automatic_review?.rounds.some(round=>round.revision_id),role:a.collaboration_role==='reviewer'?'评审 Agent':`执行 Agent · ${kindLabel(a.spec.kind)}`,cooperation:collaborationState(a),title:placements.find(value=>value.kind==='task'&&value.object_id===a.spec.id)?.title||taskTitle(a),subtitle:a.collaboration_role==='reviewer'?`固定候选评审 · ${a.automatic_review?.rounds.length||0} 次记录`:`任务 v${a.spec.version} · ${taskState(a).execution}`,icon:'layers',tone:a.runs.at(-1)?.execution_status==='running'?'running':a.runs.at(-1)?.execution_status==='failed'?'failed':'waiting'}});
   if(a.spec.parent_task_id&&taskMap.has(a.spec.parent_task_id))edges.push({id:`parent:${a.spec.parent_task_id}:${a.spec.id}`,source:`task:${a.spec.parent_task_id}`,target:key,type:'smoothstep',label:'子任务',style:{stroke:'var(--animal-primary-color)'}});
   else if(owner)edges.push({id:`owner:${owner.node.id}:${a.spec.id}`,source:`node:${owner.node.id}`,target:key,type:'smoothstep',label:'归属',style:{stroke:'var(--animal-border-color-hover)',strokeDasharray:'2 4'}});
   for(const dependency of a.dependencies||[])if(taskMap.has(dependency.upstream_task_id))edges.push({id:`dependency:${dependency.id}`,source:`task:${dependency.upstream_task_id}`,target:key,type:'smoothstep',label:dependency.result_id?'结果依赖':'等待复核',style:{stroke:'var(--animal-text-color)',strokeWidth:2,strokeDasharray:dependency.result_id?undefined:'5 4'}});
   for(const message of a.messages||[])if(message.sender_task_id&&taskMap.has(message.sender_task_id))edges.push({id:`message:${message.id}`,source:`task:${message.sender_task_id}`,target:key,type:'smoothstep',label:'协作消息',style:{stroke:'var(--animal-success-color-active)',strokeDasharray:'4 3'}});
  }
  const nodeNames=new Map(nodes.map(node=>[node.id,node.data.title]));
  for(const edge of edges){
   const message=activities.flatMap(a=>a.messages||[]).find(m=>edge.id===`message:${m.id}`);
   const dependency=activities.flatMap(a=>a.dependencies||[]).find(d=>edge.id===`dependency:${d.id}`);
   edge.data={kind:message?'message':dependency?'dependency':'relation',message,dependency,
    sourceName:nodeNames.get(edge.source),targetName:nodeNames.get(edge.target)};
   edge.interactionWidth=24;
  }
  for(const a of activities){
   const cycle=a.automatic_review;
   if(!cycle||cycle.task_id!==a.spec.id||!taskMap.has(cycle.reviewer_task_id))continue;
   if(cycle.rounds.length)edges.push({id:`review:${cycle.task_id}`,source:`task:${cycle.task_id}`,target:`task:${cycle.reviewer_task_id}`,
    type:'smoothstep',label:`评审 · ${cycle.rounds.length} 个版本`,interactionWidth:24,
    style:{stroke:'var(--animal-primary-color)',strokeWidth:2},data:{kind:'review',rounds:cycle.rounds,cycle,
     sourceName:nodeNames.get(`task:${cycle.task_id}`),targetName:'评审 Agent'}});
   const feedback=cycle.rounds.filter(round=>round.revision_id);
   if(feedback.length)edges.push({id:`revision:${cycle.task_id}`,sourceHandle:'feedback-out',targetHandle:'feedback-in',source:`task:${cycle.reviewer_task_id}`,target:`task:${cycle.task_id}`,
    type:'smoothstep',label:`修改反馈 · ${feedback.length} 条`,interactionWidth:24,
    style:{stroke:'var(--animal-success-color-active)',strokeDasharray:'5 4'},data:{kind:'revision',rounds:feedback,cycle,
     sourceName:'评审 Agent',targetName:nodeNames.get(`task:${cycle.task_id}`)}});
  }
  for(const edge of edges)edge.markerEnd={type:MarkerType.ArrowClosed};
  return {nodes,edges};
 },[researchId,projection,activities,placements]);
  const edge=graph.edges.find(value=>value.id===selectedEdge);
  const round=edge?.data.rounds?.find(value=>value.spec_version===selectedRound)||edge?.data.rounds?.at(-1);
  return <div className={`canvas ${large?'large':''}`}><ReactFlow key={researchId} nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} onNodeDragStop={(_,node)=>positions.set(`${researchId}:${node.id}`,node.position)} onNodeClick={(_,node)=>{const [kind,id]=node.id.split(':');onSelect({kind,id});}} onEdgeClick={(_,edge)=>{setSelectedEdge(edge.id);setSelectedRound(edge.data.rounds?.at(-1)?.spec_version)}} nodesConnectable={false} nodesDraggable fitView fitViewOptions={{padding:.2,maxZoom:1}} minZoom={.3} maxZoom={1.6} deleteKeyCode={null}><Background gap={22} size={1.3} color="var(--animal-border-color)"/><FlowControls/></ReactFlow><span className="canvas-hint">点击节点查看任务，点击连线查看交接 · 评审与反馈绑定结果版本 · 可平移缩放</span><Button size="small" className="icon-button expand-canvas" onClick={()=>setLarge(!large)} aria-label={large?'缩小画布':'放大画布'}><Icon name="expand"/></Button>{edge&&<Modal title={edge.label||'协作交接'} onClose={()=>setSelectedEdge(null)}>
  <p>{edge.data.sourceName} → {edge.data.targetName}</p>
  {round?<><label>查看交接版本 <select value={round.spec_version} onChange={event=>setSelectedRound(Number(event.target.value))}>{edge.data.rounds.map(value=><option key={value.spec_version} value={value.spec_version}>执行任务 v{value.spec_version}</option>)}</select></label><p>针对执行任务 v{round.spec_version} 的固定结果</p><ReviewAdvice key={round.result_id} advice={round.advice} taskId={edge.data.cycle.task_id} round={round}/><small>Agent 评审建议，不代表人工批准或采用。</small></>:edge.data.message?<><p>{edge.data.message.content}</p><small>消息状态：{edge.data.message.status}{edge.data.message.reply_to?' · 已关联回复':''}</small></>:edge.data.dependency?<><p>{edge.data.dependency.result_id?'使用固定结果版本':'等待上游结果复核'}</p><p>上游任务 v{edge.data.dependency.upstream_version} → 下游任务 v{edge.data.dependency.downstream_version}</p></>:<p>{edge.label==='展示分支'?'分支关系用于组织讨论，不表示执行依赖。':'任务的派发与归属关系。'}</p>}
  </Modal>}</div>
}
