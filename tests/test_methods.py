"""Stage 8 release, isolation, compilation and prior-stage integration contracts."""
import asyncio
import json
from uuid import uuid4
import pytest
import httpx
from test_tasks import app, create_extract
from agent_core.execution.ports import Conflict
from agent_core.methods.models import AssetVersion, WorkflowBody
from agent_core.research.models import NodeCommand, ResearchPlan, PlanStep
from agent_core.context.scopes import ScopeDenied
from agent_core.app.api import create_app
from plan_helpers import approve_plan, approve_workflow


def save_method(app, tools=('read_source','search_source')):
    methods=app[-1].methods
    original=methods.resolve('method','expert_extract','extract-v3')
    version='extract-'+str(uuid4())
    value=methods.save(AssetVersion(**{**original.model_dump(), 'version':version,
        'body':{**original.body,'instructions':'STAGE8_NEW_METHOD: Locate the unit before extracting.', 'tools':tools},
        'evidence':'Fixed control fixture; no semantic quality claim'}))
    return value


def publish(methods, value):
    return methods.publish(value.category,value.asset_id,value.version,
        methods.query('head',value.category,value.asset_id),'test-reviewer','Controlled review')


def workflow(app, *, tools=('read_source','search_source')):
    methods=app[-1].methods
    value=methods.save(AssetVersion(category='workflow',asset_id='comparison-'+str(uuid4()),version='v1',
        description='Reusable company comparison',author='test-reviewer',evidence='Equivalent manual plan fixture',sanitized=True,
        body=dict(parameters={'industry':'text','company':'text','report':'resource'},tools=tools,steps=[
            dict(key='company',goal='{industry}: research {company}',expert_id='expert_synthesize',method_version='synthesize-v3',source_parameters=['report']),
            dict(key='compare',goal='Compare {company}',expert_id='expert_analyze',method_version='analyze-v3',depends_on=['company'])])))
    publish(methods,value)
    return value


def compile_args(app,value):
    return dict(instance_id=str(uuid4()),asset_id=value.asset_id,version=value.version,
        research_id=app[3].id,parameters=dict(industry='new industry',company='NEW_COMPANY',report=app[5].model_dump(mode='json')))


async def test_publish_rollback_and_fixed_task_execution(app):
    *_,engine,tasks=app
    original=tasks.methods.resolve('method','expert_extract')
    before=create_extract(app)
    value=save_method(app)
    publish(tasks.methods,value)
    new=create_extract(app)
    assert new.method_version==value.version and before.method_version==original.version
    assert new.acceptance==before.acceptance
    assert (await tasks.run(before.id))['receipt'].method_version==original.version
    assert (await tasks.run(new.id))['receipt'].method_version==value.version
    publish(tasks.methods,original)
    rollback=create_extract(app)
    assert rollback.method_version==original.version
    assert tasks.methods.resolve('method','expert_extract',value.version)==value
    assert 'STAGE8_NEW_METHOD' not in tasks.methods.method(before.expert_id,before.method_version)[1].prompt


async def test_publish_while_inflight_and_restart_binding(app):
    *_,engine,tasks=app
    started,release=asyncio.Event(),asyncio.Event()
    old_execute=engine.execute
    async def delayed(*args):
        started.set();await release.wait();return await old_execute(*args)
    engine.execute=delayed
    spec=create_extract(app)
    running=asyncio.create_task(tasks.run(spec.id))
    await asyncio.wait_for(started.wait(),5)
    original=tasks.methods.resolve('method','expert_extract')
    value=save_method(app);publish(tasks.methods,value)
    release.set()
    receipt=(await running)['receipt']
    assert receipt.method_version==spec.method_version
    publish(tasks.methods,original)


def test_immutable_versions_and_compare_swap(app):
    methods=app[-1].methods
    value=save_method(app)
    assert methods.save(value)==value
    with pytest.raises(Conflict): methods.save({**value.model_dump(),'description':'mutated'})
    with pytest.raises(Conflict): methods.method(value.asset_id,value.version)
    original=methods.resolve('method',value.asset_id)
    publish(methods,value)
    history=methods.query('history')
    history[-1]['reason']='Attempted mutation of returned history'
    assert methods.query('history')[-1]['reason']=='Controlled review'
    with pytest.raises(Conflict): methods.publish('method',value.asset_id,original.version,original.version,'reviewer','stale page')
    publish(methods,original)


async def test_missing_parameters_cycles_and_no_partial_compile(app):
    methods=app[-1].methods;value=workflow(app);args=compile_args(app,value)
    count=len(app[2].query('list_nodes',app[3].id))
    for params in ({},dict(args['parameters'],company=3),dict(args['parameters'],extra='forbidden')):
        with pytest.raises(ValueError): await methods.instantiate(**{**args,'parameters':params})
    assert len(app[2].query('list_nodes',app[3].id))==count
    for goal in ('{company.__class__}','{company!r}','{company:>10}','{missing}'):
        body=json.loads(json.dumps(value.body));body['steps'][0]['goal']=goal
        with pytest.raises(ValueError): WorkflowBody.model_validate(body)
    body=json.loads(json.dumps(value.body));body['steps'][0]['depends_on']=['compare']
    with pytest.raises(ValueError): WorkflowBody.model_validate(body)


async def test_compile_idempotent_new_inputs_and_manual_equivalence(app):
    _,_,research,root,_,_,engine,tasks=app
    methods=tasks.methods;value=workflow(app);args=compile_args(app,value)
    # Compare actual parameterized goals, source bodies and upstream outputs; omit random IDs.
    _,new_ref=app[1].ingest(app[1].current(root.scope_id,'local'),content=b'NEW_RESOURCE_CONTROL 2026 CFO 300',name='new-company.txt',media_type='text/plain')
    args['parameters']['report']=new_ref.model_dump(mode='json')
    def business_script(request):
        payload=json.loads(request.input.messages[0].content)
        content=[f['text'] for source in payload['content'] for f in source['fragments']]
        assert 'A 2025 CFO 100' not in json.dumps(content)
        return [],{'report':json.dumps(dict(goal=payload['goal'],content=content,upstream=[r['output'] for r in payload.get('results',[])]),sort_keys=True),'blocking_missing':[]}
    engine.script=business_script
    research.execution_factory=__import__('functools').partial(__import__('agent_core.app.research_execution',fromlist=['build_execution']).build_execution,app[0],app[1],engine=engine)
    compiled=await methods.instantiate(**args)
    assert await methods.instantiate(**args)==compiled
    with pytest.raises(Conflict): await methods.instantiate(**{**args,'parameters':dict(args['parameters'],company='OTHER')})
    assert 'NEW_COMPANY' not in json.dumps(value.body)
    assert app[5].resource_id not in json.dumps(value.body)
    manual_nodes=[]
    for planned in compiled['plan']['steps']:
        node=research.node(planned['node_id'])
        assert 'NEW_COMPANY' in node.goal
        manual=await research.submit(NodeCommand(command_id=str(uuid4()),research_id=root.id,kind='create',
            goal=node.goal,instructions=node.instructions,resource_refs=node.resource_refs,allowed_tools=node.allowed_tools))
        manual_nodes.append(manual.node_id)
    manual_plan=ResearchPlan(research_id=root.id,steps=(PlanStep(node_id=manual_nodes[0]),PlanStep(node_id=manual_nodes[1],depends_on=(manual_nodes[0],))))
    approved = await approve_workflow(methods,compiled['id'])
    manual = await approve_plan(research,manual_plan)
    for planned,step in zip(approved.plan.steps,manual.plan.steps):
        assert research.read_result(research.node(planned.node_id).current_result_id)[1]==research.read_result(research.node(step.node_id).current_result_id)[1]
    prior_calls=len(engine.requests)
    await approve_workflow(methods,compiled['id'])
    assert len(engine.requests)==prior_calls


async def test_workflow_and_method_permission_intersection(app):
    _,scopes,research,root,scope,ref,engine,tasks=app
    methods=tasks.methods;value=workflow(app,tools=())
    compiled=await methods.instantiate(**compile_args(app,value))
    for step in compiled['plan']['steps']:
        assert scopes.current(research.node(step['node_id']).scope_id,'local').tools==()
    original=methods.resolve('method','expert_extract')
    narrow=save_method(app,tools=());publish(methods,narrow)
    spec=create_extract(app)
    binding,_,scope=tasks._prepare(spec)
    assert scope.tools==()
    publish(methods,original)
    scopes.revoke(scopes.current(root.scope_id,'local'),ref)
    with pytest.raises(ScopeDenied): await methods.propose(compiled['id'])
    with pytest.raises(ScopeDenied): methods.instance(compiled['id'])
    assert not engine.requests


async def test_workflow_versions_and_node_tampering(app):
    methods=app[-1].methods;value=workflow(app);args=compile_args(app,value)
    old=await methods.instantiate(**args)
    new=methods.save({**value.model_dump(),'version':'v2','description':'Revised workflow'})
    publish(methods,new)
    assert methods.instance(old['id'])['version']=='v1'
    publish(methods,value)
    node=app[2].node(old['plan']['steps'][0]['node_id'])
    await app[2].submit(NodeCommand(command_id=str(uuid4()),research_id=app[3].id,node_id=node.id,kind='mount',resource_refs=()))
    with pytest.raises(Conflict): await methods.propose(old['id'])


async def test_asset_http_entry_and_cross_origin(app):
    tasks=app[-1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(app[2],task_service=tasks)),base_url='http://test') as client:
        assert (await client.get('/api/assets')).status_code==200
        value=save_method(app)
        body=dict(category=value.category,asset_id=value.asset_id,version=value.version,
                  expected=tasks.methods.query('head',value.category,value.asset_id),actor='operator',reason='Browser fixture')
        assert (await client.post('/api/asset_publish',json=body,headers={'origin':'http://foreign'})).status_code==403
        assert (await client.get('/api/asset_publish')).status_code==405
        assert (await client.post('/api/asset_publish',json=body)).status_code==200
        tasks.methods.publish('method',value.asset_id,'extract-v3',value.version,'operator','restore test baseline')


async def test_routellm_method_publication_keeps_continuation(app):
    from legacy_composition import build_tasks
    from agent_core.routing.service import Router
    from test_routing import candidates, RouteFixture
    experts,models=candidates()
    tasks=build_tasks(app[0],app[1],app[2],app[-2],Router(experts,models,RouteFixture(['model-a','model-b']),catalog_version='stage8-fixed'))
    args=dict(research_id=app[3].id,kind='extract',user_request='Extract',required_refs=(app[5],),approved_summary='Extract approved fields',budget_usd=1)
    first=await tasks.create_routed(**args)
    original=tasks.methods.resolve('method','expert_extract')
    value=save_method(app);publish(tasks.methods,value)
    future=await tasks.create_routed(**args)
    resumed=await tasks.create_routed(**args,task_id=first.id)
    assert future.method_version==value.version
    assert resumed.method_version==first.method_version and resumed.model==first.model
    assert tasks.query('route',resumed.route_id).expert_config.method_version==first.method_version
    publish(tasks.methods,original)


async def test_routing_policy_version_is_separate_and_frozen(app):
    from pathlib import Path
    methods=app[-1].methods
    body=json.loads(Path('config/deepseek-flash.json').read_text())
    version='policy-'+str(uuid4());body['version']=version
    value=methods.save(AssetVersion(category='routing',asset_id='model-policy',version=version,
        description='Frozen single-model policy fixture',body=body,author='test',evidence='Control fixture',sanitized=True))
    publish(methods,value)
    args=dict(research_id=app[3].id,kind='extract',user_request='Extract',required_refs=(app[5],),approved_summary='Extract fields',budget_usd=1)
    first=await app[-1].create_routed(**args)
    assert first.routing_version==version
    assert first.method_version==methods.resolve('method','expert_extract').version
    next_body={**body,'version':version+'-next'}
    revised=methods.save({**value.model_dump(),'version':next_body['version'],'body':next_body})
    publish(methods,revised)
    next_task=await app[-1].create_routed(**args)
    resumed=await app[-1].create_routed(**args,task_id=first.id)
    assert next_task.routing_version==revised.version and resumed.routing_version==first.routing_version
    # Remove this test's global policy pointer so other tests retain their explicit catalog.
    with app[0].transaction() as tx:
        if hasattr(tx.methods,'heads'): tx.methods.heads.pop(('routing','model-policy'))
        else:
            from sqlalchemy import delete
            table=tx.methods.t['heads']
            tx.methods.c.execute(delete(table).where(table.c.category=='routing',table.c.asset_id=='model-policy'))


async def test_stage7_feedback_binds_published_method(app):
    from agent_core.composition import build_evaluation
    tasks=app[-1];original=tasks.methods.resolve('method','expert_extract')
    value=save_method(app);publish(tasks.methods,value)
    receipt=(await tasks.run(create_extract(app).id))['receipt']
    evaluation=build_evaluation(app[0],tasks)
    feedback=evaluation.submit(feedback_id=str(uuid4()),result_id=receipt.result_id,
        kind='fact_error',description='Control feedback',proposed_correction='Review the unit')
    assert feedback.method_version==value.version and feedback.method_digest==receipt.method_digest
    publish(tasks.methods,original)


def test_template_rejects_insufficient_budget(app):
    from pathlib import Path
    value=json.loads(Path('config/three-company-workflow.json').read_text())
    value['body']['policy']['max_runs']=3
    with pytest.raises(ValueError,match='budget'):
        app[-1].methods.save(value)


async def test_compiled_plan_rechecks_current_tool_permission(app):
    methods=app[-1].methods;value=workflow(app)
    compiled=await methods.instantiate(**compile_args(app,value))
    scopes=app[1]
    scope=scopes.current(app[3].scope_id,'local')
    scopes._replace(scope,tools=())
    with pytest.raises(PermissionError,match='tool permission'):
        await methods.propose(compiled['id'])
    assert not app[-2].requests


async def test_template_step_budget_reaches_executor_and_is_enforced(app):
    from functools import partial
    from agent_core.app.research_execution import build_execution
    db,scopes,research,_,_,_,engine,tasks=app
    research.execution_factory=partial(build_execution,db,scopes,engine=engine)
    original=engine.execute
    async def delayed(request,*args):
        if request.limits.timeout_seconds < 1:
            await asyncio.sleep(.2)
        return await original(request,*args)
    engine.execute=delayed
    value=workflow(app)
    payload=value.model_dump(mode='json');payload['version']='bounded-v2'
    payload['body']['steps'][0]['limits']={'timeout_seconds':.01,'max_turns':2,'max_calls':1}
    value=tasks.methods.save(payload);publish(tasks.methods,value)
    compiled=await tasks.methods.instantiate(**compile_args(app,value))
    approved = await approve_workflow(tasks.methods,compiled['id'])
    first=approved.plan.steps[0].model_dump()
    run=research.query('list_runs',first['node_id'])[0]
    record=research.ledger.get(run.run_id)
    assert record.request.limits.timeout_seconds==.01
    assert record.request.limits.max_turns==2 and record.request.limits.max_calls==1
    assert record.status=='failed' and record.result.error.code=='timeout'
    assert research.node(first['node_id']).current_result_id is None


async def test_bundled_three_company_workflow_uses_current_assets(app):
    from functools import partial
    from pathlib import Path
    from agent_core.app.research_execution import build_execution
    db,scopes,research,root,_,_,engine,tasks=app
    engine.script=lambda _: ([],{'report':'Bundled workflow control result','blocking_missing':[]})
    research.execution_factory=partial(build_execution,db,scopes,engine=engine)
    methods=tasks.methods
    originals=[methods.resolve('method','expert_'+kind) for kind in ('synthesize','analyze')]
    try:
        for kind in ('synthesize','analyze'):
            publish(methods,methods.save(json.loads(Path(f'config/{kind}-evidence-v7.json').read_text())))
        template=methods.save(json.loads(Path('config/three-company-workflow.json').read_text()))
        publish(methods,template)
        params={'industry':'NEW_INDUSTRY'}
        for letter in ('a','b','c'):
            _,ref=scopes.ingest(scopes.current(root.scope_id,'local'),content=f'NEW_{letter} 2026 cash flow 123'.encode(),name=f'new-{letter}.txt',media_type='text/plain')
            params['company_'+letter]='NEW_'+letter;params['report_'+letter]=ref.model_dump(mode='json')
        instance=await methods.instantiate(instance_id=str(uuid4()),asset_id=template.asset_id,version=template.version,research_id=root.id,parameters=params)
        approved = await approve_workflow(methods,instance['id'])
        assert len(engine.requests)==4
        assert {r.limits.timeout_seconds for r in engine.requests}=={180}
        for step,source in zip(approved.plan.steps,instance['plan']['steps']):
            assert research.node(step.node_id).current_result_id
            assert instance['bindings'][source['node_id']]['method_version'].endswith('evidence-v7')
        compare=json.loads(engine.requests[-1].input.messages[0].content)
        assert len(compare['results'])==3
        assert {s['ref']['resource_id'] for s in compare['sources']}=={params['report_'+l]['resource_id'] for l in ('a','b','c')}
    finally:
        for original in originals: publish(methods,original)
