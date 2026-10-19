import {chromium} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const artifact=name=>fileURLToPath(new URL(`../../docs/${name}`,import.meta.url));
const {research_id,refs}=JSON.parse(readFileSync('/private/tmp/stage8-browser-ids.json','utf8'));
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1580,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8758');
 await page.locator('#research-select').selectOption(research_id);
 await page.getByText('方法与流程版本管理',{exact:true}).click();
 const panel=page.locator('.methods-panel');
 await panel.getByRole('button',{name:'专家方法 · expert_synthesize · synthesize-evidence-v7 · 当前',exact:true}).click();
 await panel.getByText('保存新版本（高级编辑）',{exact:true}).click();
 const draft=JSON.parse(await panel.getByLabel('版本 JSON').inputValue());
 draft.version='browser-'+Date.now();draft.body.instructions+=' BROWSER_V2_UNIT_CHECK';draft.evidence='Browser control check; no live quality claim';
 await panel.getByLabel('版本 JSON').fill(JSON.stringify(draft,null,2));
 await panel.getByRole('button',{name:'保存版本',exact:true}).click();
 await panel.getByRole('button',{name:`专家方法 · expert_synthesize · ${draft.version}`,exact:true}).click();
 await panel.getByLabel('发布或回退依据').fill('Browser publication control verified');
 await panel.getByRole('button',{name:'启用此版本／回退',exact:true}).click();
 await panel.getByRole('button',{name:`专家方法 · expert_synthesize · ${draft.version} · 当前`,exact:true}).waitFor();
 await page.reload();
 await page.getByText('方法与流程版本管理',{exact:true}).click();
 await panel.getByRole('button',{name:`专家方法 · expert_synthesize · ${draft.version} · 当前`,exact:true}).waitFor();
 await panel.getByRole('button',{name:'专家方法 · expert_synthesize · synthesize-evidence-v7',exact:true}).click();
 await panel.getByLabel('发布或回退依据').fill('Rollback after browser control fixture');
 await panel.getByRole('button',{name:'启用此版本／回退',exact:true}).click();
 await panel.getByRole('button',{name:'专家方法 · expert_synthesize · synthesize-evidence-v7 · 当前',exact:true}).waitFor();
 await panel.getByRole('button',{name:'研究模板 · three-company-research · v8 · 当前',exact:true}).click();
 await panel.getByLabel('industry',{exact:true}).fill('新能源设备');
 for(const [index,letter] of ['a','b','c'].entries()){
  await panel.getByLabel('company_'+letter,{exact:true}).fill('NEW_'+letter.toUpperCase());
  await panel.getByLabel('report_'+letter,{exact:true}).selectOption(JSON.stringify(refs[index]));
 }
 const compiledResponse=page.waitForResponse(response=>response.url().endsWith('/api/workflow_compile'));
 await panel.getByRole('button',{name:'生成固定计划',exact:true}).click();
 const compiled=await (await compiledResponse).json();
 await panel.getByRole('button',{name:'执行此计划',exact:true}).waitFor();
 await panel.getByRole('button',{name:'执行此计划',exact:true}).click();
 await panel.getByText('计划执行已返回；请在研究节点中核对各步骤结果与缺失。',{exact:true}).waitFor();
 const state=await page.evaluate(async id=>{
  const [projection,assets]=await Promise.all([fetch('/api/projection?research_id='+id).then(r=>r.json()),fetch('/api/assets').then(r=>r.json())]);
  return {projection,assets};
 },research_id);
 const ids=new Set(compiled.plan.steps.map(s=>s.node_id));
 const completed=state.projection.nodes.filter(n=>ids.has(n.node.id)&&n.node.current_result_id);
 if(completed.length!==4)throw new Error('Expected four completed template steps: '+completed.length);
 if(errors.length)throw new Error(errors.join('\n'));
 const otherResearch=await page.locator('#research-select option').evaluateAll((options,id)=>options.map(o=>o.value).find(v=>v&&v!==id),research_id);
 if(otherResearch){
  await page.locator('#research-select').selectOption(otherResearch);
  if(await panel.getByRole('button',{name:'执行此计划',exact:true}).count())throw new Error('Workflow instance leaked across research switch');
  await page.locator('#research-select').selectOption(research_id);
  await panel.getByRole('button',{name:'研究模板 · three-company-research · v8 · 当前',exact:true}).click();
  if(await panel.getByLabel('company_a',{exact:true}).inputValue())throw new Error('Workflow parameters leaked across research switch');
 }
 await panel.scrollIntoViewIfNeeded();
 await page.screenshot({path:artifact('stage8-browser.png')});
 writeFileSync(artifact('stage8-browser.json'),JSON.stringify({research_id,instance_id:compiled.id,new_version:draft.version,published_persisted_after_reload:true,rollback_verified:true,research_switch_clears_instance:true,template_steps_completed:completed.length,node_results:completed.map(n=>({goal:n.node.goal,result_id:n.node.current_result_id})),javascript_errors:errors},null,2));
}finally{await browser.close();}
