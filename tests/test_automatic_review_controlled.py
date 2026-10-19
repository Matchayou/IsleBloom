"""Real PostgreSQL + SDK + controlled broker; deterministic responses, no live model."""
import os
from uuid import uuid4
from collections import deque
import json
import pytest
from agents import ModelResponse
from agents.usage import Usage as SDKUsage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.research.chat import ChatService


class Provider:
    def __init__(self, verdicts): self.verdicts=deque(verdicts);self.requests=[]
    def get_model(self,name):
        provider=self
        class Model:
            async def get_response(self,**kwargs):
                provider.requests.append(kwargs)
                schema=kwargs.get('output_schema')
                reviewing=schema is not None and schema.name()=='ReviewAdvice'
                if reviewing:
                    verdict=provider.verdicts.popleft()
                    value={'verdict':verdict,'summary':'Fixed candidate inspected.', 'issues':[] if verdict=='passed' else [
                        {'problem':'Missing limitation','evidence':'The candidate lacks the limitation.','instruction':'Include that the source is synthetic.'}]}
                else:value={'report':'Synthetic source revenue 100; not real market evidence.','limitations':['Synthetic fixture.']}
                return ModelResponse(output=[ResponseOutputMessage(id=uuid4().hex,type='message',role='assistant',status='completed',
                    content=[ResponseOutputText(type='output_text',text=json.dumps(value),annotations=[])])],
                    usage=SDKUsage(requests=1,input_tokens=17,output_tokens=11),response_id=None)
        return Model()


@pytest.fixture
def controlled(tmp_path):
    url=os.environ.get('AUTO_REVIEW_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated AUTO_REVIEW_TEST_DATABASE_URL required')
    from sqlalchemy.engine import make_url
    assert make_url(url).database.startswith('auto_review_')
    db=PostgresDatabase(url);scopes=build_resources(db,tmp_path/'blobs');provider=Provider(['needs_revision','passed'])
    research,tasks,projects=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider)
    yield db,scopes,research,tasks,projects,research.collaboration,provider
    db.close()


async def seed(controlled):
    _,scopes,research,tasks,projects,c,provider=controlled
    project=projects.create('Automatic '+uuid4().hex)
    main=await projects.create_main_task(project.id,'Controlled cooperation')
    node=research.query('list_nodes',main.research_id)[0]
    _,ref=scopes.ingest(scopes.current(research.research(main.research_id).scope_id,research.owner),
        content=b'Synthetic revenue is 100.',name='sample.txt',media_type='text/plain')
    ChatService(research,tasks).send(node.id,'Summarize with source limitations.',0)
    intent=c.delegate_task(command_id=uuid4().hex,node_id=node.id,expert_id='expert_synthesize',
        task_instruction='Summarize with source limitations.',delivery_requirements='State limitations.',resource_refs=(ref,))
    return intent


async def test_real_broker_revision_and_confirmed_recovery(controlled):
    db,_,research,tasks,projects,c,provider=controlled
    intent=await seed(controlled)
    result=await c.execute(intent.command_id)
    assert result['intent'].status=='completed',result
    loop=c.review.get(intent.task_id)
    assert loop.status=='passed' and loop.revisions_completed==1,loop
    assert len(provider.requests)==4
    assert c.budget_status(loop.research_id)['actual_model_requests']==4
    for round in loop.rounds:
        record=tasks.ledger.get(round.review_run_id)
        assert record.status=='succeeded' and record.result.usage.complete
        assert tasks.engine.ledger.usage(round.review_run_id).requests==1
        snapshot=research.scopes.repository.snapshot(round.review_run_id)
        assert snapshot.mode=='discussion'
        assert round.result_id in json.dumps(snapshot.input.model_dump())
        assert not {'write_code','revise_task','adopt_result'} & {tool.name for tool in provider.requests[loop.rounds.index(round)*2+1]['tools']}
    # Reconstruct service-local locks/state; committed evidence remains authoritative.
    from agent_core.composition import build_collaboration
    recovered=build_collaboration(db,research,tasks,projects)
    await recovered.execute(intent.command_id)
    assert len(provider.requests)==4


async def test_confirmed_review_survives_database_reopen_before_session_save(controlled,monkeypatch):
    from agent_core.adapters.postgres.collaboration import PostgresCollaboration
    db,scopes,research,tasks,projects,c,provider=controlled
    intent=await seed(controlled)
    original=PostgresCollaboration.save_session
    crashed=False
    class Crash(BaseException):pass
    def save_session(self,value,expected):
        nonlocal crashed
        if not crashed and value.messages and value.messages[-1].id.startswith('expert-reply:automatic-review:'):
            crashed=True;raise Crash()
        return original(self,value,expected)
    monkeypatch.setattr(PostgresCollaboration,'save_session',save_session)
    with pytest.raises(Crash):await c.execute(intent.command_id)
    assert len(provider.requests)==2 and not c.review.get(intent.task_id).rounds
    url=str(db.engine.url.render_as_string(hide_password=False))
    private=tasks.engine.private_root
    db.close()
    fresh=PostgresDatabase(url)
    try:
        restored_scopes=build_resources(fresh,private.parent/'blobs')
        restored_research,restored_tasks,restored_projects=build_controlled_services(fresh,restored_scopes,
            private_root=private,model_provider=provider)
        restored=restored_research.collaboration
        result=await restored.execute(intent.command_id)
        assert result['intent'].status=='completed',result
        loop=restored.review.get(intent.task_id)
        assert loop.status=='passed' and loop.revisions_completed==1
        assert len(provider.requests)==4  # The already-confirmed review was not called again.
        await restored.execute(intent.command_id)
        assert len(provider.requests)==4
    finally:fresh.close()


async def test_existing_dbos_queue_and_activity_use_latest_revision(controlled):
    import httpx
    from agent_core.durable import DurableResearch
    from agent_core.app.api import create_app
    db,_,research,tasks,projects,c,provider=controlled
    intent=await seed(controlled)
    url=db.engine.url.render_as_string(hide_password=False)
    durable=DurableResearch(tasks,url)
    try:
        await durable.start();c.durable=durable
        result=await c.execute(intent.command_id)
        assert result['intent'].status=='completed',result
        loop=c.review.get(intent.task_id)
        assert loop.status=='passed' and loop.revisions_completed==1
        assert len(provider.requests)==4
        app=create_app(research,task_service=tasks,durable=durable)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.get('/api/task_activity',params={'research_id':loop.research_id})
            assert response.status_code==200,response.text
            executor=next(row for row in response.json() if row['spec']['id']==loop.task_id)
            assert executor['job_status']=='SUCCESS'
            assert executor['automatic_review']['status']=='passed'
        await c.execute(intent.command_id)
        assert len(provider.requests)==4
    finally:c.durable=None;durable.close()


async def test_unknown_review_usage_is_visible_and_never_retried(controlled):
    import httpx
    from agent_core.app.api import create_app
    from uuid import NAMESPACE_URL,uuid5
    _,_,research,tasks,_,c,provider=controlled
    intent=await seed(controlled)
    get_model=provider.get_model
    def interrupted_model(name):
        model=get_model(name)
        class Model:
            async def get_response(self,**kwargs):
                schema=kwargs.get('output_schema')
                if schema is not None and schema.name()=='ReviewAdvice':
                    provider.requests.append(kwargs)
                    raise RuntimeError('Synthetic unconfirmed provider outcome')
                return await model.get_response(**kwargs)
        return Model()
    provider.get_model=interrupted_model
    await c.execute(intent.command_id)
    loop=c.review.get(intent.task_id)
    assert loop.status=='failed' and loop.revisions_completed==0
    run_id=str(uuid5(NAMESPACE_URL,'agent-review:automatic-review:'+intent.task_id+':v1'))
    assert not tasks.engine.ledger.usage(run_id).complete
    assert len(provider.requests)==2
    await c.execute(intent.command_id)
    assert len(provider.requests)==2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        response=await client.get('/api/run_analysis',params={'research_id':loop.research_id})
        assert response.status_code==200,response.text
        rows=response.json()['runs']
        reviewer=next(row for row in rows if row['run_id']==run_id)
        assert not reviewer['usage']['complete'] and reviewer['cost'] is None
