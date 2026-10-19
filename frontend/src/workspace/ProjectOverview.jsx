import {Button, Card, Title} from 'animal-island-ui';
import React,{useEffect,useState} from 'react';
import FlowCanvas from './FlowCanvas';
import RunAnalysis from './RunAnalysis';
import {read} from './api';
import {Icon} from './icons';

const running=value=>['running','queued','saving','ENQUEUED','PENDING','RUNNING'].includes(value);
function status(bundle){
  if(bundle?.status!=='ready')return 'idle';
  const {projection,activities}=bundle;
  if(projection.nodes.some(p=>running(p.execution_status))||activities.some(a=>running(a.job_status)||a.runs.some(r=>running(r.execution_status))||['executing','reviewing','revising'].includes(a.automatic_review?.status)))return 'running';
  const outcomes=[...projection.nodes.map(p=>p.execution_status),...activities.map(a=>a.collaboration_role==='reviewer'?(['passed','exhausted'].includes(a.automatic_review?.status)?'succeeded':'idle'):a.runs.at(-1)?.execution_status)];
  return outcomes.length&&outcomes.every(value=>value==='succeeded')?'done':'idle';
}

export default function ProjectOverview({project,contents,expanded,setExpanded,refreshKey,onFocus,onSelect,onCreate,onArrange}){
  const members=contents.tasks;
  const taskIds=members.map(m=>m.research_id).join('|');
  const [bundles,setBundles]=useState({projectId:project.id,items:{}});
  const [filter,setFilter]=useState('all');
  const [retry,setRetry]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();
    setBundles(previous=>previous.projectId===project.id?previous:{projectId:project.id,items:{}});
    const load=async id=>{
      try{
        const [projection,activities]=await Promise.all([
          read('projection',{research_id:id},controller.signal),
          read('task_activity',{research_id:id},controller.signal)
        ]);
        if(!controller.signal.aborted)setBundles(previous=>({projectId:project.id,items:{...previous.items,[id]:{status:'ready',projection,activities}}}));
      }catch(error){if(!controller.signal.aborted)setBundles(previous=>({projectId:project.id,items:{...previous.items,[id]:{status:'error',error:error.message}}}));}
    };
    members.forEach(member=>load(member.research_id));
    return()=>controller.abort();
  },[project.id,taskIds,refreshKey,retry]);
  const items=bundles.projectId===project.id?bundles.items:{};
  useEffect(()=>{
    if(!Object.values(items).some(bundle=>status(bundle)==='running'))return;
    const timer=setTimeout(()=>setRetry(value=>value+1),2500);
    return()=>clearTimeout(timer);
  },[items]);
  const count=key=>members.filter(m=>key==='all'||status(items[m.research_id])===key).length;
  const filtered=members.filter(m=>filter==='all'||status(items[m.research_id])===filter);
  return <div className="overview">
    <Card className="overview-hero" pattern="default">
      <div className="overview-title">
        <div className="hero-copy">
          <div className="hero-ribbon"><Title variant="ribbon" size="small" color="app-teal">研究手记</Title><span className="eyebrow">留一点好奇，发现更多可能</span></div>
          <h1>{project.name}</h1>
          <p>把问题种下，让思路生长。一起探索、讨论，收获有依据的答案。</p>
          <Button type="primary" icon={<Icon name="plus"/>} onClick={onCreate}>新建主任务</Button>
        </div>
        <div className="island-scene" aria-hidden="true"><span className="island-sun"><Icon name="sun" size={37}/></span><div className="island-ground"/><Icon name="tree" size={94} className="island-tree"/><span className="island-seed"><Icon name="flower" size={38}/></span><span className="island-label">让好奇心发芽</span></div>
      </div>
    </Card>
    <div className="project-stats" aria-label="项目概况">
      <div><span className="stat-icon"><Icon name="folder" size={22}/></span><span><strong>{members.length}</strong><small>研究主任务</small></span></div>
      <div><span className="stat-icon"><Icon name="chat" size={22}/></span><span><strong>{contents.conversations?.filter(c=>c.kind==='node').length||0}</strong><small>思路与对话</small></span></div>
      <div><span className="stat-icon"><Icon name="bot" size={22}/></span><span><strong>{contents.conversations?.filter(c=>c.kind==='task').length||0}</strong><small>协作专家</small></span></div>
    </div>
    <div className="section-heading"><div><span className="eyebrow">一步一步，走向答案</span><h2>研究进展</h2></div><span>选择节点继续探索</span></div>
    <div className="filter-bar" aria-label="任务状态筛选">{[['all','全部'],['running','运行中'],['done','已完成']].map(([key,label])=><Button size="small" key={key} type={filter===key?'primary':'default'} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}<span className="filter-count">{count(key)}</span></Button>)}</div>
    {filtered.length===0?<div className="empty-state"><Icon name="layers" size={30}/><h3>{members.length?'此状态下暂无主任务':'从主任务开始'}</h3><p>{members.length?'切换筛选查看其他任务。':'每个主任务都有独立的主对话、资料和结果。'}</p></div>:filtered.map(member=>{
      const id=member.research_id,bundle=items[id],open=expanded.includes(id),state=status(bundle);
      const placement=contents.placements.find(p=>p.kind==='research'&&p.object_id===id);
      const root=bundle?.projection?.nodes.find(p=>!p.node.display_parent_id);
      return <Card className={`task-card ${open?'expanded':''}`} key={id} data-main-task={id}>
        <div className="task-header"><Button size="small" type="text" className="task-title-button" aria-expanded={open} onClick={()=>{setExpanded(open?expanded.filter(key=>key!==id):[...expanded,id]);onFocus(id)}}><Icon name="chevron" size={16} className={open?'rotated':''}/><Icon name="folder" size={21}/><div><div className="task-name"><strong>{placement?.title||member.title}</strong><span className={`badge ${state==='running'?'running':state==='done'?'done':'waiting'}`}><i/>{state==='running'?'运行中':state==='done'?'执行结束':'待继续'}</span></div><p>{bundle?.status==='ready'?`${bundle.projection.nodes.length} 个聊天节点 · ${bundle.activities.length} 个专家任务`:'正在读取任务…'}</p></div></Button><Button size="small" className="nav-action" aria-label={`整理 ${placement?.title||member.title}`} onClick={()=>onArrange('research',id,placement?.title||member.title)}><Icon name="more" size={16}/></Button><Button size="small" className="open-chat" disabled={!root} onClick={()=>onSelect(id,{kind:'node',id:root.node.id})}>打开主对话 <Icon name="arrow" size={14}/></Button></div>
        {open&&<div className="task-body">{bundle?.status==='error'?<div className="empty-state"><p className="error">{bundle.error}</p><Button size="small" className="retry" onClick={()=>setRetry(value=>value+1)}>重试</Button></div>:bundle?.status==='ready'?<><p className="flow-description">点击节点打开完整对话或专家任务</p><FlowCanvas researchId={id} projection={bundle.projection} activities={bundle.activities} placements={contents.placements} onSelect={item=>onSelect(id,item)}/><RunAnalysis key={id} researchId={id}/></>:<div className="empty-state">正在读取 Flow…</div>}</div>}
      </Card>
    })}
  </div>;
}
