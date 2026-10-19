"""First-layer agents through production admission, Core and real SDK calls."""
import json
from uuid import uuid4
import pytest
from agent_core.adapters.openai import OpenAIEngine
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.agent_identity import AgentIdentity
from agent_core.context.scopes import ScopeDenied
from agent_core.execution.ports import Conflict
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from test_collaboration import workspace, seeded, fixed_review_advice
from test_sdk_contract import QueueModel, call, text
from agent_core.contracts import AgentSpec, Message, PreparedInput, ToolBinding


async def granted_sources(workspace):
    _, scopes, research, _, _, _ = workspace
    node, ref, chat = await seeded(workspace)
    scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, extra = scopes.ingest(scope, content=b'Authorized covariance evidence seven.', name='covariance.txt', media_type='text/plain')
    return node, ref, extra, chat


async def test_main_launches_temporary_and_specialist_with_original_handoff(workspace):
    _, scopes, research, tasks, _, c = workspace
    node, ref, extra, chat = await granted_sources(workspace)
    original = '请核对资料。\n不要把未知值写成零。'
    chat = ChatService(research, tasks).send(node.id, original, chat.revision)
    worker = ScriptedEngine(lambda r: ([], {'report':'review complete', 'claims':[]}))
    tasks.engine = worker
    manager = ScriptedEngine(lambda r: ([
        Call('delegate_task', {'agent_mode':'temporary', 'expert_id':'expert_synthesize',
            'role_instructions':'Review covariance for this task only.', 'task_instruction':'Check covariance',
            'delivery_requirements':'Report sources and gaps', 'resource_refs':[ref.model_dump()], 'authorized_refs':[extra.model_dump()]}, 'temp'),
        Call('delegate_task', {'agent_mode':'specialist', 'expert_id':'expert_synthesize',
            'task_instruction':'Synthesize selected evidence', 'delivery_requirements':'Report sources and gaps',
            'resource_refs':[ref.model_dump()]}, 'specialist')], {'report':'Tasks accepted', 'claims':[]}))
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(research.database, scopes, bindings, service, manager)
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=node.research_id,
        node_id=node.id, kind='chat_reply', chat_revision=chat.revision))
    assert research.ledger.get(command.run_id).result.status == 'succeeded'
    intents = c.query('intents_for', node.research_id)
    assert len(intents) == 2
    for intent in intents:
        packet = c.packet(intent.packet_id)
        spec = tasks.spec(intent.task_id)
        assert packet.agent == spec.agent and packet.user_instruction == original
        assert packet.project['research_id'] == node.research_id and packet.limits == spec.limits
        assert packet.authorized_refs == spec.authorized_refs
        await c.execute(intent.command_id)
        assert c.query('intent', intent.command_id).status == 'completed'
        run = tasks.query('list_runs', spec.id)[0]
        snapshot = scopes.repository.snapshot(run.id)
        assert run.agent == snapshot.agent == spec.agent
        assert snapshot.input.messages[-1].content == original
        assert snapshot.selection['agent']['mode'] == spec.agent.mode
        assert json.loads(snapshot.input.messages[0].content)['project']['research_id'] == node.research_id
    assert {r.agent_id for r in worker.requests} == {tasks.spec(i.task_id).agent.id for i in intents}
    assert manager.requests[0].input.messages[-1].content == original


async def test_real_sdk_expert_discovers_authorized_directory_and_reads_selected_source(workspace):
    _, scopes, research, tasks, _, c = workspace
    node, ref, extra, _ = await granted_sources(workspace)
    intent = c.delegate_task(command_id='temporary-directory', node_id=node.id,
        expert_id='expert_synthesize', agent_mode='temporary', role_instructions='Find covariance evidence.',
        task_instruction='Read the covariance source', delivery_requirements='Return the observed fragment',
        resource_refs=(ref,), authorized_refs=(extra,))
    model = QueueModel([
        [call('search_tools', 'discover', {'query':'search authorized source metadata'})],
        [call('search_context', 'directory', {'query':'covariance'})],
        [call('search_tools', 'enable-read', {'query':'read immutable source'})],
        [call('read_source', 'body', {'ref':extra.model_dump()})],
        [text(json.dumps({'report':'Authorized covariance evidence seven.', 'claims':[]}))]])
    tasks.engine = OpenAIEngine(model)
    outcome = await c.execute(intent.command_id)
    assert outcome['intent'].status == 'completed', outcome
    spec = tasks.spec(intent.task_id)
    run = tasks.query('list_runs', spec.id)[0]
    snapshot = scopes.repository.snapshot(run.id)
    initial = json.loads(snapshot.input.messages[0].content)
    assert 'Authorized covariance evidence seven.' not in snapshot.input.messages[0].content
    assert extra in snapshot.scope.allowed and extra not in [m.ref for m in snapshot.mounts]
    outputs = [json.loads(i['output'])['value'] for i in model.inputs[-1] if i.get('type') == 'function_call_output']
    directory = next(v for v in outputs if v.get('category') == 'sources')
    assert directory['items'][0]['ref'] == extra.model_dump()
    assert any('fragments' in v and v['ref'] == extra.model_dump() for v in outputs)
    assert extra in tasks.receipt(f'task-result:{run.id}').resource_refs
    assert extra in c.read_refs(run.id)
    assert 'search_context' in model.tool_names[1]


async def test_directory_rejects_foreign_read_and_revocation(workspace):
    _, scopes, research, tasks, _, c = workspace
    node, ref, extra, _ = await granted_sources(workspace)
    foreign = research.create('Other research')
    foreign_scope = scopes.current(foreign.scope_id, research.owner)
    _, foreign_ref = scopes.ingest(foreign_scope, content=b'PRIVATE', name='private.txt', media_type='text/plain')
    with pytest.raises(ScopeDenied):
        c.delegate_task(command_id='foreign', node_id=node.id, expert_id='expert_synthesize',
            task_instruction='Review', delivery_requirements='Report', resource_refs=(ref,), authorized_refs=(foreign_ref,))
    spec = tasks.create(research_id=node.research_id, kind='synthesize', user_request='Review',
        agent_mode='temporary', role_instructions='Review the admitted source only.', required_refs=(ref,), authorized_refs=(extra,))
    tasks.engine = ScriptedEngine(lambda r: ([Call('read_source', {'ref':foreign_ref.model_dump()}, 'foreign-read')], {'report':'bad'}))
    receipt = (await tasks.run(spec.id))['receipt']
    assert tasks.ledger.get(receipt.run_id).result.error.code == 'scope_denied'
    second = tasks.create(research_id=node.research_id, kind='synthesize', user_request='Review', required_refs=(ref,), authorized_refs=(extra,))
    binding, _, _ = tasks._prepare(second)
    root_scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    scopes.revoke(root_scope, extra)
    with pytest.raises(ScopeDenied): tasks._validate_durable_access(second, binding)


async def test_specialists_reuse_identity_temporaries_do_not_and_revisions_keep_directory(workspace):
    _, _, research, tasks, _, c = workspace
    node, ref, extra, _ = await granted_sources(workspace)
    kwargs = dict(node_id=node.id, expert_id='expert_synthesize', task_instruction='Review',
        delivery_requirements='Report', resource_refs=(ref,), authorized_refs=(extra,))
    first = c.delegate_task(command_id='specialist-one', **kwargs)
    second = c.delegate_task(command_id='specialist-two', **kwargs)
    assert first.session_id == second.session_id
    assert tasks.spec(first.task_id).agent.id == tasks.spec(second.task_id).agent.id == 'expert_synthesize'
    temp1 = c.delegate_task(command_id='temp-one', agent_mode='temporary', role_instructions='Review once.', **kwargs)
    temp2 = c.delegate_task(command_id='temp-two', agent_mode='temporary', role_instructions='Review once.', **kwargs)
    assert temp1.session_id != temp2.session_id
    assert tasks.spec(temp1.task_id).agent.id != tasks.spec(temp2.task_id).agent.id
    with pytest.raises(Conflict):
        c.delegate_task(command_id='reuse-temp', **{**kwargs, 'expert_id':None, 'session_id':temp1.session_id})
    old = tasks.spec(temp1.task_id)
    revision = c.revise_task(command_id='revise-temp', task_id=old.id, expected_version=old.version,
        request='Recheck the source', mode='save')
    await c.process_revision(revision.id)
    new = tasks.spec(old.id)
    assert new.agent == old.agent and new.authorized_refs == old.authorized_refs
    assert c.packet(new.context_packet_id).agent == old.agent
    assert AgentIdentity(mode='specialist', id='expert_synthesize').instructions == ''


async def test_context_discovery_crosses_controlled_broker_and_is_audited(workspace, tmp_path):
    from test_controlled import controlled_engine
    from agent_core.context.directory_tools import source_directory
    from agent_core.tasks.models import ExpertAnswer
    from agent_core.tool_routing import ToolRouter
    _, scopes, research, _, _, _ = workspace
    node, _, extra, _ = await granted_sources(workspace)
    scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    tool = source_directory(research.context.selector, scopes, scope, (extra,), lambda: scopes.valid(scope))
    discovery = ToolRouter({}, (tool,)).search_binding()
    observed = []
    queue = [
        [call('search_tools', 'search', {'query':'authorized source metadata'})],
        [call('search_context', 'directory', {'query':'covariance'})],
        [text(json.dumps({'report':'Found authorized directory.', 'claims':[]}))]]
    from agents import ModelResponse
    from agents.usage import Usage as SDKUsage
    class Model:
        async def get_response(self, **kwargs):
            names = {t.name for t in kwargs['tools']}
            assert 'host_shell' not in names
            if len(observed) == 0: assert 'search_context' not in names
            else: assert 'search_context' in names
            observed.append(kwargs)
            return ModelResponse(output=queue.pop(0), usage=SDKUsage(requests=1), response_id=None)
    engine, ledger, request = controlled_engine(tmp_path, Model())
    request = request.model_copy(update={'input': PreparedInput(messages=(Message(role='user', content='astronomy'),), tool_query='astronomy')})
    async def forbidden(_): raise AssertionError('Host shell must not run')
    tools = (tool, discovery, ToolBinding('host_shell', 'Forbidden', tool.input_type, forbidden))
    async def invoke(name, call_id, raw):
        selected = next(t for t in tools if t.name == name)
        return await selected.handler(selected.input_type.model_validate_json(raw))
    result = await engine.execute(request, AgentSpec('expert', 'Find permitted data.', tools=tools, output_type=ExpertAnswer), tools, invoke)
    assert result.status == 'succeeded', result
    operations = [args[4].operation for args in ledger.reserved]
    assert operations.count('model') == 3
    assert operations.count('tool') == 1 and operations.count('private') == 1
    assert ledger.released


async def test_directory_only_task_needs_no_web_grant_and_temporary_discussion_keeps_role(workspace):
    from agent_core.contracts import RunResult
    _, scopes, research, tasks, projects, c = workspace
    node, _, extra, _ = await granted_sources(workspace)
    root_scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    scopes._replace(root_scope, tools=('read_source', 'search_source'))
    role = 'Check covariance in this admitted directory only.'
    intent = c.delegate_task(command_id='directory-only', node_id=node.id, expert_id='expert_synthesize',
        agent_mode='temporary', role_instructions=role, task_instruction='Find covariance evidence',
        delivery_requirements='Report evidence', authorized_refs=(extra,))
    spec = tasks.spec(intent.task_id)
    assert not spec.required_refs and spec.authorized_refs == (extra,)
    binding, snapshot, scope = tasks._prepare(spec)
    assert snapshot.mounts == () and extra in scope.allowed
    class Discussion:
        async def execute(self, request, agent, tools, invoke):
            assert agent.id == spec.agent.id and request.agent_id == spec.agent.id
            assert agent.instructions.startswith(role)
            assert {'search_skills', 'load_skill', 'search_memory'} <= {t.name for t in tools}
            assert not {'delegate_task', 'search_web', 'write_source'} & {t.name for t in tools}
            return RunResult(run_id=request.run_id, status='succeeded', output='A fixed-role explanation.')
    tasks.engine = Discussion()
    thread = projects.expert_thread(spec.id)
    result = await projects.discuss(spec.id, 'Explain the role', thread.revision)
    assert result.messages[-1].content == 'A fixed-role explanation.'
