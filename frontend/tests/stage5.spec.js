import {test,expect} from '@playwright/test';
import {scene,command,nodeResult,get,openScene} from './fixtures';
test('fixed synthetic source evidence stays located after publication and reload',async({page,request})=>{
 const s=await scene(request,'固定证据回归');const source='FIXED_COUNT 7; market size unknown.';
 const response=await request.post(`/api/upload?research_id=${s.rid}&name=fixed.txt`,{data:source,headers:{'content-type':'text/plain'}});
 expect(response.ok()).toBeTruthy();const {ref}=await response.json();
 const created=await command(request,s,'create',{goal:'Browser evidence fixture',resource_refs:[ref]});await command(request,s,'run',{node_id:created.node_id,evidence_required:true});
 const node=await nodeResult(request,s,created.node_id);const result=await get(request,'result',{id:node.current_result_id});
 expect(JSON.stringify(result.evidence)).toContain(source);expect(result.version.resource_refs).toEqual([ref]);
 await openScene(page,s,node);await page.reload();await expect(page.getByRole('heading',{name:'Browser evidence fixture',exact:true})).toBeVisible();
 expect((await get(request,'result',{id:node.current_result_id})).evidence).toEqual(result.evidence);
});
test('public search without credentials cannot produce fabricated results',async({request})=>{
 const s=await scene(request,'搜索凭据回归');const response=await request.post('/api/search',{data:{research_id:s.rid,query:'public annual report'}});
 expect(response.ok()).toBeFalsy();expect(await response.text()).toContain('TINYFISH_API_KEY');expect((await get(request,'resources',{research_id:s.rid})).resources).toHaveLength(0);
});
