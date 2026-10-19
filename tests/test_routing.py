import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
import httpx

from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources
from agent_core.contracts import RunResult, Usage
from agent_core.execution.ports import Conflict
from agent_core.routing import Router, RouteBlocked, ModelCandidate
from agent_core.routing.config import CatalogFile
from agent_core.adapters.routellm import RouteLLMClient
from agent_core.methods.defaults import published_experts


class RouteFixture:
    strong_model = 'model-b'
    weak_model = 'model-a'
    checkpoint_path = '/frozen/fixture'
    threshold = 0.5

    def __init__(self, choices=()):
        self.choices, self.prompts = list(choices), []

    async def choose(self, prompt):
        self.prompts.append(prompt)
        return self.choices.pop(0)


class CaptureEngine:
    def __init__(self): self.calls = []

    async def execute(self, request, spec, tools, invoke):
        self.calls.append((request, spec.model, spec.id))
        return RunResult(run_id=request.run_id, status='succeeded', usage=Usage(requests=1,
            input_tokens=100, output_tokens=50), output={
            'report': 'Company 2025 CFO 100 capex 20 million USD',
            'fields': {'company': 'Company', 'period': '2025', 'operating_cash_flow': '100',
                       'capital_expenditure': '20', 'unit': 'million USD'},
            'sources': {'operating_cash_flow': 'line 1', 'capital_expenditure': 'line 1',
                        'unit': 'line 1'}})


def candidates():
    experts = [e for e in published_experts() if e.kind == 'extract']
    models = [ModelCandidate(id=name, description=name, supported_kinds=frozenset({'extract'}),
        tools=frozenset({'read_source'}), max_input_chars=20000,
        capability_rank=rank, input_usd_per_million_tokens=rank,
        output_usd_per_million_tokens=2 * rank,
        capability_source='frozen test fixture', price_source='frozen test fixture')
        for name, rank in (('model-a', 1), ('model-b', 2))]
    return experts, models


@pytest.fixture(params=['memory', 'postgres'])
def app(request, tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if request.param == 'postgres' and not url: pytest.skip('Dedicated TEST_DATABASE_URL required')
    db = PostgresDatabase(url) if request.param == 'postgres' else MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    research = build_research(db, scopes)
    root = research.create('RouteLLM ' + str(uuid4()))
    scope = scopes.current(root.scope_id, 'local')
    scope, ref = scopes.ingest(scope, content=b'Company 2025 CFO 100 capex 20 million USD',
        name='fixture.txt', media_type='text/plain')
    engine = CaptureEngine()
    yield db, scopes, research, root, ref, engine
    db.close()


def service(app, model_router, *, experts=None, models=None):
    db, scopes, research, _, _, engine = app
    standard_experts, standard_models = candidates()
    router = Router(experts or standard_experts, models or standard_models, model_router,
        catalog_version='frozen-fixture-v1')
    return build_tasks(db, scopes, research, engine, router)


def task_args(app, **extra):
    _, _, _, root, ref, _ = app
    args = dict(research_id=root.id, kind='extract', user_request='SECRET_AFTER_SCOPE: extract',
        required_refs=(ref,), approved_summary='Extract company cash flow and capex',
        budget_usd=1.0)
    args.update(extra)
    return args


def test_catalog_requires_frozen_pair(tmp_path):
    _, models = candidates()
    data = dict(version='v1', strong_model='model-b', weak_model='model-a',
        checkpoint_path=str(tmp_path), threshold=0.5,
        models=[model.model_dump(mode='json') for model in models])
    assert CatalogFile.model_validate(data).strong_model == 'model-b'
    data['weak_model'] = 'model-b'
    with pytest.raises(ValueError, match='pair'):
        CatalogFile.model_validate(data)


def test_verified_deepseek_single_model_catalog():
    from agent_core.routing.config import load_router
    router = load_router('config/deepseek-flash.json')
    assert tuple(router.models) == ('deepseek-flash',)
    assert router.model_router is None
    assert router.models['deepseek-flash'].input_usd_per_million_tokens == 0.3


async def test_single_model_catalog_routes_without_classifier(app):
    from agent_core.routing.config import load_router
    db, scopes, research, _, _, engine = app
    tasks = build_tasks(db, scopes, research, engine, load_router('config/deepseek-flash.json'))
    spec = await tasks.create_routed(**task_args(app))
    route = tasks.query('route', spec.route_id)
    assert spec.model == 'deepseek-flash'
    assert route.router_version == 'single-model'
    assert route.steps[-1].source == 'only_candidate'


async def test_adapter_uses_official_route_api(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-offline-fixture')
    for name in ('config.json', 'model.safetensors', 'tokenizer.json'):
        (tmp_path / name).touch()
    captured = {}
    class Controller:
        def __init__(self, **kwargs): captured['init'] = kwargs
        def route(self, prompt, *, router, threshold):
            captured['route'] = (prompt, router, threshold)
            return 'model-b'
    monkeypatch.setitem(sys.modules, 'routellm.controller',
        SimpleNamespace(Controller=Controller))
    client = RouteLLMClient(strong_model='model-b', weak_model='model-a',
        checkpoint_path=tmp_path, threshold=0.4)
    assert await client.choose('Approved summary') == 'model-b'
    assert captured['init']['config'] == {'bert': {'checkpoint_path': str(tmp_path)}}
    assert captured['route'] == ('Approved summary', 'bert', 0.4)


async def test_route_frozen_and_changes_actual_execution(app):
    selector = RouteFixture(['model-b', 'model-a'])
    tasks = service(app, selector)
    first = await tasks.create_routed(**task_args(app, preferred_expert='expert_extract_financial'))
    second = await tasks.create_routed(**task_args(app))
    assert (first.expert_id, first.model) == ('expert_extract_financial', 'model-b')
    assert (second.expert_id, second.model) == ('expert_extract', 'model-a')
    route = tasks.query('route', first.route_id)
    assert [(s.stage, s.source) for s in route.steps] == [('expert', 'user'), ('model', 'routellm')]
    assert route.catalog_version == 'frozen-fixture-v1'
    assert all('SECRET_AFTER_SCOPE' not in prompt for prompt in selector.prompts)
    assert selector.prompts == ['Extract company cash flow and capex'] * 2
    a, b = await tasks.run(first.id), await tasks.run(second.id)
    assert a['receipt'].route_id == first.route_id
    assert b['receipt'].route_id == second.route_id
    assert app[-1].calls[0][1:] == ('model-b', 'expert_extract_financial')
    assert app[-1].calls[1][1:] == ('model-a', 'expert_extract')


async def test_routed_task_keeps_branch_chat_context(app):
    from agent_core.research.chat import ChatService
    _, _, research, root, _, engine = app
    tasks = service(app, RouteFixture(['model-b']))
    chat = ChatService(research, tasks)
    main = await chat.start(root.id, '主线')
    main = chat.send(main.node_id, 'COMMON_R3', main.revision)
    branch = await chat.branch(main.node_id, main.messages[-1].id, '方向', main.revision)
    branch = chat.send(branch.node_id, 'BRANCH_ONLY_R3', branch.revision)
    main = chat.send(main.node_id, 'MAIN_AFTER_FORK_R3', main.revision)
    spec = await tasks.create_routed(**task_args(app, node_id=branch.node_id))
    assert spec.route_id and spec.chat_context[-1].content == 'BRANCH_ONLY_R3'
    outcome = await tasks.run(spec.id)
    assert outcome['receipt'].route_id == spec.route_id
    prompt = engine.calls[-1][0].input.messages[0].content
    assert 'COMMON_R3' in prompt and 'BRANCH_ONLY_R3' in prompt
    assert 'MAIN_AFTER_FORK_R3' not in prompt
    assert engine.calls[-1][1] == 'model-b'


async def test_user_selection_and_only_candidate_skip_routellm(app):
    selector = RouteFixture()
    experts, models = candidates()
    tasks = service(app, selector, experts=experts[:1], models=models[:1])
    spec = await tasks.create_routed(**task_args(app))
    assert [s.source for s in tasks.query('route', spec.route_id).steps] == [
        'only_candidate', 'only_candidate']
    assert selector.prompts == []
    with pytest.raises(RouteBlocked):
        await tasks.create_routed(**task_args(app, preferred_expert='expert_extract_financial'))
    assert app[-1].calls == []


async def test_evidence_route_requires_search_tool_and_bounded_preview(app):
    experts, models = candidates()
    tasks = service(app, RouteFixture(), experts=experts[:1], models=models[:1])
    with pytest.raises(RouteBlocked):
        await tasks.create_routed(**task_args(app, evidence_required=True))
    capable = models[0].model_copy(update={'tools':frozenset({'read_source','search_source'})})
    tasks.router = Router(experts[:1],[capable],RouteFixture(),catalog_version='evidence-fixture')
    spec = await tasks.create_routed(**task_args(app,evidence_required=True))
    assert spec.evidence_required and spec.route_id


async def test_route_error_invalid_result_and_exclusions_block(app):
    tasks = service(app, RouteFixture(['not-a-model']))
    with pytest.raises(RouteBlocked) as exc:
        await tasks.create_routed(**task_args(app))
    assert exc.value.record.status == 'blocked'
    assert tasks.query('route', exc.value.record.id)
    assert exc.value.record.steps[-1].source == 'routellm'
    assert not app[-1].calls
    class Broken(RouteFixture):
        async def choose(self, prompt): raise RuntimeError('offline')
    tasks.router.model_router = Broken()
    with pytest.raises(RouteBlocked) as exc:
        await tasks.create_routed(**task_args(app))
    assert 'RuntimeError' in exc.value.record.reason
    experts, models = candidates()
    tiny = models[0].model_copy(update={'max_input_chars': 10})
    tasks.router = Router(experts, [tiny], RouteFixture(), catalog_version='tiny')
    with pytest.raises(RouteBlocked) as exc:
        await tasks.create_routed(**task_args(app))
    assert 'no compatible model' in exc.value.record.steps[0].excluded['expert_extract']
    assert not app[-1].calls


async def test_budget_blocks_before_model_router(app):
    selector = RouteFixture()
    tasks = service(app, selector)
    with pytest.raises(RouteBlocked) as exc:
        await tasks.create_routed(**task_args(app, budget_usd=0.000000001))
    assert exc.value.record.steps[0].stage == 'expert'
    assert selector.prompts == []
    assert not app[-1].calls


async def test_frozen_model_config_and_cumulative_budget(app):
    tasks = service(app, RouteFixture(['model-a']))
    spec = await tasks.create_routed(**task_args(app))
    tasks.router.models['model-a'] = tasks.router.models['model-a'].model_copy(
        update={'max_input_chars': 100})
    await tasks.run(spec.id)
    assert app[-1].calls[0][1] == 'model-a'
    assert tasks.query('route', spec.route_id).selected_model_config.max_input_chars == 20000
    tasks.router.models['model-a'] = candidates()[1][0]
    with pytest.raises(ValueError, match='budget cannot change'):
        await tasks.create_routed(**task_args(app, task_id=spec.id, budget_usd=2.0))


async def test_optional_input_change_rechecks_actual_capacity(app):
    db, scopes, research, root, ref, engine = app
    source_scope = scopes.create('local')
    source_scope, optional = scopes.ingest(source_scope, content=b'X' * 12000,
        name='later.txt', media_type='text/plain')
    experts, models = candidates()
    models = [m.model_copy(update={'max_input_chars': 7000}) for m in models]
    tasks = service(app, RouteFixture(['model-a']), experts=experts, models=models)
    spec = await tasks.create_routed(**task_args(app, optional_refs=(optional,)))
    root_scope = scopes.current(root.scope_id, 'local')
    root_scope = scopes.grant(root_scope, source_scope, optional)
    scopes.select(root_scope, (*root_scope.allowed, optional))
    with pytest.raises(Conflict, match='capacity'):
        await tasks.run(spec.id)
    assert engine.calls == []


async def test_http_route_record_and_revocation(app):
    from agent_core.app.api import create_app
    db, scopes, research, root, ref, _ = app
    tasks = service(app, RouteFixture(['model-a']))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=create_app(research, task_service=tasks)), base_url='http://test') as client:
        created = await client.post('/api/task_create', json={
            'route': True, 'research_id': root.id, 'kind': 'extract',
            'request': 'Extract fields', 'routing_summary': 'Extract company cash flow',
            'budget_usd': 1.0,
            'required_refs': [ref.model_dump(mode='json')]})
        assert created.status_code == 201, created.text
        spec = created.json()
        shown = await client.get('/api/task_route', params={'id': spec['route_id']})
        assert shown.status_code == 200
        assert shown.json()['selected_model'] == 'model-a'
        activity = await client.get('/api/task_activity', params={'research_id': root.id})
        assert activity.json()[0]['route']['id'] == spec['route_id']
        scope = scopes.current(root.scope_id, 'local')
        scopes.revoke(scope, ref)
        hidden = await client.get('/api/task_route', params={'id': spec['route_id']})
        assert hidden.status_code == 403


async def test_continuation_keeps_route_and_reselection_is_bounded(app):
    tasks = service(app, RouteFixture(['model-a', 'model-b']))
    first = await tasks.create_routed(**task_args(app))
    continued = await tasks.create_routed(**task_args(app, task_id=first.id))
    assert (continued.expert_id, continued.model) == (first.expert_id, first.model)
    assert [s.source for s in tasks.query('route', continued.route_id).steps] == ['user', 'user']
    upgraded = await tasks.create_routed(**task_args(app, task_id=first.id,
        reroute_reason='Model capacity changed'))
    assert upgraded.expert_id == first.expert_id and upgraded.model == 'model-b'
    assert tasks.query('route', upgraded.route_id).trigger == 'Model capacity changed'
    with pytest.raises(Conflict, match='reselection limit'):
        await tasks.create_routed(**task_args(app, task_id=first.id,
            reroute_reason='Try again'))


async def test_routed_extraction_to_reviewed_analysis(app):
    experts, models = candidates()
    models = [m.model_copy(update={'supported_kinds': frozenset({'extract', 'analyze'})})
              for m in models]
    tasks = service(app, RouteFixture(['model-a', 'model-b']), experts=experts +
        [e for e in published_experts() if e.kind == 'analyze'], models=models)
    extraction = await tasks.create_routed(**task_args(app))
    receipt = (await tasks.run(extraction.id))['receipt']
    tasks.review(receipt.result_id, {n: ('passed', 'Fixture line verified') for n in range(1, 6)},
                 reviewer='test-fixture')
    analysis = await tasks.create_routed(research_id=app[3].id, kind='analyze',
        user_request='Analyze reviewed cash flow', required_results=(receipt.result_id,),
        approved_summary='Analyze reviewed extraction',
        budget_usd=1.0)
    assert analysis.expert_id == 'expert_analyze' and analysis.model == 'model-b'
    result = await tasks.run(analysis.id)
    assert result['receipt'].result_ids == (receipt.result_id,)
    assert app[-1].calls[-1][1] == 'model-b'
