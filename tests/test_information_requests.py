"""Expert pause / manager authorization / same-task continuation contracts."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from test_collaboration import workspace, seeded, fixed_review_advice
from agent_core.app.api import create_app
from agent_core.collaboration.information import RequestInformation
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_projects
from agent_core.contracts import RunResult, Usage
from agent_core.context.scopes import ScopeDenied


def output(report='Completed with existing information.'):
    return {'report': report, 'fields': {}, 'sources': {}, 'assumptions': [],
        'conflicts': [], 'missing': [], 'limitations': [], 'follow_up': [],
        'self_checks': {}, 'claims': []}


async def setup(workspace):
    _, scopes, research, tasks, _, c = workspace
    node, ref, _ = await seeded(workspace)
    root = research.research(node.research_id)
    scope = scopes.current(root.scope_id, research.owner)
    _, extra = scopes.ingest(scope, content=b'Target year: 2025. Synthetic information only.',
        name='target-year.txt', media_type='text/plain')
    intent = c.delegate_task(command_id='info:' + node.id, node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample for the target year',
        delivery_requirements='State any missing information', resource_refs=(ref,))
    return node, ref, extra, intent


class InformationEngine:
    def __init__(self, extra, mode='source'):
        self.extra, self.mode = extra, mode
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.manager_calls = self.expert_calls = 0
        self.replies = []

    async def execute(self, request, spec, tools, invoke):
        if spec.id == 'manager_information':
            self.manager_calls += 1
            self.entered.set()
            await self.release.wait()
            refs, message_ids = [], []
            if self.mode in ('source', 'partial'):
                data = (await invoke('read_information', 'read', {'resource_refs': [self.extra.model_dump()]})).value
                assert '2025' in json.dumps(data)
                refs = [self.extra.model_dump()]
            elif self.mode == 'message':
                message_ids = [json.loads(request.input.messages[0].content)['current_message_id']]
            elif self.mode == 'foreign':
                refs = [self.extra.model_dump()]
            return RunResult(run_id=request.run_id, status='succeeded', usage=Usage(requests=1, input_tokens=11),
                output={'available': self.mode != 'none', 'answer': 'Use 2025.' if refs else 'Only the original instruction is known.',
                    'resource_refs': refs, 'message_ids': message_ids,
                    'missing': ['Market size'] if self.mode == 'partial' else []})
        self.expert_calls += 1
        reply = (await invoke('request_manager_information', 'ask', {
            'question': 'Which target year and market size?', 'progress': 'Read the synthetic sample; no estimate yet.'})).value
        self.replies.append(reply)
        again = (await invoke('request_manager_information', 'ask-again', {
            'question': 'A differently phrased repeat', 'progress': 'Already asked.'})).value
        assert again['remaining_tool_calls'] == reply['remaining_tool_calls'] - 1
        assert {k: v for k, v in again.items() if k != 'remaining_tool_calls'} == {
            k: v for k, v in reply.items() if k != 'remaining_tool_calls'}
        if self.mode in ('source', 'partial'):
            read = (await invoke('read_source', 'expert-read', {'ref': self.extra.model_dump()})).value
            assert '2025' in json.dumps(read)
        return RunResult(run_id=request.run_id, status='succeeded', output=output(), usage=Usage(requests=2))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['source', 'message', 'none', 'partial'])
async def test_pause_authorize_resume_and_preserve_missing(workspace, mode):
    _, scopes, research, tasks, projects, c = workspace
    node, ref, extra, intent = await setup(workspace)
    engine = InformationEngine(extra, mode)
    tasks.engine = engine
    running = asyncio.create_task(c.execute(intent.command_id))
    await asyncio.wait_for(engine.entered.wait(), 5)
    assert not running.done() and engine.replies == []
    saved = c.query('information_for', intent.task_id)[0]
    assert saved.status == 'waiting' and saved.answer is None
    binding = tasks.query('run', saved.run_id)
    with pytest.raises(ScopeDenied):
        scopes.check(scopes.repository.scope(binding.scope_id), extra)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)), base_url='http://test') as client:
        response = await client.get('/api/task_activity', params={'research_id': node.research_id})
        row = next(row for row in response.json() if row['spec']['id'] == intent.task_id)
        assert row['information_requests'][0]['status'] == 'waiting'
    engine.release.set()
    outcome = await running
    assert outcome['intent'].status == 'completed'
    assert engine.expert_calls == engine.manager_calls == 1
    assert len(outcome['runs']) == 1 and tasks.spec(intent.task_id).version == 1
    saved = c.information.get(saved.run_id)
    assert saved.status == 'resolved'
    record = tasks.ledger.get(saved.run_id)
    assert record.status == 'succeeded' and record.result.usage.requests == 3
    if mode in ('source', 'partial'):
        assert extra in outcome['receipts'][0].resource_refs
        assert extra not in tasks.spec(intent.task_id).required_refs
    else:
        with pytest.raises(ScopeDenied): scopes.check(scopes.repository.scope(binding.scope_id), extra)
    if mode in ('none', 'partial'):
        expected = saved.question if mode == 'none' else 'Market size'
        assert expected in outcome['receipts'][0].missing
        assert expected in record.result.output['report']
    messages = projects.expert_thread(intent.task_id).messages
    assert len([m for m in messages if m.id == saved.id]) == 1
    assert len([m for m in messages if m.reply_to == saved.id]) == 1
    assert c.query('notice', saved.id + ':notice').kind == 'information_request'


@pytest.mark.asyncio
async def test_unread_information_is_not_authorized(workspace):
    _, scopes, _, tasks, _, c = workspace
    _, _, extra, intent = await setup(workspace)
    engine = InformationEngine(extra, 'foreign')
    engine.release.set()
    tasks.engine = engine
    outcome = await c.execute(intent.command_id)
    assert tasks.ledger.get(outcome['runs'][0].id).status == 'failed'
    with pytest.raises(ScopeDenied): scopes.check(scopes.repository.scope(outcome['runs'][0].scope_id), extra)
    assert not engine.replies


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['revoke', 'cancel', 'revise'])
async def test_waiting_request_cannot_resume_after_access_or_version_changes(workspace, action):
    _, scopes, research, tasks, _, c = workspace
    node, ref, extra, intent = await setup(workspace)
    engine = InformationEngine(extra)
    tasks.engine = engine
    running = asyncio.create_task(c.execute(intent.command_id))
    await asyncio.wait_for(engine.entered.wait(), 5)
    if action == 'revoke':
        root = research.research(node.research_id)
        scopes.revoke(scopes.current(root.scope_id, research.owner), ref)
    elif action == 'cancel':
        c.cancel_task(command_id='cancel-info', task_id=intent.task_id, expected_version=1, reason='Stop')
    else:
        tasks.create(research_id=node.research_id, kind='synthesize',
            user_request='New task', required_refs=(ref,), task_id=intent.task_id)
    engine.release.set()
    await running
    assert not engine.replies
    assert c.query('information_for', intent.task_id)[0].status == 'waiting'
    binding = tasks.query('list_runs', intent.task_id)[0]
    with pytest.raises(ScopeDenied): scopes.check(scopes.repository.scope(binding.scope_id), extra)


@pytest.mark.asyncio
@pytest.mark.parametrize('resolved', [False, True])
async def test_postgres_reopen_continues_saved_information_handoff(resolved):
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-recovery-verify-blobs'))
    def services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes)
        tasks = build_tasks(db, scopes, research)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration
    first = services()
    try:
        _, _, extra, intent = await setup(first)
        tasks, c, scopes = first[3], first[5], first[1]
        run_id = tasks.prepare_durable(intent.task_id, 1)
        binding = tasks.query('run', run_id)
        spec = tasks.spec(intent.task_id)
        engine = InformationEngine(extra)
        engine.release.set()
        tasks.engine = engine
        token = scopes.running.set(run_id)
        try:
            with scopes.track(run_id, scopes.repository.scope(binding.scope_id)):
                args = RequestInformation(question='Which target year?', progress='Saved progress before restart.')
                if resolved: await c.information.request(spec, binding, args)
                else: c.information.begin(spec, binding, args)
        finally:
            scopes.running.reset(token)
    finally:
        first[0].close()
    second = services()
    try:
        tasks, c = second[3], second[5]
        engine = InformationEngine(extra)
        engine.release.set()
        tasks.engine = engine
        await tasks.run_durable(intent.task_id, 1, run_id)
        record = tasks.ledger.get(run_id)
        assert record.status == 'succeeded'
        assert engine.manager_calls == (0 if resolved else 1)
        assert engine.expert_calls == 1
        assert engine.replies[0]['progress'] == 'Saved progress before restart.'
        assert len(tasks.query('list_runs', intent.task_id)) == 1
        assert extra in tasks.receipt('task-result:' + run_id).resource_refs
        assert len(second[4].expert_thread(intent.task_id).messages) == 2
    finally:
        second[0].close()


@pytest.mark.asyncio
async def test_background_expert_can_request_information_without_deadlock(workspace):
    from agent_core.app.research_execution import build_execution
    from agent_core.contracts import RunLimits
    from agent_core.research.chat import ChatService
    from agent_core.research.models import NodeCommand
    _, scopes, research, tasks, _, c = workspace
    node, ref, extra, _ = await setup(workspace)

    class NestedEngine(InformationEngine):
        async def execute(self, request, spec, tools, invoke):
            if spec.id in ('manager_information', 'expert_synthesize'):
                return await super().execute(request, spec, tools, invoke)
            handoff = (await invoke('delegate_task', 'nested', {
                'expert_id': 'expert_synthesize', 'task_instruction': 'Review sample',
                'delivery_requirements': 'State limits', 'resource_refs': [ref.model_dump()]})).value
            assert handoff['status'] == 'accepted'
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Expert completed; independent review pending.', 'blocking_missing': [], 'claims': []})

    engine = NestedEngine(extra)
    engine.release.set()
    tasks.engine = engine
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, scopes, bindings, service, engine)
    chat = ChatService(research, tasks).read(node.id)
    command = await asyncio.wait_for(research.submit(NodeCommand(command_id='nested-info',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=node.revision, chat_revision=chat.revision,
        limits=RunLimits(max_turns=8, max_calls=6, timeout_seconds=30))), 10)
    assert command.status == 'completed'
    intent = next(i for i in c.query('intents_for', node.research_id) if i.manager_run_id == command.run_id)
    await asyncio.wait_for(c.execute(intent.command_id), 10)
    assert c.query('intent', intent.command_id).status == 'completed'
    assert engine.manager_calls == engine.expert_calls == 1
    assert ChatService(research, tasks).read(node.id).messages[-1].role == 'assistant'
    binding = next(run for spec in tasks.query('list_specs', node.research_id)
        for run in tasks.query('list_runs', spec.id))
    assert c.information.visible_for(binding.task_id)
    root = research.research(node.research_id)
    scopes.revoke(scopes.current(root.scope_id, research.owner), extra)
    assert c.information.visible_for(binding.task_id) == []
    notices = c.pending_notice_summaries_for_node(node.id)
    assert all('Use 2025.' not in notice['content'] for notice in notices)
