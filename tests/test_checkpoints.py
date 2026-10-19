"""CE7 storage/fencing contracts against PostgreSQL and real local Git."""
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.controlled.ledger import ControlledLedger,digest
from agent_core.controlled.checkpoints import Checkpoints
from agent_core.controlled.workspace import Workspace
from agent_core.context.artifacts import ContextArtifacts
from agent_core.contracts import RunLimits,RunRequest,PreparedInput,Message,CoreFailure
from agent_core.controlled.protocol import Frame
from agent_core.execution.ports import Conflict
from test_coding import coding

@pytest.fixture
def store(tmp_path):
    url=os.environ.get('CE7_TEST_DATABASE_URL')
    if not url:pytest.skip('Dedicated CE7 PostgreSQL required')
    db=PostgresDatabase(url);ledger=ControlledLedger(db)
    run=uuid4().hex;workspace=uuid4().hex
    limits=RunLimits(max_turns=2,max_calls=2,timeout_seconds=60)
    identity={'run_id':run,'task':'fixed-task','method':'fixed-method','model':'fixed-model',
        'authorization':'g1','sources':['v1'],'code_baseline':'baseline1','checkpoint_protocol':'ce7-v1',
        'memory_query_contract': 'fixed-memory-query-contract'}
    token,epoch=ledger.acquire(workspace,identity,limits,db.process_id)
    work=Workspace(tmp_path/'private');valid={'value':True}
    def validate():
        if not valid['value']:raise PermissionError('revoked')
    artifacts=ContextArtifacts(work,validate,lambda _:None)
    broker=SimpleNamespace(ledger=ledger,request=RunRequest(run_id=run,agent_id='expert',limits=limits,
        input=PreparedInput(messages=(Message(role='user',content='fixed'),))),
        artifacts=artifacts,validate=validate,workspace=workspace,token=token,epoch=epoch,memory_context=None)
    yield Checkpoints(broker),broker,identity,valid
    work.close();db.close()


def test_unlimited_model_reservations_persist_beyond_old_default(store):
    _, broker, identity, _ = store
    ledger = broker.ledger
    ledger.release(broker.workspace,broker.token,broker.epoch)
    run_id = uuid4().hex
    limits = RunLimits()
    token, epoch = ledger.acquire(broker.workspace,{**identity,'run_id':run_id},limits,ledger.database.process_id)
    for i in range(25):
        ledger.reserve(broker.workspace,token,epoch,run_id,
            Frame(sequence=i+1,operation_id=f'model:{i+1}',operation='model',payload={'input':'fixed'}),limits)
    reopened = ControlledLedger(ledger.database)
    assert reopened.run_budget(run_id)['models'] == 25

@pytest.mark.parametrize('field',['task','method','model','authorization','sources','code_baseline','checkpoint_protocol','memory_query_contract'])
def test_frozen_recovery_identity_refuses_changes(store,field):
    cp,b,identity,_=store
    cp.publish({'kind':'input','value':[]})
    b.ledger.release(b.workspace,b.token,b.epoch)
    with pytest.raises(Conflict,match='binding changed'):
        b.ledger.acquire(b.workspace,{**identity,field:['v2'] if field=='sources' else 'wrong'},b.request.limits,b.ledger.database.process_id)


def test_revocation_foreign_header_corruption_and_stale_epoch(store):
    cp,b,identity,valid=store
    cp.publish({'kind':'input','value':[]})
    valid['value']=False
    with pytest.raises(PermissionError):cp.restore()
    valid['value']=True
    old=cp.identity;cp.identity='foreign-task'
    with pytest.raises(ValueError,match='binding changed'):cp.restore()
    cp.identity=old
    ref=b.ledger.checkpoint(cp.run_id,'sdk')
    (b.artifacts.root/ref).write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):cp.restore()
    b.ledger.release(b.workspace,b.token,b.epoch)
    token,epoch=b.ledger.acquire(b.workspace,identity,b.request.limits,b.ledger.database.process_id)
    with pytest.raises(Conflict,match='Stale'):cp.save('stale',{})
    with pytest.raises(Conflict,match='Stale'):cp.load('sdk')
    with pytest.raises(Conflict,match='already has a writer'):
        b.ledger.acquire(b.workspace,identity,b.request.limits,b.ledger.database.process_id)


def test_actual_code_snapshot_rejects_content_and_head_changes(store,coding):
    cp,b,_,_=store
    _,admit,_=coding;code=admit()
    cp.code_snapshot=code.execution_snapshot
    cp.publish({'kind':'input','value':[]})
    assert cp.restore()
    code.write('a.py','ANSWER = 99\n')
    with pytest.raises(ValueError,match='Code changed'):cp.restore()


def test_saved_receipt_replayed_unknown_model_charged_and_unknown_tool_stops(store):
    cp,b,_,_=store
    payload={'input':'same'}
    op,_=cp.operation('model',payload,None)
    frame=Frame(sequence=1,operation_id=op,operation='model',payload=payload)
    b.ledger.reserve(b.workspace,b.token,b.epoch,cp.run_id,frame,b.request.limits)
    cp.model_cursor=0
    retry,value=cp.operation('model',payload,None)
    assert retry=='model:1:retry:1' and value is None
    b.ledger.reserve(b.workspace,b.token,b.epoch,cp.run_id,frame.model_copy(update={'operation_id':retry}),b.request.limits)
    cp.complete(frame.model_copy(update={'operation_id':retry}),{'output':'saved'},dict(requests=1,input_tokens=3,output_tokens=4,complete=True))
    cp.model_cursor=0
    assert cp.operation('model',payload,None)==(retry,{'output':'saved'})
    assert b.ledger.usage(cp.run_id).requests==2 and not b.ledger.usage(cp.run_id).complete
    tool=Frame(sequence=2,operation_id='tool:fixed',operation='private',payload={'name':'write_private_note'})
    b.ledger.reserve(b.workspace,b.token,b.epoch,cp.run_id,tool,b.request.limits)
    with pytest.raises(CoreFailure,match='Side-effect completion is unknown'):
        cp.operation('private',tool.payload,'tool:fixed')
    with pytest.raises(ValueError,match='different request'):
        cp.operation('private',{'name':'changed'},'tool:fixed')


def test_capture_unknown_model_completion_stops_without_retry(store):
    cp,b,_,_=store
    b.memory_context=(None,SimpleNamespace(mode='memory_capture'),None)
    payload={'purpose':'task','input':[]}
    frame=Frame(sequence=1,operation_id='model:1',operation='model',payload=payload)
    b.ledger.reserve(b.workspace,b.token,b.epoch,cp.run_id,frame,b.request.limits)
    with pytest.raises(CoreFailure) as error:cp.operation('model',payload,None)
    assert error.value.error.code=='memory_query_unconfirmed'
    assert b.ledger.run_budget(cp.run_id)['models']==1
