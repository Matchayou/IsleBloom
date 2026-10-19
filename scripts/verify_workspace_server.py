"""Isolated in-memory scripted browser fixture for the writable workspace."""
import asyncio
import tempfile
from pathlib import Path
import uvicorn
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from agent_core.composition import build_resources, build_research, build_tasks


class SlowScriptedEngine(ScriptedEngine):
    async def execute(self, request, spec, tools, invoke):
        await asyncio.sleep(0.15)
        return await super().execute(request, spec, tools, invoke)


def script(request):
    if request.agent_id.startswith('expert_'):
        return [], {'report':'Scripted expert control result. No real model was called.',
                    'blocking_missing':[], 'limitations':['Control output; requires independent semantic review'],
                    'self_checks':{'1':'passed','2':'passed'}}
    return [], {'report':'Scripted chat control reply. No real model was called.', 'blocking_missing':[]}


async def main():
    db=MemoryDatabase()
    with tempfile.TemporaryDirectory(prefix='workspace-phase2-') as root:
        scopes=build_resources(db,Path(root)/'blobs')
        engine=SlowScriptedEngine(script)
        research=build_research(db,scopes,engine)
        tasks=build_tasks(db,scopes,research,engine)
        app=create_app(research,'frontend/dist',tasks)
        await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8768,log_level='warning')).serve()
    db.close()

if __name__=='__main__': asyncio.run(main())
