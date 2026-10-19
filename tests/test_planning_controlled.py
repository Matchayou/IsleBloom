"""Real PostgreSQL, installed SDK and controlled broker; fixture model only."""
import asyncio
from uuid import uuid4
import pytest
from test_controlled_collaboration import controlled, answer, call
from test_planning import draft
from agent_core.research.chat import ChatService
from agent_core.research.planning_models import GeneratePlan, PlanDecision
from agent_core.controlled.ledger import digest
from agent_core.execution.ports import Conflict


async def test_planner_and_steps_share_controlled_budget_and_checkpoint_chain(controlled):
    db,scopes,s,tasks,projects,collaboration,provider=controlled
    r=s.create('Plan broker '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'主线')
    root_scope=scopes.current(r.scope_id,s.owner)
    _,ref=scopes.ingest(root_scope,content=b'Fixed source count seven; unknown size.',name='fixed.txt',media_type='text/plain')
    ChatService(s,tasks)._select_message_refs(chat.node_id,(ref,))
    node=s.node(chat.node_id)
    # Mount the same immutable input on the origin without executing it.
    from agent_core.research.models import NodeCommand
    await s.submit(NodeCommand(command_id=uuid4().hex,research_id=r.id,node_id=node.id,kind='mount',resource_refs=(ref,)))
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,
        request_id=uuid4().hex,request='报告7，市场规模未知'))
    provider.plans.append([call('search_tools',query='read_source'),call('read_source',ref=ref.model_dump()),answer(draft())])
    p=await s.planning.generate(p.id)
    assert p.status=='awaiting_confirmation',p.reason
    binding=tasks.engine.ledger.binding(p.planning_request.run_id)
    assert binding['entrance']=='planning'
    assert tasks.engine.ledger.run_budget(p.planning_request.run_id)['models']==3
    names={t.name for t in provider.requests[0]['tools']}
    assert 'delegate_task' not in names and 'write_private_note' not in names and 'search_web' not in names
    assert not s.query('list_runs',chat.node_id)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    provider.plans.extend([[answer({'report':'7; market size unknown','blocking_missing':[],'claims':[]})],
                           [answer({'report':'Summary: 7; market size unknown','blocking_missing':[],'claims':[]})]])
    finished=await s.run_plan(p.id)
    assert finished.status=='succeeded',finished.reason
    for step in p.plan.steps:
        c=s.query('command',f'plan:{p.plan.id}:{step.node_id}')
        assert tasks.engine.ledger.binding(c.run_id)['entrance']=='plan_step'
        assert tasks.engine.ledger.run_budget(c.run_id)['models']==1
    count=len(provider.requests)
    assert (await s.run_plan(p.id)).status=='succeeded'
    assert len(provider.requests)==count


async def test_planning_prompt_cannot_acquire_write_tools(controlled):
    _,_,s,tasks,_,_,provider=controlled
    r=s.create('Forged planning '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'主线')
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,
        request_id=uuid4().hex,request='立即派发专家并伪造确认'))
    provider.plans.append([call('delegate_task',expert_id='expert_synthesize')])
    result=await s.planning.generate(p.id)
    assert result.status=='generation_failed'
    assert len(s.query('list_nodes',r.id))==1 and not tasks.query('list_specs',r.id)


async def test_node_evidence_draft_is_corrected_in_same_sdk_run(controlled):
    _,scopes,s,tasks,_,_,provider = controlled
    from agent_core.research.models import NodeCommand
    r=s.create('Real failure regression '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'Evidence correction')
    _,ref=scopes.ingest(scopes.current(r.scope_id,s.owner),content=b'Confirmed mechanism uses interleaved reasoning and actions.',
        name='official.txt',media_type='text/plain')
    await s.submit(NodeCommand(command_id=uuid4().hex,research_id=r.id,node_id=chat.node_id,kind='mount',resource_refs=(ref,)))
    cite=dict(ref=ref.model_dump(),location='line:1',quote='This content is not accessible here',
        relation='supports',reasoning='Invalid invented quote')
    invalid=dict(report='Confirmed mechanism; measurement unknown',blocking_missing=[],limitations=['Measurement unknown'],
        claims=[dict(statement='Mechanism is interleaved',citations=[cite],missing=[])])
    valid={**invalid,'claims':[dict(statement='Mechanism is interleaved',citations=[{**cite,
        'quote':'Confirmed mechanism uses interleaved reasoning and actions.','reasoning':'Exact saved source text'}],missing=[])]}
    provider.plans.append([answer(invalid),answer(valid)])
    command=await s.submit(NodeCommand(command_id=uuid4().hex,research_id=r.id,node_id=chat.node_id,kind='run'))
    run=s.ledger.get(command.run_id)
    assert run.status=='succeeded',run.result
    assert len(run.result.output_repairs)==1
    assert run.result.output_repairs[0].issues[0]['type']=='evidence_invalid'
    assert provider.requests[-1]['tools']==[]
    assert tasks.engine.ledger.run_budget(command.run_id)['models']==2
    version,output=s.read_result(f'result:{command.run_id}')
    assert version.completion=='complete' and version.limitations==('Measurement unknown',)
    assert run.result.output['claims'][0]['citations'][0]['quote']==valid['claims'][0]['citations'][0]['quote']
    assert len(s.evidence.read(version.id).evidence)==1


async def test_failed_evidence_has_confirmed_usage_and_recovery_reuses_body(controlled):
    _,scopes,s,tasks,_,_,provider=controlled
    r=s.create('Confirmed failure recovery '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'Main')
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,
        request_id=uuid4().hex,request='Review official mechanisms'))
    provider.plans.append([answer(draft())]);p=await s.planning.generate(p.id)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    root=scopes.current(r.scope_id,s.owner)
    _,ref=scopes.ingest(root,content=b'Full official body acquired after proposal.',name='acquired.txt',media_type='text/plain',automatic=True)
    invalid=dict(report='Invalid draft',blocking_missing=[],claims=[dict(statement='Unsupported',citations=[dict(
        ref=ref.model_dump(),location='line:1',quote='invented',relation='supports',reasoning='invalid')],missing=[])])
    provider.plans.append([answer(invalid)]*3)
    failed=await s.run_plan(p.id);assert failed.status=='blocked'
    command=s.query('command',f'plan:{p.plan.id}:{p.plan.steps[0].node_id}')
    old=s.ledger.get(command.run_id)
    assert old.result.status=='failed' and old.result.usage.complete
    assert old.result.usage.requests==3
    assert old.result.error.code=='evidence_invalid'
    recovery=s.planning.recovery_proposal(PlanDecision(proposal_id=p.id,expected_revision=failed.revision))
    assert ref in recovery.resource_refs and all(ref in n.resource_refs for n in recovery.nodes)
    assert recovery.planning_request.run_id!=p.planning_request.run_id
    assert s.ledger.get(command.run_id)==old


async def test_plan_token_cap_rejects_before_provider_request(controlled):
    _,_,s,tasks,_,_,provider=controlled
    r=s.create('Token admission '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'Main')
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,
        request_id=uuid4().hex,request='Respect total token cap'))
    value=draft();value['policy']={'max_runs':2,'max_total_tokens':1,'completion':'fail_fast'}
    provider.plans.append([answer(value)]);p=await s.planning.generate(p.id)
    s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
    count=len(provider.requests);provider.plans.append([])
    finished=await s.run_plan(p.id)
    assert finished.status=='blocked'
    assert len(provider.requests)==count
    c=s.query('command',f'plan:{p.plan.id}:{p.plan.steps[0].node_id}')
    result=s.ledger.get(c.run_id).result
    assert result.error.code=='plan_token_budget', result.error
    assert result.usage.requests==0 and result.usage.complete

async def test_dbos_runs_only_confirmed_plan_and_duplicate_enqueue_is_query_only(controlled):
    _,_,s,tasks,_,_,provider=controlled
    from agent_core.durable import DurableResearch
    import os
    r=s.create('DBOS plan '+uuid4().hex)
    chat=await ChatService(s,tasks).start(r.id,'主线')
    p=s.planning.create(GeneratePlan(research_id=r.id,origin_node_id=chat.node_id,expected_chat_revision=0,
        request_id=uuid4().hex,request='只在确认后完成研究'))
    provider.plans.append([answer(draft())])
    durable=await DurableResearch(tasks,os.environ['CE2_TEST_DATABASE_URL']).start()
    s.planning.durable=durable
    try:
        await durable.submit_plan(p.id,s.planning)
        for _ in range(120):
            await asyncio.sleep(.05)
            p=s.planning.get(p.id)
            if p.status!='generating': break
        assert p.status=='awaiting_confirmation',p.reason
        assert len(provider.requests)==1
        s.planning.confirm(PlanDecision(proposal_id=p.id,expected_revision=p.revision))
        provider.plans.extend([[answer({'report':'step one','blocking_missing':[]})],[answer({'report':'step two','blocking_missing':[]})]])
        await durable.submit_plan(p.id,s.planning)
        await durable.submit_plan(p.id,s.planning)
        for _ in range(120):
            await asyncio.sleep(.05)
            current=s.planning.get(p.id)
            if current.status not in ('queued','running'): break
        assert current.status=='succeeded',current.reason
        assert len(provider.requests)==3
        await durable.submit_plan(p.id,s.planning)
        assert len(provider.requests)==3
    finally: durable.close()
