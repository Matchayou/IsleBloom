import {test,expect} from '@playwright/test';
import {scene,send,reply,chat,post,get,openScene} from './fixtures';
test('message cutoff, sibling isolation and explicit transfer adoption',async({page,request})=>{
 const s=await scene(request,'分支隔离回归');await send(request,s.node.id,'BEFORE_R3');const first=await reply(request,s,s.node.id);const fork=first.messages.at(-1);
 await send(request,s.node.id,'AFTER_SECRET_R3');await reply(request,s,s.node.id);const main=await chat(request,s.node.id);
 const a=await post(request,'chat_branch',{node_id:s.node.id,fork_message_id:fork.id,goal:'方向 A',expected_revision:main.revision});
 const b=await post(request,'chat_branch',{node_id:s.node.id,fork_message_id:fork.id,goal:'方向 B',expected_revision:main.revision});
 await send(request,a.node_id,'ONLY_A_R3');const answer=await reply(request,s,a.node_id);const sibling=await chat(request,b.node_id);
 expect(JSON.stringify(sibling)).not.toContain('ONLY_A_R3');expect(JSON.stringify(answer)).not.toContain('AFTER_SECRET_R3');
 const projection=await get(request,'projection',{research_id:s.rid});const root=projection.nodes.find(v=>v.node.id===s.node.id).node;
 const transfer=await post(request,'chat_transfer',{source_node_id:a.node_id,source_message_id:answer.messages.at(-1).id,target_node_id:s.node.id,expected_revision:main.revision,expected_node_revision:root.revision});
 expect(transfer.status).toBe('for_discussion');expect((await post(request,'chat_adopt',{transfer_id:transfer.id})).status).toBe('adopted');
 await openScene(page,s);await expect(page.locator('main')).toContainText('浏览器验收固定报告');
});
test('two independent expert tasks do not insert task creation into main chat',async({page,request})=>{
 const s=await scene(request,'专家任务隔离回归');await send(request,s.node.id,'先讨论专家输入');await reply(request,s,s.node.id);const before=await chat(request,s.node.id);
 const tasks=[];for(const kind of ['search','synthesize'])tasks.push(await post(request,'task_create',{research_id:s.rid,node_id:s.node.id,kind,request:`独立${kind}任务`}));
 expect(tasks[0].id).not.toBe(tasks[1].id);expect((await chat(request,s.node.id)).messages).toEqual(before.messages);
 expect(await get(request,'task_activity',{research_id:s.rid})).toHaveLength(2);await openScene(page,s);
 await page.getByRole('button',{name:'返回 Flow',exact:true}).click();await expect(page.locator(`[data-main-task="${s.rid}"] .react-flow__node`)).toHaveCount(3);
});
