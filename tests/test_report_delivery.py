"""Actual SDK format correction inside background delivery and a single manager summary."""
import asyncio
import json

import pytest

from test_collaboration import workspace, seeded, fixed_review_advice
from test_sdk_contract import QueueModel, text
from agent_core.adapters.openai import OpenAIEngine
from agent_core.app.research_execution import build_execution
from agent_core.contracts import RunResult
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand


@pytest.mark.parametrize('b_repairs', [True, False])
async def test_wait_for_b_format_correction_before_single_summary(workspace, b_repairs):
    _, scopes, research, tasks, _, collaboration = workspace
    node, ref, chat = await seeded(workspace)
    root_scope = scopes.current(research.research(node.research_id).scope_id, research.owner)
    _, large_ref = scopes.ingest(root_scope, name='large-source.txt', media_type='text/plain',
        content=('Distinctive needle evidence.\n' + 'Large context line.\n' * 6000).encode())
    report = {'report': 'B: sample is synthetic; real size remains unknown.', 'claims': [],
        'missing': ['Real market data']}
    model = QueueModel([[text('{"report":')], [text(json.dumps(report))]] if b_repairs
        else [[text('{')]] * 3)
    sdk = OpenAIEngine(model)
    entered, release = asyncio.Event(), asyncio.Event()
    manager_calls = []

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            if agent.id == 'expert_search':
                return RunResult(run_id=request.run_id, status='succeeded', output={
                    'report': 'A: sample is synthetic.' + ' Detailed evidence.' * 2000,
                    'claims': [], 'missing': ['Real data']})
            if agent.id == 'expert_synthesize':
                entered.set()
                await release.wait()
                return await sdk.execute(request, agent, tools, invoke)
            manager_calls.append(request.input.messages[0].content)
            if len(manager_calls) == 1:
                for expert in ('expert_search', 'expert_synthesize'):
                    result = (await invoke('delegate_task', expert, {'expert_id': expert,
                        'task_instruction': 'Review sample', 'delivery_requirements': 'State missing evidence',
                        'resource_refs': [ref.model_dump()]})).value
                    assert result['status'] == 'accepted' and 'report' not in result
                answer = 'Two expert reviews are pending.'
            else:
                assert len(manager_calls) == 2
                assert len(manager_calls[-1]) < 30000
                from agent_core.tool_routing import DISCOVERY
                assert {t.name for t in tools} <= {'read_context', 'read_source', 'search_source', 'get_task_status'} | DISCOVERY
                outcomes = collaboration.pending_results_for_node(node.id)
                assert len(outcomes) == 2
                assert sum(r['execution_status'] == 'succeeded' for r in outcomes) == (2 if b_repairs else 1)
                bodies = (await invoke('read_context', 'saved-source', {'resource_refs': [ref.model_dump()]})).value
                assert bodies['sources']
                reports = await invoke('read_context', 'reports', {'result_ids': [r['result_id'] for r in outcomes
                    if r['execution_status'] == 'succeeded']})
                assert any(len(row['output']['report']) > 30000 for row in reports.value['results'])
                too_large = await invoke('read_context', 'large-source', {'resource_refs': [large_ref.model_dump()]})
                # Business handler retains the full authorized object; the controlled
                # channel persists and pages it before a real SDK model consumes it.
                assert len(str(too_large.value['sources'])) > 100000
                assert large_ref in collaboration.read_refs(request.run_id)
                located = await invoke('search_source', 'located-source', {
                    'ref': large_ref.model_dump(), 'query': 'Distinctive needle'})
                assert located.value['fragments'][0]['text'] == 'Distinctive needle evidence.'
                assert large_ref in collaboration.read_refs(request.run_id)
                answer = 'A and B agree: real size is unknown.' if b_repairs else 'A delivered; B failed final formatting. Real size unknown.'
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': answer, 'claims': [], 'blocking_missing': ['Real data']})

    engine = Engine()
    tasks.engine = engine
    research.execution_factory = lambda bindings, service: build_execution(
        research.database, research.scopes, bindings, service, engine)
    first = await research.submit(NodeCommand(command_id='delivery-manager', research_id=node.research_id,
        node_id=node.id, kind='chat_reply', expected_revision=node.revision, chat_revision=chat.revision))
    assert first.status == 'completed'
    intents = collaboration.query('intents_for', node.research_id)
    a = next(i for i in intents if tasks.spec(i.task_id).expert_id == 'expert_search')
    b = next(i for i in intents if tasks.spec(i.task_id).expert_id == 'expert_synthesize')
    await collaboration.execute(a.command_id)
    b_job = asyncio.create_task(collaboration.execute(b.command_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await collaboration.resume_manager()
        assert len(manager_calls) == 1 and not collaboration.pending_results_for_node(node.id)
        release.set()
        await b_job
    finally:
        release.set()
        if not b_job.done():
            b_job.cancel()
            await asyncio.gather(b_job, return_exceptions=True)
    b_run = tasks.query('list_runs', b.task_id)[0]
    saved = tasks.ledger.get(b_run.id)
    assert len(saved.result.output_repairs) == (1 if b_repairs else 3)
    assert saved.result.usage.requests == (2 if b_repairs else 3)
    assert tasks.query('receipt', 'task-result:' + b_run.id).delivery == ('pending_review' if b_repairs else 'rework')
    if not b_repairs:
        assert saved.result.output is None
    await collaboration.resume_manager()
    await collaboration.resume_manager()
    assert len(manager_calls) == 2
    assert len(collaboration.query('intents_for', node.research_id)) == 2
    assert len(tasks.query('list_specs', node.research_id)) == 4
    assert all(i.resume_status == 'complete' for i in collaboration.query('intents_for', node.research_id))
    assert len([m for m in ChatService(research, tasks).read(node.id).messages if m.role == 'assistant']) == 2
