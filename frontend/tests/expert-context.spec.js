import {test,expect} from '@playwright/test';

test('executor and reviewer open frozen context; history selection and revocation work',async({page,request})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const projects=await (await request.get('/api/projects')).json();
 const contents=await (await request.get(`/api/project_contents?project_id=${projects[0].id}`)).json();
 const rid=contents.tasks[0].research_id;
 const activities=await (await request.get(`/api/task_activity?research_id=${rid}`)).json();
 const executor=activities.find(a=>a.collaboration_role==='executor');
 const reviewer=activities.find(a=>a.collaboration_role==='reviewer');
 await page.addInitScript(value=>localStorage.setItem('research-workspace-selection',JSON.stringify(value)),{projectId:projects[0].id,rid,item:{kind:'task',id:executor.spec.id}});
 await page.goto('/');
 await page.getByRole('button',{name:'上下文',exact:true}).click();
 const context=page.getByRole('region',{name:'子 Agent 上下文',exact:true});
 await expect(context.getByRole('heading',{name:'开轮输入消息',exact:true})).toBeVisible();
 await expect(context).toContainText('整理收入并说明资料局限。');
 await expect(context.getByLabel('选择运行')).toHaveCount(1);
 const data=await (await request.get(`/api/expert_context?task_id=${executor.spec.id}`)).json();
 const old=data.runs.find(run=>run.spec_version===1);
 await context.getByLabel('选择运行').selectOption(old.run_id);
 await expect(context).toContainText('任务单 v1 · 执行上下文');
 await page.getByRole('button',{name:'关闭',exact:true}).last().click();
 await page.evaluate(value=>localStorage.setItem('research-workspace-selection',JSON.stringify(value)),{projectId:projects[0].id,rid,item:{kind:'task',id:reviewer.spec.id}});
 // The init script intentionally selects the executor, so navigate via Flow instead.
 await page.getByRole('button',{name:'返回 Flow',exact:true}).click();
 await page.locator(`[data-id="task:${reviewer.spec.id}"]`).click();
 await page.getByRole('button',{name:'上下文',exact:true}).click();
 await expect(context.getByRole('heading',{name:'开轮输入消息',exact:true})).toBeVisible();
 await expect(context).toContainText('评审/讨论上下文');
 const revoke=await request.post('/api/scope',{data:{research_id:rid,operation:'revoke',ref:executor.spec.required_refs[0]}});
 expect(revoke.ok()).toBeTruthy();
 await context.getByRole('button',{name:'刷新上下文',exact:true}).click();
 await expect(context.getByRole('alert')).toContainText('访问已撤回');
 await expect(context.getByRole('heading',{name:'开轮输入消息',exact:true})).toHaveCount(0);
 expect(errors).toEqual([]);
});
