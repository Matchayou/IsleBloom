"""Progressive disclosure and namespace boundaries through admitted execution."""
import json
from uuid import uuid4
import pytest
from agent_core.agent_identity import AgentIdentity
from agent_core.context.assembler import project_context
from agent_core.context.capabilities import context_tools
from agent_core.context.scopes import ScopeDenied
from agent_core.contracts import AgentSpec, Message, ToolBinding, ToolResult, Value
from agent_core.context.skills import SkillCatalog
from agent_core.context.selection import ContextSelector
from test_dynamic_context import app, skill, memory_query_stub
from test_sdk_contract import QueueModel, call, text


async def test_memory_namespace_catalog_and_runtime_discovery(app, memory_query_stub):
    db, scopes, research, tasks, root, engine = app
    from agent_core.composition import build_projects
    projects = build_projects(db, research, tasks)
    project = projects.create('Shared project')
    await projects.create_main_task(project.id, root.goal, root.id)
    sibling = await projects.create_main_task(project.id, 'Another task')
    foreign = projects.create('Foreign project')
    def memory(scope='user', scope_id=None, content='cash flow units'):
        return tasks.memories.create(kind='experience', content=content, source='User approved',
            scope=scope, scope_id=scope_id, confirmed=True)
    user = memory()
    local = memory('project', project.id)
    domain = memory('specialist', 'expert_synthesize')
    hidden = memory('specialist', 'expert_extract', 'cash flow FOREIGN DOMAIN')
    foreign_memory = memory('project', foreign.id, 'cash flow FOREIGN PROJECT')
    temporary = AgentIdentity(mode='temporary', id='temporary:test', instructions='Review evidence')
    specialist = AgentIdentity(mode='specialist', id='expert_synthesize')
    assistant = AgentIdentity(mode='assistant', id='assistant')
    assert {m.id for m in tasks.memories.list(root.id, agent=specialist)} == {user.id, local.id, domain.id}
    assert {m.id for m in tasks.memories.list(sibling.research_id)} == {user.id, local.id}
    assert {m.id for m in tasks.memories.list(root.id, agent=temporary)} == {user.id, local.id}
    for agent in (temporary, assistant):
        with pytest.raises(ScopeDenied): tasks.memories.select(root.id, (domain.id,), agent=agent)
    for value in (hidden, foreign_memory):
        with pytest.raises(ScopeDenied): tasks.memories.select(root.id, (value.id,), agent=specialist)
    scope = scopes.current(root.scope_id, research.owner)
    original = Message(role='user', content='cash flow\nKeep my original words.')
    with pytest.raises(ScopeDenied):
        research.context.assemble(scopes, scope, uuid4().hex, 'Review cash flow', agent=temporary,
            original_message=original, project=project_context(research, root.id), required_memories=(domain,))
    for agent, expected in ((specialist, {user.id, local.id, domain.id}), (temporary, {user.id, local.id}), (assistant, {user.id, local.id})):
        snapshot = research.context.assemble(scopes, scope, uuid4().hex, 'Review cash flow', agent=agent,
            original_message=original, project=project_context(research, root.id),
            memories=[m for m in tasks.memories.list(root.id, agent=agent) if m.available()])
        assert snapshot.memory_refs == ()
        assert {r.id for r in snapshot.memory_catalog} == expected
        assert snapshot.input.messages[-1] == original
        tools = context_tools(snapshot, research.context.selector,
            (), {}, lambda: None,
            memories=tasks.memories, research_id=root.id)
        search = next(t for t in tools if t.name == 'search_memory')
        if agent.mode != 'specialist':
            with pytest.raises(ScopeDenied): await search.handler(search.input_type(query='cash flow', scope='specialist'))
        else:
            value = await search.handler(search.input_type(query='cash flow', scope='specialist'))
            assert [m['id'] for m in value.value['memories']] == [domain.id]
            tasks.memories.update(domain.id, domain.version, content='corrected domain')
            with pytest.raises(ScopeDenied): await search.handler(search.input_type(query='cash flow', scope='specialist'))


async def test_sdk_skill_candidate_load_and_permitted_schema_use_broker(app, tmp_path):
    from test_controlled import controlled_engine
    from agent_core.tasks.models import ExpertAnswer
    db, scopes, research, tasks, root, _ = app
    path = skill(tmp_path/'progressive')
    path.write_text(path.read_text().replace('read_source forbidden_tool', 'read_source host_shell') + 'BODY_ONLY_AFTER_LOAD\n')
    research.context.selector = ContextSelector(SkillCatalog(tmp_path/'progressive'))
    scope = scopes.current(root.scope_id, research.owner)
    snapshot = research.context.assemble(scopes, scope, uuid4().hex, 'cash flow',
        agent=AgentIdentity(mode='specialist', id='expert_synthesize'),
        original_message=Message(role='user', content='$cash-flow\nKeep this original request.'),
        project=project_context(research, root.id))
    packet = json.loads(snapshot.input.messages[0].content)
    assert packet['skills'][0]['name'] == 'cash-flow'
    assert 'instructions' not in packet['skills'][0] and 'allowed_tools' not in packet['skills'][0]
    assert 'BODY_ONLY_AFTER_LOAD' not in snapshot.input.messages[0].content
    async def observed(args): return ToolResult(value='observed')
    tools = context_tools(snapshot, research.context.selector,
        (ToolBinding('read_source', 'weather islands', Value, observed),), {}, lambda: None,
        memories=tasks.memories, research_id=root.id)
    model = QueueModel([[call('load_skill', 'activate', {'name':'cash-flow'})],
        [call('read_source', 'read').model_copy(update={'arguments':'{}'})], [text(json.dumps({'report':'done', 'claims':[]}))]])
    engine, ledger, request = controlled_engine(tmp_path/'broker', model)
    request = request.model_copy(update={'input': snapshot.input.model_copy(update={'tool_query':'cash flow'})})
    async def invoke(name, call_id, raw):
        binding = next(t for t in tools if t.name == name)
        return await binding.handler(binding.input_type.model_validate_json(raw))
    result = await engine.execute(request, AgentSpec('expert', 'Use relevant skills', tools=tools,
        output_type=ExpertAnswer), tools, invoke)
    assert result.status == 'succeeded', result
    assert 'read_source' not in model.tool_names[0]
    assert 'read_source' in model.tool_names[1]
    assert all('host_shell' not in names for names in model.tool_names)
    assert model.inputs[0][-1]['content'] == '$cash-flow\nKeep this original request.'
    assert 'BODY_ONLY_AFTER_LOAD' in json.dumps(model.inputs[1], ensure_ascii=False)
    operations = [args[4].operation for args in ledger.reserved]
    assert operations.count('model') == 3 and operations.count('tool') == 2


async def test_expert_runtime_memory_read_and_revoke_prevents_publication(app, memory_query_stub):
    from agent_core.adapters.openai import OpenAIEngine
    db, scopes, research, tasks, root, _ = app
    scope = scopes.current(root.scope_id, research.owner)
    _, ref = scopes.ingest(scope, content=b'Current source evidence.', name='source.txt', media_type='text/plain')
    memory = tasks.memories.create(kind='experience', content='nebula domain procedure', source='Approved by user',
        scope='specialist', scope_id='expert_synthesize', confirmed=True)
    spec = tasks.create(research_id=root.id, kind='synthesize', user_request='Review the current source.',
        required_refs=(ref,))
    class RevokeAfterRead(QueueModel):
        async def get_response(self, **kwargs):
            if self.inputs:
                outputs = [json.loads(item['output'])['value'] for item in kwargs['input']
                    if item.get('type') == 'function_call_output']
                assert outputs[0]['memories'][0]['id'] == memory.id
                tasks.memories.delete(memory.id, memory.version)
            return await super().get_response(**kwargs)
    model = RevokeAfterRead([[call('search_memory', 'domain-read', {'query':'nebula', 'scope':'specialist'})],
        [text(json.dumps({'report':'Reviewed source', 'claims':[]}))]])
    tasks.engine = OpenAIEngine(model)
    outcome = await tasks.run(spec.id)
    snapshot = scopes.repository.snapshot(outcome['run_id'])
    assert 'nebula domain procedure' not in snapshot.input.messages[0].content
    assert snapshot.memory_refs == ()
    assert [r.id for r in snapshot.memory_catalog] == [memory.id]
    assert 'search_memory' in model.tool_names[0]
    assert outcome['receipt'].delivery == 'rework'
    result = tasks.ledger.get(outcome['run_id']).result
    assert result.status == 'failed' and result.error.code == 'input_revoked'


async def test_memory_api_uses_explicit_namespaces_and_rejects_removed_field(app):
    import httpx
    from agent_core.app.api import create_app
    from agent_core.composition import build_projects
    db, _, research, tasks, root, _ = app
    projects = build_projects(db, research, tasks)
    project = projects.create('API memory project')
    await projects.create_main_task(project.id, root.goal, root.id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)), base_url='http://test') as client:
        entries = {}
        for scope, scope_id in (('user', None), ('project', project.id), ('specialist', 'expert_synthesize')):
            response = await client.post('/api/memory_create', json={'kind':'experience', 'content':'cash flow',
                'source':'User approved', 'scope':scope, 'scope_id':scope_id, 'confirmed':True})
            assert response.status_code == 201, response.text
            entries[scope] = response.json()['id']
        response = await client.get('/api/memories', params={'research_id':root.id})
        assert {m['id'] for m in response.json()} == {entries['user'], entries['project']}
        response = await client.get('/api/memories', params={'research_id':root.id, 'specialist_id':'expert_synthesize'})
        assert {m['id'] for m in response.json()} == set(entries.values())
        response = await client.post('/api/memory_create', json={'kind':'experience', 'content':'old contract',
            'source':'User', 'research_id':root.id})
        assert response.status_code == 409 and 'extra_forbidden' in response.json()['error']
        response = await client.post('/api/memory_create', json={'kind':'experience', 'content':'invalid namespace',
            'source':'User', 'scope':'user', 'scope_id':project.id})
        assert response.status_code == 409
