"""Bounded manager context + real SDK/isolated PostgreSQL workflow routing."""
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from agents.run import ModelInputData
from agent_core.app.api import create_app
from agent_core.context.artifacts import ContextArtifacts, ReadContextPage
from agent_core.context.projection import dumps
from agent_core.controlled.model_projection import ModelProjection
from agent_core.controlled.workspace import Workspace
from agent_core.research.models import NodeCommand
from agent_core.research.chat import ChatService
from agent_core.context.scopes import ScopeDenied
from test_context_projection import controlled
from test_controlled_collaboration import seed, call, answer
from test_collaboration import workspace, seeded


async def reply(research, node, chat):
    command = await research.submit(NodeCommand(command_id=uuid4().hex, research_id=node.research_id,
        node_id=node.id, kind='chat_reply', chat_revision=chat.revision))
    result = research.ledger.get(command.run_id).result
    assert result.status == 'succeeded', result
    return command


async def test_sdk_status_only_reads_named_task_and_http_needs_no_model(controlled, monkeypatch):
    db, scopes, research, tasks, projects, collaboration, provider = controlled
    node, ref, chat = await seed(controlled)
    intent = collaboration.delegate_task(command_id=uuid4().hex, node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review selected evidence',
        delivery_requirements='Facts, sources and gaps', resource_refs=(ref,))
    async def fail(_): raise RuntimeError('synthetic provider failure')
    provider.plans.append([fail])
    await collaboration.execute(intent.command_id)
    def forbidden(*a, **kw): raise AssertionError('Status entered research discovery/body loading')
    monkeypatch.setattr(collaboration, 'find_experts', forbidden)
    monkeypatch.setattr(collaboration, 'context_directory', forbidden)
    monkeypatch.setattr(collaboration, 'budget_status', forbidden)
    monkeypatch.setattr(scopes, 'read', forbidden)
    chat = ChatService(research, tasks).send(node.id, '你为什么没有派发资料搜集专家？', chat.revision)
    offered_tools = set()
    async def status(kwargs):
        offered_tools.update(t.name for t in kwargs['tools'])
        from agent_core.app.manager_profiles import PROFILES
        assert PROFILES['reports'].instructions not in kwargs['system_instructions']
        return call('get_task_status', task_id=intent.task_id)
    async def finish(kwargs):
        outputs = [json.loads(i['output'])['value'] for i in kwargs['input'] if i.get('type') == 'function_call_output']
        assert outputs[-1]['execution_status'] == 'failed'
        assert outputs[-1]['error']['code'] == 'model_request_failed'
        assert 'budget' not in outputs[-1] and 'runs' not in outputs[-1]
        return answer({'report': '已派发，但执行失败；尚未取得调查结果。', 'claims': []})
    provider.plans.append([status, finish])
    await reply(research, node, chat)
    assert {'get_task_status', 'select_task_tools', 'read_context_page', 'search_tools', 'search_memory'} <= offered_tools, offered_tools
    calls = len(provider.requests)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)), base_url='http://test') as client:
        result = await client.get('/api/task_status', params={'research_id':node.research_id,'task_id':intent.task_id})
    assert result.status_code == 200, result.text
    assert result.json()['execution_status'] == 'failed'
    assert len(provider.requests) == calls
    with pytest.raises(ScopeDenied):
        collaboration.task_status(uuid4().hex, intent.task_id)
    scopes.revoke(scopes.current(research.research(node.research_id).scope_id, research.owner), ref)
    with pytest.raises(ScopeDenied):
        collaboration.task_status(node.research_id, intent.task_id)


async def test_sdk_loads_only_selected_tools_and_reads_new_content(controlled):
    db, scopes, research, tasks, projects, collaboration, provider = controlled
    node, ref, chat = await seed(controlled)
    initially_loaded = set()
    async def initial(kwargs):
        initially_loaded.update(t.name for t in kwargs['tools'])
        return call('select_task_tools', profile='sources')
    async def selected(kwargs):
        names = {t.name for t in kwargs['tools']}
        assert {'search_context', 'list_context', 'read_context', 'search_web'} <= names
        assert not ({'delegate_task', 'revise_task', 'find_experts'} - initially_loaded) & names
        return call('read_context')
    async def recover(kwargs):
        last = json.loads(kwargs['input'][-1]['output'])['value']
        assert last['status'] == 'invalid_input'
        return call('search_context', category='sources', query='ce2')
    async def read(kwargs):
        last = json.loads(kwargs['input'][-1]['output'])['value']
        assert last['items'][0]['ref'] == ref.model_dump(mode='json')
        return call('read_context', resource_refs=[ref.model_dump(mode='json')])
    async def switch(kwargs):
        assert 'confirmed count 7' in kwargs['input'][-1]['output']
        return call('select_task_tools', profile='manage')
    async def finish(kwargs):
        names = {t.name for t in kwargs['tools']}
        assert 'revise_task' in names
        assert not ({'delegate_task', 'find_experts'} - initially_loaded) & names
        return answer({'report':'Count 7; market size unknown.', 'claims':[]})
    provider.plans.append([initial, selected, recover, read, switch, finish])
    await reply(research, node, chat)
    assert len(provider.requests) == 6


async def test_directory_pages_filter_and_do_not_load_bodies(workspace, monkeypatch):
    db, scopes, research, tasks, projects, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    root = research.research(node.research_id)
    for n in range(30):
        scope = scopes.current(root.scope_id, research.owner)
        scopes.ingest(scope, content=f'private body {n}'.encode(), name=f'item-{n:03}.txt', media_type='text/plain')
    def forbidden(*a, **kw): raise AssertionError('Metadata discovery loaded body')
    monkeypatch.setattr(scopes, 'read', forbidden)
    first = collaboration.context_directory(node.id, category='sources')
    second = collaboration.context_directory(node.id, category='sources', offset=first['next_offset'])
    assert len(first['items']) == len(second['items']) == 8
    assert not {dumps(v) for v in first['items']} & {dumps(v) for v in second['items']}
    match = collaboration.context_directory(node.id, category='sources', query='item-029')
    assert [r['name'] for r in match['items']] == ['item-029.txt'] and match['next_offset'] is None
    assert collaboration.context_directory(node.id, category='results')['items'] == []
    with pytest.raises(ValueError): collaboration.context_directory(node.id, category='sources',limit=21)
    assert len(collaboration.find_experts(node.research_id, limit=2)) == 2
    assert collaboration.find_experts(node.research_id, query='nonexistent-query') == []
    assert all('search_web' in e['tools'] for e in collaboration.find_experts(node.research_id, required_tools=('search_web',)))


def test_latest_cjk_page_survives_pressure_and_next_cursor_advances(tmp_path):
    w = Workspace(tmp_path); a = ContextArtifacts(w, lambda:None, lambda _:None)
    try:
        key = a.save_text(dumps({'evidence': '必要内容' * 8000}))
        first = a.read(ReadContextPage(artifact_id=key, pointer='/evidence', limit=12000))
        items = [{'role':'user','content':'u'*32000},
            {'type':'function_call','name':'read_context_page','call_id':'old','arguments':'{}'},
            {'type':'function_call_output','call_id':'old','output':dumps({'value':first})},
            {'type':'function_call','name':'read_context_page','call_id':'new','arguments':'{}'},
            {'type':'function_call_output','call_id':'new','output':dumps({'value':first})}]
        projection = ModelProjection(a)
        projection.window_tokens = 65536
        result = projection(SimpleNamespace(model_data=ModelInputData(input=items,instructions=''),
            agent=SimpleNamespace(tools=[],output_type=None)))
        latest = json.loads(result.input[-1]['output'])['value']
        assert latest['text'] == first['text'][:len(latest['text'])] and '必要内容' in latest['text']
        assert 0 < latest['end'] < first['end']
        next_page = a.read(ReadContextPage(**latest['next_cursor']))
        assert next_page['start'] == latest['end'] and next_page['end'] > latest['end']
        assert latest['artifact_id'] == next_page['artifact_id'] == key
    finally: w.close()


async def test_main_source_prompt_stays_bounded_as_library_grows(workspace, monkeypatch):
    from agent_core.context.builder import ContextBuilder
    db, scopes, research, tasks, projects, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    root = research.research(node.research_id)
    snapshots = []
    for n in range(101):
        scope = scopes.current(root.scope_id, research.owner)
        scopes.ingest(scope, content=f'BODY_MUST_NOT_BE_IN_PROMPT_{n}'.encode(),
            name=f'growth-{n:03}.txt', media_type='text/plain')
        if n in (7, 100):
            scope = scopes.current(root.scope_id, research.owner)
            snapshot = ContextBuilder(scopes).build(scope, uuid4().hex, 'Task status only',
                lazy_sources=True, persist=False)
            snapshots.append(snapshot.input.messages[0].content)
    small, large = map(json.loads, snapshots)
    assert len(small['sources']) == len(large['sources']) == 8
    assert small['sources'] == large['sources']
    assert small['content'] == large['content'] == []
    assert 'BODY_MUST_NOT_BE_IN_PROMPT' not in snapshots[-1]
    assert abs(len(snapshots[-1]) - len(snapshots[0])) < 10


async def test_sdk_selected_profile_survives_export_and_fresh_adapter_resume():
    from copy import deepcopy
    from agents import ModelResponse, Model as SDKModel
    from agents.usage import Usage as SDKUsage
    from agent_core.adapters.openai import OpenAIEngine
    from agent_core.app.manager_profiles import PROFILES
    from agent_core.collaboration.tools import SelectTaskTools, ListContext
    from agent_core.contracts import AgentSpec, ToolBinding, ToolResult, RunRequest, PreparedInput, Message, RunLimits
    class Pause(BaseException): pass
    class Checkpoint:
        head = None
        stopped = False
        def restore(self): return deepcopy(self.head)
        def publish(self, value):
            self.head = deepcopy(value)
            if value['kind'] == 'state' and value['tool_profile'] == 'sources' and not self.stopped:
                self.stopped = True
                raise Pause()
    checkpoint = Checkpoint()
    observed = []
    class Model(SDKModel):
        async def stream_response(self, *args, **kwargs):
            raise NotImplementedError
            yield
        async def get_response(self, **kwargs):
            names = {t.name for t in kwargs['tools']}
            observed.append(names)
            step = len(observed)
            if step == 1:
                assert names == {'select_task_tools'}
                output = call('select_task_tools', profile='sources')
            elif step == 2:
                assert 'list_context' in names
                output = call('list_context', category='sources')
            else:
                assert names == observed[1]
                assert kwargs['input'][-1]['type'] == 'function_call_output'
                output = answer('done')
            return ModelResponse(output=output, usage=SDKUsage(requests=1), response_id=None)
    counts = []
    async def invoke(name, call_id, raw):
        counts.append(name)
        return ToolResult(value={'tool_profile':'sources'} if name=='select_task_tools' else {'items':[]})
    async def unused(args): raise AssertionError()
    tools = (ToolBinding('select_task_tools','Choose workflow',SelectTaskTools,unused),
        ToolBinding('list_context','List bounded metadata',ListContext,unused))
    spec = AgentSpec('manager','Answer current request',tools=tools,tool_profiles=PROFILES)
    request = RunRequest(run_id='resume-routing', agent_id=spec.id,
        input=PreparedInput(messages=(Message(role='user',content='Find the selected source'),)),
        limits=RunLimits(max_turns=6))
    with pytest.raises(Pause):
        await OpenAIEngine(Model(),checkpoints=checkpoint).execute(request,spec,tools,invoke)
    assert checkpoint.head['tool_profile'] == 'sources' and counts == ['select_task_tools']
    result = await OpenAIEngine(Model(),checkpoints=checkpoint).execute(request,spec,tools,invoke)
    assert result.status == 'succeeded', result
    assert counts == ['select_task_tools','list_context'] and len(observed)==3


def test_all_results_from_latest_tool_batch_stay_visible(tmp_path):
    w = Workspace(tmp_path); a = ContextArtifacts(w, lambda:None, lambda _:None)
    try:
        items = [{'role':'user','content':'u'*20000},
            {'type':'function_call','name':'read_context','call_id':'a','arguments':'{}'},
            {'type':'function_call','name':'read_context','call_id':'b','arguments':'{}'},
            {'type':'function_call_output','call_id':'a','output':dumps({'value':{'a':'甲'*20000}})},
            {'type':'function_call_output','call_id':'b','output':dumps({'value':{'b':'乙'*20000}})}]
        projection = ModelProjection(a)
        projection.window_tokens = 65536
        result = projection(SimpleNamespace(model_data=ModelInputData(input=items,instructions=''),
            agent=SimpleNamespace(tools=[],output_type=None)))
        for index, marker in ((3, '甲'), (4, '乙')):
            output = json.loads(result.input[index]['output'])
            assert 'external_context' not in output
            assert marker in output['value']['text']
            assert output['value']['next_cursor']['offset'] > 0
    finally: w.close()
