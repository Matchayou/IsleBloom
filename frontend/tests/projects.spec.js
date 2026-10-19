import {test,expect} from '@playwright/test';

test.beforeEach(async ({page})=>{
  await page.addInitScript(()=>localStorage.setItem('research-workspace-selection',JSON.stringify({projectId:'persistence-a'})));
});

test('project shows independent collapsible Flows without an intermediate task page',async ({page})=>{
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Persistence A'})).toBeVisible();
  const first=page.locator('[data-main-task="persistence-main-1"]');
  const second=page.locator('[data-main-task="persistence-main-2"]');
  await expect(first.locator('.react-flow__node')).toHaveCount(4);
  await expect(second.locator('.react-flow__node')).toHaveCount(0);
  await second.getByRole('button',{name:/Second 待继续/}).click();
  await expect(second.locator('.react-flow__node')).toHaveCount(1);
  await expect(first.locator('.react-flow__node')).toHaveCount(4);
  await expect(page.getByRole('button',{name:'打开主任务',exact:true})).toHaveCount(0);
  await first.locator('.react-flow__node').filter({hasText:'补充局限性并重新总结虚构市场'}).click();
  await expect(page.getByRole('region',{name:'专家对话'})).toBeVisible();
  await expect(page.getByLabel('与这位专家对话',{exact:true})).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('这份结论的来源是什么？')).toBeVisible();
  await page.getByRole('button',{name:'Persistence B',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Persistence B'})).toBeVisible();
  await expect(page.getByText('Renamed first')).toHaveCount(0);
});

test('a delayed first Flow cannot overwrite the selected second conversation',async ({page})=>{
  await page.route('**/api/projection?research_id=persistence-main-1',async route=>{
    await new Promise(resolve=>setTimeout(resolve,1200));
    try {await route.continue()} catch {}
  });
  await page.goto('/');
  const second=page.locator('[data-main-task="persistence-main-2"]');
  await second.getByRole('button',{name:'打开主对话',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Second',exact:true})).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(page.getByRole('heading',{name:'Second',exact:true})).toBeVisible();
  await expect(page.locator('main').getByText('补充局限性并重新总结虚构市场')).toHaveCount(0);
});

test('sidebar search opens matching conversations from another main task',async ({page})=>{
  await page.goto('/');
  await page.locator('[data-main-task="persistence-main-2"]').getByRole('button',{name:'打开主对话',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Second',exact:true})).toBeVisible();
  await page.getByLabel('搜索项目或对话').fill('风险讨论');
  await page.getByRole('button',{name:'风险讨论分支',exact:true}).click();
  await expect(page.getByRole('heading',{name:'风险讨论分支',exact:true})).toBeVisible();
});


test('risk expert opens a conversation with execution details behind a dialog',async ({page})=>{
  await page.goto('/');
  const first=page.locator('[data-main-task="persistence-main-1"]');
  await first.locator('.react-flow__node').filter({hasText:'分析样本风险'}).click();
  await expect(page.getByRole('heading',{name:'分析样本风险',exact:true})).toBeVisible();
  await expect(page.getByRole('log',{name:'专家聊天记录'})).toBeVisible();
  await expect(page.getByLabel('与这位专家对话',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'结果与运行记录'})).toHaveCount(0);
  await page.getByRole('button',{name:'任务与执行',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'任务与执行'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'结果与运行记录'})).toBeVisible();
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await expect(page.getByLabel('与这位专家对话',{exact:true})).toBeVisible();
});
