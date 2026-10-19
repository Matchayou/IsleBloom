"""Plan approval contracts; deterministic tests do not assert live model quality."""
import asyncio
from uuid import uuid4
import httpx
import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.composition import build_resources, build_research
from agent_core.research.chat import ChatService
from agent_core.research.planning_models import GeneratePlan, PlanDecision, RevisePlan
from agent_core.research.models import NodeCommand, ResearchPlan, PlanStep
from agent_core.execution.ports import Conflict
from agent_core.context.scopes import ScopeDenied
from agent_core.app.api import create_app


def draft(questions=()):
    return dict(analysis='先核对材料，再综合结论',questions=questions,steps=[
        dict(key='read',goal='核对资料',instructions='只使用固定资料',deliverable='有依据的摘要'),
        dict(key='report',goal='综合报告',instructions='保留局限性',deliverable='最终报告',depends_on=['read'])])


@pytest.fixture(params=['memory','postgres'])
def setup(tmp_path,request):
    if request.param == 'postgres':
        import os
        from agent_core.adapters.postgres import PostgresDatabase
        url=os.environ.get('TEST_DATABASE_URL')
        if not url: pytest.skip('Dedicated PostgreSQL required')
        db=PostgresDatabase(url)
    else: db = MemoryDatabase()
    scopes = build_resources(db, tmp_path/'blobs')
    engine = ScriptedEngine(lambda r: ([], draft() if r.agent_id.startswith('planner:') else dict(report='步骤报告',blocking_missing=[])))
    s = build_research(db,scopes,engine)
    r = s.create('计划研究 ' + str(uuid4()))
    yield s,r,engine
    db.close()


async def proposal(setup):
    s,r,e = setup
    chat = await ChatService(s).start(r.id,'主线')
    p = s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,
        expected_chat_revision=chat.revision,request_id=str(uuid4()),request='分析并综合'))
    return await s.planning.generate(p.id)


async def test_generate_confirm_execute_and_replay(setup):
    s,r,e = setup
    p = await proposal(setup)
    assert p.status == 'awaiting_confirmation' and p.plan is not None
    assert len(s.query('list_nodes',r.id)) == 1
    assert len(s.query('list_commands',r.id)) == 1  # origin creation only
    assert s.ledger.get(p.planning_request.run_id).result.status == 'succeeded'
    with pytest.raises(Conflict): await s.run_plan(p.id)
    with pytest.raises(Conflict): await s.run_plan(p.plan)
    decision = PlanDecision(proposal_id=p.id,expected_revision=p.revision)
    approved = s.planning.confirm(decision)
    assert approved.approved_by == s.owner and approved.status == 'queued'
    assert s.planning.confirm(decision) == approved
    assert len(s.query('list_nodes',r.id)) == 3
    finished = await s.run_plan(p.id)
    assert finished.status == 'succeeded', finished.reason
    assert len(e.requests) == 3
    assert (await s.run_plan(p.id)).status == 'succeeded'
    assert len(e.requests) == 3
    second = s.query('command',f'plan:{p.plan.id}:{p.plan.steps[1].node_id}')
    result = s.query('result',f'result:{second.run_id}')
    assert len(s.query('inputs',result.id)) == 1


async def test_questions_revision_and_old_confirm(setup):
    s,r,e = setup
    e.script = lambda r: ([], draft(('请确认研究期间',)))
    p = await proposal(setup)
    with pytest.raises(Conflict): s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    e.script = lambda r: ([], draft())
    new = s.planning.revise(RevisePlan(proposal_id=p.id,expected_revision=p.revision,request_id=str(uuid4()),feedback='研究2025年'))
    assert s.planning.get(p.id).status == 'rejected'
    with pytest.raises(Conflict): s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    new = await s.planning.generate(new.id)
    assert new.status == 'awaiting_confirmation'
    s.planning.confirm(PlanDecision(proposal_id=new.id,expected_revision=new.revision))


async def test_stale_conversation_and_protected_step(setup):
    s,r,e = setup
    p = await proposal(setup)
    ChatService(s).send(p.origin_node_id,'新增约束',p.chat_revision)
    with pytest.raises(Conflict): s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    fresh = await proposal_on_existing(setup,p.origin_node_id)
    approved = s.planning.confirm(PlanDecision(proposal_id=fresh.id,expected_revision=fresh.revision))
    step = fresh.plan.steps[0]
    for kind in ('run','retry','continue','mount','chat_reply'):
        with pytest.raises(ScopeDenied): await s.submit(NodeCommand(command_id='forged',research_id=r.id,kind=kind,node_id=step.node_id))
    await s.planning.decide(PlanDecision(proposal_id=approved.id,expected_revision=approved.revision),'cancel')
    assert (await s.run_plan(approved.id)).status == 'cancelled'
    assert len(e.requests) == 2


async def proposal_on_existing(setup,node_id):
    s,r,e = setup
    chat = ChatService(s).read(node_id)
    p = s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=node_id,expected_chat_revision=chat.revision,request_id=str(uuid4()),request='更新后的要求'))
    return await s.planning.generate(p.id)


async def test_planner_cannot_dispatch(setup):
    s,r,e = setup
    e.script = lambda r: ([Call('delegate_task',{},'bad')],draft())
    p = await proposal(setup)
    assert p.status == 'generation_failed'
    assert len(s.query('list_nodes',r.id)) == 1
    assert len(s.query('list_runs',p.origin_node_id)) == 0


async def test_partial_upstream_blocks_downstream(setup):
    s,r,e = setup
    p = await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    e.script = lambda r: ([],dict(report='资料不足',blocking_missing=['缺少期间']))
    finished = await s.run_plan(p.id)
    assert finished.status == 'blocked'
    assert len(e.requests) == 2
    assert s.query('command',f'plan:{p.plan.id}:{p.plan.steps[1].node_id}') is None


async def test_limitations_allow_three_steps_and_preserve_blocked_history(setup):
    s,r,e = setup
    three = draft()
    three['steps'].append(dict(key='final',goal='最终比较',instructions='综合两步',deliverable='比较表',depends_on=['report']))
    e.script = lambda request: ([],three)
    old = await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=old.id,expected_revision=old.revision))
    e.script = lambda request: ([],dict(report='Full text not obtained',blocking_missing=['Required full text unavailable']))
    old = await s.run_plan(old.id)
    first = s.query('command',f'plan:{old.plan.id}:{old.plan.steps[0].node_id}')
    saved = s.read_result(f'result:{first.run_id}')
    recovery = s.planning.recovery_proposal(PlanDecision(proposal_id=old.id,expected_revision=old.revision))
    with pytest.raises(Conflict): await s.run_plan(recovery.id)
    s.planning.confirm(PlanDecision(proposal_id=recovery.id,expected_revision=recovery.revision))
    e.script = lambda request: ([],dict(report='Official source inspected; measurements unknown',
        blocking_missing=[],limitations=['No measured cost comparison available'],claims=[]))
    done = await s.run_plan(recovery.id)
    assert done.status == 'succeeded', done.reason
    for step in done.plan.steps:
        record = s.query('command',f'plan:{done.plan.id}:{step.node_id}')
        version, _ = s.read_result(f'result:{record.run_id}')
        assert version.completion == 'complete' and version.missing == ()
        assert version.limitations == ('No measured cost comparison available',)
        binding = s.query('run',record.run_id)
        snapshot = s.scopes.repository.snapshot(binding.run_id)
        if step.depends_on:
            assert all(m.limitations == ('No measured cost comparison available',) for m in snapshot.result_mounts)
    assert s.planning.view(done.id)['execution_steps'][0]['limitations'] == ('No measured cost comparison available',)
    assert s.planning.get(old.id).status == 'blocked'
    assert s.read_result(f'result:{first.run_id}') == saved


async def test_recovery_carries_completed_report_and_validated_evidence(setup):
    s,r,e=setup
    _,ref=s.scopes.ingest(s.scopes.current(r.scope_id,s.owner),name='official.txt',
        media_type='text/plain',content=b'Exact source mechanism.')
    p=await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    def execute(request):
        if request.agent_id==p.nodes[0].node_id:
            return [],{'report':'Verified source','blocking_missing':[],'claims':[{'statement':'Mechanism verified',
                'citations':[{'ref':ref.model_dump(),'location':'line:1','quote':'Exact source mechanism.',
                    'relation':'supports','reasoning':'Exact saved source'}],'missing':[]}]}
        return [],{'report':'Table unfinished','blocking_missing':['Required table not delivered'],'claims':[]}
    e.script=execute
    old=await s.run_plan(p.id)
    command=s.query('command',f'plan:{p.plan.id}:{p.nodes[0].node_id}')
    result_id=f'result:{command.run_id}'
    saved=s.read_result(result_id)
    recovery=s.planning.recovery_proposal(PlanDecision(proposal_id=p.id,expected_revision=old.revision))
    assert recovery.result_ids==(result_id,)
    assert recovery.reused_steps=={p.nodes[0].node_id:result_id}
    assert recovery.nodes[0]==p.nodes[0]
    assert recovery.nodes[1].result_ids==()
    with pytest.raises(Conflict):await s.run_plan(recovery.id)
    s.planning.confirm(PlanDecision(proposal_id=recovery.id,expected_revision=recovery.revision))
    finish=False
    def verify(request):
        import json
        packet=json.loads(request.input.messages[0].content)
        cached=packet['results'][0]
        assert request.agent_id!=recovery.nodes[0].node_id
        assert cached['mount']['result_id']==result_id
        assert cached['output']['report']=='Verified source'
        assert cached['output']['evidence']['evidence'][0]['quote']=='Exact source mechanism.'
        return [],{'report':'Delivery based on saved source evidence','blocking_missing':[] if finish else ['Required table unfinished'],
            'claims':[{'statement':'Mechanism verified','citations':[{'ref':ref.model_dump(),'location':'line:1',
                'quote':'Exact source mechanism.','relation':'supports','reasoning':'Reuse validated source evidence'}],'missing':[]}]}
    e.script=verify
    count=len(e.requests)
    blocked=await s.run_plan(recovery.id)
    assert blocked.status=='blocked'
    assert len(e.requests)==count+1
    assert s.planning.view(recovery.id)['execution_steps'][0]['reused']
    recovery=s.planning.recovery_proposal(PlanDecision(proposal_id=recovery.id,expected_revision=blocked.revision))
    assert recovery.reused_steps=={p.nodes[0].node_id:result_id}
    s.planning.confirm(PlanDecision(proposal_id=recovery.id,expected_revision=recovery.revision))
    finish=True
    assert (await s.run_plan(recovery.id)).status=='succeeded'
    assert len(e.requests)==count+2
    assert s.read_result(result_id)==saved


async def test_cancel_generation_fences_late_output(setup):
    s,r,e = setup
    chat = await ChatService(s).start(r.id,'主线')
    p = s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,request_id='once',request='目标'))
    entered = asyncio.Event()
    async def slow(request,*args):
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError:
            from agent_core.contracts import RunResult
            return RunResult(run_id=request.run_id,status='succeeded',output=draft())
    e.execute = slow
    task = asyncio.create_task(s.planning.generate(p.id))
    await entered.wait()
    await s.planning.decide(PlanDecision(proposal_id=p.id,expected_revision=p.revision),'cancel')
    await task
    assert s.planning.get(p.id).status == 'cancelled'
    assert s.planning.get(p.id).plan is None


async def test_api_old_entry_removed_and_explicit_confirmation(setup):
    s,r,e = setup
    chat = await ChatService(s).start(r.id,'主线')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(s)),base_url='http://test') as client:
        assert (await client.post('/api/plan',json={})).status_code == 404
        body=dict(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,request_id='api',request='规划研究')
        response=await client.post('/api/plan_generate',json=body)
        assert response.status_code == 202,response.text
        key=response.json()['id']
        for _ in range(40):
            await asyncio.sleep(.005)
            p=(await client.get('/api/plan_proposal',params={'id':key})).json()
            if p['status']!='generating': break
        assert p['status']=='awaiting_confirmation',p
        assert len(e.requests)==1
        assert (await client.post('/api/plan_confirm',json={'proposal_id':key,'expected_revision':0})).status_code==409
        decision={'proposal_id':key,'expected_revision':p['revision']}
        assert (await client.post('/api/plan_confirm',json=decision)).status_code==202
        assert (await client.post('/api/plan_confirm',json=decision)).status_code==202
        for _ in range(40):
            await asyncio.sleep(.005)
            p=(await client.get('/api/plan_proposal',params={'id':key})).json()
            if p['status'] not in ('queued','running'): break
        assert p['status']=='succeeded',p
        assert len(e.requests)==3

async def test_confirm_transaction_rolls_back_and_snapshot_is_fixed(setup,monkeypatch):
    s,r,e=setup
    p=await proposal(setup)
    repository=type(s.database.repositories['research']) if isinstance(s.database,MemoryDatabase) else None
    if repository is not None:
        original=repository.create_node
        def fail(self,value):
            if value.plan_proposal_id: raise RuntimeError('injected confirmation failure')
            return original(self,value)
        with monkeypatch.context() as m:
            m.setattr(repository,'create_node',fail)
            with pytest.raises(RuntimeError): s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
        assert s.planning.get(p.id).status=='awaiting_confirmation'
        assert len(s.query('list_nodes',r.id))==1
    approved=s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    ChatService(s).send(p.origin_node_id,'后续讨论不得替换已确认输入',p.chat_revision)
    finished=await s.run_plan(p.id)
    assert finished.status=='succeeded'
    assert '后续讨论不得替换' not in e.requests[-1].input.model_dump_json()
    assert finished.approved_revision==p.revision


async def test_restart_after_confirmation_does_not_lose_queue(setup):
    s,r,e=setup
    p=await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    from agent_core.research.plan_service import PlanService
    s.planning=PlanService(s)
    s.reconcile()
    s.planning.recover()
    assert s.planning.get(p.id).status=='queued'
    assert (await s.run_plan(p.id)).status=='succeeded'
    assert len(e.requests)==3


async def test_restart_between_waves_reuses_saved_result(setup):
    s,r,e=setup
    p=await proposal(setup)
    approved=s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    from agent_core.execution.ports import now
    s.planning.change(approved,status='running',execution_started_at=now())
    step=p.plan.steps[0]
    command=NodeCommand(command_id=f'plan:{p.plan.id}:{step.node_id}',research_id=r.id,node_id=step.node_id,
        kind='adopt',resource_refs=p.resource_refs,result_ids=p.result_ids,limits=step.limits)
    record,_=s._accept(command)
    binding,snapshot=s._prepare(record)
    executor,_=s.execution_factory({step.node_id:s.scopes.current(s.node(step.node_id).scope_id,s.owner)},s)
    from agent_core.contracts import RunRequest
    await executor.execute(RunRequest(run_id=binding.run_id,agent_id=step.node_id,input=snapshot.input,limits=step.limits))
    from agent_core.research.plan_service import PlanService
    s.planning=PlanService(s)
    s.reconcile();s.planning.recover()
    assert (await s.run_plan(p.id)).status=='succeeded'
    assert len(e.requests)==3
    assert len(s.query('list_runs',step.node_id))==1


async def test_revoked_input_blocks_confirmation_and_generation(setup):
    s,r,e=setup
    chat=await ChatService(s).start(r.id,'主线')
    root=s.scopes.current(r.scope_id,s.owner)
    root,ref=s.scopes.ingest(root,content=b'fixed',name='fixed.txt',media_type='text/plain')
    await s.submit(NodeCommand(command_id=str(uuid4()),research_id=r.id,node_id=chat.node_id,kind='mount',resource_refs=(ref,)))
    p=await proposal_on_existing(setup,chat.node_id)
    s.scopes.revoke(root,ref)
    with pytest.raises(ScopeDenied): s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    assert len(s.query('list_nodes',r.id))==1

async def test_main_planner_sees_authorized_library_without_mutating_origin(setup):
    s,r,e=setup
    chat=await ChatService(s).start(r.id,'主线')
    node=s.node(chat.node_id)
    original_scope=s.scopes.current(node.scope_id,s.owner)
    root=s.scopes.current(r.scope_id,s.owner)
    _,ref=s.scopes.ingest(root,content=b'uploaded report',name='uploaded.txt',media_type='text/plain')
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=node.id,expected_chat_revision=chat.revision,
        request_id=str(uuid4()),request='根据已上传报告生成计划'))
    assert ref in p.resource_refs
    assert s.node(node.id)==node
    assert s.scopes.current(node.scope_id,s.owner)==original_scope
    snapshot=s.scopes.repository.snapshot(p.planning_request.run_id)
    assert snapshot.scope_id!=node.scope_id
    e.script=lambda request: ([Call('read_source',{'ref':ref.model_dump()},'read-plan')],draft()) if request.agent_id.startswith('planner:') else ([],{'report':'done','blocking_missing':[]})
    p=await s.planning.generate(p.id)
    assert p.status=='awaiting_confirmation',p.reason

@pytest.mark.parametrize('setup', ['postgres'], indirect=True)
async def test_real_database_reopen_preserves_approval_queue(setup,tmp_path):
    s,r,e=setup
    import os
    from agent_core.adapters.postgres import PostgresDatabase
    p=await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    s.database.close()
    reopened=PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    try:
        new_engine=ScriptedEngine(lambda request:([],dict(report='重启后的固定交付',blocking_missing=[])))
        new=build_research(reopened,build_resources(reopened,tmp_path/'blobs'),new_engine)
        new.reconcile();new.planning.recover()
        assert new.planning.get(p.id).status=='queued'
        assert (await new.run_plan(p.id)).status=='succeeded'
        assert len(new_engine.requests)==2
        assert new.planning.get(p.id).approved_revision==p.revision
    finally: reopened.close()

async def test_unconfirmed_step_recovery_stops_without_model_replay(setup):
    s,r,e=setup
    p=await proposal(setup)
    approved=s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    from agent_core.execution.ports import now
    s.planning.change(approved,status='running',execution_started_at=now())
    step=p.plan.steps[0]
    record,_=s._accept(NodeCommand(command_id=f'plan:{p.plan.id}:{step.node_id}',research_id=r.id,
        node_id=step.node_id,kind='adopt',resource_refs=p.resource_refs,result_ids=p.result_ids,limits=step.limits))
    s._prepare(record)  # process exits before execution/publication can be confirmed
    s.reconcile();s.planning.recover()
    assert s.planning.get(p.id).status=='interrupted'
    assert (await s.run_plan(p.id)).status=='interrupted'
    assert len(e.requests)==1 and len(s.query('list_runs',step.node_id))==1

async def test_approved_execution_explicitly_supersedes_historical_planning_stage(setup):
    s,r,e=setup
    p=await proposal(setup)
    from agent_core.research.planning_models import RevisePlan
    changed=s.planning.revise(RevisePlan(proposal_id=p.id,expected_revision=p.revision,request_id=str(uuid4()),feedback='当前只生成计划，等待用户确认，不执行研究'))
    p=await s.planning.generate(changed.id)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    original=e.execute
    observed=[]
    async def inspect(request,spec,tools,invoke):
        payload=__import__('json').loads(request.input.messages[0].content)
        observed.append((payload['handoff'],spec.instructions,payload['goal']))
        return await original(request,spec,tools,invoke)
    e.execute=inspect
    assert (await s.run_plan(p.id)).status=='succeeded'
    assert all(packet['phase']=='approved_plan_execution' and packet['approved_revision']==p.revision for packet,_,_ in observed)
    assert all('approved_plan_execution' in instruction for _,instruction,_ in observed)
    assert all('当前只生成计划' not in goal for _,_,goal in observed)
    assert all(packet['planning_history_ref']==p.id for packet,_,_ in observed)
    assert '当前只生成计划' in s.planning.get(p.id).request  # exact history stays in its proposal


async def test_recovery_preserves_failure_and_requires_fresh_confirmation(setup):
    s,r,e=setup
    p=await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    e.script=lambda request:([],dict(report='仍等待确认',blocking_missing=['没有执行资料获取']))
    failed=await s.run_plan(p.id)
    assert failed.status=='blocked'
    count=len(e.requests)
    decision=PlanDecision(proposal_id=p.id,expected_revision=failed.revision)
    recovery=s.planning.recovery_proposal(decision,max_total_tokens=180000)
    assert s.planning.recovery_proposal(decision,max_total_tokens=180000)==recovery
    assert recovery.recovery_of==p.id and recovery.approved_revision is None
    assert recovery.plan.policy.max_total_tokens==180000 and recovery.model==p.model
    assert s.planning.get(p.id).plan.policy==p.plan.policy
    assert [n.goal for n in recovery.nodes]==[n.goal for n in p.nodes]
    assert len(e.requests)==count and len(s.query('list_nodes',r.id))==3
    with pytest.raises(Conflict): await s.run_plan(recovery.id)
    assert s.planning.get(p.id).status=='blocked'
    e.script=lambda request:([],dict(report='真正执行的交付',blocking_missing=[]))
    s.planning.confirm(PlanDecision(proposal_id=recovery.id,expected_revision=recovery.revision))
    assert (await s.run_plan(recovery.id)).status=='succeeded'
    assert s.planning.view(p.id)['execution_steps'][0]['report']=='仍等待确认'
    assert s.planning.view(p.id)['execution_steps'][0]['completion']=='partial'

async def test_timed_out_step_exposes_error_and_blocks_dependents(setup):
    from agent_core.contracts import fail
    s,r,e=setup
    p=await proposal(setup)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    def timeout(request):
        fail('timeout','engine','Run deadline exceeded')
    e.script=timeout
    stopped=await s.run_plan(p.id)
    assert stopped.status=='blocked'
    steps=s.planning.view(p.id)['execution_steps']
    assert steps[0]['status']=='failed'
    assert steps[0]['error'].code=='timeout'
    assert steps[0]['result_id'] is None
    assert all(step['status']=='waiting' for step in steps[1:])

async def test_planning_reuses_fetched_body_without_mounting_discovery_snippets(setup):
    s,r,e=setup
    scope=s.scopes.current(r.scope_id,s.owner)
    scope,discovery=s.scopes.ingest(scope,content=b'Unverified search snippet',name='Search discovery',media_type='text/plain',kind='discovery',automatic=True)
    scope,body=s.scopes.ingest(scope,content=b'Verified original source body',name='Fetched body',media_type='text/plain',kind='web',automatic=True)
    p=await proposal(setup)
    assert p.status=='awaiting_confirmation'
    assert body in p.resource_refs and discovery not in p.resource_refs
    assert all(discovery not in n.resource_refs for n in p.nodes)
