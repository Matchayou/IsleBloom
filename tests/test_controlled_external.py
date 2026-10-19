"""Real external acceptance only. Missing dedicated DB is SKIPPED, never emulated."""
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from agent_core.controlled.ledger import ControlledLedger
from agent_core.controlled.protocol import Frame
from agent_core.contracts import RunLimits
from agent_core.execution.ports import Conflict


@pytest.fixture
def database():
    url = os.environ.get('CE1_TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated PostgreSQL coordinator environment required; persistence NOT verified')
    if not (make_url(url).database or '').startswith('ce1_'):
        pytest.fail('Database name must start with ce1_ and be dedicated to this test')
    from agent_core.adapters.postgres import PostgresDatabase
    db = PostgresDatabase(url)
    yield db
    db.close()


def test_real_postgres_fencing_persistent_budget_and_unknown_attempt(database):
    ledger = ControlledLedger(database)
    workspace, run_id = uuid4().hex, uuid4().hex
    binding = {'run_id':run_id,'task':'fixed'}
    limits = RunLimits(max_turns=1,max_calls=1)
    token,epoch=ledger.acquire(workspace,binding,limits,database.process_id)
    with pytest.raises(Conflict): ledger.acquire(workspace,binding,limits,database.process_id)
    frame=Frame(sequence=1,operation_id='model1',operation='model',payload={'input':'hi','tools':[]})
    ledger.reserve(workspace,token,epoch,run_id,frame,limits)
    assert ledger.usage(run_id).requests == 1 and not ledger.usage(run_id).complete
    # Reconstruct the adapter over the actual persisted rows, without a database replacement.
    reopened=ControlledLedger(database)
    with pytest.raises(Conflict): reopened.reserve(workspace,token,epoch,run_id,frame,limits)
    with pytest.raises(Conflict): reopened.reserve(workspace,token,epoch,run_id,
        frame.model_copy(update={'operation_id':'model2'}),limits)
    # This ledger-only test has no active SDK execution; release its writer.
    reopened.release(workspace,token,epoch)
    new_token,new_epoch=reopened.acquire(workspace,binding,limits,database.process_id)
    assert new_epoch > epoch
    with pytest.raises(Conflict): ledger.settle(workspace,token,epoch,run_id,'model1')
    with pytest.raises(Conflict): reopened.reserve(workspace,new_token,new_epoch,run_id,
        frame.model_copy(update={'operation_id':'model3'}),limits)
    reopened.release(workspace,new_token,new_epoch)


def test_real_postgres_cancellation_survives_adapter_restart(database):
    ledger = ControlledLedger(database)
    task = uuid4().hex
    ledger.check_cancelled(task,1)
    ledger.cancel_task(task,1)
    ledger.cancel_task(task,1)
    reopened = ControlledLedger(database)
    with pytest.raises(Conflict): reopened.check_cancelled(task,1)
    reopened.check_cancelled(task,2)


def test_real_postgres_broker_rejects_forgery_replay_and_late_result(database):
    import asyncio
    from agent_core.controlled.broker import Broker
    from agent_core.contracts import RunRequest, PreparedInput, Message, AgentSpec
    from agent_core.tasks.models import ExpertAnswer
    ledger=ControlledLedger(database)
    workspace,run_id,task=uuid4().hex,uuid4().hex,uuid4().hex
    request=RunRequest(run_id=run_id,agent_id='expert',input=PreparedInput(messages=(Message(role='user',content='test'),)))
    token,epoch=ledger.acquire(workspace,{'run_id':run_id},request.limits,database.process_id)
    def broker():
        # No model or business tool is used in this protocol/publication gate test.
        return Broker(request=request,spec=AgentSpec('expert','',output_type=ExpertAnswer),tools=(),
            invoke=None,model=None,ledger=ledger,workspace=workspace,token=token,epoch=epoch,
            validate=lambda:ledger.check_cancelled(task,1))
    payload={'run_id':run_id,'status':'succeeded','output':{'report':'protocol fixture, not model output'}}
    try:
        with pytest.raises(ValueError):
            asyncio.run(broker().handle(Frame(sequence=1,operation_id='result:'+run_id,operation='result',payload={**payload,'run_id':'forged'})))
        frame=Frame(sequence=1,operation_id='result:'+run_id,operation='result',payload=payload)
        reply=asyncio.run(broker().handle(frame))
        assert reply=={'accepted':True,'published':False}
        with pytest.raises(Conflict): asyncio.run(broker().handle(frame))
        ledger.cancel_task(task,1)
        with pytest.raises(Conflict): asyncio.run(broker().handle(frame))
    finally:
        ledger.release(workspace,token,epoch)


def test_real_postgres_new_process_keeps_budget_cancel_and_fence(database):
    import subprocess
    import sys
    ledger=ControlledLedger(database)
    workspace,run_id,task=uuid4().hex,uuid4().hex,uuid4().hex
    limits=RunLimits(max_turns=1,max_calls=1)
    token,epoch=ledger.acquire(workspace,{'run_id':run_id},limits,database.process_id)
    ledger.reserve(workspace,token,epoch,run_id,
        Frame(sequence=1,operation_id='sent',operation='model',payload={'input':'unconfirmed fixture'}),limits)
    ledger.cancel_task(task,1)
    database.close()  # Release through the real public API; never delete/bypass the lock.
    script='''
import json,os,sys
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.controlled.ledger import ControlledLedger
from agent_core.execution.ports import Conflict
v=json.load(sys.stdin)
db=PostgresDatabase(os.environ['CE1_TEST_DATABASE_URL'])
try:
 l=ControlledLedger(db)
 assert l.usage(v['run_id']).requests==1 and not l.usage(v['run_id']).complete
 assert l.writer(v['workspace'])['writer']==v['token']
 try: l.check_cancelled(v['task'],1)
 except Conflict: pass
 else: raise AssertionError('Cancellation lost')
 # This test has no active SDK execution; its writer can be released.
 l.release(v['workspace'],v['token'],v['epoch'])
 print('new_process_verified')
finally: db.close()
'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps(dict(workspace=workspace,run_id=run_id,
        task=task,token=token,epoch=epoch)),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'new_process_verified' in result.stdout


def test_real_postgres_tool_budget_and_persistent_deadline(database):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    ledger = ControlledLedger(database)
    workspace, run_id = uuid4().hex, uuid4().hex
    limits = RunLimits(max_turns=2, max_calls=1)
    token, epoch = ledger.acquire(workspace, {'run_id':run_id}, limits, database.process_id)
    try:
        frame = Frame(sequence=1, operation_id='tool:first', operation='private',
                      payload={'name':'write_private_note','call_id':'first','arguments':{'name':'n','text':'v'}})
        ledger.reserve(workspace, token, epoch, run_id, frame, limits)
        ledger.settle(workspace, token, epoch, run_id, frame.operation_id)
        reopened = ControlledLedger(database)
        with pytest.raises(Conflict, match='budget exhausted'):
            reopened.reserve(workspace, token, epoch, run_id,
                             frame.model_copy(update={'sequence':2,'operation_id':'tool:second'}), limits)
        # A past durable deadline must reject calls even when model allowance remains.
        with database.transaction():
            database.connection.execute(update(ledger.runs).where(ledger.runs.c.id == run_id)
                .values(deadline=datetime.now(timezone.utc)-timedelta(seconds=1)))
        with pytest.raises(Conflict, match='deadline exceeded'):
            reopened.reserve(workspace, token, epoch, run_id,
                Frame(sequence=3, operation_id='model:late', operation='model',payload={'input':'late','tools':[]}),limits)
    finally: ledger.release(workspace, token, epoch)


async def test_real_postgres_private_tool_scope_budget_and_revocation(database, tmp_path):
    from agent_core.controlled.broker import Broker
    from agent_core.controlled.workspace import Workspace
    from agent_core.contracts import RunRequest, PreparedInput, Message, AgentSpec
    from agent_core.tasks.models import ExpertAnswer
    ledger = ControlledLedger(database)
    workspace_id, run_id, task_id = uuid4().hex, uuid4().hex, uuid4().hex
    request = RunRequest(run_id=run_id, agent_id='expert',
        input=PreparedInput(messages=(Message(role='user',content='private scope test'),)))
    token, epoch = ledger.acquire(workspace_id, {'run_id':run_id}, request.limits, database.process_id)
    a, b = Workspace(tmp_path/'a'), Workspace(tmp_path/'b')
    def broker():
        return Broker(request=request, spec=AgentSpec('expert','',output_type=ExpertAnswer),
            tools=(), invoke=None, model=None, ledger=ledger, workspace=workspace_id,
            token=token, epoch=epoch, validate=lambda: ledger.check_cancelled(task_id, 1),
            private_tools=a.bindings())
    def private(name, call_id, arguments):
        return Frame(sequence=1, operation_id='tool:'+call_id, operation='private',
                     payload=dict(name=name,call_id=call_id,arguments=arguments))
    try:
        with pytest.raises(ValueError): await broker().handle(private('host_shell',uuid4().hex,{}))
        for name, args in [('write_private_note',{'name':'../b/draft','text':'forged'}),
                           ('read_private_note',{'name':'draft','workspace':'b'})]:
            denied=await broker().handle(private(name,uuid4().hex,args))
            assert denied['value']['status']=='invalid_input' and denied['value']['executed'] is False
        assert ledger.run_budget(run_id)['tools']==2
        assert not a.db.execute('SELECT 1 FROM notes').fetchone() and not b.db.execute('SELECT 1 FROM notes').fetchone()
        saved = await broker().handle(private('write_private_note','save',{'name':'draft','text':'A only'}))
        assert saved['value']['saved'] and a.read('draft')['text'] == 'A only'
        with pytest.raises(ValueError): b.read('draft')
        with pytest.raises(Conflict, match='Duplicate'):
            await broker().handle(private('write_private_note','save',{'name':'draft','text':'overwrite'}))
        assert a.read('draft')['text'] == 'A only'
        ledger.cancel_task(task_id, 1)
        with pytest.raises(Conflict):
            await broker().handle(private('read_private_note','late',{'name':'draft'}))
    finally:
        a.close(); b.close()
        ledger.release(workspace_id, token, epoch)
