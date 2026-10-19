import asyncio
import json
import os
from uuid import uuid4

import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources
from agent_core.context.scopes import ScopeDenied
from agent_core.execution.ports import Conflict
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand


@pytest.fixture(params=['memory', 'postgres'])
def app(request, tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('TEST_DATABASE_URL')
        if not url:
            pytest.skip('Dedicated TEST_DATABASE_URL required')
        db = PostgresDatabase(url)
    else:
        db = MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    engine = ScriptedEngine(lambda r: ([], {'report': 'Reply to ' + json.loads(r.input.messages[0].content)['goal'], 'blocking_missing': []}))
    research = build_research(db, scopes, engine)
    root = research.create('R3 ' + str(uuid4()))
    tasks = build_tasks(db, scopes, research, engine)
    chat = ChatService(research, tasks)
    yield db, scopes, research, root, engine, tasks, chat
    db.close()


async def seed(app):
    _, _, _, root, _, _, chat = app
    main = await chat.start(root.id, '主线')
    main = chat.send(main.node_id, 'BEFORE_MARKER', main.revision)
    main = chat.send(main.node_id, 'FORK_HERE', main.revision)
    fork_id = main.messages[-1].id
    main = chat.send(main.node_id, 'AFTER_SECRET', main.revision)
    a = await chat.branch(main.node_id, fork_id, '方向 A', main.revision)
    b = await chat.branch(main.node_id, fork_id, '方向 B', main.revision)
    return main, a, b


async def test_message_cutoff_siblings_continuation_and_upstream_hint(app):
    _, _, research, root, engine, _, chat = app
    main, a, b = await seed(app)
    assert [m.content for m in a.inherited] == ['BEFORE_MARKER', 'FORK_HERE']
    assert 'AFTER_SECRET' not in a.model_dump_json()
    a = chat.send(a.node_id, 'ONLY_A', a.revision)
    b = chat.send(b.node_id, 'ONLY_B', b.revision)
    assert 'ONLY_A' not in chat.read(b.node_id).model_dump_json()
    assert 'ONLY_B' not in chat.read(a.node_id).model_dump_json()
    await research.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
        node_id=a.node_id, kind='chat_reply', chat_revision=a.revision))
    prompt = engine.requests[-1].input.model_dump_json()
    assert 'BEFORE_MARKER' in prompt and 'ONLY_A' in prompt
    assert 'AFTER_SECRET' not in prompt and 'ONLY_B' not in prompt
    assert chat.read(a.node_id).messages[-1].role == 'assistant'
    main = chat.send(main.node_id, 'LATER_UPSTREAM', main.revision)
    assert main.revision > a.fork_source_revision
    assert 'LATER_UPSTREAM' not in chat.read(a.node_id).model_dump_json()


async def test_two_experts_same_branch_and_cross_branch_context(app):
    _, scopes, research, root, engine, tasks, chat = app
    main, a, b = await seed(app)
    a = chat.send(a.node_id, 'A_PRIVATE', a.revision)
    b = chat.send(b.node_id, 'B_PRIVATE', b.revision)
    scope = scopes.current(root.scope_id, research.owner)
    scope, ref = scopes.ingest(scope, content=b'company 2025 CFO 100 capex 20 million',
        name='report.txt', media_type='text/plain')
    def answer(request):
        return [], {'report':'Expert conclusion', 'fields':{'company':'A','period':'2025',
            'operating_cash_flow':'100','capital_expenditure':'20','unit':'million'},
            'sources':{'operating_cash_flow':'line 1','capital_expenditure':'line 1','unit':'line 1'}}
    engine.script = answer
    first = tasks.create(research_id=root.id, node_id=a.node_id, kind='extract',
        user_request='Extract one', required_refs=(ref,))
    second = tasks.create(research_id=root.id, node_id=a.node_id, kind='search',
        user_request='Search one', required_refs=(ref,))
    other = tasks.create(research_id=root.id, node_id=b.node_id, kind='extract',
        user_request='Extract other', required_refs=(ref,))
    assert 'A_PRIVATE' in first.model_dump_json() and 'A_PRIVATE' in second.model_dump_json()
    assert 'B_PRIVATE' not in first.model_dump_json()
    assert 'A_PRIVATE' not in other.model_dump_json()
    first_run, second_run = await asyncio.gather(tasks.run(first.id), tasks.run(second.id))
    for result, spec in ((first_run,first),(second_run,second)):
        receipt = result['receipt']
        tasks.review(receipt.result_id,{i.number:('passed','Checked source and report') for i in spec.acceptance},
            reviewer='test-fixture')
        a = chat.publish_expert(a.node_id, receipt.result_id, a.revision)
    assert sum(m.role == 'expert' for m in chat.read(a.node_id).messages) == 2
    assert sum(m.role == 'expert' for m in chat.read(b.node_id).messages) == 0
    a_prompts = [r.input.messages[0].content for r in engine.requests]
    assert any('A_PRIVATE' in p for p in a_prompts)
    assert all('B_PRIVATE' not in p for p in a_prompts)


async def test_complete_transfer_adoption_stale_target_and_revocation(app):
    _, scopes, research, root, _, _, chat = app
    main, a, b = await seed(app)
    a = chat.send(a.node_id, 'Question A', a.revision)
    await research.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
        node_id=a.node_id, kind='chat_reply', chat_revision=a.revision))
    a = chat.read(a.node_id)
    conclusion = a.messages[-1]
    with pytest.raises(Conflict):
        chat.transfer(a.node_id, conclusion.id, main.node_id, main.revision,
            research.node(main.node_id).revision + 1)
    transfer = chat.transfer(a.node_id, conclusion.id, main.node_id, main.revision,
        research.node(main.node_id).revision)
    assert transfer.content == conclusion.content
    assert chat.read(main.node_id).messages[-1].content == conclusion.content
    assert transfer.status == 'for_discussion'
    assert chat.adopt(transfer.id).status == 'adopted'
    assert chat.read(a.node_id).messages[-1] == conclusion
    with pytest.raises(Conflict):
        chat.transfer(a.node_id, conclusion.id, b.node_id, b.revision+1,
            research.node(b.node_id).revision)
    scope = scopes.current(root.scope_id, research.owner)
    scope, ref = scopes.ingest(scope, content=b'private',name='private.txt',media_type='text/plain')
    # A transferred expert conclusion carries its source identity; revocation hides it.
    from agent_core.research.models import ChatMessage
    sourced = ChatMessage(role='assistant', content='SOURCE_BOUND', resource_refs=(ref,))
    a = chat._append(a, sourced, a.revision)
    main = chat.read(main.node_id)
    transfer2 = chat.transfer(a.node_id, sourced.id, main.node_id, main.revision,
        research.node(main.node_id).revision)
    assert transfer2.resource_refs == (ref,)
    scopes.revoke(scope, ref)
    with pytest.raises(ScopeDenied): chat.read(main.node_id)
    with pytest.raises(ScopeDenied): chat.adopt(transfer2.id)


async def test_late_chat_reply_and_reused_message_blocked(app):
    _, _, research, root, engine, _, chat = app
    main = await chat.start(root.id, 'main')
    main = chat.send(main.node_id, 'first', 0, 'same-id')
    assert chat.send(main.node_id, 'first', 0, 'same-id') == main
    with pytest.raises(Conflict): chat.send(main.node_id, 'different', main.revision, 'same-id')
    with pytest.raises(Conflict): chat.send(main.node_id, 'stale', 0)
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowEngine:
        async def execute(self, request, spec, tools, invoke):
            from agent_core.contracts import RunResult
            entered.set()
            await release.wait()
            return RunResult(run_id=request.run_id,status='succeeded',output={'report':'LATE','blocking_missing':[]})
    engine.execute = SlowEngine().execute
    task = asyncio.create_task(research.submit(NodeCommand(command_id=str(uuid4()),
        research_id=root.id,node_id=main.node_id,kind='chat_reply',chat_revision=main.revision)))
    await entered.wait()
    main = chat.send(main.node_id, 'newer message', main.revision)
    release.set()
    await task
    assert 'LATE' not in chat.read(main.node_id).model_dump_json()


async def test_chat_source_is_frozen_as_task_dependency(app):
    _, scopes, research, root, _, tasks, chat = app
    main = await chat.start(root.id, 'main')
    scope = scopes.current(root.scope_id, research.owner)
    scope, ref = scopes.ingest(scope, content=b'fictional source', name='source.txt', media_type='text/plain')
    from agent_core.research.models import ChatMessage
    main = chat._append(main, ChatMessage(role='assistant', content='Source based context',
        resource_refs=(ref,), assumptions=('Only one source',), limitations=('Not verified',)), main.revision)
    main = chat.send(main.node_id, 'Analyze this', main.revision)
    spec = tasks.create(research_id=root.id, node_id=main.node_id, kind='search',
        user_request='Search context', required_refs=(ref,))
    assert spec.required_refs == (ref,)
    assert spec.chat_context[0].assumptions == ('Only one source',)
    scopes.revoke(scope, ref)
    with pytest.raises(ScopeDenied): await tasks.run(spec.id)
    with pytest.raises(ScopeDenied): chat.read(main.node_id)


async def test_branch_revocation_blocks_history_and_frozen_expert_context(app):
    _, scopes, research, root, _, tasks, chat = app
    main = await chat.start(root.id, 'main')
    root_scope = scopes.current(root.scope_id, research.owner)
    root_scope, ref = scopes.ingest(root_scope, content=b'private branch source',
        name='branch.txt', media_type='text/plain')
    from agent_core.research.models import ChatMessage
    main = chat._append(main, ChatMessage(role='assistant', content='SOURCE_CONTEXT',
        resource_refs=(ref,)), main.revision)
    spec = tasks.create(research_id=root.id, node_id=main.node_id, kind='search',
        user_request='Use the source', required_refs=(ref,))
    assert chat.read(main.node_id).messages[-1].content == 'SOURCE_CONTEXT'
    branch = research.node(main.node_id)
    branch_scope = scopes.current(branch.scope_id, research.owner)
    scopes.revoke(branch_scope, ref)
    assert scopes.current(root.scope_id, research.owner).version == root_scope.version
    with pytest.raises(ScopeDenied): chat.read(main.node_id)
    with pytest.raises(ScopeDenied): await tasks.run(spec.id)
    import httpx
    from agent_core.app.api import create_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
                                 base_url='http://test') as client:
        history = await client.get('/api/chat_read', params={'node_id':main.node_id})
        activity = await client.get('/api/task_activity', params={'research_id':root.id})
        assert history.status_code == 403
        assert activity.json() == []


async def test_chat_reply_save_retry_is_atomic_and_does_not_rerun(app):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from agent_core.execution.service import PublicationError
    db, _, research, root, engine, _, chat = app
    main = await chat.start(root.id, 'main')
    main = chat.send(main.node_id, 'Reply once', main.revision)
    original = db.transaction
    fault = [True]

    @contextmanager
    def broken():
        with original() as tx:
            class Proxy:
                def __getattr__(self, name): return getattr(tx.research, name)
                def publish(self, value, dependencies):
                    tx.research.publish(value, dependencies)
                    if fault[0]:
                        fault[0] = False
                        raise RuntimeError('one-time save failure')
            repos = vars(tx).copy()
            repos['research'] = Proxy()
            yield SimpleNamespace(**repos)

    db.transaction = broken
    command = NodeCommand(command_id=str(uuid4()), research_id=root.id,
        node_id=main.node_id, kind='chat_reply', chat_revision=main.revision)
    with pytest.raises(PublicationError): await research.submit(command)
    run = research.query('list_runs', main.node_id)[0]
    assert [m.role for m in chat.read(main.node_id).messages] == ['user']
    assert research.query('list_results', main.node_id) == []
    assert research.retry_save(run.run_id).status == 'completed'
    assert [m.role for m in chat.read(main.node_id).messages] == ['user', 'assistant']
    assert len(engine.requests) == 1
    db.transaction = original


async def test_revocation_blocks_late_chat_publication(app):
    _, scopes, research, root, engine, _, chat = app
    main = await chat.start(root.id, 'main')
    scope = scopes.current(root.scope_id, research.owner)
    scope, ref = scopes.ingest(scope, content=b'fictional confidential source',
        name='source.txt', media_type='text/plain')
    from agent_core.research.models import ChatMessage
    main = chat._append(main, ChatMessage(role='assistant', content='SOURCE_CONTEXT',
        resource_refs=(ref,)), main.revision)
    main = chat.send(main.node_id, 'continue', main.revision)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(request, spec, tools, invoke):
        from agent_core.contracts import RunResult
        entered.set()
        await release.wait()
        return RunResult(run_id=request.run_id, status='succeeded',
                         output={'report':'LATE_REVOKED','blocking_missing':[]})

    engine.execute = slow
    running = asyncio.create_task(research.submit(NodeCommand(command_id=str(uuid4()),
        research_id=root.id, node_id=main.node_id, kind='chat_reply', chat_revision=main.revision)))
    await entered.wait()
    scopes.revoke(scope, ref)
    release.set()
    await running
    assert research.query('list_results', main.node_id) == []
    with pytest.raises(ScopeDenied): chat.read(main.node_id)


async def test_two_experts_actually_overlap_on_one_branch(app):
    _, scopes, research, root, engine, tasks, chat = app
    main = await chat.start(root.id, 'main')
    scope = scopes.current(root.scope_id, research.owner)
    _, ref = scopes.ingest(scope, content=b'fictional source', name='source.txt', media_type='text/plain')
    first = tasks.create(research_id=root.id, node_id=main.node_id, kind='search',
        user_request='Search', required_refs=(ref,))
    second = tasks.create(research_id=root.id, node_id=main.node_id, kind='synthesize',
        user_request='Synthesize', required_refs=(ref,))
    entered = []
    both = asyncio.Event()

    async def parallel(request, spec, tools, invoke):
        from agent_core.contracts import RunResult
        entered.append(request.agent_id)
        if len(entered) == 2: both.set()
        await both.wait()
        return RunResult(run_id=request.run_id, status='succeeded',
                         output={'report':'Fixture expert result'})

    engine.execute = parallel
    outcomes = await asyncio.wait_for(asyncio.gather(tasks.run(first.id), tasks.run(second.id)), 3)
    assert set(entered) == {'expert_search', 'expert_synthesize'}
    assert all(outcome['receipt'].delivery == 'pending_review' for outcome in outcomes)
