"""Real PostgreSQL/browser fixture; scripted outputs do not claim live model quality."""
import asyncio
import json
import os
from pathlib import Path
import uvicorn
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from agent_core.composition import build_resources,build_research,build_tasks,build_evaluation,build_evolution
from agent_core.research.chat import ChatService

async def main():
    db=PostgresDatabase(os.environ['DATABASE_URL'])
    try:
        scopes=build_resources(db,'/private/tmp/stage8-pg-20261003/blobs')
        engine=ScriptedEngine(lambda request:([],{'report':'Stage 8 control result: new industry comparison; verify real semantics separately.','missing':[]}))
        research=build_research(db,scopes,engine)
        root=research.create('Stage 8 方法与模板浏览器验收')
        await ChatService(research).start(root.id,'主线')
        scope=scopes.current(root.scope_id,'local');refs=[]
        for label in ('NEW_A','NEW_B','NEW_C'):
            scope,ref=scopes.ingest(scope,content=f'{label}: 2026 CFO 100 capex 20 million USD. New industry control source.'.encode(),name=label+'.txt',media_type='text/plain')
            refs.append(ref.model_dump(mode='json'))
        tasks=build_tasks(db,scopes,research,engine)
        evaluation=build_evaluation(db,tasks)
        evolution=build_evolution(db,tasks,evaluation)
        for kind in ('synthesize','analyze'):
            asset=tasks.methods.save(json.loads(Path(f'config/{kind}-evidence-v7.json').read_text()))
            active=tasks.methods.query('head','method',asset.asset_id)
            if active!=asset.version: tasks.methods.publish('method',asset.asset_id,asset.version,active,'browser-fixture','Explicit source citation method control check')
        value=tasks.methods.save(json.loads(Path('config/three-company-workflow.json').read_text()))
        active=tasks.methods.query('head','workflow',value.asset_id)
        if active != value.version:
            tasks.methods.publish('workflow',value.asset_id,value.version,active,'browser-fixture','Manual equivalence control test')
        Path('/private/tmp/stage8-browser-ids.json').write_text(json.dumps(dict(research_id=root.id,refs=refs)))
        await uvicorn.Server(uvicorn.Config(create_app(research,'frontend/dist',tasks,evaluation=evaluation,evolution=evolution),host='127.0.0.1',port=8758,log_level='warning')).serve()
    finally: db.close()

if __name__=='__main__': asyncio.run(main())
