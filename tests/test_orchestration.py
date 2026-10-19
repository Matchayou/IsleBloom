import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_execution import database, FaultDatabase
from agent_core.adapters.scripted import ScriptedEngine, Call
from legacy_composition import build_execution, build_orchestration
from agent_core.contracts import AgentSpec, Message, PreparedInput, RunRequest, RunResult, Usage
from agent_core.core import AgentCatalog, CoreRunner
from agent_core.execution.ledger import Ledger, EventSink
from agent_core.execution.ports import Conflict
from agent_core.execution.service import ExecutionService
from agent_core.orchestration import Dispatcher, DispatchItem, DispatchPlan, DispatchPolicy, collect


def setup(db, engine, *, delegates=False):
    catalog = AgentCatalog([AgentSpec('expert', '', delegates=('expert',) if delegates else ())])
    ledger = Ledger(db)
    events = EventSink(ledger)
    service = ExecutionService(CoreRunner(engine, catalog, events), ledger, events)
    return Dispatcher(service, catalog, ledger), service


def plan(n=3, **policy):
    return DispatchPlan(items=tuple(DispatchItem(key=str(i), agent_id='expert',
        input=PreparedInput(messages=(Message(role='user', content=f'private-{i}'),))) for i in range(n)),
        policy=DispatchPolicy(**policy))


async def test_three_experts_independent_committed_and_replay(database):
    router, dispatcher = build_orchestration(database)
    assert router.route('普通问题').plan is None
    p = router.route('请三个专家分别比较方案的成本、速度与限制', mode='compare').plan
    result = await dispatcher.dispatch(p)
    assert result.complete and [i.key for i in result.items] == ['cost', 'speed', 'limits']
    for item, saved in zip(p.items, result.items):
        record = dispatcher.repository.get(item.run_id)
        assert record.request.input == item.input and len(record.request.input.messages) == 1
        assert record.request.parent_run_id == p.group_id and record.request.depth == 1
        assert record.conversation_id is None and record.commit_id == f'run:{item.run_id}'
        assert saved.result == record.result
        assert all(e.event.parent_run_id == p.group_id for e in dispatcher.repository.events(item.run_id))
    assert await dispatcher.dispatch(p) == result
    with pytest.raises(Conflict):
        await dispatcher.dispatch(p.model_copy(update={'policy': DispatchPolicy(max_runs=1)}))


async def test_barrier_concurrency_cap_and_out_of_order_collection(database):
    entered = [asyncio.Event() for _ in range(4)]
    release = [asyncio.Event() for _ in range(4)]
    order, live = [], set()
    class Barrier:
        async def execute(self, request, *args):
            i = int(request.input.messages[0].content.split('-')[-1])
            live.add(i)
            assert len(live) <= 2
            entered[i].set()
            try:
                await release[i].wait()
                order.append(i)
                return RunResult(run_id=request.run_id, status='succeeded', output=f'result-{i}')
            finally:
                live.remove(i)
    dispatcher, _ = setup(database, Barrier())
    p = plan(4, max_concurrency=2, max_runs=4)
    task = asyncio.create_task(dispatcher.dispatch(p))
    async with asyncio.timeout(5):
        await entered[0].wait()
        await entered[1].wait()
        assert live == {0, 1} and not entered[2].is_set()
        release[1].set()
        await entered[2].wait()
        release[2].set()
        await entered[3].wait()
        release[3].set()
        release[0].set()
        result = await task
    assert order[:2] == [1, 2] and not live
    assert [i.result.output for i in result.items] == [f'result-{i}' for i in range(4)]


async def test_partial_failure_remains_visible(database):
    def script(req):
        return ([Call('bad', {}, 'bad')], 'hidden') if req.input.messages[0].content == 'private-1' else ([], req.input.messages[0].content)
    dispatcher, _ = setup(database, ScriptedEngine(script))
    result = await dispatcher.dispatch(plan())
    assert [i.status for i in result.items] == ['completed', 'failed', 'completed']
    assert 'unknown_tool' in result.items[1].missing_reason and not result.complete


@pytest.mark.parametrize('stop', ['cancelled', 'timed_out', 'failed_fast'])
async def test_stop_drains_children_and_keeps_pending_visible(database, stop):
    entered = asyncio.Event()
    cleaned = []
    class Waiting:
        async def execute(self, req, *args):
            if req.input.messages[0].content == 'private-0' and stop == 'failed_fast':
                await entered.wait()
                raise RuntimeError('expert failed')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(req.run_id)
    dispatcher, _ = setup(database, Waiting())
    p = plan(max_concurrency=2, completion='fail_fast' if stop == 'failed_fast' else 'all_settled',
             timeout_seconds=0.1 if stop == 'timed_out' else 10)
    task = asyncio.create_task(dispatcher.dispatch(p))
    async with asyncio.timeout(5):
        await entered.wait()
        if stop == 'cancelled':
            task.cancel()
        result = await task
    assert result.status == stop and len(cleaned) >= 1
    assert result.items[2].status == 'not_started' and result.items[2].missing_reason == stop
    assert all(i.status not in ('running', 'confirming') for i in result.items)


@pytest.mark.parametrize('budget', ['runs', 'tokens', 'unknown_usage'])
async def test_budget_stops_admission(database, budget):
    calls = []
    class Metered:
        async def execute(self, req, *args):
            calls.append(req.run_id)
            return RunResult(run_id=req.run_id, status='succeeded', output='a',
                usage=Usage(input_tokens=10, complete=budget != 'unknown_usage'))
    dispatcher, _ = setup(database, Metered())
    p = plan(max_concurrency=1, max_runs=1 if budget == 'runs' else 3,
             max_total_tokens=None if budget == 'runs' else 10)
    result = await dispatcher.dispatch(p)
    assert result.status == 'budget_stopped' and len(calls) == 1
    assert [i.status for i in result.items] == ['completed', 'not_started', 'not_started']


async def test_zero_budget_makes_no_calls(database):
    dispatcher, _ = setup(database, ScriptedEngine(lambda _: pytest.fail('must not execute')))
    result = await dispatcher.dispatch(plan(max_runs=0))
    assert result.status == 'budget_stopped' and all(i.status == 'not_started' for i in result.items)


async def test_depth_rejects_recursive_delegate(database):
    engine = ScriptedEngine(lambda _: ([Call('delegate', {'agent_id': 'expert', 'input': 'nested'}, 'd')], 'wrong'))
    dispatcher, _ = setup(database, engine, delegates=True)
    result = await dispatcher.dispatch(plan(1))
    assert result.items[0].result.error.code == 'depth_limit'
    with pytest.raises(ValidationError):
        DispatchPlan(items=plan(1).items, depth=1)


async def test_single_item_equivalent_to_direct_execute_and_c0_c1_still_work(database):
    dispatcher, service = setup(database, ScriptedEngine(lambda req: ([], req.input.messages[0].content)))
    p = plan(1)
    direct = await service.execute(RunRequest(agent_id='expert', input=p.items[0].input))
    result = (await dispatcher.dispatch(p)).items[0].result
    assert direct.model_dump(exclude={'run_id'}) == result.model_dump(exclude={'run_id'})
    c1 = build_execution(database)
    c = str(uuid4())
    assert (await c1.send(c, 'assistant', 'calculate')).status == 'completed'
    assert (await c1.send(c, 'assistant', 'followup')).status == 'completed'
    assert c1.ledger.history(c).revision == 2


@pytest.mark.parametrize('point', ['before', 'after'])
async def test_publication_failure_is_not_success_and_retry_only_saves(database, point):
    calls = []
    wrapped = FaultDatabase(database, point)
    dispatcher, svc = setup(wrapped, ScriptedEngine(lambda req: (calls.append(req) or [], 'answer')))
    p = plan(1)
    result = await dispatcher.dispatch(p)
    if point == 'before':
        assert result.items[0].status == 'confirming' and not result.complete
        assert result.items[0].result is None and 'publication_failed' in result.items[0].missing_reason
        assert svc.retry_save(p.items[0].run_id).status == 'completed'
    else:
        assert result.complete
    assert collect(svc.ledger, p.group_id).complete and len(calls) == 1


async def test_sdk_c0_c1_c2_publication(database):
    from test_sdk_contract import QueueModel, text
    from agent_core.adapters.openai import OpenAIEngine
    class Checked(QueueModel):
        async def get_response(self, **kw):
            if hasattr(database, 'connection'):
                assert not database.connection.in_transaction()
            return await super().get_response(**kw)
    model = Checked([[text('成本分析')], [text('速度分析')], [text('限制分析')]])
    router, dispatcher = build_orchestration(database, OpenAIEngine(model))
    result = await dispatcher.dispatch(router.route('比较', mode='compare').plan)
    assert result.complete and len(model.inputs) == 3
    assert len({str(i) for i in model.inputs}) == 3
    assert all(len(i) == 1 for i in model.inputs)


def test_invalid_plans_and_foreign_child_ids(database):
    p = plan(1)
    with pytest.raises(ValidationError):
        DispatchPlan(items=(p.items[0], p.items[0]))
    for policy in ({'max_concurrency': 0}, {'max_runs': -1}, {'max_total_tokens': 0}):
        with pytest.raises(ValidationError):
            DispatchPolicy(**policy)


async def test_run_budget_finishes_admitted_children(database):
    dispatcher, _ = setup(database, ScriptedEngine(lambda req: ([], req.input.messages[0].content)))
    result = await dispatcher.dispatch(plan(4, max_runs=2, max_concurrency=3))
    assert [r.status for r in result.items] == ['completed', 'completed', 'not_started', 'not_started']


async def test_plain_core_success_cannot_be_published_by_dispatcher(database):
    catalog = AgentCatalog([AgentSpec('expert', '')])
    core = CoreRunner(ScriptedEngine(lambda _: ([], 'not saved')), catalog)
    dispatcher = Dispatcher(core, catalog, Ledger(database))
    result = await dispatcher.dispatch(plan(1))
    assert not result.complete and result.items[0].result is None
    assert 'publication_failed' in result.items[0].missing_reason


async def test_independent_execute_identity_and_no_history_changes(database):
    dispatcher, service = setup(database, ScriptedEngine(lambda _: ([], 'saved')))
    request = RunRequest(agent_id='expert', input=plan(1).items[0].input)
    first = await service.execute(request)
    assert await service.execute(request) == first
    with pytest.raises(Conflict):
        await service.execute(request.model_copy(update={'input': PreparedInput(messages=())}))
    assert service.ledger.history('unrelated').revision == 0
