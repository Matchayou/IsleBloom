import {test,expect} from '@playwright/test';

test('R3 save retry, continued chat and stopped expert',async({page})=>{
 await page.goto('/');
 await page.getByRole('combobox',{name:'打开研究'}).selectOption({label:'R3 浏览器场景二：保存失败与停止'});
 await page.getByRole('button',{name:'开启主线聊天'}).click();
 await expect(page.getByRole('heading',{name:'当前分支：主线'})).toBeVisible();
 await page.getByRole('textbox',{name:'在当前分支继续'}).fill('触发一次保存失败');
 await page.getByRole('button',{name:'发送并续聊'}).click();
 await expect(page.getByRole('button',{name:'只重试保存'})).toBeVisible();
 await expect(page.locator('.messages .message')).toHaveCount(1);
 await page.getByRole('button',{name:'只重试保存'}).click();
 await expect(page.locator('.messages .message')).toHaveCount(2);
 await expect(page.locator('.messages')).toContainText('保存重试后可继续聊天的脚本回复');
 await page.getByRole('textbox',{name:'在当前分支继续'}).fill('续聊仍保留前文');
 await page.getByRole('button',{name:'发送并续聊'}).click();
 await expect(page.locator('.messages .message')).toHaveCount(4);
 await page.getByRole('checkbox',{name:/company-1.txt ready/}).check();
 await page.getByRole('combobox',{name:'专家类型'}).selectOption('search');
 await page.getByRole('textbox',{name:'专家任务'}).fill('等待停止的专家任务');
 await page.getByRole('button',{name:'派发专家任务'}).click();
 await page.getByRole('button',{name:/停止专家/}).click();
 await expect(page.locator('.task')).toHaveCount(1);
 await page.locator('.task summary').click();
 await expect(page.locator('.task')).toContainText('运行：已取消');
 await expect(page.locator('.task')).toContainText('交付：需返工');
 await expect(page.locator('.messages .message')).toHaveCount(4);
 await page.screenshot({path:'../docs/r3-browser-failure.png',fullPage:true});
});
