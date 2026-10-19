"""Dynamic selection through the business entrance and real SDK tool loop."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.adapters.openai import OpenAIEngine
from agent_core.composition import build_resources, build_research, build_tasks, build_projects
from agent_core.context.retrieval import rank
from agent_core.context.selection import ContextSelector
from agent_core.context.skills import SkillCatalog, LoadSkill, ReadSkillResource
from agent_core.context.scopes import ScopeDenied
from agent_core.context.builder import ContextBuilder
from agent_core.context.projection import ContextBudgetExceeded
from agent_core.contracts import AgentSpec, Message, PreparedInput, RunRequest, ToolBinding, ToolResult, Value
from agent_core.research.chat import ChatService
from agent_core.research.models import ChatMessage, NodeCommand
from agent_core.tool_routing import ToolRouter
from test_sdk_contract import QueueModel, call, text


def skill(root, name='cash-flow'):
    path = root / name / 'SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text(f'---\nname: {name}\ndescription: 现金流分析，核对利润和现金流。Cash flow analysis.\nallowed-tools: read_source forbidden_tool\n---\n保留现金流原文与来源。\n', encoding='utf-8')
    return path


@pytest.fixture
def memory_query_stub():
    """Explicit side-query double for namespace tests that use the raw SDK adapter."""
    from agent_core.context.memory_retrieval import current_memory_query
    async def select(value):
        return {'choices': [{'id': r['id'], 'version': r['version'], 'reason': 'Test-selected relevant procedure'}
            for r in value['candidates'][:value['limit']]]}
    token = current_memory_query.set(select)
    yield
    current_memory_query.reset(token)


@pytest.fixture
def app(tmp_path):
    db = MemoryDatabase()
    scopes = build_resources(db, tmp_path/'blobs')
    engine = ScriptedEngine(lambda r: ([], {'report': 'done', 'claims': [], 'blocking_missing': [], 'limitations': []}))
    research = build_research(db, scopes, engine)
    tasks = build_tasks(db, scopes, research, engine)
    build_projects(db, research, tasks)
    root = research.create('Review company evidence')
    skill(tmp_path/'skills')
    research.context.selector = ContextSelector(SkillCatalog(tmp_path/'skills'))
    yield db, scopes, research, tasks, root, engine
    db.close()


def test_rank_chinese_english_no_match_and_fts_injection():
    documents = ['Operating cash flow evidence 现金流利润', 'unrelated islands weather']
    assert rank('分析现金流', documents)[0][0] == 0
    assert rank('cash flow', documents)[0][0] == 0
    assert rank('astronomy', documents) == []
    assert rank('" OR * cash', documents)[0][0] == 0


async def test_skill_frozen_resources_and_permission_hints(tmp_path):
    path = skill(tmp_path)
    reference = path.parent/'references'/'rules.md'
    reference.parent.mkdir()
    reference.write_text('abcdefghij')
    catalog = SkillCatalog(tmp_path)
    entries = catalog.freeze()
    assert catalog.search(entries, '$cash-flow')[0]['reason'] == 'explicit'
    bindings = {t.name: t for t in catalog.bindings(entries, lambda: None)}
    loaded = await bindings['load_skill'].handler(LoadSkill(name='cash-flow'))
    assert loaded.value['skill']['instructions'] == path.read_text()
    first = await bindings['read_skill_resource'].handler(ReadSkillResource(name='cash-flow', path='references/rules.md', limit=4))
    assert first.value['text'] == 'abcd' and first.value['next_cursor']['offset'] == 4
    with pytest.raises(ValueError): catalog.read(entries[0], '../secret')
    path.write_text(path.read_text()+'changed')
    with pytest.raises(ValueError, match='changed'): catalog.read(entries[0])
    reference.unlink()
    reference.symlink_to(tmp_path.parent/'outside')
    with pytest.raises(ValueError, match='leaves'): catalog.read(entries[0], 'references/rules.md')


async def test_chat_selects_original_memory_sources_and_branch_history(app):
    db, scopes, research, tasks, root, engine = app
    chat_service = ChatService(research, tasks)
    chat = await chat_service.start(root.id, root.goal)
    correction = ChatMessage(role='user', content='旧约束：不要把利润当成现金流。')
    rows = [correction] + [ChatMessage(role='assistant', content='unrelated old weather '+str(i)) for i in range(12)]
    relevant = ChatMessage(role='assistant', content='现金流对账结论，来源尚待核实。')
    rows.insert(3, relevant)
    with db.transaction() as tx:
        tx.research.save_chat(chat.model_copy(update={'revision': 1, 'messages': tuple(rows)}), 0)
    original = '请用 $cash-flow 核对现金流。\n保留这个换行和原话。'
    chat = chat_service.send(chat.node_id, original, 1)
    good = tasks.memories.create(kind='experience', content='现金流分析先核对单位', source='user confirmed', confirmed=True)
    tasks.memories.create(kind='experience', content='现金流 draft SECRET', source='draft', confirmed=False)
    tasks.memories.create(kind='experience', content='现金流 expired SECRET', source='old', confirmed=True,
        expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    other = research.create('foreign research')
    projects = build_projects(db, research, tasks)
    foreign_project = projects.create('Other project')
    await projects.create_main_task(foreign_project.id, other.goal, other.id)
    tasks.memories.create(kind='experience', content='现金流 foreign SECRET', source='other', confirmed=True, scope='project', scope_id=foreign_project.id)
    scope = scopes.current(root.scope_id, research.owner)
    scope, ref = scopes.ingest(scope, content='现金流为七，利润为九。'.encode(), name='generic.txt', media_type='text/plain')
    scopes.ingest(scope, content=b'weather islands', name='weather.txt', media_type='text/plain')
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=root.id, node_id=chat.node_id,
        kind='chat_reply', chat_revision=chat.revision))
    assert research.ledger.get(command.run_id).result.status == 'succeeded'
    request = engine.requests[-1]
    assert request.input.messages[-1].content == original
    packet = json.loads(request.input.messages[0].content)
    ids = {m['id'] for m in packet['history']}
    assert correction.id in ids and relevant.id in ids and rows[1].id not in ids
    assert 'approved_memory' not in packet  # Selection is deferred until controlled admission.
    assert 'SECRET' not in request.input.messages[0].content
    assert packet['sources'][0]['ref'] == ref.model_dump(mode='json')
    assert packet['content'] == [] and '现金流为七' not in request.input.messages[0].content
    assert packet['skills'][0]['name'] == 'cash-flow'
    snapshot = scopes.repository.snapshot(command.run_id)
    assert snapshot.memory_refs == ()
    assert [(r.id, r.version) for r in snapshot.memory_catalog] == [(good.id, good.version)]
    assert snapshot.selection['source_matches'][0]['ref'] == ref.model_dump(mode='json')
    # Deleting auto-selected memory invalidates frozen input, just like explicit memory.
    tasks.memories.delete(good.id, good.version)
    with pytest.raises(ScopeDenied): research.validate_binding(research.query('run', command.run_id))


async def test_task_automatic_memory_revalidated_before_execution(app):
    _, _, research, tasks, root, _ = app
    memory = tasks.memories.create(kind='experience', content='cash flow keep source units', source='user', confirmed=True)
    spec = tasks.create(research_id=root.id, kind='analyze', user_request='cash flow analysis')
    binding, snapshot, _ = tasks._prepare(spec)
    assert snapshot.memory_refs == ()
    assert snapshot.memory_catalog[0].id == memory.id
    assert snapshot.input.messages[-1].content == spec.user_request
    tasks.memories.update(memory.id, memory.version, content='corrected')
    with pytest.raises(ScopeDenied): tasks._validate_durable_access(spec, binding)


async def test_result_retrieval_and_automatic_memory_deletion_at_publication(app):
    _, scopes, research, tasks, root, engine = app
    projects = research.collaboration.projects
    project = projects.create('Result retrieval')
    member = await projects.create_main_task(project.id, 'Cash flow evidence')
    root = research.research(member.research_id)
    chat_service = ChatService(research, tasks)
    chat = await chat_service.start(root.id, root.goal)
    chat = chat_service.send(chat.node_id, 'Compare cash flow reports', chat.revision)
    tasks.engine = ScriptedEngine(lambda r: ([], {'report':'cash flow seven; evidence boundary preserved', 'claims':[]}))
    spec = tasks.create(research_id=root.id, kind='synthesize', user_request='cash flow report', node_id=chat.node_id)
    receipt = (await tasks.run(spec.id))['receipt']
    candidates = research.context.selector.candidates_for_chat(research, research.node(chat.node_id),
        [m.model_dump(mode='json') for m in chat.messages], ())
    selection = research.context.selector.select(chat.messages[-1].content,
        history=candidates['history'], memories=candidates['memories'], sources=candidates['sources'], results=candidates['related_results'])
    assert selection['results'][0]['result_id'] == receipt.result_id
    assert selection['results'][0]['delivery'] == receipt.delivery
    assert selection['manifest']['result_matches'][0]['result_id'] == receipt.result_id
    memory = tasks.memories.create(kind='experience', content='cash flow units', source='user', confirmed=True)
    def delete_while_running(request):
        tasks.memories.delete(memory.id, memory.version)
        return [], {'report':'cash flow response', 'claims':[]}
    tasks.engine = ScriptedEngine(delete_while_running)
    second = tasks.create(research_id=root.id, kind='synthesize', user_request='cash flow followup')
    result = await tasks.run(second.id)
    execution = tasks.ledger.get(result['receipt'].run_id)
    assert execution.result.status == 'failed' and execution.result.error.code == 'input_revoked'


def test_projection_keeps_skill_and_exact_user_under_one_budget(app):
    _, scopes, research, _, root, _ = app
    scope = scopes.current(root.scope_id, research.owner)
    scope = scopes._replace(scope, max_input_chars=6000)
    history = [{'id': 'old-user', 'role':'user', 'content':'保留旧约束：现金流单位不能变。'}]
    history += [{'id':str(i), 'role':'assistant', 'content':'cash flow '+('x'*8000)} for i in range(8)]
    original = Message(role='user', content='现金流\n请保留原话。')
    history.append({'id':'new-user', 'role':'user', 'content':original.content})
    selection = research.context.selector.select(original.content, history=history)
    snapshot = ContextBuilder(scopes).build(scope, uuid4().hex, 'cash flow', selection=selection,
        current_message=original)
    assert snapshot.input.messages[-1] == original
    assert sum(len(m.content.encode()) for m in snapshot.input.messages) <= scope.max_input_chars
    packet = json.loads(snapshot.input.messages[0].content)
    assert packet['dynamic_context'] is True and packet['skills'][0]['name'] == 'cash-flow'
    assert snapshot.input.external_context and '保留旧约束' in packet['user_constraints'][0]['content']
    with pytest.raises(ContextBudgetExceeded):
        ContextBuilder(scopes).build(scope, uuid4().hex, 'cash flow', selection=selection,
            current_message=Message(role='user', content='原话'*5000))


def test_current_user_is_retained_once_without_dropping_older_constraints(app):
    _, scopes, research, _, root, _ = app
    scope = scopes.current(root.scope_id, research.owner)
    raw = '  用户原话必须保留。\n' * 450
    original = Message(role='user', content=raw)
    history = [{'id':'older', 'role':'user', 'content':'旧约束不能被撤销。'},
        {'id':'current', 'role':'user', 'content':raw}]
    selection = research.context.selector.select(raw, history=history)
    snap = ContextBuilder(scopes).build(scope, uuid4().hex, 'Respond to this request',
        selection=selection, current_message=original)
    payload = json.loads(snap.input.messages[0].content)
    assert payload['history'] == history[:1]
    assert payload['current_message_id'] == 'current'
    assert snap.input.messages[-1] == original
    assert raw not in snap.input.messages[0].content
    assert snap.selection['history_ids'] == ['older', 'current']
    assert sum(len(m.content.encode()) for m in snap.input.messages) <= scope.max_input_chars


class Args(Value):
    pass


async def test_real_sdk_search_loads_only_permitted_schema_and_checkpoint_restores():
    async def noop(args): return ToolResult(value='ok')
    permitted = (ToolBinding('forecast', 'forecast weather islands', Args, noop),
        ToolBinding('cash_report', 'cash flow report', Args, noop))
    router = ToolRouter({}, permitted)
    tools = permitted + (router.search_binding(),)
    spec = AgentSpec('dynamic', 'Answer original user request', tools=tools)
    request = RunRequest(agent_id='dynamic', input=PreparedInput(messages=(
        Message(role='user', content=json.dumps({'dynamic_context': True, 'goal': 'astronomy'})),
        Message(role='user', content='astronomy')), tool_query='astronomy'))
    class Pause(BaseException): pass
    class Checkpoint:
        head = None
        stopped = False
        def restore(self): return deepcopy(self.head)
        def publish(self, value):
            self.head = deepcopy(value)
            if value['kind'] == 'state' and 'cash_report' in value['selected_tools'] and not self.stopped:
                self.stopped = True
                raise Pause()
    checkpoint = Checkpoint()
    calls = []
    async def invoke(name, call_id, raw):
        calls.append(name)
        binding = next(t for t in tools if t.name == name)
        return await binding.handler(binding.input_type.model_validate_json(raw))
    read = call('cash_report', 'read').model_copy(update={'arguments': '{}'})
    model = QueueModel([[call('search_tools', 'lookup', {'query': 'cash flow'})], [read], [text('done')]])
    with pytest.raises(Pause):
        await OpenAIEngine(model, checkpoints=checkpoint).execute(request, spec, tools, invoke)
    assert checkpoint.head['selected_tools'] == ['cash_report']
    result = await OpenAIEngine(model, checkpoints=checkpoint).execute(request, spec, tools, invoke)
    assert result.status == 'succeeded', result
    assert calls == ['search_tools', 'cash_report']
    assert set(model.tool_names[0]) == {'search_tools'}
    assert set(model.tool_names[1]) == {'search_tools', 'cash_report'}
    assert 'forecast' not in model.tool_names[2]
    assert model.inputs[0][-1]['content'] == 'astronomy'
    assert ToolRouter({}, permitted, 'cash flow').selected == {'cash_report'}
