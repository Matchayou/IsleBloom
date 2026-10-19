"""Identical memory/PostgreSQL publication and C0 -> C1 contracts."""
import asyncio
from contextlib import contextmanager
from uuid import uuid4
from types import SimpleNamespace
import os

import pytest

from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine, Call
from legacy_composition import build_execution, demo_script
from agent_core.contracts import Message, PreparedInput, RunRequest, RunResult, CoreEvent
from agent_core.execution.ledger import Ledger
from agent_core.execution.ports import Commit, Conflict, RunRecord, StoredRunEvent


@pytest.fixture(params=['memory', 'postgres'])
def database(request):
    if request.param == 'memory':
        db = MemoryDatabase()
    else:
        url = os.environ.get('TEST_DATABASE_URL')
        if not url:
            pytest.skip('Real PostgreSQL requires TEST_DATABASE_URL; persistence NOT verified')
        from agent_core.adapters.postgres import PostgresDatabase
        db = PostgresDatabase(url)
    yield db
    db.close()


def admitted(db, *, conversation=None):
    run = str(uuid4())
    request = RunRequest(run_id=run, agent_id='assistant', input=PreparedInput(messages=(Message(role='user', content='u'),)))
    record = RunRecord(run_id=run, conversation_id=conversation or str(uuid4()), process_id=db.process_id,
                       request=request, expected_revision=0)
    Ledger(db).start(record)
    commit = Commit(commit_id=f'run:{run}', run_id=run, conversation_id=record.conversation_id,
                    expected_revision=0, messages=(request.input.messages[0], Message(role='assistant', content='a')),
                    result=RunResult(run_id=run, status='succeeded', output='a'))
    return record, commit


async def test_c0_c1_demo_followup_and_replay(database):
    seen, states, events = [], [], []
    def script(req):
        seen.append(req)
        return demo_script(req)
    svc = build_execution(database, ScriptedEngine(script), events.append, states.append)
    c, r = str(uuid4()), str(uuid4())
    first = await svc.send(c, 'assistant', 'calculate', run_id=r)
    assert first.status == 'completed' and '96' in first.result.output
    again = await svc.send(c, 'assistant', 'calculate', run_id=r)
    assert again == first and len(seen) == 2  # parent and explicit child, no retry execution
    second = await svc.send(c, 'assistant', 'followup')
    assert second.status == 'completed' and '96' in second.result.output
    assert len(seen) == 3
    assert states == ['正在生成', '正在保存', '已完成'] * 2
    snapshot = svc.ledger.history(c)
    assert snapshot.revision == 2 and len(snapshot.messages) == 4
    assert [e.name for e in events if e.phase == 'tool' and e.status == 'started'] == ['multiply', 'delegate']
    saved = svc.ledger.events(r)
    assert [e.event for e in saved] == events[:-2]
    assert [e.sequence for e in saved] == list(range(1, len(saved) + 1))
    assert any(e.event.parent_run_id == r for e in saved)
    with pytest.raises(Conflict):
        await svc.send(c, 'assistant', 'different', run_id=r)
    with pytest.raises(Conflict):
        await svc.send(c, 'assistant', 'new', expected_revision=0)


def test_commit_identity_and_revision_contract(database):
    ledger = Ledger(database)
    record, commit = admitted(database)
    assert ledger.commit(commit).status == 'succeeded'
    assert ledger.commit(commit).result == commit.result
    assert ledger.history(record.conversation_id).revision == 1
    changed = commit.model_copy(update={'result': commit.result.model_copy(update={'output': 'other'})})
    with pytest.raises(Conflict):
        ledger.commit(changed)
    with pytest.raises(Conflict):
        ledger.commit(commit.model_copy(update={'commit_id': 'different'}))
    next_record, stale = admitted(database, conversation=record.conversation_id)
    with pytest.raises(Conflict):
        ledger.commit(stale)
    assert ledger.get(next_record.run_id).result is None
    assert ledger.history(record.conversation_id).revision == 1


def test_conversation_store_contract(database):
    c = str(uuid4())
    messages = (Message(role='user', content='u'), Message(role='assistant', content='a'))
    for _ in range(2):
        with database.transaction() as tx:
            tx.conversations.append(c, 0, 'r', messages)
    with database.transaction() as tx:
        assert tx.conversations.load(c).revision == 1
        assert tx.conversations.load(str(uuid4())).messages == ()
    for r, data in [('other', messages), ('r', messages[:1])]:
        with pytest.raises(Conflict), database.transaction() as tx:
            tx.conversations.append(c, 0, r, data)


def test_event_identity_order(database):
    r, _ = admitted(database)
    event = StoredRunEvent(root_run_id=r.run_id, sequence=1,
                          event=CoreEvent(run_id=r.run_id, parent_run_id=None, phase='run', status='started'))
    for _ in range(2):
        with database.transaction() as tx:
            tx.runs.append_event(event)
    with pytest.raises(Conflict), database.transaction() as tx:
        tx.runs.append_event(event.model_copy(update={'sequence': 3}))
    with pytest.raises(Conflict), database.transaction() as tx:
        tx.runs.append_event(event.model_copy(update={'event': event.event.model_copy(update={'status': 'failed'})}))
    assert Ledger(database).events(r.run_id) == [event]


class FaultDatabase:
    """Wraps a real transaction, failing after all writes or after COMMIT."""
    def __init__(self, db, point):
        self.db, self.process_id, self.point = db, db.process_id, point
        self.fired = False

    @contextmanager
    def transaction(self):
        finishing = False
        with self.db.transaction() as tx:
            original = tx.runs.finish
            def finish(commit):
                nonlocal finishing
                original(commit)
                finishing = True
            class RunsProxy:
                def __getattr__(self, name):
                    return finish if name == 'finish' else getattr(tx.runs, name)
            yield SimpleNamespace(conversations=tx.conversations, runs=RunsProxy())
            if finishing and not self.fired and self.point == 'before':
                self.fired = True
                raise OSError('before commit')
        if finishing and not self.fired and self.point == 'after':
            self.fired = True
            raise OSError('response lost after commit')


@pytest.mark.parametrize('point', ['before', 'after'])
async def test_atomic_failure_and_retry_without_model(database, point):
    wrapped = FaultDatabase(database, point)
    calls, states = [], []
    svc = build_execution(wrapped, ScriptedEngine(lambda req: (calls.append(req) or [], 'answer')), notify=states.append)
    c, r = str(uuid4()), str(uuid4())
    first = await svc.send(c, 'assistant', 'u', run_id=r)
    if point == 'before':
        assert first.status == 'save_failed' and first.result is None and first.preview.output == 'answer'
        assert svc.ledger.history(c).revision == 0
        assert svc.ledger.get(r).result is None
        assert '已完成' not in states
    else:
        assert first.status == 'completed'
    final = await svc.send(c, 'assistant', 'u', run_id=r)
    assert final.status == 'completed' and len(calls) == 1
    assert svc.ledger.history(c).revision == 1
    assert len(svc.ledger.history(c).messages) == 2


async def test_model_failure_does_not_change_history(database):
    svc = build_execution(database, ScriptedEngine(lambda _: ([Call('bad', {}, 'bad')], 'wrong')))
    c = str(uuid4())
    result = await svc.send(c, 'assistant', 'u')
    assert result.status == 'failed' and result.result.error.code == 'unknown_tool'
    assert svc.ledger.history(c).revision == 0
    assert svc.ledger.get(result.run_id).result == result.result
    assert any(e.event.status == 'failed' for e in svc.ledger.events(result.run_id))


async def test_cancel_and_busy_conversation(database):
    entered = asyncio.Event()
    class Waiting:
        async def execute(self, *args):
            entered.set()
            await asyncio.Event().wait()
    svc = build_execution(database, Waiting())
    c = str(uuid4())
    task = asyncio.create_task(svc.send(c, 'assistant', 'u'))
    await entered.wait()
    with pytest.raises(Conflict):
        await svc.send(c, 'assistant', 'second')
    task.cancel()
    result = await task
    assert result.status == 'cancelled' and svc.ledger.history(c).revision == 0
    assert svc.ledger.get(result.run_id).status == 'cancelled'


async def test_publication_observer_cannot_invalidate_commit(database):
    def broken(_):
        raise OSError('UI disconnected')
    svc = build_execution(database, emit=broken, notify=broken)
    result = await svc.send(str(uuid4()), 'assistant', 'u')
    assert result.status == 'completed'
    assert svc.ledger.get(result.run_id).result is not None


async def test_c0_conversation_service_against_both_repositories(database):
    from agent_core.adapters.ledger_conversation import LedgerConversationStore
    from legacy_composition import build_runner
    from legacy_conversation import ConversationService
    service = ConversationService(build_runner(), LedgerConversationStore(database))
    c = str(uuid4())
    first = await service.send(c, 'assistant', 'calculate')
    second = await service.send(c, 'assistant', 'followup')
    assert first.status == second.status == 'succeeded'
    assert '96' in first.output and '96' in second.output
    assert (await service.store.load(c)).revision == 2


async def test_sdk_c0_c1_and_no_transaction_during_model(database):
    from test_sdk_contract import QueueModel, call, text
    from agent_core.adapters.openai import OpenAIEngine
    class CheckedModel(QueueModel):
        async def get_response(self, **kwargs):
            if hasattr(database, 'connection'):
                assert not database.connection.in_transaction()
            return await super().get_response(**kwargs)
    model = CheckedModel([
        [call(name='multiply')],
        [call(name='delegate', id='d', args={'agent_id': 'expression', 'input': '结果 96'})],
        [text('结果是 96。')], [text('结果是 96。')], [text('刚才是 96。')],
    ])
    svc = build_execution(database, OpenAIEngine(model))
    c = str(uuid4())
    first = await svc.send(c, 'assistant', 'private calculate')
    second = await svc.send(c, 'assistant', 'followup')
    assert first.status == second.status == 'completed'
    assert first.result.usage.requests == 4
    assert len(model.inputs) == 5
    assert model.inputs[2] == [{'role': 'user', 'content': '结果 96'}]
    assert model.inputs[4][1]['content'] == first.result.output
    assert svc.ledger.get(first.run_id).result == first.result


async def test_event_write_failure_must_settle_before_publication(database):
    class FailingEvents:
        process_id = database.process_id
        broken = True
        @contextmanager
        def transaction(self):
            with database.transaction() as tx:
                def append_event(event):
                    if self.broken:
                        raise OSError('event write unavailable')
                    tx.runs.append_event(event)
                class RunsProxy:
                    def __getattr__(self, name):
                        return append_event if name == 'append_event' else getattr(tx.runs, name)
                yield SimpleNamespace(conversations=tx.conversations, runs=RunsProxy())
    wrapped = FailingEvents()
    svc = build_execution(wrapped)
    c = str(uuid4())
    first = await svc.send(c, 'assistant', 'u')
    assert first.status == 'save_failed' and svc.ledger.history(c).revision == 0
    assert svc.ledger.events(first.run_id) == []
    wrapped.broken = False
    final = svc.retry_save(first.run_id)
    assert final.status == 'completed'
    events = svc.ledger.events(first.run_id)
    assert len(events) == 10 and [e.sequence for e in events] == list(range(1, 11))


async def test_unknown_commit_outcome_keeps_preview_and_never_reexecutes(database):
    class OfflineAfterCommit(FaultDatabase):
        offline = False
        @contextmanager
        def transaction(self):
            if self.offline:
                raise OSError('offline')
            try:
                with super().transaction() as tx:
                    yield tx
            except OSError:
                self.offline = True
                raise
    wrapped = OfflineAfterCommit(database, 'after')
    calls = []
    svc = build_execution(wrapped, ScriptedEngine(lambda r: (calls.append(r) or [], 'answer')))
    result = await svc.send(str(uuid4()), 'assistant', 'u')
    assert result.status == 'confirming' and result.result is None and result.preview.output == 'answer'
    wrapped.offline = False
    final = svc.retry_save(result.run_id)
    assert final.status == 'completed' and len(calls) == 1


def test_bad_increment_rolls_back_without_completed_run(database):
    record, commit = admitted(database)
    wrong = commit.model_copy(update={'messages': (Message(role='user', content='different'), commit.messages[1])})
    with pytest.raises(Conflict):
        Ledger(database).commit(wrong)
    assert Ledger(database).history(record.conversation_id).revision == 0
    assert Ledger(database).get(record.run_id).result is None
