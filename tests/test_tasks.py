import asyncio
import json
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest

from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.execution.ports import Conflict


def answer(*, unit='million USD', marker='A'):
    fields = {'company': marker, 'period': '2025', 'operating_cash_flow': '100',
              'capital_expenditure': '20'}
    sources = {'operating_cash_flow': 'line 1', 'capital_expenditure': 'line 2'}
    if unit:
        fields['unit'] = unit
        sources['unit'] = 'line 3'
    return {'report': f'{marker} cash flow', 'fields': fields, 'sources': sources,
            'missing': [] if unit else ['unit'], 'limitations': [],
            'self_checks': {str(n): 'passed' for n in range(1, 6)}}


@pytest.fixture(params=['memory', 'postgres'])
def app(request, tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('TEST_DATABASE_URL')
        if not url: pytest.skip('Dedicated TEST_DATABASE_URL required')
        db = PostgresDatabase(url)
    else:
        db = MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    research = build_research(db, scopes)
    root = research.create('R1 ' + str(uuid4()))
    scope = scopes.current(root.scope_id, 'local')
    scope, ref = scopes.ingest(scope, content=b'A 2025 CFO 100 capex 20 million USD',
                               name='report.txt', media_type='text/plain')
    engine = ScriptedEngine(lambda request: ([], answer()))
    tasks = build_tasks(db, scopes, research, engine)
    yield db, scopes, research, root, scope, ref, engine, tasks
    db.close()


def create_extract(app, **kw):
    _, _, _, root, _, ref, _, tasks = app
    return tasks.create(research_id=root.id, kind='extract', user_request='Extract A cash flow',
                        required_refs=(ref,), **kw)


def review_pass(tasks, receipt):
    return tasks.review(receipt.result_id, {n: ('passed', f'Independently read report line {n}')
                                            for n in range(1, 6)}, reviewer='test-fixture')


@pytest.mark.parametrize('seconds', [30, 360])
async def test_single_task_dispatch_preserves_admitted_deadline(app, seconds):
    from agent_core.contracts import RunLimits
    *_, engine, tasks = app
    spec = create_extract(app, limits=RunLimits(timeout_seconds=seconds))
    result = await tasks.run(spec.id)
    group = tasks.ledger.get_dispatch(result['group'].group_id)
    assert group.plan.policy.timeout_seconds == seconds
    assert group.plan.items[0].limits == spec.limits
    assert engine.requests[-1].limits == spec.limits
    assert result['receipt'].delivery == 'pending_review'


async def test_extraction_review_and_analysis_handoff(app):
    _, _, _, root, _, _, engine, tasks = app
    spec = create_extract(app)
    first = await tasks.run(spec.id)
    receipt = first['receipt']
    assert receipt.delivery == 'pending_review'
    assert receipt.adoption == 'for_discussion'
    assert all(c.status == 'pending' for c in receipt.semantic_checks)
    analysis = tasks.create(research_id=root.id, kind='analyze', user_request='Analyze cash flow',
                            required_results=(receipt.result_id,))
    with pytest.raises(Conflict): await tasks.run(analysis.id)
    assert len(engine.requests) == 1
    assert review_pass(tasks, receipt).delivery == 'passed'
    second = await tasks.run(analysis.id)
    assert second['receipt'].delivery == 'pending_review'
    payload = json.loads(engine.requests[-1].input.messages[0].content)
    assert payload['results'][0]['mount']['result_id'] == receipt.result_id
    assert tasks.receipt(receipt.result_id).adoption == 'for_discussion'
    assert tasks.adopt(receipt.result_id).adoption == 'adopted'


async def test_analysis_contextual_gaps_do_not_fail_delivered_calculation(app):
    _, _, _, root, _, _, engine, tasks = app
    extracted = (await tasks.run(create_extract(app).id))['receipt']
    review_pass(tasks, extracted)
    engine.script = lambda request: ([], {
        'report': 'A 2025: 100 - 20 = 80 million USD.',
        'missing': ['Prior-year comparison unavailable'],
        'limitations': ['Single-period source'],
    })
    analysis = tasks.create(research_id=root.id, kind='analyze', user_request='Calculate A cash flow after capex',
                            required_results=(extracted.result_id,))
    receipt = (await tasks.run(analysis.id))['receipt']
    assert receipt.delivery == 'pending_review'
    assert receipt.missing == ('Prior-year comparison unavailable',)
    reviewed = tasks.review(receipt.result_id, {
        1: ('passed', 'Input matches reviewed extraction receipt'),
        2: ('passed', '100 - 20 = 80 million USD; prior year is outside the calculation'),
    }, reviewer='test-fixture')
    assert reviewed.delivery == 'passed'


async def test_missing_input_and_partial_unit_block_numeric_analysis(app):
    _, scopes, _, root, _, ref, engine, tasks = app
    wrong = ref.model_copy(update={'parse_id': 'missing'})
    blocked = tasks.create(research_id=root.id, kind='extract', user_request='Extract missing source',
                           required_refs=(wrong,))
    with pytest.raises((KeyError, PermissionError)): await tasks.run(blocked.id)
    assert not engine.requests
    engine.script = lambda request: ([], answer(unit=''))
    partial = create_extract(app)
    receipt = (await tasks.run(partial.id))['receipt']
    assert receipt.delivery == 'partial_pending_review'
    assert 'unit' in receipt.missing
    analysis = tasks.create(research_id=root.id, kind='analyze', user_request='Calculate ratio',
                            required_results=(receipt.result_id,), allow_partial_results=True)
    with pytest.raises(Conflict): await tasks.run(analysis.id)
    assert len(engine.requests) == 1
    reviewed = tasks.review(receipt.result_id, {n:('failed' if n in (4,5) else 'passed',
        'Unit or source absent in fixture' if n in (4,5) else 'Fixture key checked') for n in range(1,6)}, reviewer='test-fixture')
    assert reviewed.delivery == 'partial'
    with pytest.raises(Conflict): await tasks.run(analysis.id)


async def test_self_report_and_task_context_isolation(app):
    _, _, _, root, _, ref, engine, tasks = app
    engine.script = lambda request: ([], answer(marker=json.loads(request.input.messages[0].content)['goal'].split(':')[0]))
    a = tasks.create(research_id=root.id, kind='extract', user_request='A_SECRET: extract', required_refs=(ref,))
    b = tasks.create(research_id=root.id, kind='extract', user_request='B_SECRET: extract', required_refs=(ref,))
    ra = (await tasks.run(a.id))['receipt']
    rb = (await tasks.run(b.id))['receipt']
    inputs = [request.input.messages[0].content for request in engine.requests]
    assert 'B_SECRET' not in inputs[0] and 'A_SECRET' not in inputs[1]
    assert ra.delivery == rb.delivery == 'pending_review'
    assert all(c.status == 'pending' for c in ra.semantic_checks)


async def test_atomic_receipt_failure_save_retry_and_no_extra_model_call(app):
    db, _, _, root, _, _, engine, tasks = app
    spec = create_extract(app)
    original = db.transaction
    failing = [True]
    @contextmanager
    def broken():
        with original() as tx:
            if hasattr(tx, 'tasks'):
                class Proxy:
                    def __getattr__(self, key): return getattr(tx, key)
                repo = tx.tasks
                class TasksProxy:
                    def __getattr__(self, key): return getattr(repo, key)
                    def save_receipt(self, value, expected=None):
                        repo.save_receipt(value, expected)
                        if failing[0]: raise RuntimeError('after receipt before commit')
                proxy = Proxy()
                proxy.tasks = TasksProxy()
                yield proxy
            else: yield tx
    db.transaction = broken
    result = await tasks.run(spec.id)
    assert result['receipt'] is None
    run_id = result['run_id']
    assert tasks.ledger.get(run_id).commit_id is None
    assert len(engine.requests) == 1
    downstream = tasks.create(research_id=root.id, kind='analyze', user_request='Analyze',
        required_results=(f'task-result:{run_id}',))
    with pytest.raises(KeyError): await tasks.run(downstream.id)
    failing[0] = False
    tasks.retry_save(run_id)
    assert tasks.receipt(f'task-result:{run_id}').delivery == 'pending_review'
    assert len(engine.requests) == 1
    db.transaction = original


async def test_late_old_spec_and_cancelled_dependency(app):
    _, _, _, root, _, _, engine, tasks = app
    spec = create_extract(app)
    entered, release = asyncio.Event(), asyncio.Event()
    async def late(request, spec, tools, invoke):
        from agent_core.contracts import RunResult
        entered.set()
        await release.wait()
        return RunResult(run_id=request.run_id, status='succeeded', output=answer())
    engine.execute = late
    running = asyncio.create_task(tasks.run(spec.id))
    await entered.wait()
    tasks.create(research_id=root.id, kind='extract', user_request='Updated extraction',
                 required_refs=spec.required_refs, task_id=spec.id)
    release.set()
    result = await running
    assert result['receipt'].delivery == 'rework'
    assert result['receipt'].spec_version == 1
    assert tasks.spec(spec.id).version == 2
    analysis = tasks.create(research_id=root.id, kind='analyze', user_request='Analyze',
                            required_results=(result['receipt'].result_id,))
    with pytest.raises(Conflict): await tasks.run(analysis.id)


async def test_cancelled_run_never_unlocks_dependency(app):
    _, _, _, root, _, _, engine, tasks = app
    spec = create_extract(app)
    entered = asyncio.Event()
    async def wait_forever(request, spec, tools, invoke):
        entered.set()
        await asyncio.Event().wait()
    engine.execute = wait_forever
    running = asyncio.create_task(tasks.run(spec.id))
    await entered.wait()
    assert tasks.stop(spec.id)
    result = await running
    assert result['receipt'] is None or result['receipt'].delivery != 'passed'
    analysis = tasks.create(research_id=root.id, kind='analyze', user_request='Analyze',
                            required_results=(f'task-result:{result["run_id"]}',))
    with pytest.raises((KeyError, Conflict)): await tasks.run(analysis.id)


async def test_http_task_activity_and_handoff(app):
    import httpx
    from agent_core.app.api import create_app
    _, _, research, root, _, ref, _, tasks = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research, task_service=tasks)),
                                 base_url='http://test') as client:
        created = await client.post('/api/task_create', json={'research_id':root.id,'kind':'extract',
            'request':'Extract cash flow','required_refs':[ref.model_dump(mode='json')]})
        assert created.status_code == 201
        task_id = created.json()['id']
        ran = await client.post('/api/task_run',json={'task_id':task_id})
        assert ran.status_code == 200
        result_id = ran.json()['receipt']['result_id']
        activity = await client.get('/api/task_activity',params={'research_id':root.id})
        assert activity.status_code == 200
        assert activity.json()[0]['runs'][0]['receipt']['delivery'] == 'pending_review'
        reviewed = await client.post('/api/task_review',json={'result_id':result_id,'reviewer':'test-fixture',
            'findings':{str(n):{'status':'passed','evidence':'Fixture source checked'} for n in range(1,6)}})
        assert reviewed.status_code == 200 and reviewed.json()['delivery'] == 'passed'
        analysis = await client.post('/api/task_create',json={'research_id':root.id,'kind':'analyze',
            'request':'Analyze cash flow','required_results':[result_id]})
        assert analysis.status_code == 201
        downstream = await client.post('/api/task_run',json={'task_id':analysis.json()['id']})
        assert downstream.status_code == 200
        assert downstream.json()['receipt']['result_ids'] == [result_id]


async def test_partial_handoff_preserves_missing_and_revocation_blocks_review(app):
    _, scopes, _, root, scope, ref, engine, tasks = app
    engine.script = lambda request: ([], answer(unit=''))
    source = create_extract(app)
    receipt = (await tasks.run(source.id))['receipt']
    assert receipt.delivery == 'partial_pending_review'
    synthesis = tasks.create(research_id=root.id, kind='synthesize',
        user_request='Discuss available findings with gaps', required_results=(receipt.result_id,),
        allow_partial_results=True)
    with pytest.raises(Conflict): await tasks.run(synthesis.id)
    tasks.review(receipt.result_id, {n:('failed' if n in (4,5) else 'passed',
        'Unit or source absent in fixture' if n in (4,5) else 'Fixture key checked') for n in range(1,6)}, reviewer='test-fixture')
    await tasks.run(synthesis.id)
    payload = json.loads(engine.requests[-1].input.messages[0].content)
    assert 'unit' in payload['results'][0]['mount']['missing']
    scopes.revoke(scope, ref)
    blocked = tasks.create(research_id=root.id, kind='synthesize',
        user_request='Reuse revoked source', required_results=(receipt.result_id,),
        allow_partial_results=True)
    with pytest.raises(PermissionError): await tasks.run(blocked.id)


async def test_target_node_revision_blocks_old_task(app):
    from agent_core.research.models import NodeCommand
    _, _, research, root, _, ref, engine, tasks = app
    command = await research.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
                                                kind='create', goal='A branch'))
    spec = tasks.create(research_id=root.id, node_id=command.node_id, kind='extract',
                        user_request='Extract', required_refs=(ref,))
    await research.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
                                      node_id=command.node_id, kind='exclude'))
    with pytest.raises(Conflict): await tasks.run(spec.id)
    assert not engine.requests


async def test_optional_input_is_reported_without_blocking(app):
    _, _, _, root, _, ref, engine, tasks = app
    unavailable = ref.model_copy(update={'parse_id':'unavailable'})
    spec = tasks.create(research_id=root.id, kind='extract', user_request='Extract A',
                        required_refs=(ref,), optional_refs=(unavailable,))
    receipt = (await tasks.run(spec.id))['receipt']
    assert receipt.missing_optional_refs == (unavailable,)
    assert 'Optional inputs unavailable' in engine.requests[0].input.messages[0].content
