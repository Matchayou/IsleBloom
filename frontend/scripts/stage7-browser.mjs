import {chromium} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const artifact=name=>fileURLToPath(new URL(`../../docs/${name}`,import.meta.url));

const {research_id,result_id}=JSON.parse(readFileSync(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 await page.goto('http://127.0.0.1:8757');
 await page.locator('#research-select').selectOption(research_id);
 await page.locator('.detail .task').first().locator('summary').first().click();
 await page.locator('.detail .feedback').first().locator('summary').click();
 await page.locator('.detail .feedback textarea').first().fill('第 4 项单位错误：原文是 thousand USD，回答写为 million USD。');
 await page.locator('.detail .feedback input').first().fill('thousand USD');
 await page.getByRole('button',{name:'提交反馈'}).click();
 await page.getByText('待核验',{exact:false}).first().waitFor();
 const feedback=await page.evaluate(async researchId=>{
  const response=await fetch('/api/feedbacks?research_id='+encodeURIComponent(researchId));
  return response.json();
 },research_id);
 const found=feedback.find(x=>x.result_id===result_id && x.description.includes('thousand USD'));
 if(!found || found.status!=='pending')throw new Error('Browser feedback was not persisted');
 await page.reload();
 await page.locator('#research-select').selectOption(research_id);
 await page.locator('.detail .task').first().locator('summary').first().click();
 await page.locator('.detail .feedback').first().locator('summary').click();
 await page.getByText('待核验',{exact:false}).first().waitFor();
 await page.screenshot({path:artifact('stage7-browser-feedback.png'),fullPage:true});
 writeFileSync(artifact('stage7-browser-feedback.json'),JSON.stringify({
  research_id,result_id,feedback_id:found.id,status:found.status,
  task_id:found.task_id,run_id:found.run_id,method_version:found.method_version,
  model:found.model,after_reload_visible:true},null,2));
} finally {await browser.close();}
