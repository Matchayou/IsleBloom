"""C1 handoff contracts and a real Core tool-to-expert-to-manager control chain."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from agent_core.adapters.memory_ledger import MemoryDatabase
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_projects
from agent_core.contracts import RunError, RunLimits, RunResult, Usage
from agent_core.context.scopes import ScopeDenied
from agent_core.execution.ports import Conflict
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand


@pytest.fixture(autouse=True)
def fixed_review_advice(monkeypatch):
    """Coordination fixtures supply a separate, deterministic reviewer outcome.

    Review behavior and real SDK execution are covered in test_automatic_review*;
    these tests keep their existing executor/manager scripts focused on handoff.
    """
    from agent_core.core import CoreRunner
    from agent_core.collaboration.models import ReviewAdvice
    original = CoreRunner.execute
    async def execute(self, request):
        if self.catalog.get(request.agent_id).output_type is ReviewAdvice:
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'verdict':'passed','summary':'Independent deterministic control advice.','issues':[]})
        return await original(self, request)
    monkeypatch.setattr(CoreRunner, 'execute', execute)


@pytest.fixture
def workspace(tmp_path):
    db = MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    research = build_research(db, scopes)
    tasks = build_tasks(db, scopes, research)
    projects = build_projects(db, research, tasks)
    yield db, scopes, research, tasks, projects, research.collaboration
    db.close()


async def seeded(workspace):
    _, scopes, research, tasks, projects, collaboration = workspace
    project = projects.create('C1')
    main = await projects.create_main_task(project.id, 'Examine sample')
    node = research.query('list_nodes', main.research_id)[0]
    root = research.research(main.research_id)
    scope = scopes.current(root.scope_id, research.owner)
    _, ref = scopes.ingest(scope, content=b'Synthetic sample only; real market size unknown.',
                           name='sample.txt', media_type='text/plain')
    chat = ChatService(research, tasks).send(node.id,
        'Ask an expert to examine the sample and state what remains unknown.', 0, 'user-1')
    return node, ref, chat


@pytest.mark.asyncio
async def test_handoff_is_atomic_idempotent_and_reuses_expert_session(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    kwargs = dict(command_id='first-command', node_id=node.id, expert_id='expert_synthesize',
        task_instruction='Review this sample only', delivery_requirements='State missing evidence',
        resource_refs=(ref,))
    first = collaboration.delegate_task(**kwargs)
    assert collaboration.delegate_task(**kwargs) == first
    assert tasks.spec(first.task_id).context_packet_id == first.packet_id
    packet = collaboration.packet(first.packet_id)
    assert packet.user_instruction.startswith('Ask an expert')
    assert packet.manager_instruction == 'Review this sample only'
    assert packet.resource_refs == (ref,)
    listed = next(v for v in collaboration.find_experts(node.research_id, query='synthesize')
        if v['expert_id'] == 'expert_synthesize')
    assert listed['session_id'] == first.session_id
    assert listed['tasks'][0]['task_id'] == first.task_id
    with pytest.raises(Conflict):
        collaboration.delegate_task(**{**kwargs, 'task_instruction': 'Changed'})
    second = collaboration.delegate_task(**{**kwargs, 'command_id': 'second-command',
        'expert_id': None, 'session_id': first.session_id})
    assert second.session_id == first.session_id and second.task_id != first.task_id
    assert len(collaboration.query('sessions_for', research.node(node.id).research_id)) == 1


@pytest.mark.asyncio
async def test_reviewed_upstream_result_keeps_provenance_in_handoff(workspace):
    import httpx
    from agent_core.app.api import create_app
    from agent_core.adapters.scripted import ScriptedEngine
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'Only a synthetic source is available.', 'fields': {}, 'sources': {},
        'assumptions': ['The sample is illustrative'], 'conflicts': [],
        'missing': ['Real market data'], 'limitations': ['No external validation'],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    upstream = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Assess the sample', required_refs=(ref,))
    receipt = (await tasks.run(upstream.id))['receipt']
    passed = tasks.review(receipt.result_id, {
        1: ('passed', 'Checked fixed source access'),
        2: ('passed', 'Independently checked the synthetic limitation'),
    }, reviewer='independent-test-reviewer')
    selected = collaboration.read_context(node.id, result_ids=(passed.result_id,))['results'][0]
    assert selected['expert_session_id'] == upstream.expert_session_id
    assert selected['spec_version'] == 1 and selected['delivery'] == 'passed'
    assert selected['assumptions'] == ('The sample is illustrative',)
    assert selected['limitations'] == ('No external validation',)
    intent = collaboration.delegate_task(command_id='upstream-handoff', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Combine reviewed finding with the source',
        delivery_requirements='Keep the limitation explicit',
        resource_refs=(ref,), result_ids=(passed.result_id,))
    packet = collaboration.packet(intent.packet_id)
    assert packet.selected_results[0]['result_id'] == passed.result_id
    assert packet.selected_results[0]['expert_session_id'] == upstream.expert_session_id
    assert packet.selected_results[0]['delivery'] == 'passed'
    assert packet.selected_results[0]['limitations'] == ('No external validation',)
    edge = collaboration.query('dependencies_for', node.research_id)[0]
    assert (edge.downstream_task_id, edge.upstream_task_id, edge.result_id) == (
        intent.task_id, upstream.id, passed.result_id)
    assert (edge.downstream_version, edge.upstream_version) == (1, upstream.version)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
            base_url='http://testserver') as client:
        response = await client.get('/api/task_activity', params={'research_id': node.research_id})
        assert response.status_code == 200
        downstream = next(row for row in response.json() if row['spec']['id'] == intent.task_id)
        assert downstream['dependencies'][0]['result_id'] == passed.result_id
    assert tasks.spec(intent.task_id).required_results == (passed.result_id,)
    adopted = tasks.adopt(passed.result_id, 'Independently verified source limits')
    assert adopted.adoption == 'adopted'
    assert adopted.adoption_reason == 'Independently verified source limits'
    assert tasks.adopt(passed.result_id, 'Independently verified source limits') == adopted
    with pytest.raises(Conflict):
        tasks.adopt(passed.result_id, 'Different reason')
    tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='New upstream version', required_refs=(ref,), task_id=upstream.id)
    assert tasks.spec(upstream.id).version == 2
    outcome = await collaboration.execute(intent.command_id)
    assert outcome['intent'].status == 'completed'
    assert outcome['runs'][0].result_ids == (passed.result_id,)
    assert collaboration.query('dependencies_for', node.research_id)[0] == edge


@pytest.mark.asyncio
async def test_waiting_dependency_starts_only_after_reviewed_fixed_result(workspace):
    from agent_core.adapters.scripted import ScriptedEngine
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'The fixed sample is synthetic.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    upstream = collaboration.delegate_task(command_id='future-upstream', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Check the sample',
        delivery_requirements='State the limitation', resource_refs=(ref,))
    downstream = collaboration.delegate_task(command_id='future-downstream', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Combine reviewed finding',
        delivery_requirements='Keep provenance', upstream_task_ids=(upstream.task_id,))
    assert downstream.status == 'waiting'
    assert tasks.spec(downstream.task_id).required_results == ()
    assert (await collaboration.execute(downstream.command_id))['intent'].status == 'waiting'
    waiting_edge = next(e for e in collaboration.query('dependencies_for', node.research_id)
        if e.downstream_task_id == downstream.task_id)
    assert waiting_edge.result_id is None
    collaboration.recover_waiting()
    assert collaboration.query('intent', downstream.command_id).status == 'waiting'
    outcome = await collaboration.execute(upstream.command_id)
    receipt = outcome['receipts'][0]
    assert receipt.delivery == 'pending_review'
    collaboration.recover_waiting()
    assert collaboration.query('intent', downstream.command_id).status == 'waiting'
    tasks.review(receipt.result_id, {
        1: ('passed', 'Checked fixed source'),
        2: ('passed', 'Independently checked conclusion'),
    }, reviewer='independent-test-reviewer')
    collaboration.recover_waiting()
    ready = collaboration.query('intent', downstream.command_id)
    assert ready.status == 'accepted'
    fixed = tasks.spec(downstream.task_id)
    assert fixed.version == 2 and fixed.required_results == (receipt.result_id,)
    result = await collaboration.execute(downstream.command_id)
    assert result['intent'].status == 'completed'
    assert result['runs'][0].result_ids == (receipt.result_id,)
    assert next(e for e in collaboration.query('dependencies_for', node.research_id)
        if e.downstream_task_id == downstream.task_id and e.downstream_version == 2).result_id == receipt.result_id


@pytest.mark.asyncio
async def test_waiting_dependency_fails_when_frozen_upstream_version_was_skipped(workspace):
    from agent_core.adapters.scripted import ScriptedEngine
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'Only the later version ran.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    upstream = collaboration.delegate_task(command_id='skipped-upstream', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='First version',
        delivery_requirements='State limits', resource_refs=(ref,))
    downstream = collaboration.delegate_task(command_id='skipped-downstream', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Use the first version only',
        delivery_requirements='Preserve version', upstream_task_ids=(upstream.task_id,))
    tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Later version', required_refs=(ref,), task_id=upstream.task_id)
    await tasks.run(upstream.task_id)
    collaboration.recover_waiting()
    stopped = collaboration.query('intent', downstream.command_id)
    assert stopped.status == 'failed'
    assert 'superseded before execution' in stopped.error
    assert tasks.query('list_runs', downstream.task_id) == []


@pytest.mark.asyncio
async def test_main_task_defaults_to_unlimited_model_turns_and_recorded_tokens(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    first = collaboration.delegate_task(command_id='budget-first', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='First review',
        delivery_requirements='State limits', resource_refs=(ref,))
    collaboration.delegate_task(command_id='budget-second', node_id=node.id,
        expert_id='expert_search', task_instruction='Second review',
        delivery_requirements='State limits', resource_refs=(ref,))
    budget = collaboration.budget_status(node.research_id)
    assert budget['task_count'] == 2 and budget['reserved_model_turns'] is None
    assert budget['limits']['reserved_model_turns'] is None
    assert budget['limits']['recorded_tokens'] is None
    collaboration.delegate_task(command_id='budget-third', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Third review',
        delivery_requirements='State limits', resource_refs=(ref,))
    assert tasks.spec(first.task_id).limits.max_turns is None
    assert tasks.spec(first.task_id).version == 1


@pytest.mark.asyncio
async def test_failed_user_revision_reports_run_failure_to_manager(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)

    class FailingEngine:
        async def execute(self, request, agent, tools, invoke):
            return RunResult(run_id=request.run_id, status='failed',
                error=RunError(code='model_output_invalid', origin='engine',
                               message='Expert tool input was invalid'),
                usage=Usage(complete=False))

    tasks.engine = FailingEngine()
    intent = collaboration.delegate_task(command_id='revision-failure-base', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review sample',
        delivery_requirements='State limits', resource_refs=(ref,))
    revision = collaboration.revise_task(command_id='revision-failure',
        task_id=intent.task_id, expected_version=1, request='Retry review',
        mode='after_current', notify_manager=True)
    completed = await collaboration.process_revision(revision.id)
    assert completed.status == 'failed'
    assert 'Expert tool input was invalid' in completed.error
    outcome = collaboration.query('notice', 'notice:revision-result:' + revision.id)
    assert outcome.status == 'pending'
    assert 'receipt rework' in outcome.content and 'failed' in outcome.content
    notice_rows = collaboration.pending_notice_summaries_for_node(node.id)
    assert all(row['latest_run']['execution_status'] == 'failed' for row in notice_rows)
    assert all(row['latest_run']['delivery'] == 'rework' for row in notice_rows)


@pytest.mark.asyncio
async def test_postgres_reopens_waiting_dependency_and_user_notice():
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    from agent_core.adapters.scripted import ScriptedEngine
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-recovery-verify-blobs'))
    engine = ScriptedEngine(lambda request: ([], {
        'report': 'The sample is synthetic.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))

    def open_services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes, engine)
        tasks = build_tasks(db, scopes, research, engine)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration

    first = open_services()
    try:
        node, ref, _ = await seeded(first)
        upstream = first[-1].delegate_task(command_id='pg-wait-upstream-' + node.id,
            node_id=node.id, expert_id='expert_synthesize',
            task_instruction='Review sample', delivery_requirements='State limits',
            resource_refs=(ref,))
        downstream = first[-1].delegate_task(command_id='pg-wait-downstream-' + node.id,
            node_id=node.id, expert_id='expert_synthesize',
            task_instruction='Use reviewed finding', delivery_requirements='Keep provenance',
            upstream_task_ids=(upstream.task_id,))
        separate = first[-1].delegate_task(command_id='pg-notice-task-' + node.id,
            node_id=node.id, expert_id='expert_search',
            task_instruction='Review another angle', delivery_requirements='State limits',
            resource_refs=(ref,))
        revision = first[-1].revise_task(command_id='pg-user-revision-' + node.id,
            task_id=separate.task_id, expected_version=1,
            request='Record my proposed change', mode='save', notify_manager=True)
        await first[-1].process_revision(revision.id)
        assert first[-1].query('notice', 'notice:' + revision.id).status == 'pending'
    finally:
        first[0].close()
    second = open_services()
    try:
        collaboration, tasks = second[-1], second[3]
        assert collaboration.query('intent', downstream.command_id).status == 'waiting'
        assert collaboration.query('notice', 'notice:' + revision.id).status == 'pending'
        outcome = await collaboration.execute(upstream.command_id)
        receipt = outcome['receipts'][0]
        collaboration.recover_waiting()
        assert collaboration.query('intent', downstream.command_id).status == 'waiting'
        tasks.review(receipt.result_id, {
            1: ('passed', 'Checked fixed source'),
            2: ('passed', 'Independently checked conclusion'),
        }, reviewer='independent-test-reviewer')
        collaboration.recover_waiting()
        assert collaboration.query('intent', downstream.command_id).status == 'accepted'
    finally:
        second[0].close()


@pytest.mark.asyncio
async def test_parent_cancellation_requests_all_descendants(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    parent = collaboration.delegate_task(command_id='parent-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    child = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Check one aspect', required_refs=(ref,),
        parent_task_id=parent.task_id, expert_session_id=parent.session_id,
        context_packet_id=parent.packet_id)
    grandchild = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Check a narrower aspect', required_refs=(ref,),
        parent_task_id=child.id, expert_session_id=parent.session_id,
        context_packet_id=parent.packet_id)
    request = collaboration.cancel_task(command_id='stop-tree', task_id=parent.task_id,
        expected_version=1, reason='User stopped the work', include_children=True)
    assert request.include_children
    for task_id in (parent.task_id, child.id, grandchild.id):
        assert collaboration.cancellation_requested(task_id, 1)
    assert collaboration.cancel_task(command_id='stop-tree', task_id=parent.task_id,
        expected_version=1, reason='User stopped the work', include_children=True) == request
    await collaboration.recover_pending_cancellations()
    assert all(collaboration.query('cancellations_for', task_id)[0].status == 'cancelled'
               for task_id in (parent.task_id, child.id, grandchild.id))
    later = tasks.create(research_id=node.research_id, kind='synthesize',
        user_request='Late child', required_refs=(ref,), parent_task_id=parent.task_id,
        expert_session_id=parent.session_id, context_packet_id=parent.packet_id)
    assert collaboration.cancellation_requested(later.id, later.version)
    with pytest.raises(Conflict, match='cancell'):
        tasks.prepare_durable(later.id, later.version)


@pytest.mark.asyncio
async def test_expert_discussion_excerpt_is_frozen_with_its_session(workspace):
    from agent_core.collaboration.models import ExpertMessageRef
    from agent_core.adapters.scripted import ScriptedEngine
    _, scopes, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    upstream = collaboration.delegate_task(command_id='excerpt-source', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Assess sample',
        delivery_requirements='State limits', resource_refs=(ref,))
    message = collaboration.send_agent_message(command_id='excerpt-message',
        task_id=upstream.task_id, session_id=upstream.session_id,
        kind='information', content='A collaborator notes that this sample is illustrative.')
    assert (await collaboration.execute_message(message.id)).status == 'delivered'
    excerpt = ExpertMessageRef(session_id=upstream.session_id, message_id=message.id)
    selected = collaboration.read_context(node.id, expert_message_refs=(excerpt,))
    assert selected['messages'][0].source_session_id == upstream.session_id
    assert selected['messages'][0].task_id == upstream.task_id
    downstream = collaboration.delegate_task(command_id='excerpt-target', node_id=node.id,
        expert_id='expert_search', task_instruction='Review the collaborator note',
        delivery_requirements='Keep provenance', expert_message_refs=(excerpt,))
    packet = collaboration.packet(downstream.packet_id)
    assert packet.expert_message_refs == (excerpt,)
    assert packet.selected_messages[0].source_session_id == upstream.session_id
    assert tasks.spec(downstream.task_id).required_refs == (ref,)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'The note is illustrative.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    status = await collaboration.execute(downstream.command_id)
    assert status['intent'].status == 'completed', (status['intent'].error, collaboration.review.get(downstream.task_id))
    with pytest.raises(ScopeDenied):
        collaboration.read_context(node.id, expert_message_refs=(
            ExpertMessageRef(session_id='foreign', message_id=message.id),))
    root = research.research(node.research_id)
    scopes.revoke(scopes.current(root.scope_id, research.owner), ref)
    with pytest.raises(ScopeDenied): collaboration.packet(downstream.packet_id)


@pytest.mark.asyncio
async def test_agent_message_tracks_sender_reply_and_does_not_start_task(workspace):
    _, scopes, research, tasks, projects, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    root = research.research(node.research_id)
    _, extra_ref = scopes.ingest(scopes.current(root.scope_id, research.owner),
        content=b'Additional saved discussion source: market size remains unknown.',
        name='discussion.txt', media_type='text/plain')
    intent = collaboration.delegate_task(command_id='message-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    calls = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            calls.append(request)
            return RunResult(run_id=request.run_id, status='succeeded',
                output='The sample does not establish real market size.')

    tasks.engine = Engine()
    info = collaboration.send_agent_message(command_id='message-info', task_id=intent.task_id,
        session_id=intent.session_id, kind='information', content='Only use the saved sample.',
        resource_refs=(extra_ref,))
    assert info.status == 'queued'
    assert collaboration.send_agent_message(command_id='message-info', task_id=intent.task_id,
        session_id=intent.session_id, kind='information', content='Only use the saved sample.',
        resource_refs=(extra_ref,)) == info
    with pytest.raises(Conflict):
        collaboration.send_agent_message(command_id='message-info', task_id=intent.task_id,
            session_id=intent.session_id, kind='information', content='Changed content')
    assert (await collaboration.execute_message(info.id)).status == 'delivered'
    assert len(calls) == 0
    assert tasks.spec(intent.task_id).required_refs == (ref,)
    question = collaboration.send_agent_message(command_id='message-question', task_id=intent.task_id,
        session_id=intent.session_id, kind='question', content='Can it support a real market size?',
        reply_to=info.id, requires_reply=True)
    done = await collaboration.execute_message(question.id)
    assert done.status == 'consumed' and done.response_id == 'expert-reply:message-question'
    consumed_info = collaboration.query('message', info.id)
    assert consumed_info.status == 'consumed'
    assert consumed_info.consumer_turn_id == done.response_id
    assert 'Additional saved discussion source' in calls[0].input.messages[0].content
    scopes.revoke(scopes.current(root.scope_id, research.owner), extra_ref)
    with pytest.raises(ScopeDenied): projects.expert_thread(intent.task_id)
    with pytest.raises(ScopeDenied):
        collaboration.send_agent_message(command_id='message-info', task_id=intent.task_id,
            session_id=intent.session_id, kind='information',
            content='Only use the saved sample.', resource_refs=(extra_ref,))
    assert len(calls) == 1
    assert (await collaboration.execute_message(question.id)) == done
    assert len(calls) == 1
    session = collaboration.query('session', intent.session_id)
    assert [m.sender for m in session.messages] == ['manager', 'manager', 'expert']
    assert session.messages[-1].reply_to == question.id
    assert session.messages[-1].spec_version == 1
    assert tasks.query('list_runs', intent.task_id) == []


@pytest.mark.asyncio
async def test_cancellation_survives_dispatch_and_is_idempotent(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='cancel-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    kwargs = dict(command_id='cancel-command', task_id=intent.task_id,
        expected_version=1, reason='User stopped this work')
    requested = collaboration.cancel_task(**kwargs)
    assert requested.status == 'requested'
    assert collaboration.cancel_task(**kwargs) == requested
    with pytest.raises(Conflict):
        collaboration.cancel_task(**{**kwargs, 'reason': 'Different reason'})
    with pytest.raises(Conflict):
        tasks.prepare_durable(intent.task_id, 1)
    settled = await collaboration.process_cancellation(requested.id)
    assert settled.status == 'cancelled'
    assert (await collaboration.execute(intent.command_id))['intent'].status == 'failed'
    assert tasks.query('list_runs', intent.task_id) == []
    with pytest.raises(Conflict):
        collaboration.cancel_task(command_id='second-cancel-command', task_id=intent.task_id,
            expected_version=1, reason='Already stopped')


@pytest.mark.asyncio
async def test_cancellation_during_model_call_blocks_late_publication(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='inflight-cancel-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    started, release = asyncio.Event(), asyncio.Event()

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            started.set()
            await release.wait()
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Late answer', 'fields': {}, 'sources': {}, 'assumptions': [],
                'conflicts': [], 'missing': [], 'limitations': [], 'follow_up': [],
                'self_checks': {}, 'claims': []})

    tasks.engine = Engine()
    executing = asyncio.create_task(collaboration.execute(intent.command_id))
    await asyncio.wait_for(started.wait(), 5)
    request = collaboration.cancel_task(command_id='inflight-cancel-command',
        task_id=intent.task_id, expected_version=1, reason='Stop while model is running')
    assert (await collaboration.process_cancellation(request.id)).status == 'stopping'
    release.set()
    await executing
    assert (await collaboration.process_cancellation(request.id)).status == 'cancelled'
    run = tasks.query('list_runs', intent.task_id)[0]
    record = tasks.ledger.get(run.id)
    assert record.status == 'failed'
    assert record.result.error.code == 'task_cancelled'


@pytest.mark.asyncio
async def test_user_revise_api_uses_collaboration_revision(workspace):
    import httpx
    from agent_core.app.api import create_app
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='user-revise-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    data = {'command_id': 'user-revise-command', 'task_id': intent.task_id,
        'expected_version': 1, 'request': 'Review the sample and state every limitation',
        'mode': 'save'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
            base_url='http://testserver') as client:
        response = await client.post('/api/expert_revise', json=data)
        assert response.status_code == 201
        assert response.json()['status'] == 'saved'
        assert (await client.post('/api/expert_revise', json=data)).json()['new_version'] == 2
    assert tasks.spec(intent.task_id).version == 2


@pytest.mark.asyncio
async def test_user_stop_api_persists_collaboration_cancellation(workspace):
    import httpx
    from agent_core.app.api import create_app
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='user-stop-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State gaps', resource_refs=(ref,))
    data = {'command_id': 'user-stop-command', 'task_id': intent.task_id,
        'expected_version': 1, 'reason': 'User requested stop'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
            base_url='http://testserver') as client:
        first = await client.post('/api/task_stop', json=data)
        assert first.status_code == 200
        assert first.json()['status'] == 'cancelled'
        assert (await client.post('/api/task_stop', json=data)).json()['id'] == data['command_id']
    assert collaboration.query('intent', intent.command_id).status == 'failed'


@pytest.mark.asyncio
async def test_manager_tool_sends_question_without_running_formal_task(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='manager-question-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Assess saved source',
        delivery_requirements='State gaps', resource_refs=(ref,))

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded',
                    output='The source is synthetic, so it cannot establish market size.')
            assert 'send_agent_message' in {tool.name for tool in tools}
            answer = (await invoke('send_agent_message', 'ask-expert', {
                'task_id': intent.task_id, 'session_id': intent.session_id,
                'kind': 'question', 'content': 'Can this source establish market size?',
                'requires_reply': True})).value
            assert answer['status'] == 'consumed'
            assert 'synthetic' in answer['answer']
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': answer['answer'], 'blocking_missing': [], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    record = await research.submit(NodeCommand(command_id='manager-question',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=node.revision, chat_revision=chat.revision,
        limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
    assert record.status == 'completed'
    assert len(collaboration.query('messages_for', intent.session_id)) == 1
    assert tasks.query('list_runs', intent.task_id) == []


@pytest.mark.asyncio
async def test_revision_saves_new_snapshot_then_runs_explicit_version(workspace):
    from agent_core.adapters.scripted import ScriptedEngine
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='revise-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='First goal',
        delivery_requirements='State gaps', resource_refs=(ref,))
    original = tasks.spec(intent.task_id)
    saved = collaboration.revise_task(command_id='revision-save', task_id=intent.task_id,
        expected_version=1, request='Second goal', mode='save')
    assert (await collaboration.process_revision(saved.id)).status == 'saved'
    assert tasks.spec(intent.task_id).version == 2
    assert tasks.spec(intent.task_id, 1) == original
    assert tasks.spec(intent.task_id).context_packet_id != original.context_packet_id
    with pytest.raises(Conflict):
        collaboration.revise_task(command_id='stale-revision', task_id=intent.task_id,
            expected_version=1, request='Stale goal', mode='save')
    assert collaboration.revise_task(command_id='revision-save', task_id=intent.task_id,
        expected_version=1, request='Second goal', mode='save') == collaboration.query('revision', saved.id)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'Third goal assessed.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    revision = collaboration.revise_task(command_id='revision-run', task_id=intent.task_id,
        expected_version=2, request='Third goal', mode='after_current')
    done = await collaboration.process_revision(revision.id)
    assert done.status == 'complete' and done.new_version == 3
    assert tasks.spec(intent.task_id, 2).goal == 'Second goal'
    assert tasks.spec(intent.task_id, 3).goal == 'Third goal'
    assert [run.spec_version for run in tasks.query('list_runs', intent.task_id)] == [3]
    assert tasks.query('receipt', f'task-result:{done.run_id}') is not None


@pytest.mark.asyncio
async def test_revision_waits_for_old_run_and_keeps_its_input(workspace):
    from agent_core.adapters.scripted import ScriptedEngine
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='queued-revise-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Original goal',
        delivery_requirements='State gaps', resource_refs=(ref,))
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'Fixed input assessed.', 'fields': {}, 'sources': {},
        'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    old_run = tasks.prepare_durable(intent.task_id, 1)
    revision = collaboration.revise_task(command_id='queued-revision', task_id=intent.task_id,
        expected_version=1, request='Revised goal', mode='after_current')
    waiting = await collaboration.process_revision(revision.id)
    assert waiting.status == 'queued' and tasks.spec(intent.task_id).version == 1
    await tasks.run_durable(intent.task_id, 1, old_run)
    done = await collaboration.process_revision(revision.id)
    assert done.status == 'complete' and done.new_version == 2
    assert [run.spec_version for run in tasks.query('list_runs', intent.task_id)] == [1, 2]
    assert tasks.spec(intent.task_id, 1).goal.startswith('Original goal')
    assert tasks.spec(intent.task_id, 2).goal == 'Revised goal'
    third = collaboration.revise_task(command_id='third-version', task_id=intent.task_id,
        expected_version=2, request='Third goal', mode='after_current')
    assert (await collaboration.process_revision(third.id)).status == 'complete'
    assert [run.spec_version for run in tasks.query('list_runs', intent.task_id)] == [1, 2, 3]


@pytest.mark.asyncio
async def test_replace_revision_stops_old_run_before_new_version(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intent = collaboration.delegate_task(command_id='replace-task', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Original goal',
        delivery_requirements='State gaps', resource_refs=(ref,))
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Assessment', 'fields': {}, 'sources': {}, 'assumptions': [],
                'conflicts': [], 'missing': [], 'limitations': [], 'follow_up': [],
                'self_checks': {}, 'claims': []})

    tasks.engine = Engine()
    old_execution = asyncio.create_task(collaboration.execute(intent.command_id))
    await asyncio.wait_for(started.wait(), 5)
    revision = collaboration.revise_task(command_id='replace-revision', task_id=intent.task_id,
        expected_version=1, request='Replacement goal', mode='replace')
    waiting = await collaboration.process_revision(revision.id)
    assert waiting.status == 'queued' and waiting.cancellation_id
    assert tasks.spec(intent.task_id).version == 1
    assert collaboration.query('cancellation', waiting.cancellation_id).status == 'stopping'
    release.set()
    await old_execution
    assert tasks.ledger.get(tasks.query('list_runs', intent.task_id)[0].id).status == 'failed'
    done = await collaboration.process_revision(revision.id)
    assert done.status == 'complete' and done.new_version == 2
    assert [run.spec_version for run in tasks.query('list_runs', intent.task_id)] == [1, 2]
    assert tasks.spec(intent.task_id, 1).goal.startswith('Original goal')
    assert tasks.spec(intent.task_id, 2).goal == 'Replacement goal'
    assert calls == 2


@pytest.mark.asyncio
async def test_independent_pending_experts_run_concurrently(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    intents = [collaboration.delegate_task(command_id=f'parallel-task-{i}', node_id=node.id,
        expert_id=expert_id, task_instruction=f'Independent review {i}',
        delivery_requirements='State gaps', resource_refs=(ref,))
        for i, expert_id in enumerate(('expert_synthesize', 'expert_search'))]
    both_started, release = asyncio.Event(), asyncio.Event()
    entered = set()

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            entered.add(request.run_id)
            if len(entered) == 2: both_started.set()
            await release.wait()
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Independent answer', 'fields': {}, 'sources': {},
                'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                'follow_up': [], 'self_checks': {}, 'claims': []})

    tasks.engine = Engine()
    recovering = asyncio.create_task(collaboration.recover_pending())
    try:
        await asyncio.wait_for(both_started.wait(), 5)
    finally:
        release.set()
        await recovering
    assert len(entered) == 2
    assert all(collaboration.query('intent', row.command_id).status == 'completed' for row in intents)
    assert all(len(tasks.query('list_runs', row.task_id)) == 1 for row in intents)


@pytest.mark.asyncio
async def test_expert_can_ask_only_manager_approved_collaborator(workspace):
    import httpx
    from agent_core.app.api import create_app
    _, _, research, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    target = collaboration.delegate_task(command_id='collaborator-target', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Assess sample',
        delivery_requirements='State limits', resource_refs=(ref,))
    source = collaboration.delegate_task(command_id='collaborator-source', node_id=node.id,
        expert_id='expert_search', task_instruction='Investigate sample',
        delivery_requirements='Ask the approved collaborator once', resource_refs=(ref,),
        collaborator_session_ids=(target.session_id,))
    packet = collaboration.packet(source.packet_id)
    assert packet.collaborator_session_ids == (target.session_id,)

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if agent.output_type is None:
                assert {'search_skills', 'load_skill', 'search_memory'} <= {t.name for t in tools}
                assert not {'delegate_task', 'search_web'} & {t.name for t in tools}
                return RunResult(run_id=request.run_id, status='succeeded',
                    output='The saved sample cannot establish a real market size.')
            assert {tool.name for tool in tools} >= {'ask_collaborator', 'delegate_collaborator'}
            answer = (await invoke('ask_collaborator', 'ask-once', {
                'task_id': target.task_id, 'session_id': target.session_id,
                'question': 'Can the sample establish real market size?'})).value
            assert 'cannot establish' in answer['answer']
            child = (await invoke('delegate_collaborator', 'delegate-child', {
                'session_id': target.session_id,
                'task_instruction': 'Independently examine the synthetic sample',
                'delivery_requirements': 'State its limits',
                'resource_refs': [ref.model_dump(mode='json')]})).value
            assert child['parent_task_id'] == source.task_id
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': answer['answer'], 'fields': {}, 'sources': {},
                'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                'follow_up': [], 'self_checks': {}, 'claims': []})

    tasks.engine = Engine()
    await collaboration.execute(source.command_id)
    message = collaboration.query('messages_for', target.session_id)[0]
    assert message.sender == 'expert' and message.sender_session_id == source.session_id
    assert message.sender_task_id == source.task_id and message.response_id
    assert message.status == 'consumed'
    child = next(row for row in collaboration.query('intents_for', node.research_id)
                 if row.command_id.startswith('expert-child:'))
    assert tasks.spec(child.task_id).parent_task_id == source.task_id
    assert collaboration.packet(child.packet_id).user_message_id == 'user-1'
    assert child.status == 'accepted'
    target_thread = collaboration.query('session', target.session_id)
    assert target_thread.messages[0].sender_session_id == source.session_id
    assert target_thread.messages[1].reply_to == message.id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
            base_url='http://testserver') as client:
        response = await client.get('/api/task_activity', params={'research_id': node.research_id})
        assert response.status_code == 200
        target_activity = next(row for row in response.json() if row['spec']['id'] == target.task_id)
        assert target_activity['messages'][0]['sender_task_id'] == source.task_id
    with pytest.raises(ScopeDenied):
        collaboration.delegate_task(command_id='foreign-collaborator', node_id=node.id,
            expert_id='expert_search', task_instruction='Investigate sample',
            delivery_requirements='State limits', resource_refs=(ref,),
            collaborator_session_ids=('foreign-session',))


def test_result_dependency_cycle_is_rejected(workspace):
    from agent_core.collaboration.models import TaskDependency
    db, _, _, _, _, collaboration = workspace
    with db.transaction() as tx:
        tx.collaboration.insert('dependencies', 'a-needs-b', TaskDependency(
            id='a-needs-b', research_id='research-cycle', downstream_task_id='task-a',
            downstream_version=1, upstream_task_id='task-b', upstream_version=1,
            result_id='result-b'))
    with pytest.raises(Conflict, match='cycle'):
        collaboration._dependencies('research-cycle', 'task-b', 1, [{
            'task_id': 'task-a', 'spec_version': 1, 'result_id': 'result-a'}])


@pytest.mark.asyncio
async def test_market_expert_fetches_body_and_keeps_source_in_receipt(workspace):
    from agent_core.resources.ingestion import Discovery, FetchedSource
    _, _, research, tasks, projects, collaboration = workspace
    node, _, _ = await seeded(workspace)
    url = 'https://example.org/market-report'

    class Provider:
        async def search(self, query):
            return (Discovery(query=query, title='Market Q report', url=url,
                summary='Search clue only; not evidence.'),)

        async def fetch(self, target):
            assert target == url
            return FetchedSource(url=url, actual_url=url, media_type='text/markdown',
                content=b'Market Q in 2025 was USD 10 million. This is test data.')

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            assert {'search_web', 'fetch_web', 'read_source', 'search_source'} <= {t.name for t in tools}
            found = (await invoke('search_web', 'market-search', {'query': 'Market Q 2025'})).value
            assert found['results'][0]['kind'] == 'discovery_only'
            body = (await invoke('fetch_web', 'market-fetch', {'ref': found['results'][0]['ref']})).value
            assert body['kind'] == 'web_body'
            fragment = body['fragments'][0]
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': f"Market Q source: {body['url']}",
                'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                'follow_up': [], 'claims': [{
                    'statement': 'The saved test source states USD 10 million for Market Q in 2025.',
                    'citations': [{'ref': body['ref'], 'location': fragment['location'],
                        'quote': 'Market Q in 2025 was USD 10 million.',
                        'relation': 'supports', 'reasoning': 'Exact text in fetched body'}]}]})

    research.search.provider = Provider()
    tasks.engine = Engine()
    intent = collaboration.delegate_task(command_id='market-web', node_id=node.id,
        expert_id='expert_market_research', task_instruction='Research Market Q in 2025',
        delivery_requirements='Cite fetched body, not search clue')
    outcome = await collaboration.execute(intent.command_id)
    assert outcome['intent'].status == 'completed'
    assert len(outcome['receipts']) == 1
    receipt = outcome['receipts'][0]
    assert receipt.delivery == 'pending_review'
    assert len(receipt.resource_refs) == 1
    metadata = research.scopes.resources.source_metadata(receipt.resource_refs[0])
    assert metadata['actual_url'] == url
    bundle = research.evidence.read(receipt.result_id)
    assert bundle.evidence[0].ref == receipt.resource_refs[0]
    project_id = projects.main_task(node.research_id).project_id
    acquired = [row for row in projects.file_links(project_id, task_id=intent.task_id)
                if row['relation'] == 'acquired' and row['ref'] == receipt.resource_refs[0]]
    assert len(acquired) == 1 and acquired[0]['url'] == url and acquired[0]['at']
    assert acquired[0]['spec_version'] == 1
    clues = [row for row in projects.file_links(project_id, task_id=intent.task_id)
             if row['relation'] == 'discovery']
    assert len(clues) == 1 and clues[0]['ref'] != receipt.resource_refs[0]
    root = research.research(node.research_id)
    root_scope = research.scopes.current(root.scope_id, research.owner)
    research.scopes.revoke(root_scope, receipt.resource_refs[0])
    with pytest.raises(ScopeDenied):
        research.evidence.read(receipt.result_id)
    with pytest.raises(ScopeDenied):
        collaboration.read_context(node.id, result_ids=(receipt.result_id,))


@pytest.mark.asyncio
async def test_market_search_failure_reports_missing_source_without_fabrication(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, _, _ = await seeded(workspace)

    class Provider:
        async def search(self, query):
            raise RuntimeError('Search provider unavailable')

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            found = (await invoke('search_web', 'failed-search', {'query': 'Market Q'})).value
            assert found['status'] == 'unavailable'
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Cannot establish market size without a fetched source.',
                'assumptions': [], 'conflicts': [],
                'missing': ['Public source body unavailable'],
                'limitations': ['Search provider unavailable'],
                'follow_up': [], 'claims': [{
                    'statement': 'Market size remains unknown',
                    'missing': ['No fetched source body']} ]})

    research.search.provider = Provider()
    tasks.engine = Engine()
    intent = collaboration.delegate_task(command_id='market-search-failure', node_id=node.id,
        expert_id='expert_market_research', task_instruction='Research Market Q',
        delivery_requirements='State unavailable evidence')
    outcome = await collaboration.execute(intent.command_id)
    assert outcome['intent'].status == 'completed'
    receipt = outcome['receipts'][0]
    assert receipt.resource_refs == ()
    assert receipt.missing == ('Public source body unavailable',)
    assert research.evidence.read(receipt.result_id).evidence == ()


def test_market_output_contract_supports_strict_json():
    from agents import AgentOutputSchema
    from agent_core.tasks.models import MarketAnswer
    schema = AgentOutputSchema(MarketAnswer, strict_json_schema=True)
    assert schema.is_strict_json_schema()
    assert 'claims' in schema.json_schema()['properties']


@pytest.mark.asyncio
async def test_manager_tool_executes_expert_and_continues_with_review_status(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    observed = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                observed.append(('expert', request.input.messages[0].content))
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'The sample is synthetic; market size is unknown.', 'fields': {},
                    'sources': {}, 'assumptions': [], 'conflicts': [],
                    'missing': ['Real market figures'], 'limitations': ['Synthetic input'],
                    'follow_up': [], 'self_checks': {}, 'claims': []})
            if 'delegate_task' not in {tool.name for tool in tools}:
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Expert preliminary result: synthetic input; market size unknown.',
                    'blocking_missing': ['Independent review pending'], 'claims': []})
            names = {tool.name for tool in tools}
            assert {'find_experts', 'read_context', 'delegate_task', 'get_task_status', 'adopt_result'} <= names
            found = (await invoke('find_experts', 'find-1', {'query': 'synthesize'})).value
            assert any(v['expert_id'] == 'expert_synthesize' for v in found['experts'])
            listed = (await invoke('list_context', 'read-1', {'category': 'messages'})).value
            assert any(m['id'] == 'user-1' for m in listed['items'])
            handoff = (await invoke('delegate_task', 'delegate-1', {
                'expert_id': 'expert_synthesize', 'task_instruction': 'Review the sample only',
                'delivery_requirements': 'State limitations',
                'message_ids': ['user-1'], 'resource_refs': [ref.model_dump(mode='json')]})).value
            observed.append(('manager', handoff))
            assert handoff['status'] == 'accepted'
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Expert preliminary result: synthetic input; market size unknown.',
                'blocking_missing': ['Independent review pending'], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    research.execution_factory = lambda bindings, service: __import__(
        'agent_core.app.research_execution', fromlist=['build_execution']).build_execution(
            research.database, research.scopes, bindings, service, engine)
    record = await research.submit(NodeCommand(command_id='manager-reply',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=node.revision, chat_revision=chat.revision,
        limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
    assert record.status == 'completed'
    assert [v[0] for v in observed] == ['manager']
    await collaboration.recover_pending()
    await collaboration.resume_manager()
    assert [v[0] for v in observed] == ['manager', 'expert']
    assert 'Ask an expert' in observed[1][1]
    assert 'Review the sample only' in observed[1][1]
    assert ChatService(research, tasks).read(node.id).messages[-1].role == 'assistant'
    assert len([s for s in collaboration.query('sessions_for', node.research_id) if s.agent.mode == 'specialist']) == 1
    assert collaboration.query('intents_for', node.research_id)[0].resume_status == 'complete'
    await collaboration.resume_manager()
    assert len([m for m in ChatService(research, tasks).read(node.id).messages if m.role == 'assistant']) == 2


@pytest.mark.asyncio
async def test_background_experts_notify_manager_after_accepted_reply(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    manager_inputs = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Only a synthetic sample is available.', 'fields': {},
                    'sources': {}, 'assumptions': [], 'conflicts': [],
                    'missing': ['Real market evidence'], 'limitations': ['Synthetic input'],
                    'follow_up': [], 'self_checks': {}, 'claims': []})
            manager_inputs.append(request.input.messages[0].content)
            if len(manager_inputs) == 1:
                for index in (1, 2):
                    result = (await invoke('delegate_task', f'background-{index}', {
                        'expert_id': 'expert_synthesize',
                        'task_instruction': f'Independent review {index}',
                        'delivery_requirements': 'State evidence limits',
                        'resource_refs': [ref.model_dump(mode='json')]})).value
                    assert result['status'] in ('accepted', 'queued')
                # Fast experts may finish while the manager is composing acceptance.
                # Those results were not in its input and must still trigger a summary.
                await collaboration.recover_pending()
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Two expert reviews have been accepted.', 'blocking_missing': [], 'claims': []})
            assert json.loads(manager_inputs[-1])['goal'].count('Only a synthetic sample is available.') == 2
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Both reviews found only a synthetic sample; independent review is pending.',
                'blocking_missing': ['Real market evidence'], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    await research.submit(NodeCommand(command_id='background-manager', research_id=node.research_id,
        node_id=node.id, kind='chat_reply', expected_revision=node.revision,
        chat_revision=chat.revision, limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
    assert len(manager_inputs) == 1
    assert ChatService(research, tasks).read(node.id).messages[-1].role == 'assistant'
    await collaboration.recover_pending()
    assert len(collaboration.pending_intents_for_node(node.id)) == 2
    await collaboration.resume_manager()
    messages = ChatService(research, tasks).read(node.id).messages
    assert messages[-1].role == 'assistant'
    assert 'Both reviews' in messages[-1].content
    assert len(manager_inputs) == 2
    assert not collaboration.pending_intents_for_node(node.id)
    await collaboration.resume_manager()
    assert len(manager_inputs) == 2


@pytest.mark.asyncio
async def test_user_expert_discussion_notifies_manager_once(workspace):
    _, _, research, tasks, projects, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    manager_inputs = []
    manager_tool_counts = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded',
                    output='This sample cannot establish the market size.')
            manager_inputs.append(request.input.messages[0].content)
            manager_tool_counts.append(len(tools))
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'I have recorded the expert discussion.', 'blocking_missing': [], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    await research.submit(NodeCommand(command_id='notice-initial-manager',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=node.revision, chat_revision=chat.revision))
    intent = collaboration.delegate_task(command_id='notice-expert', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State limits', resource_refs=(ref,))
    thread = projects.expert_thread(intent.task_id)
    await projects.discuss(intent.task_id, 'Please explain the limitation.',
        thread.revision, 'direct-discussion')
    notice = collaboration.query('notice', 'notice:direct-discussion')
    assert notice.status == 'pending' and notice.kind == 'expert_discussion'
    await collaboration.resume_manager()
    assert len(manager_inputs) == 2
    assert 'Please explain the limitation.' in manager_inputs[-1]
    assert manager_tool_counts[-1] == 0
    assert collaboration.query('notice', notice.id).status == 'complete'
    await collaboration.resume_manager()
    assert len(manager_inputs) == 2


@pytest.mark.asyncio
async def test_failed_background_expert_notifies_manager(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    manager_inputs = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='failed',
                    error=RunError(code='source_unavailable', origin='engine',
                                   message='Could not inspect the selected source'),
                    usage=Usage(complete=False))
            manager_inputs.append(request.input.messages[0].content)
            if len(manager_inputs) == 1:
                await invoke('delegate_task', 'background-failure', {
                    'expert_id': 'expert_synthesize', 'task_instruction': 'Review sample',
                    'delivery_requirements': 'State gaps',
                    'resource_refs': [ref.model_dump(mode='json')]})
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Expert review accepted.', 'blocking_missing': [], 'claims': []})
            assert 'Could not inspect the selected source' in manager_inputs[-1]
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'The expert failed; no reviewed conclusion is available.',
                'blocking_missing': ['Source unavailable'], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    await research.submit(NodeCommand(command_id='failure-manager', research_id=node.research_id,
        node_id=node.id, kind='chat_reply', expected_revision=node.revision,
        chat_revision=chat.revision))
    await collaboration.recover_pending()
    intent = collaboration.query('intents_for', node.research_id)[0]
    assert intent.status == 'failed'
    await collaboration.resume_manager()
    assert 'failed' in ChatService(research, tasks).read(node.id).messages[-1].content
    assert len(manager_inputs) == 2
    assert collaboration.query('intent', intent.command_id).resume_status == 'complete'
    assert collaboration.budget_status(node.research_id)['uncertain_run_ids']
    retry = collaboration.delegate_task(command_id='after-uncertain-failure', node_id=node.id,
        expert_id='expert_search', task_instruction='Inspect the failure from another angle',
        delivery_requirements='State missing evidence', resource_refs=(ref,))
    assert retry.status == 'accepted'


@pytest.mark.asyncio
async def test_completed_background_result_waits_for_new_user_turn(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    manager_inputs = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'The saved sample is synthetic.', 'fields': {}, 'sources': {},
                    'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                    'follow_up': [], 'self_checks': {}, 'claims': []})
            manager_inputs.append(request.input.messages[0].content)
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'I have the update.', 'blocking_missing': [], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    first = await research.submit(NodeCommand(command_id='first-user-reply',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=node.revision, chat_revision=chat.revision))
    intent = collaboration.delegate_task(command_id='late-expert', node_id=node.id,
        expert_id='expert_synthesize', task_instruction='Review the sample',
        delivery_requirements='State source limits', resource_refs=(ref,),
        manager_run_id=first.run_id)
    await collaboration.execute(intent.command_id)
    current = ChatService(research, tasks).read(node.id)
    ChatService(research, tasks).send(node.id, 'Also answer my new question.', current.revision)
    await collaboration.resume_manager()
    assert len(manager_inputs) == 1
    assert len(collaboration.pending_intents_for_node(node.id)) == 1
    current = ChatService(research, tasks).read(node.id)
    await research.submit(NodeCommand(command_id='second-user-reply',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=research.node(node.id).revision, chat_revision=current.revision))
    assert 'The saved sample is synthetic.' in manager_inputs[-1]
    assert not collaboration.pending_intents_for_node(node.id)
    await collaboration.resume_manager()
    assert len(manager_inputs) == 2


@pytest.mark.asyncio
async def test_saved_expert_result_resumes_failed_manager_turn(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    manager_inputs = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Synthetic sample; no real market estimate.', 'fields': {},
                    'sources': {}, 'assumptions': [], 'conflicts': [], 'missing': ['Market data'],
                    'limitations': ['Synthetic'], 'follow_up': [], 'self_checks': {}, 'claims': []})
            manager_inputs.append(request.input.messages[0].content)
            if len(manager_inputs) == 1:
                await invoke('delegate_task', 'delegate-1', {
                    'expert_id': 'expert_synthesize', 'task_instruction': 'Examine sample',
                    'delivery_requirements': 'State uncertainty',
                    'resource_refs': [ref.model_dump(mode='json')]})
                raise RuntimeError('Simulated manager interruption after expert commit')
            assert '本轮专家已保存的结果及复核状态' in manager_inputs[-1]
            assert 'pending_review' in manager_inputs[-1]
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'The expert found only synthetic evidence; independent review is pending.',
                'blocking_missing': ['Real market evidence'], 'claims': []})

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    first = await research.submit(NodeCommand(command_id='manager-first', research_id=node.research_id,
        node_id=node.id, kind='chat_reply', expected_revision=node.revision,
        chat_revision=chat.revision, limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
    assert research.ledger.get(first.run_id).status == 'failed'
    await collaboration.recover_pending()
    await collaboration.resume_manager()
    messages = ChatService(research, tasks).read(node.id).messages
    assert messages[-1].role == 'assistant'
    assert 'independent review is pending' in messages[-1].content
    assert len(manager_inputs) == 2
    intent = collaboration.query('intents_for', node.research_id)[0]
    assert intent.resume_status == 'complete'


@pytest.mark.asyncio
async def test_manager_continuation_stops_after_two_failed_attempts(workspace):
    _, _, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    calls = 0

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            nonlocal calls
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'Synthetic sample only.', 'fields': {}, 'sources': {},
                    'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                    'follow_up': [], 'self_checks': {}, 'claims': []})
            calls += 1
            if calls == 1:
                for side in ('A', 'B'):
                    await invoke('delegate_task', 'bounded-delegate-' + side, {
                        'expert_id': 'expert_synthesize', 'task_instruction': 'Review sample ' + side,
                        'delivery_requirements': 'State source limits',
                        'resource_refs': [ref.model_dump(mode='json')]})
            raise RuntimeError('Simulated manager failure')

    engine = Engine()
    tasks.engine = engine
    from agent_core.app.research_execution import build_execution
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    await research.submit(NodeCommand(command_id='bounded-manager', research_id=node.research_id,
        node_id=node.id, kind='chat_reply', expected_revision=node.revision,
        chat_revision=chat.revision, limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
    await collaboration.recover_pending()
    for _ in range(3):
        await collaboration.resume_manager()
    intents = collaboration.query('intents_for', node.research_id)
    assert len(intents) == 2
    assert all(i.resume_attempt == 2 and i.resume_status == 'deferred' for i in intents)
    assert calls == 3


@pytest.mark.asyncio
async def test_postgres_reopens_accepted_handoff_and_recovers_result():
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    from agent_core.adapters.scripted import ScriptedEngine
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-verify-blobs'))

    def open_services(engine=None):
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes, engine)
        tasks = build_tasks(db, scopes, research, engine)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration

    first = open_services()
    try:
        node, ref, _ = await seeded(first)
        kwargs = dict(command_id='restart:' + node.id, node_id=node.id,
            expert_id='expert_synthesize', task_instruction='Review only the saved sample',
            delivery_requirements='State missing evidence', resource_refs=(ref,))
        intent = first[5].delegate_task(**kwargs)
        assert intent.status == 'accepted'
        note = first[5].send_agent_message(command_id='restart-message:' + node.id,
            task_id=intent.task_id, session_id=intent.session_id,
            kind='information', content='Preserve the synthetic-source limitation.')
        assert note.status == 'queued'
        with first[0].transaction() as tx:
            current = tx.research.node(node.id)
            tx.research.save_node(current.model_copy(update={
                'active_run_id': 'simulated-interrupted-manager',
                'revision': current.revision + 1}), current.revision)
    finally:
        first[0].close()
    engine = ScriptedEngine(lambda request: ([], {'report': 'Synthetic sample only.',
        'fields': {}, 'sources': {}, 'assumptions': [], 'conflicts': [],
        'missing': ['Real figures'], 'limitations': ['Synthetic'],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    second = open_services(engine)
    try:
        second[2].reconcile()
        assert second[2].node(node.id).active_run_id is None
        assert second[5].delegate_task(**kwargs).task_id == intent.task_id
        await second[5].recover_pending()
        await second[5].recover_pending_messages()
        status = second[5].status(intent.command_id)
        assert status['intent'].status == 'completed'
        assert len(status['runs']) == 1
        assert status['receipts'][0].delivery == 'pending_review'
        assert second[5].packet(intent.packet_id).user_instruction.startswith('Ask an expert')
        assert second[5].query('message', note.id).status == 'delivered'
        assert second[5].query('session', intent.session_id).messages[0].sender == 'manager'
        revision = second[5].revise_task(command_id='restart-revision:' + node.id,
            task_id=intent.task_id, expected_version=1,
            request='Preserve the source limitation in a new version', mode='save')
        assert revision.status == 'queued'
    finally:
        second[0].close()
    third = open_services()
    try:
        status = third[5].status(intent.command_id)
        assert status['intent'].status == 'completed'
        assert len(status['runs']) == 1
        assert len(third[5].query('sessions_for', node.research_id)) == 1
        assert (await third[5].process_revision(revision.id)).status == 'saved'
        assert third[3].spec(intent.task_id).version == 2
    finally:
        third[0].close()


@pytest.mark.asyncio
async def test_postgres_recovers_cancellation_before_dispatch():
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-recovery-verify-blobs'))

    def open_services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes)
        tasks = build_tasks(db, scopes, research)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration

    first = open_services()
    try:
        node, ref, _ = await seeded(first)
        intent = first[5].delegate_task(command_id='restart-cancel:' + node.id, node_id=node.id,
            expert_id='expert_synthesize', task_instruction='Review sample',
            delivery_requirements='State gaps', resource_refs=(ref,))
        request = first[5].cancel_task(command_id='restart-stop:' + node.id,
            task_id=intent.task_id, expected_version=1, reason='User stopped before dispatch')
        assert request.status == 'requested'
    finally:
        first[0].close()
    second = open_services()
    try:
        await second[5].recover_pending_cancellations()
        await second[5].recover_pending()
        assert second[5].query('cancellation', request.id).status == 'cancelled'
        assert second[5].query('intent', intent.command_id).status == 'failed'
        assert second[3].query('list_runs', intent.task_id) == []
    finally:
        second[0].close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault_point', ['run_admitted', 'receipt_committed'])
async def test_postgres_recovers_handoff_without_duplicate_run(fault_point):
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    from agent_core.adapters.scripted import ScriptedEngine
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-recovery-verify-blobs'))
    engine = ScriptedEngine(lambda request: ([], {'report': 'Recovered fixed input.',
        'fields': {}, 'sources': {}, 'assumptions': [], 'conflicts': [], 'missing': [],
        'limitations': [], 'follow_up': [], 'self_checks': {}, 'claims': []}))

    def open_services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes, engine)
        tasks = build_tasks(db, scopes, research, engine)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration

    first = open_services()
    try:
        node, ref, _ = await seeded(first)
        intent = first[5].delegate_task(command_id=f'{fault_point}:{node.id}', node_id=node.id,
            expert_id='expert_synthesize', task_instruction='Review fixed input',
            delivery_requirements='Report missing evidence', resource_refs=(ref,))
        run_id = first[3].prepare_durable(intent.task_id, 1)
        if fault_point == 'receipt_committed':
            with first[0].transaction() as tx:
                tx.collaboration.save_intent(intent.model_copy(update={'status': 'queued'}), 'accepted')
            await first[3].run_durable(intent.task_id, 1, run_id)
            assert first[3].query('receipt', f'task-result:{run_id}') is not None
    finally:
        first[0].close()
    second = open_services()
    try:
        second[2].reconcile()
        await second[5].recover_pending()
        status = second[5].status(intent.command_id)
        assert status['intent'].status == 'completed'
        assert [v.id for v in status['runs']] == [run_id]
        assert len(status['receipts']) == 1
        assert status['receipts'][0].run_id == run_id
    finally:
        second[0].close()


@pytest.mark.asyncio
async def test_postgres_restart_resumes_manager_after_expert_result():
    url = os.environ.get('COLLABORATION_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated collaboration PostgreSQL URL not configured')
    from agent_core.adapters.postgres import PostgresDatabase
    blobs = Path(os.environ.get('COLLABORATION_TEST_BLOBS', '/private/tmp/collaboration-recovery-verify-blobs'))
    phase = {'resume': False}

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if request.agent_id == 'expert_synthesize':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'The saved sample is synthetic.', 'fields': {}, 'sources': {},
                    'assumptions': [], 'conflicts': [], 'missing': [], 'limitations': [],
                    'follow_up': [], 'self_checks': {}, 'claims': []})
            if not phase['resume']:
                await invoke('delegate_task', 'expert-call', {'expert_id': 'expert_synthesize',
                    'task_instruction': 'Review saved sample',
                    'delivery_requirements': 'State source limits',
                    'resource_refs': [ref.model_dump(mode='json')]})
                raise RuntimeError('Simulated manager interruption after result commit')
            assert '本轮专家已保存的结果及复核状态' in request.input.messages[0].content
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Expert result received; independent review is pending.',
                'blocking_missing': [], 'claims': []})

    engine = Engine()
    def open_services():
        db = PostgresDatabase(url)
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes, engine)
        tasks = build_tasks(db, scopes, research, engine)
        projects = build_projects(db, research, tasks)
        return db, scopes, research, tasks, projects, research.collaboration

    first = open_services()
    try:
        node, ref, chat = await seeded(first)
        command = await first[2].submit(NodeCommand(command_id='manager-crash:' + node.id,
            research_id=node.research_id, node_id=node.id, kind='chat_reply',
            expected_revision=node.revision, chat_revision=chat.revision,
            limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=8)))
        assert first[2].ledger.get(command.run_id).status == 'failed'
        await first[5].recover_pending()
        assert first[5].query('intents_for', node.research_id)[0].status == 'completed'
    finally:
        first[0].close()
    phase['resume'] = True
    second = open_services()
    try:
        second[2].reconcile()
        await second[5].resume_manager()
        messages = ChatService(second[2], second[3]).read(node.id).messages
        assert messages[-1].role == 'assistant'
        assert 'independent review is pending' in messages[-1].content
        intent = second[5].query('intents_for', node.research_id)[0]
        assert intent.resume_status == 'complete'
        assert len(second[3].query('list_runs', intent.task_id)) == 1
    finally:
        second[0].close()
