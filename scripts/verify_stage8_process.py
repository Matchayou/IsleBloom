"""Two-process proof: publication survives restart; queued tasks retain old methods."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources,build_research,build_tasks

ROOT=Path(__file__).resolve().parents[1]
STATE=Path(os.environ.get('STAGE8_STATE','/private/tmp/stage8-process-state.json'))
BLOBS=Path('/private/tmp/stage8-pg-20261003/blobs')


class Capture(ScriptedEngine):
    def __init__(self):
        super().__init__(lambda request:([],{'report':'Stage 8 process control result','fields':{},'sources':{}}))
        self.instructions=[]
    async def execute(self,request,spec,tools,invoke):
        self.instructions.append(spec.instructions)
        return await super().execute(request,spec,tools,invoke)


def setup():
    db=PostgresDatabase(os.environ['DATABASE_URL'])
    scopes=build_resources(db,BLOBS);research=build_research(db,scopes)
    engine=Capture();tasks=build_tasks(db,scopes,research,engine)
    return db,scopes,research,engine,tasks


async def verify():
    state=json.loads(STATE.read_text());db,scopes,research,engine,tasks=setup()
    try:
        assert tasks.methods.resolve('method','expert_synthesize').version==state['new_version']
        receipts=[]
        for task_id in state['tasks']:
            receipts.append((await tasks.run(task_id))['receipt'])
        assert receipts[0].method_version==state['old_version']
        assert receipts[1].method_version==state['new_version']
        assert 'STAGE8_PROCESS_NEW' not in engine.instructions[0]
        assert 'STAGE8_PROCESS_NEW' in engine.instructions[1]
        tasks.methods.publish('method','expert_synthesize',state['old_version'],state['new_version'],'process-check','Rollback control fixture')
        before=tasks.spec(state['tasks'][0])
        rollback=tasks.create(research_id=before.research_id,kind=before.kind,user_request=before.user_request,required_refs=before.required_refs)
        assert rollback.method_version==state['old_version']
        result=dict(new_process_verified=True,queued_old_method_preserved=True,new_method_executed=True,
            rollback_future_version=rollback.method_version,receipts=[r.model_dump(mode='json') for r in receipts])
        (ROOT/'docs/stage8-process.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    finally: db.close()


def main():
    if len(sys.argv)>1: return asyncio.run(verify())
    db,scopes,research,engine,tasks=setup()
    try:
        root=research.create('Stage 8 cross-process '+str(uuid4()))
        _,ref=scopes.ingest(scopes.current(root.scope_id,'local'),content=b'NEW_COMPANY input only',name='new.txt',media_type='text/plain')
        original=tasks.methods.resolve('method','expert_synthesize')
        before=tasks.create(research_id=root.id,kind='synthesize',user_request='Read the new company',required_refs=(ref,))
        value=tasks.methods.save({**original.model_dump(),'version':'process-'+str(uuid4()),
            'body':{**original.body,'instructions':original.body['instructions']+' STAGE8_PROCESS_NEW'},
            'evidence':'Two-process execution fixture, no real model quality claim'})
        tasks.methods.publish('method',value.asset_id,value.version,original.version,'process-check','Control proof')
        after=tasks.create(research_id=root.id,kind='synthesize',user_request=before.user_request,required_refs=(ref,))
        STATE.write_text(json.dumps(dict(tasks=[before.id,after.id],old_version=original.version,new_version=value.version)))
    finally: db.close()
    subprocess.run([sys.executable,__file__,'verify'],check=True)


if __name__=='__main__': main()
