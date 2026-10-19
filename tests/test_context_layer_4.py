"""Domain learning gates, lifecycle, isolation and controlled execution."""
import json
import os
from uuid import uuid4
import pytest
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.adapters.openai import OpenAIEngine
from agent_core.agent_identity import AgentIdentity
from agent_core.contracts import RunResult, Usage, Message
from agent_core.context.assembler import project_context
from agent_core.context.capabilities import context_tools
from agent_core.context.scopes import ScopeDenied
from agent_core.execution.ports import Conflict
from agent_core.learning.models import DomainCall, DomainCandidate, DomainValidation, DomainPublication, Trajectory
from test_collaboration import workspace, seeded
from test_sdk_contract import QueueModel, call, text


async def test_postgres_published_domain_memory_is_selected_and_used_by_next_task(tmp_path):
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated PostgreSQL required for domain publication and reuse')
    from agent_core.adapters.postgres import PostgresDatabase
    from agent_core.adapters.parsing.text import TextParser
    from agent_core.composition import build_resources,build_projects
    from legacy_composition import build_research,build_tasks
    db=PostgresDatabase(url)
    try:
        scopes=build_resources(db,tmp_path/'blobs',parser=TextParser())
        research=build_research(db,scopes,owner='domain-pg:'+uuid4().hex);tasks=build_tasks(db,scopes,research)
        projects=build_projects(db,research,tasks)
        spec,receipt,ref=await completed((db,scopes,research,tasks,projects,research.collaboration))
        approve(tasks,receipt);tasks.engine=LearningEngine()
        published=await tasks.learning.process(receipt.run_id)
        assert published['state']=='published'
        memory=tasks.memories.select(spec.research_id,(published['publication'].memory_id,),agent=spec.agent)[0]
        raw='  Preserve source units in the next task.\nKeep unknown values unknown.  '
        next_spec=tasks.create(research_id=spec.research_id,kind='synthesize',user_request=raw,
            required_refs=(ref,),memory_ids=(memory.id,))
        tasks.engine=ScriptedEngine(lambda request: ([
            Call('search_memory',{'scope':'specialist','query':'source units'},'domain-memory'),
            Call('load_skill',{'name':published['publication'].skill_name},'domain-skill')],
            {'report':'Preserve source units; unknown remains unknown.','claims':[]}))
        # This is a database publication/reuse test; the controlled LLM protocol
        # is covered separately. Supply the script engine's semantic response.
        from agent_core.context.memory_retrieval import current_memory_query
        async def select_published(payload):
            return {'choices': [{'id': memory.id, 'version': memory.version, 'reason': 'Preserve source units'}]}
        token=current_memory_query.set(select_published)
        try: saved=await tasks.run(next_spec.id)
        finally: current_memory_query.reset(token)
        assert tasks.ledger.get(saved['run_id']).result.status=='succeeded'
        snapshot=scopes.repository.snapshot(saved['run_id'])
        assert snapshot.input.messages[-1].content==raw
        assert memory.id in {r.id for r in snapshot.memory_catalog}
        assert any(e['name']==published['publication'].skill_name for e in snapshot.skill_catalog)
    finally:db.close()

DRAFT = {'memory':'Preserve source units before comparing cash flow.',
    'skill':{'description':'Verify cash flow source units before synthesis.',
        'instructions':'Read the permitted source. Preserve its units. Record missing evidence explicitly.',
        'allowed_tools':['read_source']}}
REVIEW = {'grounded':True,'generalizable':True,'privacy_safe':True,'permission_safe':True,
    'evidence_quotes':['Preserve source units'], 'reason':'The reviewed trajectory supports a generic procedure, not case facts.'}


async def completed(workspace, mode='specialist', user='  Preserve source units\nKeep unknown values unknown.  '):
    _, scopes, research, tasks, _, _ = workspace
    node, ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {'report':'Preserve source units; real size unknown.', 'claims':[]}))
    spec = tasks.create(research_id=node.research_id, kind='synthesize', user_request=user,
        required_refs=(ref,), agent_mode=mode, role_instructions='Review this source only.' if mode == 'temporary' else '')
    result = await tasks.run(spec.id)
    return spec, result['receipt'], ref


def approve(tasks, receipt):
    spec = tasks.spec(receipt.task_id)
    return tasks.review(receipt.result_id, {c.number:('passed','Independent source review confirms this item.') for c in spec.acceptance})


async def test_unknown_marker_is_reusable_but_concrete_source_values_are_rejected(workspace):
    _, _, _, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    learning=tasks.learning
    trajectory=learning.get(Trajectory,receipt.run_id)
    trajectory=trajectory.model_copy(update={'outcome':{'output':{'fields':{'market_size':'unknown','company':'CaseCompany'}}}})
    draft={'memory':'Preserve unknown values instead of estimating.', 'skill':None}
    candidate=DomainCandidate(id=trajectory.id,trajectory_id=trajectory.id,owner=trajectory.owner,
        expert_id=trajectory.expert_id,draft=draft,digest='test',generation_run_id='test')
    _,method=tasks.methods.method(spec.expert_id,spec.method_version)
    learning.static_checks(candidate,trajectory,method)
    leaked=candidate.model_copy(update={'draft':candidate.draft.model_copy(update={'memory':'Use CaseCompany as the comparison target.'})})
    with pytest.raises(ValueError,match='source-specific'):
        learning.static_checks(leaked,trajectory,method)


async def test_learning_selects_committed_evidence_without_self_checks_or_failed_repairs(workspace):
    _,_,_,tasks,_,_=workspace
    spec,receipt,_=await completed(workspace)
    trajectory=tasks.learning.get(Trajectory,receipt.run_id)
    outcome={**trajectory.outcome,'output':{**trajectory.outcome['output'],'self_checks':{'claim':'UNVERIFIED_SELF_CHECK'}},
        'output_repairs':[{'raw_output':'FAILED_FORMAT_ASSERTION'}]}
    trajectory=trajectory.model_copy(update={'outcome':outcome})
    selected=tasks.learning.model_trajectory(trajectory)
    assert selected['original_user']==spec.user_request
    assert selected['outcome']['output']['report']==outcome['output']['report']
    assert 'self_checks' not in selected['outcome']['output'] and 'output_repairs' not in selected['outcome']
    assert 'UNVERIFIED_SELF_CHECK' not in tuple(tasks.learning.evidence_strings(selected))
    assert 'FAILED_FORMAT_ASSERTION' not in tuple(tasks.learning.evidence_strings(selected))
    assert trajectory.outcome==outcome


class LearningEngine:
    def __init__(self, draft=None, review=None): self.requests=[]; self.draft=draft or DRAFT; self.review=review or REVIEW
    async def execute(self, request, agent, tools, invoke):
        self.requests.append(request)
        assert request.agent_id.startswith('domain_') and not tools
        return RunResult(run_id=request.run_id, status='succeeded',
            output=self.draft if request.agent_id.startswith('domain_generate:') else self.review,
            usage=Usage(requests=1))


async def test_candidate_isolation_review_gate_atomic_publication_and_scoped_retrieval(workspace):
    _, scopes, research, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    learning = tasks.learning
    trajectory = learning.get(Trajectory, receipt.run_id)
    assert trajectory.original_user == spec.user_request
    engine = LearningEngine(); tasks.engine = engine
    first = await learning.process(receipt.run_id)
    assert first['state'] == 'candidate' and first['validation'] is None
    assert len(engine.requests) == 1 and engine.requests[0].input.messages[-1].content == spec.user_request
    assert tasks.memories.list(spec.research_id, agent=spec.agent) == []
    assert learning.skills(spec.agent) == []
    with pytest.raises(ScopeDenied): learning.publish(receipt.run_id)
    approve(tasks, receipt)
    result = await learning.process(receipt.run_id)
    assert result['state'] == 'published' and result['validation'].passed
    assert len(engine.requests) == 2
    formal = tasks.memories.list(spec.research_id, agent=spec.agent)
    assert len(formal) == 1 and formal[0].content == DRAFT['memory']
    for identity in (AgentIdentity(mode='assistant', id='main'), AgentIdentity(mode='temporary', id='temp', instructions='Review'),
            AgentIdentity(mode='specialist', id='expert_extract')):
        assert not learning.skills(identity)
        with pytest.raises(ScopeDenied): tasks.memories.select(spec.research_id, (formal[0].id,), agent=identity)
    scope = scopes.current(research.research(spec.research_id).scope_id, research.owner)
    snapshot = research.context.assemble(scopes, scope, uuid4().hex, 'cash flow units', agent=spec.agent,
        original_message=Message(role='user', content='cash flow units'), project=project_context(research, spec.research_id),
        memories=formal)
    payload = json.loads(snapshot.input.messages[0].content)
    assert payload['skills'][0]['name'] == result['publication'].skill_name and 'instructions' not in payload['skills'][0]
    tools = context_tools(snapshot, research.context.selector, (), {}, lambda: None, memories=tasks.memories, research_id=spec.research_id)
    tool = next(t for t in tools if t.name == 'load_skill')
    loaded = await tool.handler(tool.input_type(name=result['publication'].skill_name))
    assert DRAFT['skill']['instructions'] in loaded.value['skill']['instructions']
    with pytest.raises(ScopeDenied): tasks.memories.update(formal[0].id, formal[0].version, content='Unvalidated replacement')
    learning.revoke(receipt.run_id, 'Procedure no longer applicable')
    assert not tasks.memories.list(spec.research_id, agent=spec.agent) and not learning.skills(spec.agent)
    with pytest.raises(ScopeDenied): await tool.handler(tool.input_type(name=result['publication'].skill_name))


async def test_temporary_never_records_and_specialist_failure_retained_without_release(workspace):
    _, _, _, tasks, _, _ = workspace
    temporary, receipt, _ = await completed(workspace, 'temporary')
    assert tasks.learning.get(Trajectory, receipt.run_id) is None and tasks.learning.trajectories() == []
    spec, _, ref = await completed(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {'report':'', 'claims':[]}))
    failed = await tasks.run(spec.id)
    trace = tasks.learning.get(Trajectory, failed['run_id'])
    assert trace is not None
    tasks.engine = LearningEngine()
    status = await tasks.learning.process(failed['run_id'])
    assert status['state'] == 'rejected' and not tasks.learning.skills(spec.agent)


@pytest.mark.parametrize('draft,review', [
    ({'memory':'Use 2025 company figures.', 'skill':None}, REVIEW),
    ({'memory':None, 'skill':{**DRAFT['skill'], 'allowed_tools':['host_shell']}}, REVIEW),
    (DRAFT, {**REVIEW, 'privacy_safe':False}),
    (DRAFT, {**REVIEW, 'evidence_quotes':['Invented support not in this trace']}),
])
async def test_bad_candidate_or_independent_validation_never_releases(workspace, draft, review):
    _, _, _, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    approve(tasks, receipt)
    tasks.engine = LearningEngine(draft, review)
    result = await tasks.learning.process(receipt.run_id)
    if draft.get('skill') and draft['skill']['allowed_tools']==['host_shell']:
        assert result['state']=='failed' and result['candidate'] is None
    else:
        assert result['state']=='rejected'
    assert result['publication'] is None
    if review['evidence_quotes'] == ['Invented support not in this trace']:
        assert 'evidence_quotes[0] is not a verbatim trajectory substring' in result['validation'].reason
    assert not tasks.learning.skills(spec.agent) and not tasks.memories.list(spec.research_id, agent=spec.agent)
    with pytest.raises(KeyError if result['candidate'] is None else ScopeDenied): tasks.learning.publish(receipt.run_id)


async def test_recovery_reuses_generation_and_release_is_idempotent(workspace):
    from agent_core.composition import build_learning
    _, _, research, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    engine = LearningEngine(); tasks.engine = engine
    await tasks.learning.process(receipt.run_id)
    approve(tasks, receipt)
    new = build_learning(tasks.database, tasks)
    tasks.learning = research.learning = research.context.learning = new
    first = await new.process(receipt.run_id)
    second = await new.process(receipt.run_id)
    assert first['state'] == second['state'] == 'published' and len(engine.requests) == 2
    assert new.publish(receipt.run_id) == first['publication']
    assert len(tasks.memories.list(spec.research_id, agent=spec.agent)) == 1


async def test_published_provenance_revocation_hides_memory_and_skill(workspace):
    _, scopes, research, tasks, _, _ = workspace
    spec, receipt, ref = await completed(workspace)
    approve(tasks, receipt); tasks.engine = LearningEngine()
    await tasks.learning.process(receipt.run_id)
    root = research.research(spec.research_id)
    scopes.revoke(scopes.current(root.scope_id, research.owner), ref)
    assert tasks.learning.status(receipt.run_id)['state'] == 'unavailable'
    assert not tasks.memories.list(spec.research_id, agent=spec.agent) and not tasks.learning.skills(spec.agent)


async def test_domain_generation_and_validation_use_real_controlled_entry(workspace, tmp_path):
    from types import SimpleNamespace
    from test_controlled import EngineLedgerProbe
    from agent_core.controlled.engine import ControlledEngine
    _, scopes, research, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    approve(tasks, receipt)
    class Ledger(EngineLedgerProbe):
        def __init__(self): super().__init__(); self.bindings={}; self.current=None
        def acquire(self, workspace, frozen, limits, process):
            self.current=frozen['run_id']; self.bindings[self.current]=frozen
            return super().acquire(workspace, frozen, limits, process)
        def binding(self, run): return self.bindings.get(run)
        def checkpoint(self, run, key): return self.saved.get((run, key))
        def save_checkpoint(self, w,t,e,r,key,artifact): self.saved[(r,key)]=artifact
        def check_cancelled(self, *args): pass
    ledger = Ledger()
    ledger.database = SimpleNamespace(process_id='domain-test')
    model = QueueModel([[text(json.dumps(DRAFT))], [text(json.dumps(REVIEW))]])
    tasks.engine = ControlledEngine(tasks, tmp_path/'private', ledger, SimpleNamespace(get_model=lambda _:model))
    result = await tasks.learning.process(receipt.run_id)
    assert result['state'] == 'published', result
    assert len(model.inputs) == 2
    for model_input in model.inputs: assert model_input[-1]['content'] == spec.user_request
    assert all(not {'read_source', 'search_web', 'delegate_task', 'write_private_note'} & set(names) for names in model.tool_names)
    assert all(value['entrance'] == 'learning' for value in ledger.bindings.values())
    assert [args[4].operation for args in ledger.reserved].count('model') == 2
    assert ledger.released


async def test_learner_background_releases_after_source_review(workspace):
    import asyncio
    from agent_core.app.api import create_app
    _, _, research, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    approve(tasks, receipt)
    tasks.engine = LearningEngine()
    app = create_app(research, task_service=tasks)
    async with app.router.lifespan_context(app):
        for _ in range(30):
            if tasks.learning.get(DomainPublication, receipt.run_id): break
            await asyncio.sleep(.01)
        assert tasks.learning.status(receipt.run_id)['state'] == 'published'


async def test_generation_failure_is_isolated_and_not_automatically_repeated(workspace):
    _, _, _, tasks, _, _ = workspace
    spec, receipt, _ = await completed(workspace)
    class Invalid(LearningEngine):
        async def execute(self, request, agent, tools, invoke):
            self.requests.append(request)
            return RunResult(run_id=request.run_id, status='succeeded', output={'memory':'Do not trust unconfirmed usage'},
                usage=Usage(requests=1, complete=False))
    engine=Invalid(); tasks.engine=engine
    result=await tasks.learning.process(receipt.run_id)
    assert result['state']=='failed' and result['publication'] is None
    await tasks.learning.drain()
    assert len(engine.requests)==1 and tasks.receipt(receipt.result_id).delivery=='pending_review'


async def test_longitudinal_dependencies_and_domain_retrieval_across_projects(workspace):
    _, scopes, research, tasks, projects, _ = workspace
    first_spec, first, first_ref = await completed(workspace)
    approve(tasks, first); tasks.engine=LearningEngine()
    await tasks.learning.process(first.run_id)
    project=projects.create('Next domain project')
    main=await projects.create_main_task(project.id,'Next task')
    root=research.research(main.research_id)
    _,ref=scopes.ingest(scopes.current(root.scope_id,research.owner),content=b'Other source units.',name='other.txt',media_type='text/plain')
    tasks.engine=ScriptedEngine(lambda request:([],{'report':'Preserve source units in this reviewed task.', 'claims':[]}))
    spec=tasks.create(research_id=root.id,kind='synthesize',user_request='Preserve source units',required_refs=(ref,))
    second=(await tasks.run(spec.id))['receipt']
    snap=scopes.repository.snapshot(second.run_id)
    assert snap.memory_catalog and any('publication_id' in entry for entry in snap.skill_catalog)
    approve(tasks,second); tasks.engine=LearningEngine()
    assert (await tasks.learning.process(second.run_id))['state']=='published'
    assert len(tasks.learning.trajectories(spec.expert_id))==2
    tasks.learning.revoke(first.run_id,'Retire upstream lesson')
    assert tasks.learning.status(second.run_id)['state']=='unavailable'
    assert not tasks.learning.skills(spec.agent) and not tasks.memories.list(root.id,agent=spec.agent)


async def test_learning_api_hides_revoked_source_data_and_denies_foreign_owner(workspace):
    import httpx
    from agent_core.app.api import create_app
    from agent_core.composition import build_research
    from agent_core.learning.service import DomainLearning
    _, scopes, research, tasks, _, _ = workspace
    spec,receipt,ref=await completed(workspace)
    approve(tasks,receipt);tasks.engine=LearningEngine()
    await tasks.learning.process(receipt.run_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        response=await client.get('/api/domain_candidate',params={'trajectory_id':receipt.run_id})
        assert response.status_code==200 and response.json()['state']=='published'
        root=research.research(spec.research_id)
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
        response=await client.get('/api/domain_candidate',params={'trajectory_id':receipt.run_id})
        assert response.json()['state']=='unavailable' and response.json()['candidate'] is None
        assert 'original_user' not in response.text and 'outcome' not in response.text
        assert (await client.get('/api/domain_trajectories')).json()==[]
    other=build_research(tasks.database,scopes,owner='other-owner')
    from types import SimpleNamespace
    foreign=DomainLearning(tasks.database,SimpleNamespace(research=other))
    with pytest.raises(ScopeDenied): foreign.status(receipt.run_id)


async def test_publication_write_failure_rolls_back_bundle_then_resumes_without_models(workspace, monkeypatch):
    from agent_core.adapters.memory_evolution import MemoryEvolution
    _, _, _, tasks, _, _ = workspace
    spec,receipt,_=await completed(workspace)
    approve(tasks,receipt);engine=LearningEngine();tasks.engine=engine
    original=MemoryEvolution.add
    failed=False
    def fail_once(self,key,value):
        nonlocal failed
        if key.startswith('DomainPublication:') and not failed:
            failed=True
            raise RuntimeError('Simulated release storage failure')
        return original(self,key,value)
    monkeypatch.setattr(MemoryEvolution,'add',fail_once)
    with pytest.raises(RuntimeError): await tasks.learning.process(receipt.run_id)
    assert tasks.learning.get(DomainValidation,receipt.run_id).passed
    assert tasks.learning.get(DomainPublication,receipt.run_id) is None
    assert not tasks.memories.list(spec.research_id,agent=spec.agent) and not tasks.learning.skills(spec.agent)
    assert (await tasks.learning.process(receipt.run_id))['state']=='published'
    assert len(engine.requests)==2 and len(tasks.memories.list(spec.research_id,agent=spec.agent))==1
