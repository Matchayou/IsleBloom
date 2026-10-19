import json
"""Control-chain tests: new data collection, original expert reassessment, manager reply.

Models and web provider are controlled fixtures, not a semantic-quality evaluation.
"""
import pytest

from test_collaboration import workspace, seeded, fixed_review_advice
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.contracts import RunLimits, RunResult
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.resources.ingestion import Discovery, FetchedSource


@pytest.mark.asyncio
@pytest.mark.parametrize('b_available', [True, False])
async def test_two_sided_investigation_returns_to_original_experts(workspace, b_available):
    _, _, research, tasks, projects, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    tasks.engine = ScriptedEngine(lambda request: ([], {
        'report': 'Preliminary finding; denominator and method need investigation.',
        'fields': {}, 'sources': {}, 'assumptions': [], 'conflicts': [],
        'missing': ['Comparable denominator and method'], 'limitations': [],
        'follow_up': [], 'self_checks': {}, 'claims': []}))
    originals = []
    for side, expert in [('A', 'expert_search'), ('B', 'expert_synthesize')]:
        intent = collaboration.delegate_task(command_id=f'original-{side}',
            node_id=node.id, expert_id=expert, task_instruction=f'Review side {side}',
            delivery_requirements='Identify missing calculation basis', resource_refs=(ref,))
        receipt = (await collaboration.execute(intent.command_id))['receipts'][0]
        originals.append((side, intent, receipt, tasks.ledger.get(receipt.run_id)))

    searched, fetched, supplemental = [], [], []

    class Provider:
        async def search(self, query):
            assert query in ('A calculation basis', 'B calculation basis')
            side = query[0]
            searched.append(side)
            if side == 'B' and not b_available:
                return ()
            return (Discovery(query=query, title=f'{side} calculation table',
                url=f'https://example.org/{side}', summary='Discovery only; read body.'),)

        async def fetch(self, target):
            side = target.rsplit('/', 1)[1]
            fetched.append(side)
            return FetchedSource(url=target, actual_url=target, media_type='text/plain',
                content=f'Synthetic fixture {side}: matched quarter, same stores, no adjustment; 16 of 100.'.encode())

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            def result(output):
                return RunResult(run_id=request.run_id, status='succeeded', output=output)

            if request.agent_id in ('expert_search', 'expert_synthesize') and json.loads(request.input.messages[0].content).get('context_mode') == 'discussion':
                # Ordinary questions only discuss fixed or attached material.
                assert not {'search_web', 'fetch_web', 'delegate_task'} & {t.name for t in tools}
                side = 'A' if request.agent_id == 'expert_search' else 'B'
                assert request.input.messages[-1].content.endswith('identify missing basis')
                return result(f'{side}: investigate denominator and adjustment method.')

            if request.agent_id in ('expert_search', 'expert_synthesize'):
                assert {'search_web', 'fetch_web'} <= {tool.name for tool in tools}
                payload = request.input.messages[0].content
                side = 'A' if 'Investigate side A' in payload else 'B'
                found = (await invoke('search_web', f'search-{side}',
                    {'query': f'{side} calculation basis'})).value
                claims, missing = [], []
                if found.get('results'):
                    body = (await invoke('fetch_web', f'fetch-{side}',
                        {'ref': found['results'][0]['ref']})).value
                    fragment = body['fragments'][0]
                    report = f'{side}: new saved source says 16 of 100 with matching basis.'
                    claims = [{'statement': report, 'citations': [{
                        'ref': body['ref'], 'location': fragment['location'],
                        'quote': f'Synthetic fixture {side}: matched quarter, same stores, no adjustment; 16 of 100.',
                        'relation': 'supports', 'reasoning': 'Exact calculation basis in saved fixture.'}]}]
                else:
                    report = 'No source body found for B; unresolved.'
                    missing = ['No source body found for B']
                    claims = [{'statement': 'B calculation basis remains unknown', 'missing': missing}]
                return result({'report': report, 'assumptions': [], 'conflicts': [],
                    'missing': missing, 'limitations': ['Controlled synthetic fixture'],
                    'follow_up': [], 'claims': claims})

            if 'delegate_task' in {t.name for t in tools}:
                catalog = (await invoke('find_experts', 'catalog', {})).value
                assert all({'search_web', 'fetch_web'} <= set(e['tools'])
                    for e in catalog['experts'] if e['expert_id'] in ('expert_search', 'expert_synthesize'))
                await invoke('read_context', 'original-reports',
                    {'result_ids': [r.result_id for _, _, r, _ in originals]})
                for side, intent, _, _ in originals:
                    question = (await invoke('send_agent_message', f'question-{side}', {
                        'task_id': intent.task_id, 'session_id': intent.session_id,
                        'kind': 'question', 'requires_reply': True,
                        'content': f'{side}: identify missing basis'})).value
                    assert 'investigate denominator' in question['answer']
                    followup = (await invoke('delegate_task', f'investigate-{side}', {
                        'session_id': intent.session_id,
                        'task_instruction': f'Investigate side {side}: denominator and adjustment method.',
                        'delivery_requirements': 'Search, fetch body, report changed conclusion or unresolved gap.',
                        'resource_refs': [ref.model_dump(mode='json')]})).value
                    assert followup['status'] == 'accepted'
                    assert followup['session_id'] == intent.session_id
                    supplemental.append({'side': side, **followup})
                return result({'report': 'Both investigations accepted; results pending.', 'blocking_missing': [], 'claims': []})
            from agent_core.tool_routing import DISCOVERY
            assert {t.name for t in tools} <= {'read_context', 'read_source', 'search_source', 'get_task_status'} | DISCOVERY
            for supplement in supplemental:
                receipt = collaboration.status(collaboration.query('intent_for_task', supplement['task_id']).command_id)['receipts'][0]
                supplement['result_id'] = receipt.result_id
            reports = (await invoke('read_context', 'new-reports',
                {'result_ids': [s['result_id'] for s in supplemental]})).value['results']
            for report in reports:
                if report['resource_refs']:
                    bodies = (await invoke('read_context', 'body-' + report['result_id'],
                        {'resource_refs': report['resource_refs']})).value
                    assert bodies['sources']
            return result({'report': 'Both now use 16 of 100; preliminary agreement.' if b_available
                else 'A now has calculation evidence; B remains unknown after search.',
                'blocking_missing': ['Independent review'] if b_available else ['B calculation basis', 'Independent review'],
                'claims': []})

    engine = Engine()
    tasks.engine = engine
    research.engine = engine
    research.search.provider = Provider()
    research.execution_factory = lambda bindings, service: __import__(
        'agent_core.app.research_execution', fromlist=['build_execution']).build_execution(
            research.database, research.scopes, bindings, service, engine)
    chat_service = ChatService(research, tasks)
    chat = chat_service.read(node.id)
    chat = chat_service.send(node.id, 'Investigate both gaps, return evidence to A and B, then reassess.',
        chat.revision, 'investigate-both')
    record = await research.submit(NodeCommand(command_id='manager-investigation',
        research_id=node.research_id, node_id=node.id, kind='chat_reply',
        expected_revision=research.node(node.id).revision, chat_revision=chat.revision,
        limits=RunLimits(max_turns=12, timeout_seconds=180, max_calls=12)))
    assert record.status == 'completed'
    run = research.ledger.get(record.run_id)
    assert run.status == 'succeeded', run.result
    assert not searched
    await collaboration.recover_pending()
    await collaboration.resume_manager()
    assert sorted(searched) == ['A', 'B']
    assert sorted(fetched) == (['A', 'B'] if b_available else ['A'])
    assert len(collaboration.query('intents_for', node.research_id)) == 4
    assert len(collaboration.query('review_loops_for', node.research_id)) == 4
    assert len(tasks.query('list_specs', node.research_id)) == 8
    assert len([s for s in collaboration.query('sessions_for', node.research_id) if s.agent.mode=='specialist']) == 2
    for _, intent, receipt, run in originals:
        assert tasks.spec(intent.task_id).version == 1
        assert len(tasks.query('list_runs', intent.task_id)) == 1
        assert tasks.receipt(receipt.result_id) == receipt
        assert tasks.ledger.get(receipt.run_id) == run
    for supplement in supplemental:
        assert tasks.receipt(supplement['result_id']).delivery == 'pending_review'
    final = chat_service.read(node.id).messages[-1].content
    assert ('preliminary agreement' if b_available else 'B remains unknown') in final
