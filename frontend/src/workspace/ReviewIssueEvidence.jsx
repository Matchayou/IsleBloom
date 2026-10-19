import React,{useEffect,useState} from 'react';
import {Button} from 'animal-island-ui';
import {read} from './api';
import {RunEvidence} from './ExpertRun';

export default function ReviewIssueEvidence({taskId,version,index}){
 const [state,setState]=useState({status:'loading'}),[attempt,setAttempt]=useState(0),[all,setAll]=useState(false);
 useEffect(()=>{
  const controller=new AbortController();setState({status:'loading'});
  read('review_issue',{task_id:taskId,spec_version:version,issue_index:index},controller.signal)
   .then(data=>{if(!controller.signal.aborted)setState({status:'ready',data})})
   .catch(error=>{if(!controller.signal.aborted)setState({status:'error',error:error.message})});
  return()=>controller.abort();
 },[taskId,version,index,attempt]);
 if(state.status==='error')return <><p className="error" role="alert">{state.error}</p><Button size="small" onClick={()=>setAttempt(value=>value+1)}>重试读取证据</Button></>;
 if(state.status!=='ready')return <p role="status">正在读取问题证据…</p>;
 const data=state.data,ids=data.issue.tool_call_ids;
 return <section className="review-issue-evidence expert-run" aria-label="评审问题证据">
  <h4>对应报告 v{data.spec_version}</h4>
  <p>此问题针对已保存的固定候选报告，后续修订不会改变这里的版本。</p>
  <details><summary>报告与评审标识</summary><p>报告：{data.result_id}</p><p>评审运行：{data.review_run_id}</p><p>报告 SHA256：{data.artifact_sha256}</p></details>
  <p>{all?'当前展示本版本全部调用，供继续核查；不表示它们都与此问题相关。':ids.length?'下方是评审 Agent 选出的相关调用，供核查依据；关联不代表根因已确认。':'评审未定位到具体调用。可先检查报告，或展开该版本全部调用继续核查。'}</p>
  <Button size="small" onClick={()=>setAll(value=>!value)}>{all?'仅看问题关联调用':'查看本版本全部工具调用'}</Button>
  <RunEvidence taskId={taskId} data={data.execution} callIds={all?null:ids}/>
 </section>;
}
