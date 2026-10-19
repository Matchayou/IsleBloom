import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const base='http://127.0.0.1:8768';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:960}});
const errors=[];const writeStatuses=[];
page.on('response',response=>{if(response.request().method()==='POST')writeStatuses.push([new URL(response.url()).pathname,response.status()])});
page.on('pageerror',error=>errors.push(error.message));
const name=`Workspace isolated browser test ${Date.now()}`;
try{
 await page.goto(base);
 await page.getByRole('button',{name:'新建对话'}).click();
 await page.locator('#new-research').fill(name);
 await page.getByRole('button',{name:'创建研究'}).click();
 await page.getByRole('heading',{name}).waitFor();
 await page.getByRole('button',{name:'上传资料'}).first().click();
 await page.locator('#upload-file').setInputFiles({name:'workspace-control.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic local browser control. No private data.\n')});
 await page.getByRole('dialog',{name:'上传资料'}).getByRole('button',{name:'上传资料'}).click();
 await page.getByRole('button',{name:'workspace-control.txt'}).waitFor();
 await page.getByRole('button',{name:'workspace-control.txt'}).click();
 await page.getByRole('link',{name:'查看或下载原件'}).waitFor();
 await page.getByRole('button',{name:'打开主对话'}).click();
 await page.getByLabel('发送到此节点').fill('Please give a control reply.');
 await page.getByRole('button',{name:'发送并执行回复'}).click();
 await page.getByText('Scripted chat control reply.',{exact:false}).first().waitFor({timeout:20000});
 await page.getByRole('button',{name:'创建分支'}).first().click();
 await page.getByLabel('分支截止消息').selectOption({index:2});
 await page.getByLabel('分支目标').fill('Isolated follow-up branch');
 await page.getByRole('button',{name:'创建分支'}).last().click();
 await page.getByRole('heading',{name:'Isolated follow-up branch'}).waitFor();
 await page.getByRole('button',{name:'派发专家'}).last().click();
 await page.locator('#task-request').fill('Summarize the synthetic control source.');
 await page.getByRole('dialog',{name:'派发专家任务'}).locator('input[type=checkbox]').first().check();
 await page.getByRole('button',{name:'创建并派发'}).click();
 await page.getByRole('heading',{name:'Summarize the synthetic control source.'}).waitFor({timeout:20000});
 await page.getByText('Scripted expert control result.',{exact:false}).first().waitFor({timeout:20000});
 await page.locator('.file-list .file-row').last().click();
 await page.locator('.file-content h2').getByText('Summarize the synthetic control source.').waitFor();
 await page.reload();
 await page.getByRole('heading',{name:'Summarize the synthetic control source.'}).first().waitFor();
 await page.getByText('Scripted expert control result.',{exact:false}).first().waitFor();
 // A conflict must retain the draft without duplicating a message.
 await page.locator('.project-children .conversation-row').first().click();
 await page.getByRole('heading',{name}).waitFor();
 await page.route('**/api/chat_send',route=>route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Chat revision changed'})}));
 await page.getByLabel('发送到此节点').fill('Conflict probe, no saved message.');
 await page.getByRole('button',{name:'发送并执行回复'}).click();
 await page.getByText('消息未保存',{exact:false}).waitFor();
 await page.unroute('**/api/chat_send');
 assert.equal(await page.locator('.chat-scroll').getByText('Conflict probe, no saved message.',{exact:true}).count(),0);
 // Revoked result reads replace the previous content with an error.
 await page.route('**/api/result?*',route=>route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'Access denied'})}));
 await page.locator('.file-list .file-row').filter({hasText:'v1'}).first().click();
 await page.getByText('访问已撤回或无权读取').waitFor();
 await page.unroute('**/api/result?*');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,name,writeStatuses,errors}));
}catch(error){console.error((await page.locator('body').innerText()).slice(-5000));console.error(errors);throw error}finally{await browser.close()}
