"""Actual SQL rollback and fresh-process state; provider behavior tested separately."""
import os
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources,build_research,build_tasks,build_projects
from agent_core.contracts import RunResult
from agent_core.memories.write_models import WriteMemory
from test_memory_capture_budget import source
from test_memory_writes import admitted

async def test_capture_and_memory_commit_rollback_then_reopen(tmp_path,monkeypatch):
    url=os.environ.get('CE7_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated PostgreSQL required')
    owner='capture-pg:'+uuid4().hex
    def open_services():
        db=PostgresDatabase(url);scopes=build_resources(db,tmp_path/'blobs')
        engine=ScriptedEngine(lambda r:([],{'report':'收到','claims':[],'blocking_missing':[],'limitations':[]}))
        research=build_research(db,scopes,engine,owner);tasks=build_tasks(db,scopes,research,engine)
        build_projects(db,research,tasks)
        return db,scopes,research,tasks
    db,scopes,research,tasks=open_services()
    try:
        root=research.create('Capture SQL acceptance')
        app=(db,scopes,research,tasks,root,research.engine)
        job,_=await source(app);ready=research.memories.capture.prepare(job)
        snap=scopes.repository.snapshot(ready.request.run_id)
        args=WriteMemory(scope='user',content='先给结论，再解释原因',basis='user_statement',quote=job.content,reason='User preference')
        with admitted(snap,args) as payload:receipt=await research.memories.writes.submit(snap,root.id,args)
        result=RunResult(run_id=snap.run_id,status='succeeded',output={'reason':'Candidate'})
        from agent_core.adapters.postgres.memories import PostgresMemories
        original=PostgresMemories.save_capture
        def fail(self,value,expected_state=None):
            if value.state=='completed':raise RuntimeError('crash inside capture commit')
            return original(self,value,expected_state)
        with monkeypatch.context() as m:
            m.setattr(PostgresMemories,'save_capture',fail)
            with pytest.raises(RuntimeError,match='crash'):research.memories.capture.finish(ready,result)
        with db.transaction() as tx:
            assert tx.memories.capture(job.id).state=='ready'
            assert tx.memories.get(receipt['memory_id']) is None
            assert tx.memories.list_writes(owner,snap.run_id)[0].state=='pending'
    finally:db.close()
    db,scopes,research,tasks=open_services()
    try:
        assert research.memories.writes.replay(snap.run_id,payload['call_id'],payload)['value']==receipt
        value=research.memories.capture.finish(ready,result)
        repeated=research.memories.capture.finish(ready,result)
        assert value==repeated and value.state=='completed'
        with db.transaction() as tx:
            entry=tx.memories.get(receipt['memory_id']);assert entry.version==1 and entry.status=='draft'
            assert len(tx.memories.list(owner))==1
            assert len(tx.memories.list_captures(owner))==1
    finally:db.close()
