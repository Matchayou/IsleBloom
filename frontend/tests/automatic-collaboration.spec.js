import {test,expect} from '@playwright/test';

test('real API cycle appears in Flow, edges open advice, versions remain after reload',async({page,request})=>{
 const failures=[];page.on('pageerror',error=>failures.push(error.message));
 const projects=await (await request.get('/api/projects')).json();
 const contents=await (await request.get(`/api/project_contents?project_id=${projects[0].id}`)).json();
 const researchId=contents.tasks[0].research_id;
 const activities=await (await request.get(`/api/task_activity?research_id=${researchId}`)).json();
 const executor=activities.find(a=>a.collaboration_role==='executor');
 expect(executor.automatic_review.status).toBe('passed');
 expect(executor.automatic_review.revisions_completed).toBe(1);
 await page.goto('/');
 await expect(page.getByText('执行 Agent · 综合整理',{exact:true})).toBeVisible();
 await expect(page.getByText('评审 Agent',{exact:true})).toBeVisible();
 // React Flow labels identify the exact recorded handoff, rather than a painted placeholder.
 const edges=page.locator('.react-flow__edge');
 await expect(edges).toHaveCount(4); // owner, reviewer owner, grouped reviews and feedback
 await page.screenshot({path:decodeURIComponent(new URL('../../docs/automatic-collaboration-flow.png',import.meta.url).pathname),fullPage:true,animations:'disabled'});
 const firstReview=page.locator(`[data-id="review:${executor.spec.id}"]`);
 const point=await firstReview.locator('.react-flow__edge-interaction').evaluate(path=>{const point=path.getPointAtLength(path.getTotalLength()*.3);const screen=new DOMPoint(point.x,point.y).matrixTransform(path.getScreenCTM());return {x:screen.x,y:screen.y}});
 await page.mouse.click(point.x,point.y);
 await page.getByLabel('查看交接版本').selectOption('1');
 await expect(page.getByText('针对执行任务 v1 的固定结果',{exact:true})).toBeVisible();
 await expect(page.getByText('缺少资料局限说明',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'关闭',exact:true}).last().click();
 await page.locator(`[data-id="task:${executor.spec.id}"]`).click();
 await expect(page.getByRole('region',{name:'自动协作进展'})).toBeVisible();
 await expect(page.getByText('已修订 1 / 2 轮',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'比较修订版本'}).click();
 await expect(page.getByLabel('修订前版本')).toHaveValue(executor.automatic_review.rounds[0].result_id);
 await expect(page.getByLabel('修订后版本')).toHaveValue(executor.automatic_review.rounds[1].result_id);
 await expect(page.locator('.version-comparison')).toContainText('执行产物 v1');
 await expect(page.locator('.version-comparison')).toContainText('执行产物 v2');
 await page.screenshot({path:decodeURIComponent(new URL('../../docs/automatic-collaboration-preview.png',import.meta.url).pathname),fullPage:true,animations:'disabled'});
 await page.getByRole('button',{name:'关闭',exact:true}).last().click();
 await page.reload();
 await expect(page.getByRole('region',{name:'自动协作进展'})).toBeVisible();
 await expect(page.getByText('已修订 1 / 2 轮',{exact:true})).toBeVisible();
 expect(failures).toEqual([]);
});
