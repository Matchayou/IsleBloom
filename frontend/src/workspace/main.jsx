import {Button} from 'animal-island-ui';
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import MarkdownText from './MarkdownText';
import Modal from './Modal';
import ExpertView from './ExpertView';
import RightPanel from './RightPanel';
import {read,refKey} from './api';
import ProjectSidebar from './ProjectSidebar';
import ProjectOverview from './ProjectOverview';
import {ProjectForm,MainTaskForm,FolderForm,ArrangeForm,ProjectFilePanel} from './ProjectParts';
import {ChatActions,TaskCreate} from './Actions';
import {execution,delivery,adoption,job,label,nodeTitle,taskTitle,taskState,validItem,kindLabel} from './model';
import {Icon} from './icons';
import '@xyflow/react/dist/style.css';
import 'animal-island-ui/style';
import './style.css';

const STORAGE='research-workspace-selection';
function saved(){try{return JSON.parse(localStorage.getItem(STORAGE))||{}}catch{return {}}}
function store(projectId,rid,item,right){localStorage.setItem(STORAGE,JSON.stringify({projectId,rid,item,right}))}
function Status({title,detail,retry}){return <div className="empty-state" role="status"><Icon name="layers" size={30}/><h3>{title}</h3>{detail&&<p>{detail}</p>}{retry&&<Button size="small" className="retry" onClick={retry}>重试</Button>}</div>}
function Badge({text,tone='waiting'}){return <span className={`badge ${tone}`}><i/>{text}</span>}
function tone(status){return ['running','queued','saving','ENQUEUED','PENDING','RUNNING'].includes(status)?'running':status==='succeeded'||status==='SUCCESS'?'done':'waiting'}
function rootFor(node,nodeMap){let current=node,seen=new Set();while(current.display_parent_id&&nodeMap.has(current.display_parent_id)&&!seen.has(current.id)){seen.add(current.id);current=nodeMap.get(current.display_parent_id)}return current.id}
function groupsFor(projection,activities){
 const nodeMap=new Map(projection.nodes.map(p=>[p.node.id,p.node]));
 const roots=projection.nodes.filter(p=>!p.node.display_parent_id||!nodeMap.has(p.node.display_parent_id));
 const groups=roots.map(root=>({id:root.node.id,root,nodes:projection.nodes.filter(p=>rootFor(p.node,nodeMap)===root.node.id),activities:[]}));
 const taskMap=new Map(activities.map(a=>[a.spec.id,a]));
 const ownerFor=(task,seen=new Set())=>{if(task.spec.node_id&&nodeMap.has(task.spec.node_id))return rootFor(nodeMap.get(task.spec.node_id),nodeMap);if(task.spec.parent_task_id&&taskMap.has(task.spec.parent_task_id)&&!seen.has(task.spec.id))return ownerFor(taskMap.get(task.spec.parent_task_id),new Set([...seen,task.spec.id]));return null};
 for(const task of activities){const owner=ownerFor(task);const group=groups.find(g=>g.id===owner);if(group)group.activities.push(task)}
 const ungrouped=activities.filter(a=>!groups.some(g=>g.activities.includes(a)));
 if(ungrouped.length)groups.push({id:'research-tasks',root:null,nodes:[],activities:ungrouped});
 return groups;
}
function ChatView({displayTitle,projection,chat,researchId,onChanged,onBranched,onBack,onDispatch}){
 const [branchOpen,setBranchOpen]=useState(false);const [replyPreview,setReplyPreview]=useState(null);const node=projection.node;
 const messages=chat.status==='ready'?[...chat.data.chat.inherited,...chat.data.chat.messages]:[];
 const scrollRef=useRef(null),followReply=useRef(true);
 useEffect(()=>{const el=scrollRef.current;if(el&&followReply.current)el.scrollTop=el.scrollHeight},[messages.length,replyPreview?.text,chat.status]);
 return <section className="chat-view"><div className="view-heading"><Button size="small" className="back-link" onClick={onBack}><Icon name="back" size={16}/>返回 Flow</Button><div className="heading-actions"><Button size="small" className="subtle-button" onClick={()=>setBranchOpen(true)} disabled={chat.status!=='ready'||messages.length===0}><Icon name="plus" size={15}/>创建分支</Button><Button size="small" className="subtle-button" onClick={onDispatch}><Icon name="bot" size={15}/>派发专家</Button></div></div><div className="chat-title conversation-header"><h1 title={displayTitle||nodeTitle(projection)}>{displayTitle||nodeTitle(projection)}</h1><p><span>{node.display_parent_id?'分支对话':'研究主线'}</span><Badge text={label(execution,projection.execution_status)} tone={tone(projection.execution_status)}/></p></div><div className="chat-scroll" ref={scrollRef} onScroll={e=>{const el=e.currentTarget;followReply.current=el.scrollHeight-el.scrollTop-el.clientHeight<80}}>{chat.status==='loading'?<Status title="正在读取对话…"/>:chat.status==='error'?<Status title={chat.error} retry={()=>onChanged(researchId,node.id)}/>:chat.status==='none'?<Status title="这个节点暂无对话"/>:messages.length===0?<div className="chat-welcome"><Icon name="bot" size={30}/><h2>从这里开始研究</h2><p>提出问题，主助手会在这条研究主线中继续工作。</p></div>:messages.map(m=><article className={`message ${m.role}`} key={m.id}><div className="message-avatar">{m.role==='user'?'你':<Icon name="bot" size={16}/>}</div><div className="message-content"><small>{({user:'你',assistant:'主助手',expert:'专家结论',transfer:'分支回传'})[m.role]||m.role}{chat.data.chat.inherited.some(i=>i.id===m.id)?' · 分叉快照':''}</small><div className="message-body"><MarkdownText value={m.content}/></div></div></article>)}{replyPreview?.text&&!messages.some(m=>m.run_id===replyPreview.run_id&&m.role==='assistant')&&<article className="message assistant streaming-reply" aria-label="正在生成的回复"><div className="message-avatar"><Icon name="bot" size={16}/></div><div className="message-content"><small>主助手 · {['failed','cancelled','interrupted','blocked','save_failed'].includes(replyPreview.status)?'未完成的回复':replyPreview.phase==='reconnecting'?'正在重新连接…':'正在生成…'}</small><div className="message-body"><MarkdownText value={replyPreview.text}/></div></div></article>}</div><div className="chat-compose"><ChatActions onReplyPreview={setReplyPreview} researchId={researchId} nodeId={node.id} chat={chat} onChanged={onChanged} onBranched={(...args)=>{setBranchOpen(false);onBranched(...args)}} branchOpen={branchOpen} onBranchClose={()=>setBranchOpen(false)}/></div><details className="view-details"><summary>详细信息与来源</summary><p>对话版本 {chat.status==='ready'?chat.data.chat.revision:'—'} · 挂载资料 {node.resource_refs.length} 份 · 输入结果 {node.result_ids.length} 个</p><p>展示分支与任务归属不表示执行依赖。</p>{projection.stop_reason&&<p>停止原因：{projection.stop_reason}</p>}</details></section>
}
function App(){
 const initial=saved();
 const [projectList,setProjectList]=useState({status:'loading',data:[],error:''});
 const [projectId,setProjectId]=useState(initial.projectId||'');
 const [contents,setContents]=useState({status:'idle',projectId:'',data:null,error:''});
 const [rid,setRid]=useState(initial.rid||'');
 const [bundle,setBundle]=useState({status:'idle',rid:'',data:null,error:''});
 const [item,setItem]=useState(initial.item||null),[right,setRight]=useState(initial.right||null);
 const [chat,setChat]=useState({status:'idle'}),[result,setResult]=useState({status:'idle'});
 const [projectTick,setProjectTick]=useState(0),[contentsTick,setContentsTick]=useState(0),[bundleTick,setBundleTick]=useState(0),[detailTick,setDetailTick]=useState(0);
 const [expanded,setExpanded]=useState([]),[navOpen,setNavOpen]=useState([]);
 const [modal,setModal]=useState(null),[fileOpen,setFileOpen]=useState(()=>window.innerWidth>1000),[sidebarOpen,setSidebarOpen]=useState(()=>window.innerWidth>600);
 useEffect(()=>{
   const narrow=window.matchMedia('(max-width:600px)'),compact=window.matchMedia('(max-width:1000px)');
   const collapseNav=event=>{if(event.matches)setSidebarOpen(false)};
   const collapseFiles=event=>{if(event.matches)setFileOpen(false)};
   narrow.addEventListener('change',collapseNav);compact.addEventListener('change',collapseFiles);
   return()=>{narrow.removeEventListener('change',collapseNav);compact.removeEventListener('change',collapseFiles)};
 },[]);
 useEffect(()=>{const controller=new AbortController();setProjectList({status:'loading',data:[],error:''});read('projects',{},controller.signal).then(data=>setProjectList({status:'ready',data,error:''})).catch(e=>{if(e.name!=='AbortError')setProjectList({status:'error',data:[],error:e.message})});return()=>controller.abort()},[projectTick]);
 useEffect(()=>{if(projectList.status!=='ready')return;if(!projectList.data.length){setProjectId('');setRid('');return}if(!projectList.data.some(p=>p.id===projectId)){setProjectId(projectList.data[0].id);setRid('');setItem(null);setRight(null)}},[projectList,projectId]);
 useEffect(()=>{if(!projectId){setContents({status:'idle',projectId:'',data:null,error:''});return}const controller=new AbortController();setContents({status:'loading',projectId,data:null,error:''});read('project_contents',{project_id:projectId},controller.signal).then(data=>setContents({status:'ready',projectId,data,error:''})).catch(e=>{if(e.name!=='AbortError')setContents({status:'error',projectId,data:null,error:e.message})});return()=>controller.abort()},[projectId,contentsTick]);
 const currentContents=contents.projectId===projectId?contents:{status:'loading'};
 const project=projectList.data.find(p=>p.id===projectId);
 const members=currentContents.status==='ready'?currentContents.data.tasks:[];
 useEffect(()=>{if(currentContents.status==='ready'&&rid&&!members.some(m=>m.research_id===rid)){setRid('');setItem(null);setRight(null)}},[currentContents.status,contents.data,rid]);
 useEffect(()=>{if(!rid){setBundle({status:'idle',rid:'',data:null,error:''});return}const controller=new AbortController();setBundle(previous=>previous.rid===rid&&previous.status==='ready'?previous:{status:'loading',rid,data:null,error:''});Promise.all([read('projection',{research_id:rid},controller.signal),read('task_activity',{research_id:rid},controller.signal),read('resources',{research_id:rid},controller.signal)]).then(([projection,activities,library])=>setBundle({status:'ready',rid,data:{projection,activities,resources:library.resources},error:''})).catch(e=>{if(e.name!=='AbortError')setBundle({status:'error',rid,data:null,error:e.message})});return()=>controller.abort()},[rid,bundleTick]);
 const currentBundle=bundle.rid===rid?bundle:{status:'loading'};
 const loaded=currentBundle.status==='ready'?currentBundle.data:null;
 const projection=loaded?.projection,activities=loaded?.activities||[],resources=loaded?.resources||[];
 useEffect(()=>{if(!loaded)return;if(item&&!validItem(item,projection,resources,activities))setItem(null);if(right&&!validItem(right,projection,resources,activities))setRight(null)},[loaded,item,right,projection,resources,activities]);
 useEffect(()=>store(projectId,rid,item,right),[projectId,rid,item,right]);
 useEffect(()=>{if(!loaded||item?.kind!=='node'){setChat({status:'idle'});return}const controller=new AbortController();setChat(previous=>previous.rid===rid&&previous.id===item.id&&previous.status==='ready'?previous:{status:'loading',rid,id:item.id});read('chat_read',{node_id:item.id},controller.signal).then(data=>setChat({status:'ready',rid,id:item.id,data})).catch(e=>{if(e.name==='AbortError')return;setChat(e.status===404?{status:'none',rid,id:item.id}:{status:'error',rid,id:item.id,error:e.message})});return()=>controller.abort()},[loaded,item?.kind,item?.id,detailTick]);
 useEffect(()=>{if(!loaded||right?.kind!=='result'){setResult({status:'idle'});return}const controller=new AbortController();setResult({status:'loading',rid,id:right.id});read('result',{id:right.id},controller.signal).then(data=>setResult({status:'ready',rid,id:right.id,data})).catch(e=>{if(e.name!=='AbortError')setResult({status:'error',rid,id:right.id,error:e.message})});return()=>controller.abort()},[loaded,right?.kind,right?.id,detailTick]);
 useEffect(()=>{if(!loaded)return;const timer=setInterval(()=>setBundleTick(x=>x+1),5000);return()=>clearInterval(timer)},[loaded]);
 const groups=useMemo(()=>projection?groupsFor(projection,activities):[],[projection,activities]);
 useEffect(()=>{if(groups.length)setNavOpen(previous=>previous.length?previous:[groups[0].id])},[rid,groups.length]);
 useEffect(()=>{if(members.length)setExpanded(previous=>previous.length?previous:[members[0].research_id])},[projectId,contents.data]);
 const member=members.find(m=>m.research_id===rid),research=member?{id:rid,goal:currentContents.data.placements.find(p=>p.kind==='research'&&p.object_id===rid)?.title||member.title}:null;
 const selectedNode=item?.kind==='node'&&projection?.nodes.find(p=>p.node.id===item.id);
 const selectedTask=item?.kind==='task'&&activities.find(a=>a.spec.id===item.id);
 function changed(target){if(target!==rid)return;setBundleTick(x=>x+1);setDetailTick(x=>x+1)}
 function chooseProject(id){if(window.innerWidth<=600)setSidebarOpen(false);setProjectId(id);setRid('');setItem(null);setRight(null);setExpanded([]);setNavOpen([])}
 function chooseTask(id){if(window.innerWidth<=600)setSidebarOpen(false);setRid(id);setItem(null);setRight(null);setExpanded(previous=>previous.includes(id)?previous:[...previous,id]);setNavOpen([])}
 function chooseConversation(target,next){if(window.innerWidth<=600)setSidebarOpen(false);setRid(target);setItem(next);setRight(null);setExpanded(previous=>previous.includes(target)?previous:[...previous,target])}
 function createdProject(value){setProjectList(state=>({...state,status:'ready',data:[...state.data,value]}));chooseProject(value.id);setModal(null);setContentsTick(x=>x+1)}
 function createdTask(value){setContents(state=>({...state,data:{...state.data,tasks:[...state.data.tasks,value]}}));setContentsTick(x=>x+1);chooseTask(value.research_id);setModal(null)}
 function organized(){setContentsTick(x=>x+1);setModal(null)}
 function arrange(kind,id,title){const placement=currentContents.data?.placements.find(p=>p.kind===kind&&p.object_id===id);setModal({kind:'arrange',objectKind:kind,id,title,placement})}
 function branched(target,node){if(target!==rid)return;setContentsTick(x=>x+1);setBundle({status:'loading',rid,data:null,error:''});setItem({kind:'node',id:node});setBundleTick(x=>x+1);setDetailTick(x=>x+1)}
 function taskCreated(target,task){if(target!==rid)return;setContentsTick(x=>x+1);setBundle({status:'loading',rid,data:null,error:''});setModal(null);setItem({kind:'task',id:task});setBundleTick(x=>x+1)}
 function uploaded(target,value){if(target!==rid)return;setBundle({status:'loading',rid,data:null,error:''});setRight({kind:'resource',id:refKey(value.ref)});setBundleTick(x=>x+1)}
 return <div className={`demo-app ${!sidebarOpen?'sidebar-hidden':''} ${!fileOpen?'files-hidden':''}`}>
 <ProjectSidebar projects={projectList.data} projectId={projectId} contents={currentContents.data} rid={rid} item={item} groups={groups} navOpen={navOpen} setNavOpen={setNavOpen} onProject={chooseProject} onTask={chooseTask} onSelect={(next,target)=>chooseConversation(target||rid,next)} onCreateProject={()=>setModal({kind:'project'})} onCreateTask={()=>setModal({kind:'mainTask'})} onCreateFolder={()=>setModal({kind:'folder'})} onArrange={arrange} onEditFolder={folder=>setModal({kind:'folder',folder})}/>
 <main className="main-panel"><header className="topbar"><Button size="small" className="icon-button" aria-label={sidebarOpen?'收起侧栏':'展开侧栏'} onClick={()=>setSidebarOpen(!sidebarOpen)}><Icon name="panel"/></Button><div className="breadcrumb"><Button size="small" onClick={()=>{setRid('');setItem(null)}}>{project?.name||'项目'}</Button>{rid&&item&&<><span>/</span><Button size="small" onClick={()=>setItem(null)}>{research?.goal||'主任务'}</Button></>}{item&&<><span>/</span><strong>{currentContents.data?.placements.find(p=>p.kind===item.kind&&p.object_id===item.id)?.title||(selectedNode?nodeTitle(selectedNode):selectedTask?taskTitle(selectedTask):'')}</strong></>}</div><Button size="small" className="file-toggle" aria-pressed={fileOpen} onClick={()=>setFileOpen(!fileOpen)}><Icon name="file" size={16}/>资料与结果</Button></header>
 {projectList.status==='loading'?<Status title="正在读取项目…"/>:projectList.status==='error'?<Status title={projectList.error} retry={()=>setProjectTick(x=>x+1)}/>:!projectId?<Status title="暂无项目" detail="从左侧新建项目开始。"/>:currentContents.status==='loading'?<Status title="正在读取项目…"/>:currentContents.status==='error'?<Status title={currentContents.error} retry={()=>setContentsTick(x=>x+1)}/>:!item?<ProjectOverview key={projectId} project={project} contents={currentContents.data} expanded={expanded} setExpanded={setExpanded} refreshKey={bundleTick} onFocus={id=>{setRid(id);setRight(null)}} onSelect={chooseConversation} onCreate={()=>setModal({kind:'mainTask'})} onArrange={arrange}/>:currentBundle.status==='loading'?<Status title="正在读取主任务…"/>:currentBundle.status==='error'?<Status title={bundle.error} retry={()=>setBundleTick(x=>x+1)}/>:loaded&&research?selectedNode?<ChatView key={selectedNode.node.id} displayTitle={currentContents.data.placements.find(p=>p.kind==='node'&&p.object_id===item.id)?.title} projection={selectedNode} chat={chat.rid===rid&&chat.id===item?.id?chat:{status:'loading'}} researchId={rid} onChanged={changed} onBranched={branched} onBack={()=>setItem(null)} onDispatch={()=>setModal({kind:'task',nodeId:selectedNode.node.id})}/>:selectedTask?<ExpertView key={selectedTask.spec.id} displayTitle={currentContents.data.placements.find(p=>p.kind==='task'&&p.object_id===item.id)?.title} activity={selectedTask} refreshKey={bundleTick} onChanged={()=>{setBundleTick(x=>x+1);setContentsTick(x=>x+1)}} onBack={()=>setItem(null)} onDispatch={()=>setModal({kind:'task',nodeId:selectedTask.spec.node_id,parentTaskId:selectedTask.spec.id})}/>:null:null}
 </main>
 {fileOpen&&projectId&&(!rid?<ProjectFilePanel projectId={projectId} onClose={()=>setFileOpen(false)}/>:loaded&&<RightPanel projectId={projectId} researchId={rid} activeItem={item} onUploaded={uploaded} data={projection} resources={resources} activities={activities} selection={right} setSelection={setRight} detail={result.rid===rid&&result.id===right?.id?result:{status:'loading'}} retry={()=>setDetailTick(x=>x+1)} close={()=>setFileOpen(false)}/>)}
 {modal?.kind==='project'&&<Modal title="新建项目" onClose={()=>setModal(null)}><ProjectForm onDone={createdProject}/></Modal>}
 {modal?.kind==='mainTask'&&projectId&&<Modal title="新建主任务" onClose={()=>setModal(null)}><MainTaskForm projectId={projectId} onDone={createdTask}/></Modal>}
 {modal?.kind==='folder'&&projectId&&<Modal title={modal.folder?'编辑文件夹':'新建文件夹'} onClose={()=>setModal(null)}><FolderForm projectId={projectId} folder={modal.folder} folders={currentContents.data?.folders||[]} onDone={organized}/></Modal>}
 {modal?.kind==='arrange'&&projectId&&<Modal title="整理对话" onClose={()=>setModal(null)}><ArrangeForm projectId={projectId} kind={modal.objectKind} objectId={modal.id} placement={modal.placement} folders={currentContents.data?.folders||[]} defaultTitle={modal.title} onDone={organized}/></Modal>}
 {modal?.kind==='task'&&loaded&&<Modal title="派发专家任务" onClose={()=>setModal(null)}><TaskCreate researchId={rid} nodeId={modal.nodeId} parentTaskId={modal.parentTaskId} selectionId={item?.id||null} resources={resources} activities={activities} onCreated={taskCreated}/></Modal>}
 </div>
}
createRoot(document.getElementById('root')).render(<App/>);
