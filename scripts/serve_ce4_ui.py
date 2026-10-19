"""Disposable CE4 browser fixture: actual DB/SDK/API, deterministic synthetic Model."""
import asyncio,json,os,sys
from pathlib import Path
from uuid import uuid4
import uvicorn
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.app.api import create_app
from agent_core.contracts import RunLimits
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_controlled_collaboration import Provider,answer
async def main():
    url=os.environ['CE4_TEST_DATABASE_URL']
    assert make_url(url).database.startswith('ce4_ui_')
    root=Path('.ce4/20261007/ui')/make_url(url).database;root.mkdir(parents=True,exist_ok=True)
    db=PostgresDatabase(url);provider=Provider();scopes=build_resources(db,root/'blobs')
    research,tasks,projects=build_controlled_services(db,scopes,private_root=root/'private',model_provider=provider)
    if research.query('list_researches',research.owner):raise ValueError('Fresh UI fixture DB required')
    project=projects.create('CE4 浏览器验收');main=await projects.create_main_task(project.id,'合成验收工作区')
    node=research.query('list_nodes',main.research_id)[0];c=research.collaboration
    from agent_core.research.chat import ChatService
    ChatService(research,tasks).send(node.id,'请执行合成界面验收任务。',0)
    cases={}
    for name,content in [('完整报告','已确认 7；市场规模未知。\n\n'+('这是合成资料的明确局限。 '*200)+'\n\n全文末尾标记 CE4_FULL_END'),('格式失败',None),('可复核报告','可复核的合成报告，未知仍未知。')]:
        intent=c.delegate_task(command_id=uuid4().hex,node_id=node.id,expert_id='expert_synthesize',task_instruction='CE4 UI '+name,delivery_requirements='合成验收，不冒充真实研究')
        provider.plans.append([answer({'report':content})] if content else [answer('{')]*3)
        out=await c.execute(intent.command_id);cases[name]={'task_id':intent.task_id,'receipt':out['receipts'][0].model_dump(mode='json')}
    final=tasks.create(research_id=node.research_id,node_id=node.id,kind='synthesize',user_request='CE4 UI 正在收尾',limits=RunLimits(max_calls=0,max_turns=2,timeout_seconds=240))
    entered=asyncio.Event()
    async def pause(kwargs):
        assert not kwargs['tools'];entered.set();await asyncio.Event().wait()
    provider.plans.append([pause]);running=asyncio.create_task(tasks.run(final.id))
    await asyncio.wait_for(entered.wait(),10)
    cases['正在收尾']={'task_id':final.id}
    (root.parent/'cases.json').write_text(json.dumps({'project_id':project.id,'research_id':node.research_id,'cases':cases},ensure_ascii=False,indent=2))
    try:
        server=uvicorn.Server(uvicorn.Config(create_app(research,'.ce4/20261007/frontend',tasks),host='127.0.0.1',port=8794,log_level='warning'))
        await server.serve()
    finally:
        running.cancel();await asyncio.gather(running,return_exceptions=True);db.close()
if __name__=='__main__':asyncio.run(main())
