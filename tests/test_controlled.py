"""CE1 unit/SDK contracts. These unit tests do not claim live model or PostgreSQL acceptance."""
import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from agents import ModelResponse
from agents.usage import Usage as SDKUsage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from agent_core.contracts import AgentSpec, RunRequest, PreparedInput, Message, RunLimits, ToolResult
from agent_core.tasks.models import ExpertAnswer
from agent_core.adapters.openai import OpenAIEngine
from agent_core.controlled.protocol import Frame, MAX_FRAME, encode, decode
from agent_core.controlled.workspace import Workspace
from agent_core.controlled.model import BrokerModel, RESPONSE
from agent_core.controlled.broker import Broker


def test_protocol_rejects_extra_identity_unknown_op_duplicate_fields_and_size():
    base = dict(sequence=1, operation_id='x', operation='private', payload={})
    for bad in ({**base, 'run_id': 'forged'}, {**base, 'operation': 'http'}, {**base, 'sequence': True}):
        with pytest.raises(ValidationError): Frame.model_validate(bad)
    for raw in (b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'{}', b'x' * (MAX_FRAME+1)):
        with pytest.raises(ValueError): decode(raw)
    with pytest.raises(ValueError): encode({'text': 'x' * MAX_FRAME})


def test_private_atomic_storage_reopen_hash_and_symlink(tmp_path):
    work = Workspace(tmp_path / 'private')
    saved = work.save('draft', '中段判断：尚未证实')
    work.close()
    reopened = Workspace(tmp_path / 'private')
    assert reopened.read('draft')['sha256'] == saved['sha256']
    assert reopened.read('draft')['text'] == '中段判断：尚未证实'
    with pytest.raises(ValueError): reopened.save('../outside', 'denied')
    path = tmp_path / 'private' / saved['sha256']
    path.unlink()
    outside = tmp_path / 'other'
    outside.write_text('secret')
    path.symlink_to(outside)
    with pytest.raises(OSError): reopened.read('draft')
    reopened.close()


def test_failed_index_write_preserves_previous_note(tmp_path):
    work = Workspace(tmp_path)
    original = work.save('draft', 'old')
    work.db.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON notes BEGIN SELECT RAISE(FAIL, 'disk fault'); END")
    with pytest.raises(Exception): work.save('draft', 'new')
    assert work.read('draft')['sha256'] == original['sha256']
    work.close()


class LedgerProbe:
    """Only a call-order probe; never presented as persistent budget evidence."""
    def __init__(self):
        self.reserved=[]; self.settled=[]
        self.state={'phase':'researching','repeat_streak':0}
    def progress(self,*args): return self.state
    def set_phase(self,run,phase,reason): self.state.update(phase=phase,reason=reason)
    def record_progress(self,*args,**kwargs): return self.state
    def reject_tool(self,*args): pass
    def run_budget(self,*args):
        from datetime import datetime,timedelta,timezone
        return {'models':sum(a[4].operation=='model' for a in self.reserved),
            'tools':sum(a[4].operation in ('tool','private') for a in self.reserved),
            'deadline':datetime.now(timezone.utc)+timedelta(seconds=60)}
    def save_manifest(self,*args): pass
    def input_observation(self,run_id): return None
    def task_model_durations(self,run_id): return ()
    def record_read(self,*args): pass
    def reserve(self,*args): self.reserved.append(args)
    def settle(self,*args): self.settled.append(args)
    def usage(self,*args):
        from agent_core.contracts import Usage
        return Usage(requests=1)


def make_broker():
    request = RunRequest(run_id='r', agent_id='expert', input=PreparedInput(messages=(Message(role='user',content='hi'),)))
    state = {'valid':True, 'model_calls':0}
    ledger = LedgerProbe()
    def validate():
        if not state['valid']: raise PermissionError('revoked')
    class Model:
        async def get_response(self, **kw):
            state['model_calls'] += 1
            state['model_kwargs'] = kw
            if state.get('revoke_during_call'): state['valid']=False
            return ModelResponse(output=[ResponseOutputMessage(id='m',type='message',role='assistant',status='completed',
                content=[ResponseOutputText(type='output_text',text='{"report":"done"}',annotations=[])])],
                usage=SDKUsage(requests=1,input_tokens=3,output_tokens=4),response_id=None)
    async def invoke(*_): raise AssertionError('No tool expected')
    broker = Broker(request=request,spec=AgentSpec('expert','frozen',output_type=ExpertAnswer), tools=(),
        invoke=invoke, model=Model(),ledger=ledger,workspace='w',token='t',epoch=1,validate=validate)
    return broker, state, ledger


async def test_broker_denies_forgery_unknown_tool_sequence_and_remote_media():
    for payload in ({'input':'hi','tools':[],'instructions':'frozen','model':'other'},
                    {'input':'hi','tools':['host_shell'],'instructions':'frozen'},
                    {'input':[{'role':'user','content':[{'type':'input_image','image_url':'http://localhost'}]}],'tools':[],'instructions':'frozen'}):
        broker,state,ledger=make_broker()
        with pytest.raises(ValueError): await broker.handle(Frame(sequence=1,operation_id='a',operation='model',payload=payload))
        assert not state['model_calls'] and not ledger.reserved
    broker,_,_=make_broker()
    with pytest.raises(ValueError): await broker.handle(Frame(sequence=2,operation_id='a',operation='model',payload={'input':'hi','tools':[],'instructions':'frozen'}))


async def test_revoke_during_model_retains_unconfirmed_reservation():
    broker,state,ledger=make_broker()
    state['revoke_during_call']=True
    with pytest.raises(PermissionError): await broker.handle(Frame(sequence=1,operation_id='a',operation='model',payload={'input':'hi','tools':[],'instructions':'frozen'}))
    assert len(ledger.reserved)==1 and not ledger.settled


async def test_invalid_private_page_arguments_are_audited_and_recoverable():
    from agent_core.context.artifacts import ReadContextPage
    from agent_core.contracts import ToolBinding
    broker,_,ledger=make_broker()
    seen=[]
    async def read(args):
        seen.append(args.artifact_id)
        return ToolResult(value={'text':'authorized page'})
    broker.private_tools={'read_context_page':ToolBinding('read_context_page','Read admitted context',ReadContextPage,read)}
    invalid=await broker.handle(Frame(sequence=1,operation_id='tool:bad',operation='private',
        payload={'name':'read_context_page','call_id':'bad','arguments':{'artifact_id':'trajectory-uuid'}}))
    assert invalid['value']['status']=='invalid_input' and invalid['value']['executed'] is False
    assert not seen and len(ledger.reserved)==len(ledger.settled)==1
    valid=await broker.handle(Frame(sequence=2,operation_id='tool:good',operation='private',
        payload={'name':'read_context_page','call_id':'good','arguments':{'artifact_id':'a'*64}}))
    assert valid['value']['text']=='authorized page' and seen==['a'*64]
    assert len(ledger.reserved)==len(ledger.settled)==2


async def test_real_sdk_runs_through_public_model_proxy_contract_only():
    broker,state,ledger=make_broker()
    class Channel:
        sequence=0
        async def call(self,operation,payload):
            self.sequence+=1
            return await broker.handle(Frame(sequence=self.sequence,operation_id=str(self.sequence),operation=operation,payload=payload))
    engine=OpenAIEngine(BrokerModel(Channel()))
    async def invoke(*_): raise AssertionError('No tool expected')
    result=await engine.execute(broker.request,broker.spec,(),invoke)
    assert result.status=='succeeded' and result.output['report']=='done'
    assert result.usage.requests==1 and len(ledger.settled)==1
    assert state['model_kwargs']['system_instructions'].startswith(broker.spec.instructions)


async def test_result_does_not_publish_or_trust_client_usage():
    broker,_,_=make_broker()
    reply=await broker.handle(Frame(sequence=1,operation_id='result:r',operation='result',payload={
        'run_id':'r','status':'succeeded','output':{'report':'done'},'usage':{'requests':999}}))
    assert reply['published'] is False and broker.result.usage.requests==1
    with pytest.raises(ValueError): await broker.handle(Frame(sequence=2,operation_id='result:r',operation='result',payload={
        'run_id':'r','status':'succeeded','output':{'report':'done'}}))


async def test_sdk_private_write_read_and_structure_are_preserved(tmp_path):
    from openai.types.responses import ResponseFunctionToolCall
    broker,state,ledger=make_broker()
    workspace=Workspace(tmp_path)
    bindings=workspace.bindings()
    broker.private_tools={t.name:t for t in bindings}
    queue=[
        [ResponseFunctionToolCall(type='function_call',name='write_private_note',call_id='write',arguments='{"name":"draft","text":"marker CE1"}')],
        [ResponseFunctionToolCall(type='function_call',name='read_private_note',call_id='read',arguments='{"name":"draft"}')],
        [ResponseOutputMessage(id='final',type='message',role='assistant',status='completed',content=[
            ResponseOutputText(type='output_text',text='{"report":"marker CE1"}',annotations=[])])],
    ]
    class Model:
        async def get_response(self,**kwargs):
            return ModelResponse(output=queue.pop(0),usage=SDKUsage(requests=1),response_id=None)
    broker.model=Model()
    class Channel:
        sequence=0
        async def call(self,operation,payload,operation_id=None):
            self.sequence+=1
            return await broker.handle(Frame(sequence=self.sequence,operation_id=operation_id or str(self.sequence),operation=operation,payload=payload))
    channel=Channel()
    async def invoke(name,call_id,arguments):
        return ToolResult.model_validate(await channel.call('private',{'name':name,'call_id':call_id,'arguments':arguments},'tool:'+call_id))
    try:
        result=await OpenAIEngine(BrokerModel(channel)).execute(broker.request,broker.spec,bindings,invoke)
        assert result.status=='succeeded' and result.output['report']=='marker CE1'
        assert workspace.read('draft')['text']=='marker CE1'
        assert [args[4].operation for args in ledger.reserved]==['model','private','model','private','model']
    finally:
        workspace.close()


async def test_sdk_reasoning_replay_keeps_public_input_items():
    broker,state,_=make_broker()
    items=[{'type':'reasoning','id':'reasoning1','summary':[{'type':'summary_text','text':'Checking source'}],
            'content':[{'type':'reasoning_text','text':'Previously returned reasoning'}]},
           {'role':'user','content':'Continue'}]
    await broker.handle(Frame(sequence=1,operation_id='reasoning',operation='model',payload={'input':items,'tools':[],'instructions':'frozen'}))
    assert state['model_kwargs']['input']==items


def test_text_acceptance_parser_shares_real_document_text_path():
    from agent_core.adapters.parsing import DocumentParser
    from agent_core.adapters.parsing.text import TextParser
    content='第一行\n\nThird line\n'.encode()
    parsed=TextParser().parse(content,'text/plain',lambda _:None)
    assert [(f.location,f.text) for f in parsed.fragments]==[('line:1','第一行'),('line:3','Third line')]
    # The text branch needs no document-parser state and uses the same implementation.
    assert DocumentParser.parse(None,content,'text/plain',lambda _:None)==parsed
    with pytest.raises(UnicodeDecodeError): TextParser().parse(b'\xff','text/plain',lambda _:None)
    with pytest.raises(ValueError): TextParser().parse(b'pdf','application/pdf',lambda _:None)


class EngineLedgerProbe(LedgerProbe):
    """Lifecycle probe only; persistent accounting is tested against real PostgreSQL."""
    database = SimpleNamespace(process_id='unit-process')
    def __init__(self):
        super().__init__()
        self.released = False
        self.saved = {}
        self.operations = {}
    def acquire(self, *args): self.frozen=args[1]; return 'token', 1
    def binding(self, *args): return self.frozen
    def check_writer(self, *args): pass
    def checkpoint(self, run, key): return self.saved.get(key)
    def save_checkpoint(self, w, t, e, r, key, artifact): self.saved[key]=artifact
    def operation(self, run, key): return self.operations.get(key)
    def settle(self, *args, artifact=None, code_artifact=None):
        super().settle(*args)
        if artifact: self.saved['receipt:'+args[4]]=artifact
        if code_artifact: self.saved['code']=code_artifact
    def release(self, *args): self.released = True


def controlled_engine(tmp_path, model):
    from agent_core.controlled.engine import ControlledEngine
    ledger = EngineLedgerProbe()
    engine = ControlledEngine(None, tmp_path, ledger, SimpleNamespace(get_model=lambda _: model))
    engine.binding = lambda _: (None, {'run_id': 'r', 'entrance': 'expert'}, 'a' * 64)
    request = RunRequest(run_id='r', agent_id='expert',
                         input=PreparedInput(messages=(Message(role='user', content='Use the notebook'),)))
    return engine, ledger, request


async def test_controlled_engine_runs_sdk_private_tools_and_denies_shell(tmp_path):
    from openai.types.responses import ResponseFunctionToolCall
    from agent_core.contracts import ToolBinding
    queue = [
        [ResponseFunctionToolCall(type='function_call', name='write_private_note', call_id='w',
                                 arguments='{"name":"draft","text":"persisted marker"}')],
        [ResponseFunctionToolCall(type='function_call', name='read_private_note', call_id='r', arguments='{"name":"draft"}')],
        [ResponseOutputMessage(id='final', type='message', role='assistant', status='completed', content=[
            ResponseOutputText(type='output_text', text='{"report":"persisted marker"}', annotations=[])])],
    ]
    class Model:
        async def get_response(self, **kwargs):
            assert {t.name for t in kwargs['tools']} == {'write_private_note', 'read_private_note', 'read_context_page'}
            return ModelResponse(output=queue.pop(0), usage=SDKUsage(requests=1), response_id=None)
    engine, ledger, request = controlled_engine(tmp_path, Model())
    async def forbidden(*_): raise AssertionError('Shell must not run')
    shell = ToolBinding('host_shell', 'Forbidden', SimpleNamespace(), forbidden)
    result = await engine.execute(request, AgentSpec('expert', '', output_type=ExpertAnswer), (shell,), forbidden)
    assert result.status == 'succeeded' and ledger.released and not engine.active
    workspace = Workspace(tmp_path / ('a' * 64))
    try:
        assert workspace.read('draft')['text'] == 'persisted marker'
        assert [args[4].operation for args in ledger.reserved] == ['model', 'private', 'model', 'private', 'model', 'result']
    finally: workspace.close()


async def test_controlled_cancellation_awaits_sdk_and_keeps_unknown_request(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()
    class Model:
        async def get_response(self, **kwargs):
            started.set()
            try: await asyncio.Event().wait()
            finally: stopped.set()
    engine, ledger, request = controlled_engine(tmp_path, Model())
    execution = asyncio.create_task(engine.execute(request, AgentSpec('expert', '', output_type=ExpertAnswer), (), None))
    await asyncio.wait_for(started.wait(), 2)
    execution.cancel()
    result = await asyncio.wait_for(execution, 2)
    assert result.status == 'cancelled' and result.output is None
    assert stopped.is_set() and ledger.released and not engine.active
    assert len(ledger.reserved) == 1 and not ledger.settled


def test_private_notes_survive_new_process_without_cross_workspace_access(tmp_path):
    a, b = Workspace(tmp_path/'a'), Workspace(tmp_path/'b')
    saved = a.save('draft', 'private A')
    try:
        with pytest.raises(ValueError): b.read('draft')
    finally: a.close(); b.close()
    process = subprocess.run([sys.executable, '-c',
        "import json,sys; from agent_core.controlled.workspace import Workspace; w=Workspace(sys.argv[1]); print(json.dumps(w.read('draft'))); w.close()",
        str(tmp_path/'a')], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(process.stdout)['sha256'] == saved['sha256']


async def test_provider_error_keeps_reservation_without_exposing_exception_body():
    from agent_core.contracts import CoreFailure
    broker, _, ledger = make_broker()
    class Model:
        async def get_response(self, **kwargs):
            error = RuntimeError('sensitive body must never reach the result')
            error.status_code = 400
            raise error
    broker.model = Model()
    with pytest.raises(CoreFailure) as caught:
        await broker.handle(Frame(sequence=1, operation_id='model:error', operation='model',
                                  payload={'input':'hello','tools':[],'instructions':'frozen'}))
    assert caught.value.error.code == 'model_request_failed'
    assert caught.value.error.message == 'Provider request failed (RuntimeError; HTTP 400)'
    assert len(ledger.reserved) == 1 and not ledger.settled
