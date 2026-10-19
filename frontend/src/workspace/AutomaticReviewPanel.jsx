import React,{useState} from 'react';
import {Button} from 'animal-island-ui';
import {collaborationState} from './model';
import ReviewAdvice from './ReviewAdvice';
import TaskArtifact from './TaskArtifact';
import Modal from './Modal';

function VersionComparison({rounds}){
 const [before,setBefore]=useState(rounds[0].result_id),[after,setAfter]=useState(rounds.at(-1).result_id);
 const column=(side,value,setValue,other)=><section><label>{side}版本<select value={value} onChange={event=>setValue(event.target.value)}>{rounds.map(round=><option key={round.result_id} value={round.result_id} disabled={round.result_id===other}>执行任务 v{round.spec_version}</option>)}</select></label><TaskArtifact key={value} resultId={value}/></section>;
 return <div className="version-comparison">{column('修订前',before,setBefore,after)}{column('修订后',after,setAfter,before)}</div>;
}
export default function AutomaticReviewPanel({activity}){
 const cycle=activity.automatic_review,[comparing,setComparing]=useState(false);
 if(!cycle)return null;
 const last=cycle.rounds.at(-1);
 return <section className="automatic-review-panel" aria-label="自动协作进展"><div className="automatic-review-heading"><strong>{collaborationState(activity)}</strong><span>已修订 {cycle.revisions_completed} / 2 轮</span></div>
 <p>{collaborationState(activity)==='旧版本评审记录'?'以下评审属于先前的任务要求；新版本不会继承旧评审。':'执行 Agent 产出后，由评审 Agent 检查并自动推进修订；无需填写评审意见。'}</p>
 {cycle.reason&&<p role="status">{cycle.reason==='Reached two revisions; remaining issues are reported'?'已完成两轮修订，下方列出了仍未解决的问题。':cycle.status==='blocked'?'资料权限或任务版本已变化，自动协作暂停。':cycle.status==='cancelled'?'任务已停止。':cycle.status==='failed'?'执行或评审未完成，请查看运行记录。':cycle.reason}</p>}
 {last&&<><p>最近评审针对执行任务 v{last.spec_version}</p><ReviewAdvice key={last.result_id} advice={last.advice} taskId={cycle.task_id} round={last}/></>}
 {cycle.rounds.length>1&&<Button size="small" onClick={()=>setComparing(true)}>比较修订版本</Button>}
 {cycle.rounds.length>0&&<details><summary>查看每轮评审与交接</summary>{cycle.rounds.map(round=><article key={round.result_id}><h4>执行任务 v{round.spec_version}</h4><ReviewAdvice advice={round.advice} taskId={cycle.task_id} round={round}/><TaskArtifact resultId={round.result_id} expandable/></article>)}</details>}
 <small>这里记录 Agent 的协作评审；人工采用与发布状态单独保留。</small>
 {comparing&&cycle.rounds.length>1&&<Modal title="修订前后结果" width="min(1100px, calc(100vw - 24px))" onClose={()=>setComparing(false)}><VersionComparison key={cycle.id} rounds={cycle.rounds}/></Modal>}
 </section>;
}
