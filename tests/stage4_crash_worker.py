import asyncio
import json
import os
import signal
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from legacy_composition import build_research
from agent_core.composition import build_resources
from agent_core.research.models import NodeCommand
from agent_core.app.commits import ResearchLedger

async def main():
    mode,research_id,blobs=sys.argv[1:]
    database=PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    def script(request):
        if mode=='running': os.kill(os.getpid(),signal.SIGKILL)
        return [],{'report':'Atomically saved','blocking_missing':[]}
    s=build_research(database,build_resources(database,blobs),ScriptedEngine(script))
    r=s.create('crash',research_id)
    node=await s.submit(NodeCommand(command_id=str(uuid4()),research_id=r.id,kind='create',goal='crash'))
    print(json.dumps({'research_id':r.id,'node_id':node.node_id}),flush=True)
    if mode=='before':
        original=database.transaction
        @contextmanager
        def transaction():
            with original() as tx:
                class Proxy:
                    def __getattr__(self,key):return getattr(tx.research,key)
                    def publish(self,value,deps):
                        tx.research.publish(value,deps)
                        os.kill(os.getpid(),signal.SIGKILL)
                repositories = vars(tx).copy()
                repositories['research'] = Proxy()
                yield SimpleNamespace(**repositories)
        database.transaction=transaction
    if mode=='after':
        original=ResearchLedger.commit
        def commit(self,value):
            original(self,value)
            os.kill(os.getpid(),signal.SIGKILL)
        ResearchLedger.commit=commit
    await s.submit(NodeCommand(command_id=str(uuid4()),research_id=r.id,kind='run',node_id=node.node_id))

asyncio.run(main())
