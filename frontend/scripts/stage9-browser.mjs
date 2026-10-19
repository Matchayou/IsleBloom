import {chromium} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const artifact=name=>fileURLToPath(new URL(`../../docs/${name}`,import.meta.url));
const {research_id}=JSON.parse(readFileSync('/private/tmp/stage9-browser-ids.json','utf8'));
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1580,height:1100}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:8759');
 await page.locator('#research-select').selectOption(research_id);
 try{await page.getByText('可选偏好与获准经验',{exact:true}).waitFor({timeout:10000});}
 catch{throw new Error(JSON.stringify({page:(await page.locator('body').innerText()).slice(0,1600),errors}));}
 await page.getByText('可选偏好与获准经验',{exact:true}).click();
 const panel=page.locator('.memory-panel');
 await panel.getByLabel('内容',{exact:true}).fill('公司研究先看现金流');
 await panel.getByLabel('来源',{exact:true}).fill('用户明确保存；Stage 9 浏览器验收');
 await panel.getByRole('button',{name:'确认并保存'}).click();
 const item=panel.locator('.memory-item').filter({hasText:'公司研究先看现金流'});
 await item.waitFor();
 await item.locator('input[type=checkbox]').check();
 await page.getByRole('checkbox',{name:/company-A.txt/}).check();
 await page.getByLabel('专家任务').fill('本次先看竞争风险，再核对现金流');
 await page.getByRole('button',{name:'派发专家任务'}).click();
 await page.waitForFunction(async id=>{
  const values=await fetch('/api/task_activity?research_id='+id).then(r=>r.json());
  return values.some(value=>value.spec.memory_refs.length===1&&value.runs.some(run=>run.output?.report?.includes('已应用偏好')));
 },research_id);
 const before=await page.evaluate(async id=>fetch('/api/task_activity?research_id='+id).then(r=>r.json()),research_id);
 const applied=before.find(value=>value.spec.memory_refs.length===1);
 if(!applied.runs[0].output.report.includes('本次指令优先：先看竞争风险'))throw new Error('Current instruction did not override memory');
 await item.getByRole('button',{name:'删除'}).click();
 await item.waitFor({state:'detached'});
 await page.getByLabel('专家任务').fill('再次研究公司现金流');
 await page.getByRole('button',{name:'派发专家任务'}).click();
 await page.waitForFunction(async id=>{
  const values=await fetch('/api/task_activity?research_id='+id).then(r=>r.json());
  return values.some(value=>value.spec.user_request==='再次研究公司现金流'&&value.runs.some(run=>run.output?.report?.includes('未应用偏好')));
 },research_id);
 await page.reload();
 await page.getByText('可选偏好与获准经验',{exact:true}).click();
 if(await panel.getByText('公司研究先看现金流').count())throw new Error('Deleted memory returned after reload');
 if(errors.length)throw new Error(errors.join('\n'));
 await page.screenshot({path:artifact('stage9-browser.png')});
 const after=await page.evaluate(async id=>fetch('/api/task_activity?research_id='+id).then(r=>r.json()),research_id);
 const globalMarker='跨研究也需明确选择-'+Date.now();
 await panel.getByLabel('适用范围').selectOption('owner');
 await panel.getByLabel('内容',{exact:true}).fill(globalMarker);
 await panel.getByLabel('来源',{exact:true}).fill('用户确认的全局偏好');
 await panel.getByRole('button',{name:'确认并保存'}).click();
 const globalItem=panel.locator('.memory-item').filter({hasText:globalMarker});
 await globalItem.locator('input[type=checkbox]').check();
 await page.getByLabel('新的研究目标').fill('Stage 9 第二个研究');
 await page.getByRole('button',{name:'创建研究'}).click();
 await page.waitForFunction(old=>document.querySelector('#research-select').value!==old,research_id);
 await globalItem.waitFor();
 if(await globalItem.locator('input[type=checkbox]').isChecked())throw new Error('Global memory selection carried across research switch');
 writeFileSync(artifact('stage9-browser.json'),JSON.stringify({research_id,
  selected_task_id:applied.spec.id,selected_memory_ref:applied.spec.memory_refs[0],
  current_instruction_override:applied.runs[0].output.report,
  after_delete_task_id:after.find(value=>value.spec.user_request==='再次研究公司现金流').spec.id,
  after_delete_memory_count:after.find(value=>value.spec.user_request==='再次研究公司现金流').spec.memory_refs.length,
  deleted_stays_absent_after_reload:true,global_memory_requires_new_selection:true,javascript_errors:errors},null,2));
}finally{await browser.close();}
