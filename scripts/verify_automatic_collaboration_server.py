"""Disposable browser fixture exercising the real collaboration service, no paid model."""
import asyncio
import tempfile
from pathlib import Path
import uvicorn
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_research, build_tasks, build_projects
from agent_core.research.chat import ChatService


async def main():
    db=MemoryDatabase()
    with tempfile.TemporaryDirectory(prefix='automatic-collaboration-') as directory:
        scopes=build_resources(db,Path(directory)/'blobs')
        calls={'executor':0,'reviewer':0}
        def script(request):
            if request.agent_id.startswith('temporary:'):
                calls['reviewer']+=1
                return [],{'verdict':'passed' if calls['reviewer']>1 else 'needs_revision',
                    'summary':'已检查固定版本的报告。','issues':[] if calls['reviewer']>1 else [
                        {'problem':'缺少资料局限说明','evidence':'v1 没有说明样本性质','instruction':'补充这是合成样本，不能推断真实市场。'}]}
            calls['executor']+=1
            return [],{'report':f"收入为 100。执行产物 v{calls['executor']}。" + ('这是合成样本，不能推断真实市场。' if calls['executor']>1 else ''),
                'limitations':['确定性浏览器夹具，不是真实模型能力评测。']}
        engine=ScriptedEngine(script)
        research=build_research(db,scopes,engine);tasks=build_tasks(db,scopes,research,engine)
        projects=build_projects(db,research,tasks)
        project=projects.create('自动协作验证项目')
        member=await projects.create_main_task(project.id,'自动评审与修订')
        node=research.query('list_nodes',member.research_id)[0]
        _,ref=scopes.ingest(scopes.current(research.research(member.research_id).scope_id,research.owner),
            content=b'Synthetic revenue is 100.',name='sample.txt',media_type='text/plain')
        ChatService(research,tasks).send(node.id,'整理收入并说明资料局限。',0)
        intent=research.collaboration.delegate_task(command_id='browser-cycle',node_id=node.id,expert_id='expert_synthesize',
            task_instruction='整理收入并说明资料局限。',delivery_requirements='提供清晰报告。',resource_refs=(ref,))
        await research.collaboration.execute(intent.command_id)
        await uvicorn.Server(uvicorn.Config(create_app(research,'frontend/dist',tasks),
            host='127.0.0.1',port=18746,log_level='warning')).serve()
    db.close()

if __name__=='__main__':asyncio.run(main())
