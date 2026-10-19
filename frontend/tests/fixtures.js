import {expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';

export async function post(request,name,data){const response=await request.post(`/api/${name}`,{data});expect(response.ok(),await response.text()).toBeTruthy();return response.json();}
export async function get(request,name,params){const response=await request.get(`/api/${name}`,{params});expect(response.ok(),await response.text()).toBeTruthy();return response.json();}
export async function scene(request,title){const project=await post(request,'projects',{name:`${title} ${randomUUID()}`});const main=await post(request,'project_main_task',{project_id:project.id,title});const projection=await get(request,'projection',{research_id:main.research_id});return {projectId:project.id,rid:main.research_id,node:projection.nodes[0].node,title};}
export async function openScene(page,s,node=s.node){await page.addInitScript(value=>localStorage.setItem('research-workspace-selection',JSON.stringify(value)),{projectId:s.projectId,rid:s.rid,item:{kind:'node',id:node.id}});await page.goto('/');await expect(page.getByRole('heading',{name:node.goal,exact:true})).toBeVisible();}
export async function command(request,s,kind,values={}){return post(request,'command',{command_id:randomUUID(),research_id:s.rid,kind,...values});}
export async function nodeResult(request,s,nodeId){let row;await expect.poll(async()=>{row=(await get(request,'projection',{research_id:s.rid})).nodes.find(v=>v.node.id===nodeId);return row.node.current_result_id;}).toBeTruthy();return row.node;}
export async function chat(request,nodeId){return (await get(request,'chat_read',{node_id:nodeId})).chat;}
export async function send(request,nodeId,content){const before=await chat(request,nodeId);return post(request,'chat_send',{node_id:nodeId,content,expected_revision:before.revision});}
export async function reply(request,s,nodeId){const before=await chat(request,nodeId);await post(request,'chat_reply',{node_id:nodeId,expected_revision:before.revision,command_id:randomUUID()});await expect.poll(async()=>(await chat(request,nodeId)).messages.at(-1)?.role).toBe('assistant');return chat(request,nodeId);}
