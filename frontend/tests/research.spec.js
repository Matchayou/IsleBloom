import {test,expect} from '@playwright/test';
import {scene,command,nodeResult,get,openScene} from './fixtures';
test('fixed source, independent node, comparison and history in the current project',async({page,request})=>{
 const s=await scene(request,'节点固定输入回归');
 const response=await request.post(`/api/upload?research_id=${s.rid}&name=idea.txt`,{data:'BASE_DOCUMENT',headers:{'content-type':'text/plain'}});
 expect(response.ok()).toBeTruthy();const {ref}=await response.json();
 const base=await command(request,s,'create',{goal:'基础节点',resource_refs:[ref]});
 await command(request,s,'run',{node_id:base.node_id});const saved=await nodeResult(request,s,base.node_id);
 const branch=await command(request,s,'branch',{node_id:saved.id,goal:'独立方向',resource_refs:[ref],result_ids:[saved.current_result_id]});
 await command(request,s,'run',{node_id:branch.node_id});const child=await nodeResult(request,s,branch.node_id);
 await command(request,s,'move',{node_id:child.id,position:[90,30]});
 await command(request,s,'exclude',{node_id:child.id});await command(request,s,'select',{node_id:child.id});
 const compared=await command(request,s,'compare',{goal:'明确比较',result_ids:[saved.current_result_id,child.current_result_id]});
 const comparison=await nodeResult(request,s,compared.node_id);const output=await get(request,'result',{id:comparison.current_result_id});
 expect(output.inputs).toEqual([saved.current_result_id,child.current_result_id]);expect(output.version.resource_refs).toEqual([ref]);
 await openScene(page,s,comparison);await page.reload();await expect(page.getByRole('heading',{name:'明确比较',exact:true})).toBeVisible();
});
