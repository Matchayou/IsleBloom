"""CE4 real PostgreSQL and SDK: bounded investigation and immutable publication."""
import asyncio
import json
import os
from uuid import uuid4
import httpx
import pytest
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.contracts import RunLimits
from agent_core.context.scopes import ScopeDenied
from agent_core.tasks.artifacts import describe
from test_controlled_collaboration import Provider, answer, call, seed


def test_plan_tokens_reserve_delivery_before_turn_time_or_tool_limit():
    from datetime import datetime,timedelta,timezone
    from types import SimpleNamespace
    from agent_core.contracts import Usage
    from agent_core.controlled.progress import ProgressPolicy
    state={'phase':'researching'}
    ledger=SimpleNamespace(
        run_budget=lambda _:dict(models=1,tools=1,deadline=datetime.now(timezone.utc)+timedelta(seconds=590)),
        progress=lambda _:state,
        input_observation=lambda _:dict(input_tokens=4437),
        plan_usage=lambda _:Usage(input_tokens=125752,output_tokens=4643),
        task_model_durations=lambda _:(),
        set_phase=lambda _,phase,reason:state.update(phase=phase,reason=reason))
    request=SimpleNamespace(run_id='third',limits=RunLimits(max_turns=8,max_calls=6,timeout_seconds=600))
    policy=ProgressPolicy(ledger,request,plan_budget={'node_ids':('first','second','third'),'max_tokens':162249})
    assert policy.prepare_model()=={'phase':'finalizing','reason':'reserved_tokens'}

@pytest.fixture
def controlled(tmp_path):
    url=os.environ.get('CE4_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated CE4 PostgreSQL required')
    assert make_url(url).database.startswith('ce4_')
    db=PostgresDatabase(url);provider=Provider();scopes=build_resources(db,tmp_path/'blobs')
    research,tasks,projects=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider)
    yield db,scopes,research,tasks,projects,research.collaboration,provider
    db.close()

async def task(controlled,**limits):
    node,ref,_=await seed(controlled)
    tasks=controlled[3]
    spec=tasks.create(research_id=node.research_id,kind='synthesize',user_request='Review count and preserve unknowns',
        required_refs=(ref,),limits=RunLimits(**limits))
    return spec,ref

async def test_reserved_turns_close_tools_and_bound_format_repair(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,ref=await task(controlled,max_turns=4,max_calls=8)
    provider.plans.append([call('read_source',ref=ref.model_dump()),call('read_source',ref=ref.model_dump()),
        answer('{'),answer({'report':'Count 7; market size unknown.'})])
    receipt=(await tasks.run(spec.id))['receipt'];record=tasks.ledger.get(receipt.run_id)
    assert record.result.status=='succeeded' and record.result.usage.requests==4
    assert len(record.result.output_repairs)==1
    assert provider.requests[0]['tools'] and provider.requests[1]['tools']
    assert all(not r['tools'] and r['model_settings'].tool_choice=='none' and 'finalizing' in r['system_instructions'] for r in provider.requests[2:])
    state=tasks.engine.ledger.progress(receipt.run_id)
    assert state['transitions']==['researching','finalizing','delivered']
    assert state['finalization_reason']=='reserved_turns'
    assert receipt.delivery=='pending_review' and receipt.adoption=='for_discussion'

async def test_batched_calls_stop_at_actual_tool_budget(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,ref=await task(controlled,max_turns=5,max_calls=1)
    # Both calls target an initially disclosed tool; the second call must be
    # closed by the budget, rather than rejected for an undisclosed schema.
    provider.plans.append([call('read_source',ref=ref.model_dump())+call('read_source',ref=ref.model_dump()),
        answer({'report':'Only confirmed source; missing extra investigation.'})])
    receipt=(await tasks.run(spec.id))['receipt']
    assert receipt.artifact and tasks.engine.ledger.run_budget(receipt.run_id)['tools']==1
    assert 'investigation_closed' in json.dumps(provider.requests[-1]['input'])
    assert not provider.requests[-1]['tools']
    from agent_core.controlled.workspace import Workspace
    work=Workspace(tasks.engine.private_root/tasks.engine.workspace_id(tasks.engine.ledger.binding(receipt.run_id)))
    try:
        with pytest.raises(ValueError):work.read('forbidden')
    finally:work.close()

async def test_repeated_reads_finalize_but_new_ranges_and_waits_do_not(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,ref=await task(controlled,max_turns=10,max_calls=9)
    provider.plans.append([*[call('read_source',ref=ref.model_dump()) for _ in range(4)],answer({'report':'Count 7; unknown size.'})])
    receipt=(await tasks.run(spec.id))['receipt'];state=tasks.engine.ledger.progress(receipt.run_id)
    assert state['finalization_reason']=='no_new_information'
    assert 'repeat_without_new_information' in json.dumps(provider.requests[2]['input'])
    assert not provider.requests[-1]['tools']
    # Exercise the same persistent detector with distinct result versions/ranges and legitimate waits.
    other,ref=await task(controlled,max_turns=10,max_calls=9)
    async def inspect(kwargs):
        run=tasks.query('list_runs',other.id)[0]
        from agent_core.controlled.progress import ProgressPolicy
        policy=ProgressPolicy(tasks.engine.ledger,tasks.ledger.get(run.id).request)
        for offset in range(5):
            assert policy.after_tool('read_source',{'offset':offset},{'version':'v1','text':str(offset)})['repeat_streak']==0
        for _ in range(5):
            assert policy.after_tool('get_task_status',{}, {'status':'waiting'})['phase']=='researching'
        assert policy.after_tool('read_source',{'offset':4},{'version':'v2','text':'changed'})['repeat_streak']==0
        return answer({'report':'New evidence and waiting are not loops.'})
    provider.plans.append([inspect]);assert (await tasks.run(other.id))['receipt'].artifact

async def test_model_timeout_uses_reserved_budget_and_keeps_unknown_usage(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,_=await task(controlled,max_turns=4,max_calls=5,timeout_seconds=4)
    async def hang(_):await asyncio.Event().wait()
    provider.plans.append([hang,answer({'report':'Evidence insufficient; investigation timed out.', 'missing':['source verification']})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert result.status=='succeeded',result
    assert len(provider.requests)==2 and not provider.requests[-1]['tools']
    assert tasks.engine.ledger.run_budget(receipt.run_id)['models']==2 and not result.usage.complete
    assert tasks.engine.ledger.progress(receipt.run_id)['finalization_reason']=='investigation_timeout'

async def test_artifact_api_hash_review_failures_and_save_retry(controlled,monkeypatch):
    db,scopes,research,tasks,_,_,provider=controlled
    spec,ref=await task(controlled)
    report='Confirmed count 7; size unknown. '+('Evidence detail. '*300)
    provider.plans.append([answer({'report':report})])
    receipt=(await tasks.run(spec.id))['receipt']
    artifact=tasks.read_artifact(receipt.result_id)
    assert artifact['artifact']==describe(receipt.result_id,artifact['output'])
    assert receipt.artifact.brief==report[:1200] and receipt.artifact.brief_truncated
    with pytest.raises(ValueError):tasks.review(receipt.result_id,{})
    from agent_core.adapters.postgres.tasks import PostgresTasks
    original=PostgresTasks.save_receipt
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise TimeoutError('Lost reviewer transaction')
    with monkeypatch.context() as m:
        m.setattr(PostgresTasks,'save_receipt',fail)
        with pytest.raises(TimeoutError):tasks.review(receipt.result_id,{i.number:('passed','fixture evidence') for i in spec.acceptance})
    assert tasks.receipt(receipt.result_id)==receipt
    tasks.executors.clear()
    assert tasks.retry_save(receipt.run_id).status=='completed' and len(provider.requests)==1
    from agent_core.app.api import create_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        activity=(await client.get('/api/task_activity',params={'research_id':spec.research_id})).json()
        row=next(r for r in activity if r['spec']['id']==spec.id)['runs'][0]
        assert 'output' not in row and row['artifact']['sha256']==receipt.artifact.sha256
        response=await client.get('/api/task_artifact',params={'id':receipt.result_id})
        assert response.status_code==200 and response.json()['output']['report']==report
        root=scopes.current(research.research(spec.research_id).scope_id,research.owner);scopes.revoke(root,ref)
        assert (await client.get('/api/task_artifact',params={'id':receipt.result_id})).status_code==403

async def test_tool_timeout_leaves_uncertain_operation_and_finalizes(controlled,monkeypatch):
    tasks,provider=controlled[3],controlled[-1]
    spec,_=await task(controlled,max_turns=5,max_calls=5,timeout_seconds=4)
    from agent_core.controlled.workspace import Workspace
    from agent_core.contracts import ToolBinding,ToolResult,Value
    class Args(Value):name:str;text:str
    async def hang(_):await asyncio.Event().wait()
    monkeypatch.setattr(Workspace,'bindings',lambda self:(ToolBinding('write_private_note','Review count and preserve unknowns; fixture stalled private write',Args,hang),))
    provider.plans.append([call('write_private_note',name='draft',text='unknown'),answer({'report':'Tool completion unconfirmed; evidence insufficient.'})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert result.status=='succeeded',result
    assert 'investigation_timeout' in json.dumps(provider.requests[-1]['input']) and not provider.requests[-1]['tools']
    from sqlalchemy import select
    ledger=tasks.engine.ledger
    with controlled[0].transaction():
        states=controlled[0].connection.execute(select(ledger.operations.c.status).where(
            ledger.operations.c.run_id==receipt.run_id,ledger.operations.c.kind=='private')).scalars().all()
    assert states==['unconfirmed']

async def test_failed_final_format_is_not_an_artifact(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,_=await task(controlled,max_turns=2,max_calls=0)
    provider.plans.append([answer('{'),answer('{')])
    receipt=(await tasks.run(spec.id))['receipt']
    assert receipt.artifact is None and receipt.delivery=='rework'
    assert tasks.engine.ledger.progress(receipt.run_id)['phase']=='incomplete'
    assert len(provider.requests)==2 and all(not r['tools'] for r in provider.requests)

async def test_lost_save_response_is_retried_without_model(controlled,monkeypatch):
    tasks,provider=controlled[3],controlled[-1]
    spec,_=await task(controlled)
    provider.plans.append([answer({'report':'Saved exact output before response loss.'})])
    original=tasks.ledger.commit
    lost=[]
    def commit_then_lose(commit):
        value=original(commit)
        if not lost:
            lost.append(True)
            raise TimeoutError('Commit saved but response lost')
        return value
    monkeypatch.setattr(tasks.ledger,'commit',commit_then_lose)
    await tasks.run(spec.id)
    run=tasks.query('list_runs',spec.id)[0]
    before=tasks.read_artifact('task-result:'+run.id)
    tasks.executors.clear()
    assert tasks.retry_save(run.id).status=='completed'
    assert tasks.read_artifact('task-result:'+run.id)==before and len(provider.requests)==1

async def test_revocation_during_finalization_prevents_publication(controlled):
    _,scopes,research,tasks,_,_,provider=controlled
    spec,ref=await task(controlled,max_turns=2,max_calls=0)
    entered=asyncio.Event()
    async def paused(kwargs):
        assert not kwargs['tools'];entered.set();await asyncio.Event().wait()
    provider.plans.append([paused]);running=asyncio.create_task(tasks.run(spec.id))
    await asyncio.wait_for(entered.wait(),5)
    root=scopes.current(research.research(spec.research_id).scope_id,research.owner);scopes.revoke(root,ref)
    await asyncio.wait_for(running,5)
    run=tasks.query('list_runs',spec.id)[0]
    assert tasks.ledger.get(run.id).result.output is None
    assert tasks.engine.ledger.progress(run.id)['phase']=='incomplete'
    assert tasks.query('receipt','task-result:'+run.id).artifact is None

async def test_real_dsml_failure_shape_is_corrected_without_executing_markup(controlled):
    tasks,provider=controlled[3],controlled[-1]
    node,ref,_=await seed(controlled)
    spec=tasks.create(research_id=node.research_id,kind='synthesize',model='deepseek-v4-pro',
        user_request='Compare using the approved source only',required_refs=(ref,),
        limits=RunLimits(max_turns=4,max_calls=1,timeout_seconds=180))
    leaked=('已取得 ReAct 论文摘要页正文。现在检索官方文档。\n'
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="search_web">'
        '<｜｜DSML｜｜parameter name="query" string="true">official documentation</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>')
    provider.plans.append([call('read_source',ref=ref.model_dump()),answer(leaked),
        answer({'report':'Only saved evidence; workflow comparison unavailable.', 'missing':['official workflow source']})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert result.status=='succeeded'
    assert result.output_repairs[0].issues[0]['type']=='model_protocol'
    assert tasks.engine.ledger.run_budget(receipt.run_id)['tools']==1
    assert provider.requests[0]['model_settings'].reasoning.effort=='low'
    for request in provider.requests[1:]:
        assert request['tools']==[] and request['model_settings'].tool_choice=='none'
        assert request['model_settings'].reasoning.effort=='none'
        assert 'finalizing' in request['system_instructions']

async def test_delivery_reserve_scales_and_uses_only_task_latency(controlled):
    tasks,provider=controlled[3],controlled[-1]
    spec,_=await task(controlled,max_turns=8,max_calls=6,timeout_seconds=180)
    async def inspect(kwargs):
        from agent_core.controlled.progress import ProgressPolicy
        run=tasks.query('list_runs',spec.id)[0];ledger=tasks.engine.ledger
        policy=ProgressPolicy(ledger,tasks.ledger.get(run.id).request)
        assert policy.reserve_seconds==90
        ledger.save_manifest(run.id,'observed-task','responded',{'purpose':'task','duration_seconds':62})
        ledger.save_manifest(run.id,'observed-summary','responded',{'purpose':'summary','duration_seconds':999})
        assert policy.reserve_seconds==124
        return answer({'report':'Budget observation verified.'})
    provider.plans.append([inspect])
    assert (await tasks.run(spec.id))['receipt'].artifact
