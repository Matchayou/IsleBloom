from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from agent_core.contracts import CoreEvent, RunError, RunResult, Usage
from agent_core.execution.ports import StoredRunEvent
from agent_core.execution.analysis import analyze_run
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources
from agent_core.research.models import NodeCommand
from agent_core.app.api import create_app
from legacy_composition import build_research


def test_replay_pairs_interleaved_tools_and_preserves_unknown_usage():
    start=datetime(2026,10,13,tzinfo=timezone.utc)
    record=SimpleNamespace(run_id='r',request=SimpleNamespace(agent_id='a'),created_at=start,status='failed',
        result=RunResult(run_id='r',status='failed',error=RunError(code='timeout',origin='engine',message='secret')))
    events=[]
    for sequence,(call,status,offset) in enumerate([('one','started',0),('two','started',1),('two','completed',3),('one','failed',5)],1):
        events.append(StoredRunEvent(root_run_id='r',sequence=sequence,occurred_at=start+timedelta(seconds=offset),
            event=CoreEvent(run_id='r',parent_run_id=None,phase='tool',name='search',tool_call_id=call,status=status)))
    result=analyze_run(record,list(reversed(events)),operations=[{'kind':'model','status':'unconfirmed','usage':None}])
    assert [row['duration_seconds'] for row in result['timeline']]==[None,None,2,5]
    assert result['usage']==Usage(requests=1,complete=False).model_dump()
    assert result['elapsed_seconds'] is None and result['cost'] is None
    assert result['failure']['sequence']==4 and result['failure']['error_code']=='timeout'
    assert 'secret' not in str(result)
    record.status='succeeded'
    assert analyze_run(record,events)['failure'] is None


@pytest.mark.asyncio
async def test_api_history_is_read_only_content_free_and_revocation_aware(tmp_path):
    db=MemoryDatabase();scopes=build_resources(db,tmp_path/'blobs');service=build_research(db,scopes,ScriptedEngine(lambda request:([],{'report':'private result'})))
    research=service.create('private user input')
    await service.submit(NodeCommand(command_id=str(uuid4()),research_id=research.id,kind='create',goal='private user input'))
    node=service.query('list_nodes',research.id)[0]
    scope=scopes.current(research.scope_id,service.owner)
    scope,ref=scopes.ingest(scope,content=b'private source',name='source.txt',media_type='text/plain')
    await service.submit(NodeCommand(command_id=str(uuid4()),research_id=research.id,node_id=node.id,kind='mount',resource_refs=(ref,)))
    await service.submit(NodeCommand(command_id=str(uuid4()),research_id=research.id,node_id=node.id,kind='run'))
    app=create_app(service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/run_analysis',params={'research_id':research.id})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        rows=response.json()['runs'];assert len(rows)==1 and rows[0]['status']=='succeeded', service.ledger.get(rows[0]['run_id']).result.error
        assert rows[0]['timeline'] and 'private user input' not in response.text and 'private result' not in response.text
        assert (await client.get('/api/run_analysis',params={'research_id':'missing'})).status_code==404
        root=scopes.current(research.scope_id,service.owner);scopes.revoke(root,ref)
        denied=(await client.get('/api/run_analysis',params={'research_id':research.id})).json()
        assert denied['runs']==[] and denied['unavailable_runs']==1
    db.close()


@pytest.mark.asyncio
async def test_expert_runs_are_included_and_hidden_on_revocation(tmp_path):
    from legacy_composition import build_tasks
    db=MemoryDatabase();scopes=build_resources(db,tmp_path/'blobs');service=build_research(db,scopes)
    root=service.create('expert analysis')
    scope=scopes.current(root.scope_id,service.owner)
    _,ref=scopes.ingest(scope,content=b'input',name='source.txt',media_type='text/plain')
    tasks=build_tasks(db,scopes,service,ScriptedEngine(lambda request:([],{'report':'private expert result'})))
    spec=tasks.create(research_id=root.id,kind='extract',user_request='Summarize',required_refs=(ref,))
    await tasks.run(spec.id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service,task_service=tasks)),base_url='http://test') as client:
        response=await client.get('/api/run_analysis',params={'research_id':root.id})
        assert response.status_code==200
        rows=response.json()['runs'];assert len(rows)==1 and rows[0]['kind']=='task'
        assert rows[0]['object_id']==spec.id and 'private expert result' not in response.text
        scopes.revoke(scopes.current(root.scope_id,service.owner),ref)
        value=(await client.get('/api/run_analysis',params={'research_id':root.id})).json()
        assert value['runs']==[] and value['unavailable_runs']==1
    db.close()
