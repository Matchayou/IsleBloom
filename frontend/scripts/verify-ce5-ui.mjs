import {chromium,expect} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=new URL('../../',import.meta.url);
const evidence=new URL('docs/ce5-evidence/live-attempt4/',root);
const fixture=JSON.parse(await readFile(new URL('live.json',evidence),'utf8'));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1050}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:8795/');await page.evaluate(()=>localStorage.clear());await page.reload();
 await expect(page.getByRole('heading',{name:'CE5 real coding acceptance',exact:true})).toBeVisible();
 await page.locator(`[data-id="task:${fixture.receipts[0].task_id}"]`).click();
 await expect(page.getByRole('region',{name:'专家对话'})).toBeVisible();
 await page.getByRole('button',{name:'查看完整报告',exact:true}).first().click();
 await expect(page.getByRole('heading',{name:'冻结代码候选',exact:true})).toBeVisible();
 await page.getByText('查看代码改动',{exact:true}).click();
 await expect(page.locator('pre').filter({hasText:'+ANSWER = 7'})).toBeVisible();
 await page.getByText('对应快照的测试记录',{exact:true}).click();
 await expect(page.locator('pre').filter({hasText:'Approved AST checks passed for a'})).toBeVisible();
 await expect(page.getByText(fixture.receipts[0].code_candidate.commit,{exact:true})).toBeVisible();
 expect(errors).toEqual([]);
 await page.screenshot({path:fileURLToPath(new URL('browser-code.png',evidence)),fullPage:true});
 await writeFile(new URL('browser.json',evidence),JSON.stringify({status:'passed',real_saved_model_result:true,
  checks:['fixed candidate commit visible','formal diff includes added file and actual change','snapshot-specific test log visible'],page_errors:errors},null,2));
 console.log('CE5 browser passed');
}catch(error){await page.screenshot({path:fileURLToPath(new URL('browser-failure.png',evidence)),fullPage:true});throw error}
finally{await browser.close()}
