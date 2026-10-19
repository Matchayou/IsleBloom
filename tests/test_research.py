import asyncio
import json
import os
from contextlib import contextmanager
from uuid import uuid4
import pytest
from legacy_composition import build_research
from agent_core.composition import build_resources
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.research.models import NodeCommand, ResearchPlan, PlanStep
from agent_core.research.projection import project
from agent_core.context.scopes import ScopeDenied
from agent_core.execution.ports import Conflict
from agent_core.execution.service import PublicationError
from plan_helpers import approve_plan


def answer(report, missing=()):
    return {'report':report, 'blocking_missing':list(missing)}


@pytest.fixture(params=['memory', 'postgres'])
def app(request, tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('TEST_DATABASE_URL')
        if not url: pytest.skip('TEST_DATABASE_URL required for real PostgreSQL')
        db = PostgresDatabase(url)
    else: db = MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    engine = ScriptedEngine(lambda r: ([], answer(json.loads(r.input.messages[0].content)['goal'])))
    service = build_research(db, scopes, engine)
    research = service.create('研究 ' + str(uuid4()))
    yield service, research, engine
    db.close()


async def command(s, r, kind, node=None, **kw):
    return await s.submit(NodeCommand(command_id=str(uuid4()), research_id=r.id, kind=kind, node_id=node, **kw))


async def result(s, r, goal, parent=None, inputs=()):
    c = await command(s, r, 'branch' if parent else 'create', parent, goal=goal, result_ids=inputs)
    await command(s, r, 'run', c.node_id)
    return s.node(c.node_id)


async def test_branches_versions_compare_layout_idempotency(app):
    s, r, e = app
    root = await result(s, r, 'BASE_V1')
    a = await result(s, r, 'UNIQUE_A', root.id, (root.current_result_id,))
    b = await result(s, r, 'UNIQUE_B', root.id, (root.current_result_id,))
    b_run = s.query('list_runs', b.id)[0]
    b_input = s.scopes.repository.snapshot(b_run.run_id).input.model_dump_json()
    assert 'UNIQUE_A' not in b_input and 'BASE_V1' in b_input
    c = NodeCommand(command_id=str(uuid4()), research_id=r.id, kind='continue', node_id=root.id, goal='BASE_V2')
    first = await s.submit(c)
    assert await s.submit(c) == first
    assert len(s.query('list_results', root.id)) == 2
    assert s.query('inputs', b.current_result_id) == (root.current_result_id,)
    projection = project(s, r.id)
    assert root.id in next(p for p in projection['nodes'] if p.node.id == b.id).upstream_updates
    old = s.node(b.id)
    await command(s, r, 'move', b.id, position=(90, 30))
    moved = s.node(b.id)
    assert moved.revision == old.revision and moved.result_ids == old.result_ids
    comp = await command(s, r, 'compare', goal='比较', result_ids=(a.current_result_id, b.current_result_id))
    v = s.node(comp.node_id).current_result_id
    assert s.query('inputs', v) == (a.current_result_id, b.current_result_id)
    assert s.read_result(root.current_result_id)[1].startswith('BASE_V1')
    with pytest.raises(Conflict): await s.submit(c.model_copy(update={'goal': 'different'}))


async def test_resource_revocation_mixed_excluded_inputs(app):
    s, r, e = app
    scope = s.scopes.current(r.scope_id, s.owner)
    scope, a = s.scopes.ingest(scope, content=b'A marker', name='a.txt', media_type='text/plain')
    scope, b = s.scopes.ingest(scope, content=b'B secret', name='b.txt', media_type='text/plain')
    scope, mix = s.scopes.ingest(scope, content=b'A and B summary', name='mix', media_type='text/plain', kind='note', dependencies=(a,b))
    ca = await command(s,r,'create',goal='A',resource_refs=(a,))
    cb = await command(s,r,'create',goal='B',resource_refs=(b,))
    await command(s,r,'run',ca.node_id)
    await command(s,r,'run',cb.node_id)
    bv = s.node(cb.node_id).current_result_id
    await command(s,r,'exclude',cb.node_id)
    with pytest.raises(ScopeDenied): await command(s,r,'compare',result_ids=(bv,))
    with pytest.raises(ScopeDenied): await command(s,r,'create',resource_refs=(a,b,mix))
    av = s.node(ca.node_id).current_result_id
    s.scopes.revoke(scope,a)
    with pytest.raises(ScopeDenied): s.read_result(av)


async def test_cancel_swallowed_late_result_cannot_publish(app):
    s,r,e = app
    node = await command(s,r,'create',goal='wait')
    entered = asyncio.Event()
    from agent_core.contracts import RunResult
    async def execute(request, spec, tools, invoke):
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: return RunResult(run_id=request.run_id,status='succeeded',output=answer('LATE_SECRET'))
    e.execute = execute
    task = asyncio.create_task(command(s,r,'run',node.node_id))
    await entered.wait()
    await command(s,r,'exclude',node.node_id)
    await task
    assert s.node(node.node_id).current_result_id is None
    run = s.query('list_runs',node.node_id)[0]
    assert s.ledger.get(run.run_id).result.output is None


async def test_atomic_publication_failure_and_save_retry(app):
    s,r,e = app
    n = await command(s,r,'create',goal='atomic')
    original = s.database.transaction
    fail = [True]
    @contextmanager
    def broken():
        with original() as tx:
            old = tx.research.publish
            def publish(value,deps):
                old(value,deps)
                if fail[0]: raise RuntimeError('after result before commit')
            tx.research.publish = publish
            yield tx
    # Proxy avoids storing a patched closure in the memory repository snapshot.
    from types import SimpleNamespace
    @contextmanager
    def broken():
        with original() as tx:
            class Proxy:
                def __getattr__(self,key): return getattr(tx.research,key)
                def publish(self,value,deps):
                    tx.research.publish(value,deps)
                    if fail[0]: raise RuntimeError('after result before commit')
            yield SimpleNamespace(runs=tx.runs,conversations=tx.conversations,research=Proxy(),evidence=tx.evidence,memories=tx.memories)
    s.database.transaction = broken
    with pytest.raises(PublicationError): await command(s,r,'run',n.node_id)
    run = s.query('list_runs',n.node_id)[0]
    assert s.ledger.get(run.run_id).result is None
    assert s.query('list_results',n.node_id) == []
    fail[0] = False
    assert s.retry_save(run.run_id).status == 'completed'
    assert len(s.query('list_results',n.node_id)) == 1
    s.database.transaction = original


def test_plan_cycle():
    with pytest.raises(ValueError): ResearchPlan(research_id='r',steps=(PlanStep(node_id='a',depends_on=('b',)),PlanStep(node_id='b',depends_on=('a',))))


async def test_plan_waves_and_partial(app):
    s,r,e = app
    a = await command(s,r,'create',goal='a')
    b = await command(s,r,'create',goal='b')
    c = await command(s,r,'create',goal='compare')
    plan = ResearchPlan(research_id=r.id,steps=(PlanStep(node_id=a.node_id),PlanStep(node_id=b.node_id),PlanStep(node_id=c.node_id,depends_on=(a.node_id,b.node_id))))
    p = await approve_plan(s, plan)
    assert s.planning.get(p.id).status == 'succeeded'
    first,second,last = [s.node(step.node_id) for step in p.plan.steps]
    current = last.current_result_id
    assert set(s.query('inputs',current)) == {first.current_result_id,second.current_result_id}
    assert (await s.run_plan(p.id)).status == 'succeeded'



async def test_retry_fixed_input_and_queue(app):
    s,r,e=app
    from agent_core.contracts import RunResult, RunError
    inputs=[]
    async def fail(request,*args):
        inputs.append(request.input)
        return RunResult(run_id=request.run_id,status='failed',error=RunError(code='test',origin='engine',message='test'))
    e.execute=fail
    n=await command(s,r,'create',goal='FIXED')
    await command(s,r,'run',n.node_id)
    await command(s,r,'retry',n.node_id)
    assert inputs[0]==inputs[1]
    entered=asyncio.Event();release=asyncio.Event();active=0;maximum=0
    async def slow(request,*args):
        nonlocal active,maximum
        active+=1;maximum=max(maximum,active);entered.set()
        await release.wait();active-=1
        return RunResult(run_id=request.run_id,status='succeeded',output=answer('answer'))
    e.execute=slow
    first=asyncio.create_task(command(s,r,'continue',n.node_id,goal='first'))
    await entered.wait()
    second=asyncio.create_task(command(s,r,'continue',n.node_id,goal='second'))
    await asyncio.sleep(0)
    release.set();await asyncio.gather(first,second)
    assert maximum==1 and len(s.query('list_results',n.node_id))==2
    assert 'first' in s.node(n.node_id).goal and 'second' in s.node(n.node_id).goal


async def test_scope_switch_retry_and_publish_permission(app):
    s,r,e=app
    root=s.scopes.current(r.scope_id,s.owner)
    root,a=s.scopes.ingest(root,content=b'PRIVATE',name='a',media_type='text/plain')
    n=await command(s,r,'create',goal='a',resource_refs=(a,))
    entered=asyncio.Event();release=asyncio.Event()
    from agent_core.contracts import RunResult
    async def slow(request,*args):
        entered.set();await release.wait()
        return RunResult(run_id=request.run_id,status='succeeded',output=answer('PRIVATE'))
    e.execute=slow
    t=asyncio.create_task(command(s,r,'run',n.node_id));await entered.wait()
    s.scopes.revoke(root,a);release.set();await t
    assert s.node(n.node_id).current_result_id is None
    with pytest.raises(ScopeDenied): await command(s,r,'retry',n.node_id)


async def test_company_period_mismatch_rejected_before_model(app):
    s,r,e=app
    root=s.scopes.current(r.scope_id,s.owner);refs=[]
    for company,period in [('A','2025'),('B','2024')]:
        body=json.dumps(dict(record_type='company_financials',company=company,period=period,currency='CNY',unit='万元',basis='合并'))
        root,ref=s.scopes.ingest(root,content=body.encode(),name=company+'.txt',media_type='text/plain');refs.append(ref)
    n=await command(s,r,'create',resource_refs=tuple(refs))
    with pytest.raises(ValueError,match='报告期'): await command(s,r,'run',n.node_id)
    assert s.query('list_runs',n.node_id)==[]


async def test_http_product_loop(app):
    import httpx
    from agent_core.app.api import create_app
    s,r,e=app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(s)),base_url='http://test') as c:
        response=await c.get('/api/projection',params={'research_id':r.id});assert response.status_code==200
        response=await c.post('/api/upload',params={'research_id':r.id,'name':'test.txt'},content=b'HELLO',headers={'Content-Type':'text/plain'})
        assert response.status_code==201 and response.json()['parse']['status']=='ready'
        response=await c.post('/api/command',json=NodeCommand(command_id=str(uuid4()),research_id=r.id,kind='create',goal='HTTP').model_dump(mode='json'))
        assert response.status_code==200
        node=response.json()['node_id']
        response=await c.post('/api/command',json=NodeCommand(command_id=str(uuid4()),research_id=r.id,kind='run',node_id=node).model_dump(mode='json'))
        assert response.status_code in (200,202)
        for _ in range(30):
            await asyncio.sleep(0.01)
            if s.node(node).current_result_id:break
        response=await c.get('/api/result',params={'id':s.node(node).current_result_id});assert response.status_code==200
        assert (await c.get('/api/command')).status_code==405
        assert (await c.post('/api/researches',headers={'Origin':'https://foreign.example'},json={'goal':'foreign'})).status_code==403


async def test_partial_plan_does_not_consume_old_success(app):
    s,r,e=app
    from agent_core.contracts import RunResult,RunError
    a=await result(s,r,'A_OLD');b=await command(s,r,'create',goal='B');c=await command(s,r,'create',goal='summary')
    async def controlled(request,*args):
        if 'A_OLD' in request.input.messages[0].content:return RunResult(run_id=request.run_id,status='failed',error=RunError(code='bad',origin='engine',message='bad'))
        return RunResult(run_id=request.run_id,status='succeeded',output=answer('ok'))
    e.execute=controlled
    p = await approve_plan(s, ResearchPlan(research_id=r.id,steps=(PlanStep(node_id=a.id),PlanStep(node_id=b.node_id),PlanStep(node_id=c.node_id,depends_on=(a.id,b.node_id)))))
    assert s.planning.get(p.id).status == 'blocked'
    assert s.node(p.plan.steps[-1].node_id).current_result_id is None
    assert a.current_result_id is not None  # old success is preserved, never substituted



async def test_two_product_scenarios(app):
    from pathlib import Path
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'evals/stage4_research/scenarios.py'
    spec=importlib.util.spec_from_file_location('stage4_scenarios',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    s,r,e=app
    e.script=mod.scripted
    await mod.brainstorm(s)
    await mod.companies(s,Path(__file__).parent/'fixtures/stage4')


async def test_result_only_branch_has_discovery_without_source_access(app):
    from agent_core.app.research_execution import build_execution
    s,r,e=app
    observed=[]
    def factory(bindings,service):
        execution,catalog=build_execution(s.database,s.scopes,bindings,service,e)
        for key in bindings:
            observed.append(catalog.get(key).tools)
        return execution,catalog
    s.execution_factory=factory
    parent=await result(s,r,'creative baseline')
    branch=await result(s,r,'creative branch',parent.id,(parent.current_result_id,))
    assert branch.current_result_id
    assert len(observed) == 2
    assert all({'search_memory', 'search_tools'} <= {tool.name for tool in tools} for tools in observed)
    assert not s.scopes.current(branch.scope_id, s.owner).allowed
    assert s.query('inputs',branch.current_result_id)==(parent.current_result_id,)


async def test_sdk_read_tool_research_atomic_boundary(app):
    from test_sdk_contract import QueueModel,call,text
    from agent_core.adapters.openai import OpenAIEngine
    from agent_core.app.research_execution import build_execution
    s,r,e=app
    root=s.scopes.current(r.scope_id,s.owner)
    root,a=s.scopes.ingest(root,content=b'ONLY_A_TOOL',name='A',media_type='text/plain')
    root,b=s.scopes.ingest(root,content=b'B_TOOL_SECRET',name='B',media_type='text/plain')
    model=QueueModel([[call(name='read_source',args={'ref':a.model_dump()})],[text(json.dumps(answer('ONLY_A_TOOL')))]])
    s.execution_factory=lambda bindings,service:build_execution(s.database,s.scopes,bindings,service,OpenAIEngine(model))
    n=await command(s,r,'create',goal='read A',resource_refs=(a,))
    c=await command(s,r,'run',n.node_id)
    record=s.ledger.get(c.run_id)
    assert record.status=='succeeded'
    assert 'B_TOOL_SECRET' not in json.dumps(model.inputs)
    assert len(model.inputs)==2
    assert s.query('result',s.node(n.node_id).current_result_id).run_id==record.run_id
    assert record.commit_id


async def test_after_commit_response_loss_is_not_second_execution(app):
    from types import SimpleNamespace
    s,r,e=app
    n=await command(s,r,'create',goal='response loss')
    original=s.database.transaction
    lost=[False]
    @contextmanager
    def transaction():
        published=[False]
        with original() as tx:
            class Proxy:
                def __getattr__(self,key):return getattr(tx.research,key)
                def publish(self,value,deps):
                    tx.research.publish(value,deps);published[0]=True
            yield SimpleNamespace(runs=tx.runs,conversations=tx.conversations,research=Proxy(),evidence=tx.evidence,memories=tx.memories)
        if published[0] and not lost[0]:lost[0]=True;raise ConnectionError('commit ack lost')
    s.database.transaction=transaction
    c=await command(s,r,'run',n.node_id)
    assert c.status=='completed' and len(e.requests)==1
    assert len(s.query('list_results',n.node_id))==1
    s.database.transaction=original


async def test_disabling_research_and_scope_preserves_basic_execution(tmp_path):
    from legacy_composition import build_execution, build_orchestration
    db=MemoryDatabase()
    assert (await build_execution(db).send('basic','assistant','calculate')).status=='completed'
    with pytest.raises(ValueError):build_research(db,None)
    scopes=build_resources(db,tmp_path/'blobs');s=build_research(db,scopes);r=s.create('off')
    scopes.disable(scopes.current(r.scope_id,s.owner))
    with pytest.raises(ScopeDenied):s.research(r.id)
    assert (await build_execution(db).send('another','assistant','calculate')).status=='completed'


async def test_plan_identity_budget_and_resource_replay(app):
    from agent_core.orchestration.models import DispatchPolicy
    s,r,e=app
    scope=s.scopes.current(r.scope_id,s.owner)
    scope,ref=s.scopes.ingest(scope,content=b'fixed material',name='a',media_type='text/plain')
    a=await command(s,r,'create',goal='A',resource_refs=(ref,))
    b=await command(s,r,'create',goal='B')
    plan=ResearchPlan(research_id=r.id,steps=(PlanStep(node_id=a.node_id),PlanStep(node_id=b.node_id,depends_on=(a.node_id,))),policy=DispatchPolicy(max_runs=1))
    with pytest.raises(Conflict): await s.run_plan(plan)
    with pytest.raises(Conflict): await s.planning.import_plan(plan, 'bad-budget')
    assert not e.requests and s.research(r.id).plans == ()



async def test_model_missing_is_partial_and_propagates(app):
    s,r,e=app
    e.script=lambda req:([],answer('Unknown cash flow',('cash_flow',)))
    a=await result(s,r,'a')
    v=s.query('result',a.current_result_id)
    assert v.completion=='partial' and v.missing==('cash_flow',)
    e.script=lambda req:([],answer('comparison'))
    c=await command(s,r,'compare',goal='compare partial',result_ids=(a.current_result_id,))
    v=s.query('result',s.node(c.node_id).current_result_id)
    assert v.completion=='partial' and v.missing==('cash_flow',)
    binding=s.query('list_runs',a.id)[0]
    executor=s.executors[binding.run_id]
    await command(s,r,'exclude',a.id)
    with pytest.raises(ScopeDenied):await executor.execute(s.ledger.get(binding.run_id).request)
