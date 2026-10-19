import React,{useId,useState} from 'react';
import MarkdownText from './MarkdownText';
import ReviewIssueEvidence from './ReviewIssueEvidence';
export default function ReviewAdvice({advice,taskId,round}){
 const [selected,setSelected]=useState(null);
 const evidenceId=useId();
 return <div className="review-advice"><strong>{advice.verdict==='passed'?'Agent 评审通过':'需要修订'}</strong><MarkdownText value={advice.summary}/>{advice.issues.map((issue,index)=><div key={index} className="review-issue">
  {taskId&&round?<button type="button" className="review-issue-link" aria-expanded={selected===index} aria-controls={`${evidenceId}-${index}`} onClick={()=>setSelected(selected===index?null:index)}>{index+1}. {issue.problem}<small>{selected===index?'收起证据':'查看问题证据'}</small></button>:<strong>{index+1}. {issue.problem}</strong>}
  <p>依据：{issue.evidence}</p><p>修改要求：{issue.instruction}</p>
  <div id={`${evidenceId}-${index}`}>{selected===index&&taskId&&round&&<ReviewIssueEvidence key={`${round.result_id}:${index}`} taskId={taskId} version={round.spec_version} index={index}/>}</div>
 </div>)}</div>;
}
