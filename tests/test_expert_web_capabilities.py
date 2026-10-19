"""Research experts respect the web grant; coding needs its separate contract."""
import pytest

from test_collaboration import workspace, seeded
from agent_core.contracts import RunResult
from agent_core.methods.defaults import published_experts
from agent_core.methods.models import AssetVersion
from agent_core.resources.ingestion import Discovery, FetchedSource


@pytest.mark.asyncio
@pytest.mark.parametrize('expert', published_experts(), ids=lambda expert: expert.id)
@pytest.mark.parametrize('web_allowed', [True, False])
async def test_each_expert_executes_web_tools_within_root_grant(workspace, expert, web_allowed):
    _, scopes, research, tasks, _, _ = workspace
    node, ref, _ = await seeded(workspace)
    root = research.research(node.research_id)
    if not web_allowed:
        scopes._replace(scopes.current(root.scope_id, research.owner),
            tools=('read_source', 'search_source'))
    calls = []
    url = 'https://example.org/expert-source'

    class Provider:
        async def search(self, query):
            calls.append('search')
            return (Discovery(query=query, title='Controlled source', url=url, summary='Clue only'),)

        async def fetch(self, target):
            assert target == url
            calls.append('fetch')
            return FetchedSource(url=url, actual_url=url, media_type='text/plain',
                content=b'Synthetic supplemental source; financial fields not supplied.')

    class Engine:
        async def execute(self, request, agent, tools, invoke):
            names = {t.name for t in tools}
            assert {'read_source', 'search_source'} <= names
            if web_allowed:
                assert {'search_web', 'fetch_web'} <= names
                found = (await invoke('search_web', 'search', {'query': 'supplemental source'})).value
                body = (await invoke('fetch_web', 'fetch', {'ref': found['results'][0]['ref']})).value
                assert 'Synthetic supplemental source' in body['fragments'][0]['text']
            else:
                assert not {'search_web', 'fetch_web'} & names
            output = {
                'report': 'Controlled capability check; no financial conclusion.',
                'fields': {}, 'sources': {}, 'assumptions': [], 'conflicts': [],
                'missing': ['Financial fields not supplied'], 'limitations': [],
                'follow_up': [], 'self_checks': {}, 'claims': []}
            if expert.id == 'expert_market_research':
                for key in ('fields', 'sources', 'self_checks'):
                    del output[key]
            return RunResult(run_id=request.run_id, status='succeeded', output=output)

    tasks.engine = Engine()
    research.search.provider = Provider()
    if expert.kind == 'coding':
        with pytest.raises(ValueError, match='[Cc]oding'):
            tasks.create(research_id=node.research_id, expert_id=expert.id, kind=expert.kind,
                user_request='Web grants do not authorize a coding repository', required_refs=(ref,))
        assert calls == []
        return
    spec = tasks.create(research_id=node.research_id, expert_id=expert.id, kind=expert.kind,
        user_request='Check available supplemental evidence', required_refs=(ref,))
    outcome = await tasks.run(spec.id)
    assert tasks.ledger.get(outcome['receipt'].run_id).status == 'succeeded'
    assert calls == (['search', 'fetch'] if web_allowed else [])
    if web_allowed:
        assert any(scopes.resources.source_metadata(r).get('actual_url') == url
            for r in outcome['receipt'].resource_refs)


@pytest.mark.asyncio
async def test_existing_session_uses_current_method_for_new_task_and_revision(workspace):
    _, _, _, tasks, _, collaboration = workspace
    node, ref, _ = await seeded(workspace)
    methods = tasks.methods
    current = methods.resolve('method', 'expert_search')
    previous = methods.save(AssetVersion(category='method', asset_id='expert_search',
        version='old-source-only', description='Prior source-only configuration',
        body={'kind': 'search', 'instructions': 'Read fixed sources.',
              'tools': ['read_source', 'search_source']},
        author='test', evidence='Controlled previous release', sanitized=True))
    methods.publish('method', previous.asset_id, previous.version, current.version, 'test', 'Set up old session')
    old = collaboration.delegate_task(command_id='old-session-task', node_id=node.id,
        expert_id='expert_search', task_instruction='Read existing source',
        delivery_requirements='State gaps', resource_refs=(ref,))
    methods.publish('method', current.asset_id, current.version, previous.version, 'test', 'Enable web for new work')
    new = collaboration.delegate_task(command_id='new-session-task', node_id=node.id,
        session_id=old.session_id, task_instruction='Search supplemental evidence',
        delivery_requirements='Fetch source bodies', resource_refs=(ref,))
    assert new.session_id == old.session_id
    assert tasks.spec(new.task_id).method_version == current.version
    assert tasks.spec(old.task_id).method_version == previous.version
    listed = next(e for e in collaboration.find_experts(node.research_id) if e['expert_id'] == 'expert_search')
    assert listed['method_version'] == current.version
    assert {'search_web', 'fetch_web'} <= set(listed['tools'])
    revision = collaboration.revise_task(command_id='revise-old', task_id=old.task_id,
        expected_version=1, request='Search supplemental evidence', mode='save')
    await collaboration.process_revision(revision.id)
    assert tasks.spec(old.task_id).method_version == current.version
    assert tasks.spec(old.task_id, 1).method_version == previous.version
