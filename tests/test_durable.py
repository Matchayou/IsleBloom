"""Current DBOS orchestration uses the controlled SDK, including fixture models."""
import asyncio,os,threading
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_controlled_services
from agent_core.durable import DurableResearch,CODE_VERSION
from agent_core.execution.ports import Conflict
from test_controlled_collaboration import Provider,answer

async def finished(durable,jobs):
    for _ in range(150):
        states=await asyncio.gather(*(durable.status(job) for job in jobs))
        if all(s['status'] in ('SUCCESS','ERROR','CANCELLED') for s in states):return states
        await asyncio.sleep(.1)
    raise AssertionError('DBOS completion timed out')

@pytest.mark.parametrize('parallel',[False,True])
async def test_fixed_sequences_and_parallel_jobs(tmp_path,parallel):
    url=os.environ.get('CE7_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated CE7 PostgreSQL required')
    db=PostgresDatabase(url);durable=None
    try:
        scopes=build_resources(db,tmp_path/'blobs');provider=Provider()
        research,tasks,_=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider)
        root=research.create('CE7 DBOS '+uuid4().hex)
        specs=[tasks.create(research_id=root.id,kind='synthesize',user_request='Report fixed fact 7; income unknown.') for _ in range(3)]
        entered=0;release=threading.Event();lock=threading.Lock()
        async def response(kwargs):
            nonlocal entered
            with lock:
                entered+=1
                if entered==2:release.set()
            if parallel:assert await asyncio.to_thread(release.wait,5)
            return answer({'report':'7 devices; income unknown.'})
        provider.plans.extend([[response] for _ in specs])
        durable=await DurableResearch(tasks,url).start()
        jobs=[uuid4().hex for _ in (specs if parallel else [None])]
        if parallel:
            await asyncio.gather(*(durable.submit(job,[spec.id]) for job,spec in zip(jobs,specs)))
        else:await durable.submit(jobs[0],[s.id for s in specs])
        states=await finished(durable,jobs)
        assert all(s['status']=='SUCCESS' for s in states),states
        assert entered==3
        assert all(len(t['receipts'])==1 for s in states for t in s['tasks'])
        assert all(len(t['operations'][t['run_ids'][0]])==2 for s in states for t in s['tasks'])
        count=len(provider.requests)
        same=await durable.submit(jobs[0],[specs[0].id] if parallel else [s.id for s in specs])
        assert same['status']=='SUCCESS' and len(provider.requests)==count
        with pytest.raises(Conflict):await durable.submit(jobs[0],[specs[-1].id])
        from sqlalchemy import text
        with db.engine.begin() as c:
            c.execute(text('UPDATE dbos.workflow_status SET application_version=:v WHERE workflow_uuid=:id'),{'v':'obsolete-protocol','id':'research:'+jobs[0]})
        with pytest.raises(Conflict,match='checkpoint application version'):await durable.status(jobs[0])
        with db.engine.begin() as c:
            c.execute(text('UPDATE dbos.workflow_status SET application_version=:v WHERE workflow_uuid=:id'),{'v':CODE_VERSION,'id':'research:'+jobs[0]})
    finally:
        if durable:durable.close()
        db.close()


def test_ungoverned_durable_backend_rejected(tmp_path):
    from agent_core.adapters.memory_ledger import MemoryDatabase
    db=MemoryDatabase();scopes=build_resources(db,tmp_path/'blobs');research=build_research(db,scopes)
    tasks=build_tasks(db,scopes,research)
    with pytest.raises(Conflict,match='controlled checkpoint'):
        DurableResearch(tasks,'unused')
