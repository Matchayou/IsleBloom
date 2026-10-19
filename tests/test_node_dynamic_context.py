"""Non-chat node commands use dynamic assembly without widening their inputs."""
import json
from uuid import uuid4

from agent_core.adapters.openai import OpenAIEngine
from agent_core.contracts import RunError, RunResult
from agent_core.research.models import NodeCommand
from test_dynamic_context import app
from test_sdk_contract import QueueModel, call, text


async def submit(research, root, kind, node_id=None, **values):
    return await research.submit(NodeCommand(command_id=uuid4().hex, research_id=root.id,
        kind=kind, node_id=node_id, **values))


async def test_plain_node_sdk_skill_activation_raw_fields_and_scope(app):
    _, scopes, research, tasks, root, _ = app
    scope = scopes.current(root.scope_id, research.owner)
    scope, selected = scopes.ingest(scope, content=b'Cash flow selected evidence.',
        name='selected.txt', media_type='text/plain')
    _, unrelated = scopes.ingest(scope, content=b'Cash flow PRIVATE_LIBRARY_MARKER',
        name='unrelated.txt', media_type='text/plain')
    memory = research.memories.create(kind='experience', content='cash flow preserve units',
        source='User', confirmed=True)
    domain = tasks.memories.create(kind='experience', content='cash flow DOMAIN_PRIVATE',
        source='User', confirmed=True, scope='specialist', scope_id='expert_synthesize')
    raw_goal = '  cash flow analysis\nretain unknowns  '
    raw_instruction = '  $cash-flow\n核对原文，不补造数字。  '
    node = await submit(research, root, 'create', goal=raw_goal, instructions=raw_instruction,
        resource_refs=(selected,))
    model = QueueModel([[call('load_skill', 'activate', {'name':'cash-flow'})],
        [call('read_source', 'outside', {'ref':unrelated.model_dump()})],
        [text(json.dumps({'report':'No invented numbers.', 'claims':[],
            'blocking_missing':[], 'limitations':[]}))]])
    research.engine = OpenAIEngine(model)
    command = await submit(research, root, 'run', node.node_id)
    snap = scopes.repository.snapshot(command.run_id)
    payload = json.loads(snap.input.messages[0].content)
    assert snap.selection and snap.agent.id == node.node_id
    assert snap.input.messages[-1].content == raw_instruction
    assert payload['node_request'] == {'goal':raw_goal, 'instructions':raw_instruction}
    assert 'history' not in payload
    assert payload['skills'][0]['name'] == 'cash-flow'
    assert '保留现金流原文与来源。' not in snap.input.messages[0].content
    assert '保留现金流原文与来源。' in json.dumps(model.inputs[1], ensure_ascii=False)
    assert snap.scope.allowed == (selected,) and unrelated not in snap.scope.allowed
    assert 'PRIVATE_LIBRARY_MARKER' not in snap.input.model_dump_json()
    assert 'PRIVATE_LIBRARY_MARKER' not in json.dumps(model.inputs, ensure_ascii=False)
    assert 'unavailable' in json.dumps(model.inputs[2], ensure_ascii=False)
    assert [r.id for r in snap.memory_catalog] == [memory.id]
    assert domain.id not in [r.id for r in snap.memory_catalog]
    assert all('forbidden_tool' not in names for names in model.tool_names)
    assert research.node(node.node_id).current_result_id is not None


async def test_plain_continue_preserves_command_and_retry_reuses_snapshot(app, monkeypatch):
    _, scopes, research, _, root, engine = app
    node = await submit(research, root, 'create', goal='cash flow initial')
    raw_goal, raw_instruction = '  cash flow next\n  ', '  do not invent\n  '
    async def fail(request, *args):
        return RunResult(run_id=request.run_id, status='failed',
            error=RunError(code='test', origin='engine', message='expected failure'))
    engine.execute = fail
    command = await submit(research, root, 'continue', node.node_id,
        goal=raw_goal, instructions=raw_instruction)
    original = scopes.repository.snapshot(command.run_id)
    payload = json.loads(original.input.messages[0].content)
    assert original.input.messages[-1].content == raw_instruction
    assert payload['node_command'] == {'kind':'continue', 'goal':raw_goal, 'instructions':raw_instruction}
    research.memories.create(kind='experience', content='cash flow NEW_MEMORY', source='User', confirmed=True)
    def unexpected(*args, **kwargs):
        raise AssertionError('Retry must not assemble or retrieve again')
    monkeypatch.setattr(research.context, 'assemble', unexpected)
    retry = await submit(research, root, 'retry', node.node_id)
    frozen = scopes.repository.snapshot(retry.run_id)
    assert frozen.input == original.input
    assert frozen.selection == original.selection
    assert frozen.memory_catalog == original.memory_catalog
    assert frozen.skill_catalog == original.skill_catalog
    assert 'NEW_MEMORY' not in frozen.input.model_dump_json()


async def test_plain_node_rejects_memory_revoked_during_execution(app):
    _, _, research, _, root, engine = app
    memory = research.memories.create(kind='experience', content='cash flow units', source='User', confirmed=True)
    node = await submit(research, root, 'create', goal='cash flow')
    async def revoke(request, *args):
        research.memories.delete(memory.id, memory.version)
        return RunResult(run_id=request.run_id, status='succeeded', output={
            'report':'Stale result', 'claims':[], 'blocking_missing':[], 'limitations':[]})
    engine.execute = revoke
    command = await submit(research, root, 'run', node.node_id)
    result = research.ledger.get(command.run_id).result
    assert result.status == 'failed' and result.error.code == 'scope_invalidated'
    assert research.node(node.node_id).current_result_id is None
