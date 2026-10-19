"""Auxiliary modes use production assembly and scoped tool execution."""
import json
from uuid import uuid4
import pytest
from agent_core.adapters.openai import OpenAIEngine
from agent_core.agent_identity import AgentIdentity
from agent_core.context.scopes import ScopeDenied
from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog
from agent_core.contracts import RunResult
from agent_core.research.chat import ChatService
from agent_core.research.planning_models import GeneratePlan
from test_collaboration import workspace, seeded, fixed_review_advice
from test_dynamic_context import skill, memory_query_stub
from test_sdk_contract import QueueModel, call, text
from test_planning import draft


def capabilities(research, tmp_path):
    path = skill(tmp_path/'skills')
    path.write_text(path.read_text() + 'SKILL_BODY_PRIVATE_UNTIL_ACTIVATION\n')
    research.context.selector = ContextSelector(SkillCatalog(tmp_path/'skills'))


async def test_planning_sdk_candidate_activation_raw_request_and_branch_boundary(workspace, tmp_path):
    _, scopes, research, tasks, _, _ = workspace
    node, ref, chat = await seeded(workspace)
    capabilities(research, tmp_path)
    raw = '  $cash-flow 生成计划\n保留未知，不要执行。  '
    user = tasks.memories.create(kind='experience', content='cash flow user rule', source='User', confirmed=True)
    domain = tasks.memories.create(kind='experience', content='cash flow DOMAIN_SECRET', source='User', confirmed=True,
        scope='specialist', scope_id='expert_synthesize')
    # A branch sees its inherited and selected refs, never the main library as a whole.
    branch = await ChatService(research, tasks).branch(node.id, chat.messages[0].id, 'Branch', chat.revision)
    root_scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, sibling = scopes.ingest(root_scope, content=b'cash flow SIBLING_SECRET', name='sibling.txt', media_type='text/plain')
    p = research.planning.create(GeneratePlan(research_id=node.research_id, origin_node_id=branch.node_id,
        expected_chat_revision=branch.revision, request_id=uuid4().hex, request=raw))
    snap = scopes.repository.snapshot(p.planning_request.run_id)
    assert snap.mode == 'planning' and snap.agent.id == p.planning_request.agent_id
    assert snap.input.messages[-1].content == raw
    assert sibling not in snap.scope.allowed and 'SIBLING_SECRET' not in snap.input.messages[0].content
    assert [r.id for r in snap.memory_catalog] == [user.id] and domain.id not in [r.id for r in snap.memory_catalog]
    assert 'SKILL_BODY_PRIVATE' not in snap.input.messages[0].content
    model = QueueModel([[call('load_skill', 'activate', {'name':'cash-flow'})], [text(json.dumps(draft()))]])
    research.engine = OpenAIEngine(model)
    p = await research.planning.generate(p.id)
    assert p.status == 'awaiting_confirmation', p.reason
    assert 'SKILL_BODY_PRIVATE_UNTIL_ACTIVATION' in json.dumps(model.inputs[1], ensure_ascii=False)
    assert all(not {'delegate_task', 'search_web', 'fetch_web'} & set(names) for names in model.tool_names)
    assert len(tasks.query('list_specs', node.research_id)) == 0


async def test_discussion_sdk_read_scope_identity_memory_and_raw_words(workspace, tmp_path):
    _, scopes, research, tasks, projects, c = workspace
    node, ref, _ = await seeded(workspace)
    capabilities(research, tmp_path)
    root_scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, peer = scopes.ingest(root_scope, content=b'PEER_SECRET outside the task', name='peer.txt', media_type='text/plain')
    memory = tasks.memories.create(kind='experience', content='cash flow domain procedure', source='User', confirmed=True,
        scope='specialist', scope_id='expert_synthesize')
    intent = c.delegate_task(command_id='discussion-dynamic', node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Examine sample', delivery_requirements='State limits', resource_refs=(ref,))
    spec = tasks.spec(intent.task_id)
    raw = '  $cash-flow\n解释这份资料，不执行。  '
    model = QueueModel([[call('load_skill', 'skill', {'name':'cash-flow'})],
        [call('read_source', 'body', {'ref':ref.model_dump()})], [text('The sample leaves the real market size unknown.')]])
    tasks.engine = OpenAIEngine(model)
    result = await projects.discuss(spec.id, raw, 0)
    snap = scopes.repository.snapshot(result.messages[-1].run_id)
    assert result.messages[-2].content == raw and snap.input.messages[-1].content == raw
    assert snap.mode == 'discussion' and snap.agent == spec.agent
    assert [r.id for r in snap.memory_catalog] == [memory.id]
    assert peer not in snap.scope.allowed and 'PEER_SECRET' not in snap.input.messages[0].content
    assert tasks.spec(spec.id) == spec and not tasks.query('list_runs', spec.id)
    assert all(not {'delegate_task', 'revise_task', 'search_web'} & set(names) for names in model.tool_names)
    assert 'SKILL_BODY_PRIVATE_UNTIL_ACTIVATION' in json.dumps(model.inputs[1], ensure_ascii=False)


async def test_discussion_memory_revoked_before_save_and_temporary_has_no_domain(workspace):
    _, _, research, tasks, projects, c = workspace
    node, ref, _ = await seeded(workspace)
    memory = tasks.memories.create(kind='experience', content='source advice', source='User', confirmed=True)
    intent = c.delegate_task(command_id='temp-discussion', node_id=node.id, expert_id='expert_synthesize',
        agent_mode='temporary', role_instructions='Explain current source only.',
        task_instruction='Explain sample', delivery_requirements='State limits', resource_refs=(ref,))
    domain = tasks.memories.create(kind='experience', content='source DOMAIN_SECRET', source='User', confirmed=True,
        scope='specialist', scope_id='expert_synthesize')
    class Revoke:
        async def execute(self, request, agent, tools, invoke):
            snap = research.scopes.repository.snapshot(request.run_id)
            assert domain.id not in [r.id for r in snap.memory_catalog]
            tool = next(t for t in tools if t.name == 'search_memory')
            with pytest.raises(ScopeDenied): await tool.handler(tool.input_type(query='source', scope='specialist'))
            tasks.memories.delete(memory.id, memory.version)
            return RunResult(run_id=request.run_id, status='succeeded', output='Do not save stale advice.')
    tasks.engine = Revoke()
    with pytest.raises(ScopeDenied): await projects.discuss(intent.task_id, 'Explain source', 0)
    assert projects.expert_thread(intent.task_id).revision == 0


async def test_consultation_sdk_scoped_memory_and_read_before_grant(workspace, tmp_path, memory_query_stub):
    _, scopes, research, tasks, projects, c = workspace
    node, ref, chat = await seeded(workspace)
    capabilities(research, tmp_path)
    raw = '  $cash-flow 核对年份\n未知市场规模留空。  '
    ChatService(research, tasks).send(node.id, raw, chat.revision)
    root = research.research(node.research_id)
    _, extra = scopes.ingest(scopes.current(root.scope_id, research.owner), content=b'Target year 2025.', name='year.txt', media_type='text/plain')
    user = tasks.memories.create(kind='experience', content='cash flow preserve units', source='User', confirmed=True)
    domain = tasks.memories.create(kind='experience', content='cash flow DOMAIN_SECRET', source='User', confirmed=True,
        scope='specialist', scope_id='expert_synthesize')
    intent = c.delegate_task(command_id='consultation-dynamic', node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Find the year', delivery_requirements='State gaps', resource_refs=(ref,))
    model = QueueModel([[call('load_skill', 'skill', {'name':'cash-flow'})],
        [call('search_memory', 'memory', {'query':'cash flow', 'scope':'user'})],
        [call('search_tools', 'discover-read', {'query':'read_information'})],
        [call('read_information', 'read-year', {'resource_refs':[extra.model_dump()]})],
        [text(json.dumps({'available':True, 'answer':'Target year 2025.', 'resource_refs':[extra.model_dump()], 'missing':[]}))]])
    seen = []
    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if agent.id == 'manager_information':
                snap = scopes.repository.snapshot(request.run_id)
                assert snap.mode == 'consultation' and snap.input.messages[-1].content == raw
                assert {r.id for r in snap.memory_catalog} == {user.id}
                assert domain.id not in {r.id for r in snap.memory_catalog}
                assert 'read_source' not in {t.name for t in tools} and 'delegate_task' not in {t.name for t in tools}
                seen.append(snap)
                return await OpenAIEngine(model).execute(request, agent, tools, invoke)
            response = await invoke('request_manager_information', 'ask', {'question':'$cash-flow What is the target year?', 'progress':'Source did not specify the year.'})
            assert response.value['available'], json.dumps(response.value, ensure_ascii=False)
            return RunResult(run_id=request.run_id, status='succeeded', output={'report':'Year provided; size unknown.', 'claims':[]})
    tasks.engine = Engine()
    outcome = await c.execute(intent.command_id)
    assert outcome['intent'].status == 'completed', c.information.get(tasks.query('list_runs', intent.task_id)[0].id).answer
    assert len(seen) == 1
    assert 'SKILL_BODY_PRIVATE' not in model.inputs[0][0]['content']
    assert 'SKILL_BODY_PRIVATE_UNTIL_ACTIVATION' in json.dumps(model.inputs[1], ensure_ascii=False)
    assert all(not {'read_source', 'delegate_task', 'search_web'} & set(names) for names in model.tool_names)
    run = tasks.query('list_runs', intent.task_id)[0]
    information = c.information.get(run.id)
    assert information.answer.available and extra in information.answer.resource_refs


@pytest.mark.parametrize('mode', ['planning', 'discussion', 'consultation'])
async def test_auxiliary_real_controlled_binding_and_broker_budget(workspace, tmp_path, mode):
    from types import SimpleNamespace
    from test_controlled import EngineLedgerProbe
    from agent_core.controlled.engine import ControlledEngine
    from agent_core.controlled.admission import admit
    from agent_core.context.assembler import project_context
    from agent_core.context.capabilities import context_tools
    from agent_core.contracts import AgentSpec, Message, RunRequest, ToolBinding, Value
    from agent_core.core import CoreRunner, AgentCatalog
    _, scopes, research, tasks, _, _ = workspace
    node, _, _ = await seeded(workspace)
    capabilities(research, tmp_path)
    scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    run_id = uuid4().hex
    identity = AgentIdentity(mode='specialist' if mode == 'discussion' else 'assistant',
        id='expert_synthesize' if mode == 'discussion' else mode + ':' + run_id)
    snap = research.context.assemble(scopes, scope, run_id, 'cash flow', agent=identity, mode=mode,
        original_message=Message(role='user', content='  $cash-flow\nOriginal words.  '),
        project=project_context(research, node.research_id))
    async def forbidden(_): raise AssertionError('Auxiliary modes cannot delegate')
    tools = context_tools(snap, research.context.selector,
        (ToolBinding('delegate_task', 'Delegate and execute tasks', Value, forbidden),), {}, lambda: None,
        memories=research.memories, research_id=node.research_id)
    model = QueueModel([[call('load_skill', 'skill', {'name':'cash-flow'})], [text('Loaded relevant skill.')]])
    ledger = EngineLedgerProbe()
    ledger.check_cancelled = lambda *args: None
    engine = ControlledEngine(tasks, tmp_path/'private', ledger, SimpleNamespace(get_model=lambda _: model))
    request = RunRequest(run_id=run_id, agent_id=identity.id, input=snap.input)
    with admit(request, research_id=node.research_id, session_id=mode + ':session', task_id=mode + ':task',
            spec_version=1, kind=mode, validate=lambda: None):
        with pytest.raises(ValueError):
            engine.binding(request.model_copy(update={'input': snap.input.model_copy(update={'messages':(Message(role='user', content='tampered'),)})}))
        result = await CoreRunner(engine, AgentCatalog([AgentSpec(identity.id, 'Use relevant skill.', tools=tools)])).execute(request)
    assert result.status == 'succeeded', result
    assert ledger.frozen['entrance'] == mode and ledger.frozen['agent'] == identity.model_dump()
    assert all(not {'delegate_task', 'write_private_note', 'search_web'} & set(names) for names in model.tool_names)
    assert model.inputs[0][-1]['content'] == '  $cash-flow\nOriginal words.  '
    operations = [args[4].operation for args in ledger.reserved]
    assert operations.count('model') == 2 and operations.count('tool') == 1 and ledger.released


async def test_discussion_report_directory_rejects_unattached_peer_reports(workspace):
    from test_report_information import reports
    _, _, _, tasks, projects, _ = workspace
    _, _, saved, followup = await reports(workspace)
    own, peer = saved[0][1], saved[1][1]
    class ReadOnly:
        async def execute(self, request, agent, tools, invoke):
            tool = next(t for t in tools if t.name == 'read_context')
            listing = (await invoke('read_context', 'directory', {})).value
            assert {row['result_id'] for row in listing['results']} == {own.result_id}
            body = (await invoke('read_context', 'own', {'result_ids':[own.result_id]})).value
            assert body['results'][0]['output']['report'].startswith('Historical draft')
            tool = next(t for t in tools if t.name == 'read_context')
            with pytest.raises(ScopeDenied): await tool.handler(tool.input_type(result_ids=(peer.result_id,)))
            return RunResult(run_id=request.run_id, status='succeeded', output='Own report discussed without adoption.')
    tasks.engine = ReadOnly()
    thread = projects.expert_thread(followup.task_id)
    result = await projects.discuss(followup.task_id, 'Explain the prior report.', thread.revision)
    assert result.messages[-1].content == 'Own report discussed without adoption.'
