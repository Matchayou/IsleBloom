import {sameRef} from './api';

export const execution = {idle:'未运行',queued:'排队中',running:'运行中',succeeded:'执行结束',failed:'执行失败',interrupted:'已中断',saving:'保存中',save_failed:'保存失败',cancelled:'已取消'};
export const delivery = {pending_review:'已保存待复核',partial_pending_review:'部分结果已保存待复核',passed:'交付通过',partial:'部分交付',rework:'需返工',rejected:'交付未通过'};
export const adoption = {for_discussion:'供讨论',adopted:'已采用'};
export const job = {ENQUEUED:'已入队',PENDING:'等待中',RUNNING:'后台运行中',SUCCESS:'后台执行结束',ERROR:'后台失败',CANCELLED:'已取消',ACCESS_DENIED:'访问已撤回'};
export const dispatch = {waiting:'等待上游复核',accepted:'专家已接收',queued:'准备执行',completed:'执行已结束',failed:'派发失败'};
export const kinds = {coding:'编码',search:'资料搜索',extract:'信息提取',analyze:'内容分析',synthesize:'综合整理'};
export function kindLabel(kind){return kinds[kind]||kind.replace(/^expert_/, '专家');}
export function label(map, key, fallback='未知') { return key == null ? fallback : map[key] || key; }
export function nodeTitle(p) {return p.node.goal.split('\n')[0] || p.node.id;}
export function taskTitle(a) {const value=(a.collaboration_role!=='reviewer'&&a.automatic_review?taskRequest(a):a.spec.goal || a.spec.user_request || kindLabel(a.spec.kind)).replace(/\s+/g,' ').trim();return value.length>28?`${value.slice(0,28)}…`:value;}
export function taskState(a) {if(a.collaboration_role==='reviewer'&&a.automatic_review)return {execution:collaborationState(a),delivery:'评审建议单独记录',adoption:'不自动采用'};const run=a.runs.filter(r=>r.binding.spec_version===a.spec.version).at(-1);const waiting=run&&['running','interrupted'].includes(run.execution_status)&&a.information_requests?.some(item=>item.run_id===run.binding.id&&item.status==='waiting');return {execution:waiting?'等待主助手补充信息':run?.execution_phase?.phase==='finalizing'?'正在收尾':label(execution,run?.execution_status,a.dispatch_status ? label(dispatch,a.dispatch_status) : a.job_status ? label(job,a.job_status) : '未运行'),delivery:label(delivery,run?.receipt?.delivery,'未交付'),adoption:label(adoption,run?.receipt?.adoption,'未采用')};}
export function resourceSources(ref, data, tasks) {
  const names=[];
  data.nodes.forEach(p=>{if(p.node.resource_refs.some(r=>sameRef(r,ref)))names.push(`节点：${nodeTitle(p)}`);});
  tasks.forEach(a=>{if([...a.spec.required_refs,...a.spec.optional_refs].some(r=>sameRef(r,ref)))names.push(`任务：${taskTitle(a)}`);});
  return names;
}
export function validItem(item,data,resources,tasks) {
  if(!item)return null;
  if(item.kind==='node')return data.nodes.some(p=>p.node.id===item.id)?item:null;
  if(item.kind==='task')return tasks.some(a=>a.spec.id===item.id)?item:null;
  if(item.kind==='result')return data.nodes.some(p=>p.versions.some(v=>v.id===item.id))?item:null;
  if(item.kind==='resource')return resources.some(r=>`${r.ref.resource_id}:${r.ref.snapshot_id}:${r.ref.parse_id}`===item.id)?item:null;
  if(item.kind==='task-result')return tasks.some(a=>a.runs.some(run=>run.receipt?.result_id===item.id&&run.access==='available'))?item:null;
  return null;
}

export const automaticReviewLabels={executing:'执行完成后自动评审',reviewing:'自动评审中',revising:'自动修订中',passed:'Agent 评审通过',exhausted:'修订已达两轮，仍有问题',failed:'协作执行失败',blocked:'协作已暂停',cancelled:'协作已停止'};
export function collaborationState(activity){
 const cycle=activity.automatic_review;
 if(!cycle)return null;
 if(activity.collaboration_role==='reviewer'&&cycle.status==='executing')return '等待执行产物';
 if(activity.collaboration_role!=='reviewer'&&cycle.status==='revising'&&activity.spec.version===cycle.current_version+1)return '自动修订中';
 if(activity.collaboration_role!=='reviewer'&&cycle.current_version!==activity.spec.version)return '旧版本评审记录';
 const sourceRun=activity.runs?.filter(run=>run.binding.spec_version===activity.spec.version).at(-1);
 if(activity.collaboration_role!=='reviewer'&&cycle.status==='reviewing'&&sourceRun?.execution_status==='running')return '执行后自动评审';
 return automaticReviewLabels[cycle.status]||cycle.status;
}

export function taskRequest(activity){
 const cycle=activity.automatic_review;
 const sameCycle=cycle&&(cycle.current_version===activity.spec.version||cycle.status==='revising'&&activity.spec.version===cycle.current_version+1);
 return activity.collaboration_role!=='reviewer'&&sameCycle?cycle.original_request:activity.spec.user_request;
}
