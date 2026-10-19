"""Semantic memory contract and real SDK/Broker calls with deterministic models."""
import json
from types import SimpleNamespace
import pytest
from agent_core.agent_identity import AgentIdentity
from agent_core.context.assembler import project_context
from agent_core.context.capabilities import context_tools
from agent_core.context.memory_retrieval import current_memory_query, select_memories, MAX_INPUT_BYTES
from agent_core.context.projection import ContextBudgetExceeded
from agent_core.contracts import AgentSpec, Message, CoreFailure, RunLimits
from agent_core.memories.models import MemoryEntry
from agent_core.tasks.models import ExpertAnswer
from test_dynamic_context import app
from test_controlled import controlled_engine
from test_sdk_contract import QueueModel, call, text


def choice(memory, reason='Relevant despite different wording'):
    return {'id': memory.id, 'version': memory.version, 'reason': reason}


@pytest.mark.parametrize('bad', ['foreign', 'version', 'duplicate', 'excess'])
async def test_invalid_selection_rejected(bad):
    memories = [MemoryEntry(id=str(i), owner='local', kind='experience', content='procedure', source='user') for i in range(4)]
    rows = [choice(memories[0])]
    if bad == 'foreign': rows[0]['id'] = 'not-in-catalog'
    if bad == 'version': rows[0]['version'] = 99
    if bad == 'duplicate': rows *= 2
    if bad == 'excess': rows = [choice(m) for m in memories]
    async def query(_): return {'choices': rows}
    token = current_memory_query.set(query)
    try:
        with pytest.raises(ValueError): await select_memories('query', memories)
    finally: current_memory_query.reset(token)


async def test_batch_all_bodies_then_merge_reasons_and_load_originals():
    memories = [MemoryEntry(id=str(i), owner='local', kind='experience', content=('经验' * 900) + str(i), source='user') for i in range(12)]
    seen, merges = [], []
    async def query(value):
        rows = value['candidates']
        if 'content' in rows[0]: seen.extend(r['id'] for r in rows)
        else: merges.append(rows)
        return {'choices': [{'id': r['id'], 'version': r['version'], 'reason': 'Useful procedure'} for r in rows[:value['limit']]]}
    token = current_memory_query.set(query)
    try:
        selected, reasons = await select_memories('Different wording', memories)
        assert seen == [m.id for m in memories] and merges
        assert len(selected) == 3 and len(reasons) == 3
        assert selected[0] is memories[0]  # Never substitute the model's explanation for original content.
        seen.clear()
        too_large = memories[-1].model_copy(update={'content': 'x' * MAX_INPUT_BYTES})
        with pytest.raises(ContextBudgetExceeded): await select_memories('query', [memories[0], too_large])
        assert not seen  # Validate the complete catalog before billing any batch.
    finally: current_memory_query.reset(token)


async def test_empty_catalog_or_no_match():
    assert await select_memories('query', []) == ((), ())
    memory = MemoryEntry(owner='local', kind='preference', content='Use metric units', source='user')
    async def query(_): return {'choices': []}
    token = current_memory_query.set(query)
    try: assert await select_memories('unrelated', [memory]) == ((), ())
    finally: current_memory_query.reset(token)


def admitted(app, tmp_path, primary, side):
    _, scopes, research, tasks, root, _ = app
    engine, ledger, request = controlled_engine(tmp_path, primary)
    from agent_core.controlled.ledger import digest
    reserve = ledger.reserve
    def record(*args):
        reserve(*args)
        frame = args[4]
        ledger.operations[frame.operation_id] = {'request_hash': digest(frame.payload), 'kind': frame.operation}
    ledger.reserve = record
    engine.tasks = tasks
    def binding(_):
        research.memories.validate(root.id, snapshot.memory_catalog, agent=snapshot.agent)
        return None, {'run_id': 'r', 'entrance': 'expert', 'research_id': root.id}, 'a' * 64
    engine.binding = binding
    names = []
    def model(name):
        names.append(name)
        return side if name == 'light-test-model' else primary
    engine.model_provider = SimpleNamespace(get_model=model)
    manifests = []
    ledger.save_manifest = lambda *args: manifests.append(args)
    scope = scopes.current(root.scope_id, research.owner)
    snapshot = research.context.assemble(scopes, scope, 'r', 'Verify the financial evidence',
        agent=AgentIdentity(mode='specialist', id='expert_synthesize'),
        original_message=Message(role='user', content='  Reconcile liquidity.\nKeep this exact request.  '),
        project=project_context(research, root.id), memories=research.memories.list(root.id, agent=AgentIdentity(mode='specialist', id='expert_synthesize')))
    request = request.model_copy(update={'input': snapshot.input})
    tools = context_tools(snapshot, research.context.selector, (), {}, lambda: None,
        memories=research.memories, research_id=root.id)
    async def invoke(name, call_id, raw):
        binding = next(t for t in tools if t.name == name)
        return await binding.handler(binding.input_type.model_validate_json(raw))
    return engine, ledger, request, snapshot, tools, invoke, names, manifests


async def test_initial_and_runtime_queries_use_light_model_budget_audit_and_checkpoint(app, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_MEMORY_MODEL', 'light-test-model')
    _, scopes, research, tasks, root, _ = app
    memory = research.memories.create(kind='experience', content='现金流分析先核对单位', source='user', confirmed=True,
        scope='specialist', scope_id='expert_synthesize')
    research.memories.create(kind='experience', content='FOREIGN_DOMAIN_SECRET', source='user', confirmed=True,
        scope='specialist', scope_id='expert_extract')
    research.memories.create(kind='experience', content='DRAFT_SECRET', source='user')
    primary = QueueModel([[call('search_memory', 'lookup', {'scope': 'specialist', 'query': '现金流'})],
        [text(json.dumps({'report': 'done', 'claims': []}))]])
    side = QueueModel([[text(json.dumps({'choices': [choice(memory)]}))],
        [text(json.dumps({'choices': [choice(memory)]}))]])
    engine, ledger, request, snapshot, tools, invoke, names, manifests = admitted(app, tmp_path, primary, side)
    result = await engine.execute(request, AgentSpec('expert', 'Use the authorized evidence', output_type=ExpertAnswer), tools, invoke)
    assert result.status == 'succeeded', result
    assert names[-1] == 'light-test-model' and len(side.inputs) == 2
    assert all(not names for names in side.tool_names)
    candidate_input = json.dumps(side.inputs, ensure_ascii=False)
    assert memory.content in candidate_input and 'FOREIGN_DOMAIN_SECRET' not in candidate_input and 'DRAFT_SECRET' not in candidate_input
    assert primary.inputs[0][-1]['content'] == request.input.messages[-1].content
    assert memory.content in json.dumps(primary.inputs[0], ensure_ascii=False)
    returned = [json.loads(i['output'])['value'] for i in primary.inputs[1] if i.get('type') == 'function_call_output']
    assert returned[0]['memories'][0]['content'] == memory.content
    frames = [args[4] for args in ledger.reserved]
    assert [f.payload.get('purpose', 'task') for f in frames if f.operation == 'model'] == ['memory', 'task', 'memory', 'task']
    assert len([args for args in manifests if args[2] == 'responded' and args[3]['purpose'] == 'memory']) == 2
    assert 'memory:initial' in ledger.saved
    assert snapshot.memory_refs == () and snapshot.memory_catalog[0].id == memory.id
    # Receipts also recover a side response if the process died before the
    # aggregate initial-memory checkpoint or SDK state was published.
    from agent_core.controlled.checkpoints import Checkpoints
    from agent_core.controlled.workspace import Workspace
    from agent_core.context.artifacts import ContextArtifacts
    work = Workspace(tmp_path / ('a' * 64))
    try:
        broker = SimpleNamespace(ledger=ledger, request=request, validate=lambda: None,
            workspace='a' * 64, token='token', epoch=1,
            artifacts=ContextArtifacts(work, lambda: None, lambda _: None))
        checkpoint = Checkpoints(broker)
        side_frame = next(f for f in frames if f.payload.get('purpose') == 'memory')
        operation_id, response = checkpoint.operation('model', side_frame.payload, None)
        assert operation_id == side_frame.operation_id
        assert json.loads(response['output'][0]['content'][0]['text'])['choices'][0]['id'] == memory.id
        assert checkpoint.model_cursor == 0  # Side queries never advance the SDK task cursor.
    finally: work.close()
    # A fresh process-equivalent engine reuses the final SDK state and side-query receipts.
    second = await engine.execute(request, AgentSpec('expert', 'Use the authorized evidence', output_type=ExpertAnswer), tools, invoke)
    assert second.status == 'succeeded' and len(side.inputs) == 2 and len(primary.inputs) == 2


async def test_side_query_unknown_completion_cannot_be_retried(app, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_MEMORY_MODEL', 'light-test-model')
    research = app[2]
    research.memories.create(kind='experience', content='Reusable procedure', source='user', confirmed=True)
    class Fail(QueueModel):
        async def get_response(self, **kwargs):
            self.inputs.append(kwargs['input'])
            raise TimeoutError()
    primary, side = QueueModel([]), Fail([])
    engine, ledger, request, _, tools, invoke, _, _ = admitted(app, tmp_path, primary, side)
    spec = AgentSpec('expert', 'Use evidence', output_type=ExpertAnswer)
    result = await engine.execute(request, spec, tools, invoke)
    assert result.status == 'failed' and len(side.inputs) == 1 and not primary.inputs
    again = await engine.execute(request, spec, tools, invoke)
    assert again.status == 'failed' and len(side.inputs) == 1
    assert any(f.payload.get('purpose') == 'memory' for a in ledger.reserved for f in [a[4]])
    # Check the persistent operation rule directly, even if no SDK head survived.
    from agent_core.controlled.checkpoints import Checkpoints
    cp = Checkpoints(SimpleNamespace(ledger=ledger, request=request, memory_context=None))
    cp.load = lambda _: None
    payload = next(a[4].payload for a in ledger.reserved if a[4].payload.get('purpose') == 'memory')
    with pytest.raises(CoreFailure) as error: cp.operation('model', payload, None)
    assert error.value.error.code == 'memory_query_unconfirmed'


async def test_budget_failure_does_not_call_side_or_fallback(app, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_MEMORY_MODEL', 'light-test-model')
    app[2].memories.create(kind='experience', content='Reusable procedure', source='user', confirmed=True)
    primary, side = QueueModel([]), QueueModel([])
    engine, ledger, request, _, tools, invoke, _, _ = admitted(app, tmp_path, primary, side)
    request = request.model_copy(update={'limits': RunLimits(max_turns=1)})
    result = await engine.execute(request, AgentSpec('expert', 'Use evidence', output_type=ExpertAnswer), tools, invoke)
    assert result.status == 'failed' and not side.inputs and not primary.inputs
    assert result.error.code == 'memory_query_budget'


async def test_revocation_during_side_query_stops_before_primary_model(app, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_MEMORY_MODEL', 'light-test-model')
    research = app[2]
    memory = research.memories.create(kind='experience', content='Reusable procedure', source='user', confirmed=True)
    class Revoke(QueueModel):
        async def get_response(self, **kwargs):
            response = await super().get_response(**kwargs)
            research.memories.delete(memory.id, memory.version)
            return response
    primary = QueueModel([])
    side = Revoke([[text(json.dumps({'choices': [choice(memory)]}))]])
    engine, ledger, request, _, tools, invoke, _, _ = admitted(app, tmp_path, primary, side)
    result = await engine.execute(request, AgentSpec('expert', 'Use evidence', output_type=ExpertAnswer), tools, invoke)
    assert result.status == 'failed' and len(side.inputs) == 1 and not primary.inputs
    assert 'memory:initial' not in ledger.saved
