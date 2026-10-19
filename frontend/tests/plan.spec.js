import {test,expect} from '@playwright/test';
test.use({baseURL:process.env.PLAYWRIGHT_PLAN_BASE_URL||'http://127.0.0.1:18769'});

test('generate, revise, confirm, refresh and inspect saved step deliveries',async ({page,request})=>{
 const marker='验收-'+Date.now();
 await page.goto('/');
 await page.getByRole('button',{name:'打开主对话',exact:true}).click();
 await page.getByRole('button',{name:'计划',exact:true}).click();
 await page.getByLabel('先分析并生成计划').fill(marker+'：生成一份2025年度的研究计划');
 await page.getByRole('button',{name:'生成计划',exact:true}).click();
 let card=page.getByRole('article',{name:'计划：等待你确认'});
 await expect(card).toHaveCount(1);
 await expect(card.getByText('先检查固定资料，再形成综合报告。')).toBeVisible();
 const proposals=await (await request.get('/api/plan_proposals?research_id='+(await page.evaluate(()=>JSON.parse(localStorage.getItem('research-workspace-selection')).rid)))).json();
 expect(proposals[0].execution_steps).toHaveLength(0);
 await card.getByLabel('修改要求或澄清回答').fill('额外比较2024年，最终用中文交付');
 await card.getByRole('button',{name:'修改计划',exact:true}).click();
 await expect(page.getByRole('article',{name:'计划：已拒绝'}).filter({hasText:marker})).toHaveCount(1);
 card=page.getByRole('article',{name:'计划：等待你确认'});
 await expect(card.getByText(/额外比较2024年/)).toBeVisible();
 await page.reload();
 card=page.getByRole('article',{name:'计划：等待你确认'});
 await expect(card).toHaveCount(1);
 await card.getByRole('button',{name:'确认并执行',exact:true}).click();
 const done=page.getByRole('article',{name:'计划：计划执行完成'}).filter({hasText:marker});
 await expect(done).toHaveCount(1);
 await done.getByText('核对固定资料 · 已交付，待审核').click();
 await expect(done.getByText('浏览器验收固定报告：已完成当前步骤，真实市场结论仍待核实。',{exact:true}).first()).toBeVisible();
 await page.reload();
 await expect(page.getByRole('article',{name:'计划：计划执行完成'}).filter({hasText:marker})).toHaveCount(1);
 await page.screenshot({path:'/private/tmp/plan-mode-ui.png',fullPage:true});
});

test('required questions prevent approval in both UI and API',async ({page,request})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'打开主对话',exact:true}).click();
 await page.getByRole('button',{name:'计划',exact:true}).click();
 await page.getByLabel('先分析并生成计划').fill('需要澄清报告期的研究计划');
 await page.getByRole('button',{name:'生成计划',exact:true}).click();
 const card=page.getByRole('article',{name:'计划：等待你确认'});
 await expect(card.getByText('请确认报告期',{exact:true})).toBeVisible();
 await expect(card.getByRole('button',{name:'确认并执行',exact:true})).toBeDisabled();
 const researchId=await page.evaluate(()=>JSON.parse(localStorage.getItem('research-workspace-selection')).rid);
 const proposals=await (await request.get('/api/plan_proposals?research_id='+researchId)).json();
 const p=proposals.find(p=>p.questions.length>0&&p.status==='awaiting_confirmation');
 const response=await request.post('/api/plan_confirm',{data:{proposal_id:p.id,expected_revision:p.revision}});
 expect(response.status()).toBe(409);
 await card.getByRole('button',{name:'拒绝计划',exact:true}).click();
 await expect(page.getByRole('article',{name:'计划：等待你确认'})).toHaveCount(0);
});

test.describe('short approval windows',()=>{
 test.use({viewport:{width:615,height:635}});
 test('approval controls stay inside the viewport after scrolling the plan',async ({page})=>{
   await page.goto('/');
   await page.getByRole('button',{name:'打开主对话',exact:true}).click();
   await page.getByRole('button',{name:'计划',exact:true}).click();
   await page.getByLabel('先分析并生成计划').fill('短窗口计划审核 '+Date.now());
   await page.getByRole('button',{name:'生成计划',exact:true}).click();
   const card=page.getByRole('article',{name:'计划：等待你确认'});
   await expect(card).toHaveCount(1);
   const confirm=card.getByRole('button',{name:'确认并执行',exact:true});
   await confirm.scrollIntoViewIfNeeded();
   const bounds=await confirm.evaluate(el=>({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,height:el.ownerDocument.documentElement.clientHeight}));
   expect(bounds.top).toBeGreaterThanOrEqual(0);
   expect(bounds.bottom).toBeLessThanOrEqual(bounds.height);
   await expect(confirm).toBeEnabled();
 });
});
