"""Real stream consumption and API admission controls, with deterministic provider events."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai.types.responses import Response
from pydantic import BaseModel

from agent_core.controlled.streaming import stream_model, reply_preview, preview_for
from agent_core.context.audit import audit_http_response, after_receive
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from agent_core.app.replies import ChatReplies, CHAT_TIMEOUT_SECONDS
from agent_core.composition import build_resources
from agent_core.research.chat import ChatService
from legacy_composition import build_research, build_tasks


def completed(text='{"report":"你好\\n世界","missing":[]}'):
    return Response(id='resp_test', object='response', created_at=0, model='test', status='completed',
        output=[{'id':'msg_test','type':'message','role':'assistant','status':'completed','content':[
            {'type':'output_text','text':text,'annotations':[]}]}],
        parallel_tool_calls=False, tool_choice='auto', tools=[],
        usage={'input_tokens':10,'output_tokens':4,'total_tokens':14,
               'input_tokens_details':{'cached_tokens':3,'cache_write_tokens':0},'output_tokens_details':{'reasoning_tokens':1}})


async def test_provider_preview_arrives_before_completion_and_only_contains_report():
    first=asyncio.Event();finish=asyncio.Event();updates=[];metadata={}
    class Model:
        async def stream_response(self, **kwargs):
            yield SimpleNamespace(type='response.reasoning_text.delta',delta='PRIVATE_REASONING')
            yield SimpleNamespace(type='response.function_call_arguments.delta',delta='PRIVATE_TOOL')
            for delta in ['{"report":"你','好\\n','世界","missing":["PRIVATE_METADATA"]}']:
                yield SimpleNamespace(type='response.output_text.delta',delta=delta)
                first.set()
                await finish.wait()
            yield SimpleNamespace(type='response.completed',response=completed())
    def emit(**value):updates.append(value)
    task=asyncio.create_task(stream_model(Model(),{'output_schema':object()},emit,lambda:None,metadata))
    await first.wait()
    assert not task.done() and updates[-1]['text']=='你'
    finish.set();result=await task
    assert updates[-1]['text']=='你好\n世界'
    assert 'PRIVATE' not in json.dumps(updates)
    assert result.usage.input_tokens==10 and result.usage.input_tokens_details.cached_tokens==3
    assert metadata['usage']['input_tokens']==10


@pytest.mark.parametrize('mode',['truncated','failed','no_usage'])
async def test_partial_stream_cannot_become_success(mode):
    class Model:
        async def stream_response(self, **kwargs):
            yield SimpleNamespace(type='response.output_text.delta',delta='{"report":"partial')
            if mode=='failed':yield SimpleNamespace(type='response.failed',response=completed().model_copy(update={'status':'failed'}))
            if mode=='no_usage':yield SimpleNamespace(type='response.completed',response=completed().model_copy(update={'usage':None}))
    with pytest.raises(RuntimeError):
        await stream_model(Model(),{'output_schema':object()},lambda **_:None,lambda:None,{})


@pytest.mark.parametrize('known_usage', [True, False])
async def test_output_limit_preserves_confirmed_usage_and_reports_truncation(known_usage):
    from test_controlled import make_broker
    from agent_core.controlled.protocol import Frame
    from agent_core.contracts import CoreFailure
    # Use the actual provider response type, including incomplete details.
    terminal = Response.model_validate({**completed().model_dump(), 'status':'incomplete',
        'incomplete_details':{'reason':'max_output_tokens'},
        'usage':completed().usage.model_dump() if known_usage else None})
    class Model:
        async def stream_response(self, **kwargs):
            yield SimpleNamespace(type='response.output_text.delta',delta='{"report":"partial')
            yield SimpleNamespace(type='response.incomplete',response=terminal)
    broker, _, ledger = make_broker()
    broker.model = Model()
    manifests = []
    ledger.save_manifest = lambda *args: manifests.append(args)
    token = reply_preview.set(('expert', lambda **_:None))
    try:
        with pytest.raises(CoreFailure) as caught:
            await broker.handle(Frame(sequence=1,operation_id='model:1',operation='model',
                payload={'input':'hello','tools':[],'instructions':'frozen'}))
    finally:
        reply_preview.reset(token)
    assert caught.value.error.code == 'output_token_limit'
    assert 'reasoning and answer share this budget' in caught.value.error.message
    assert manifests[-1][2] == ('incomplete' if known_usage else 'unknown')
    if known_usage:
        assert ledger.settled[-1][-1] == {'requests':1,'input_tokens':10,'output_tokens':4,'complete':True}
    else:
        assert not ledger.settled


def test_task_output_budget_leaves_room_for_reasoning_and_long_deliverables():
    from agent_core.controlled.model_projection import model_settings, OUTPUT_TOKENS
    assert model_settings().max_tokens == OUTPUT_TOKENS == 16384
    assert model_settings(purpose='summary').max_tokens == 8192
    assert model_settings(purpose='memory').max_tokens == 2048


async def test_http_audit_does_not_buffer_sse():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):raise AssertionError('SSE was buffered');yield b''
    token=after_receive.set(lambda _:None)
    try:await audit_http_response(httpx.Response(200,headers={'Content-Type':'text/event-stream; charset=utf-8'},stream=Body()))
    finally:after_receive.reset(token)


async def test_provider_nullable_cache_details_use_sdk_usage_normalization():
    terminal=completed()
    terminal.usage.input_tokens_details=terminal.usage.input_tokens_details.model_copy(update={'cache_write_tokens':None})
    class Model:
        async def stream_response(self, **kwargs):
            yield SimpleNamespace(type='response.completed',response=terminal)
    result=await stream_model(Model(),{'output_schema':None},lambda **_:None,lambda:None,{})
    assert result.usage.input_tokens==10 and result.usage.input_tokens_details.cache_write_tokens==0


def test_child_expert_does_not_emit_into_parent_chat():
    sink=lambda **_:None
    token=reply_preview.set(('parent',sink))
    try:
        assert preview_for(SimpleNamespace(agent_id='parent')) is sink
        assert preview_for(SimpleNamespace(agent_id='expert')) is None
    finally:reply_preview.reset(token)


@pytest.fixture
def workspace(tmp_path):
    db=MemoryDatabase();scopes=build_resources(db,tmp_path/'blobs')
    engine=ScriptedEngine(lambda _: ([],{'report':'final answer','blocking_missing':[]}))
    research=build_research(db,scopes,engine)
    tasks=build_tasks(db,scopes,research,engine)
    chat=ChatService(research,tasks)
    yield research,tasks,chat,engine
    db.close()


async def test_retry_after_node_revision_change_returns_original_command_and_rejects_changed_input(workspace):
    research,tasks,chat,engine=workspace
    root=research.create('test');thread=await chat.start(root.id,'test')
    thread=chat.send(thread.node_id,'hello',0)
    app=create_app(research,task_service=tasks)
    body={'node_id':thread.node_id,'expected_revision':1,'command_id':'request-1'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        first=await client.post('/api/chat_reply',json=body)
        assert first.status_code in (200,202)
        for _ in range(100):
            if research.query('command','request-1').status=='completed':break
            await asyncio.sleep(.01)
        original=research.query('command','request-1')
        assert original.command.limits.timeout_seconds==CHAT_TIMEOUT_SECONDS==900
        assert research.node(thread.node_id).revision!=original.command.expected_revision
        replay=await client.post('/api/chat_reply',json=body)
        assert replay.status_code==200 and replay.json()['run_id']==original.run_id
        assert len(engine.requests)==1
        bad=await client.post('/api/chat_reply',json={**body,'expected_revision':2})
        assert bad.status_code==409
        events=await client.get('/api/chat_stream',params={'node_id':thread.node_id,'command_id':'request-1'})
        assert events.status_code==200 and '"status": "succeeded"' in events.text
        foreign=await chat.start(research.create('other').id,'other')
        denied=await client.get('/api/chat_stream',params={'node_id':foreign.node_id,'command_id':'request-1'})
        assert denied.status_code==403
        assert (await client.post('/api/chat_reply',json=body,headers={'Origin':'http://evil'})).status_code==403


async def test_sse_reconnect_and_disconnect_do_not_resubmit_or_cancel(workspace):
    research,_,chat,engine=workspace
    release=asyncio.Event()
    original=engine.execute
    async def slow(request,*args):
        sink=preview_for(request)
        sink(text='首字',phase='writing')
        await release.wait()
        return await original(request,*args)
    engine.execute=slow
    root=research.create('test');thread=await chat.start(root.id,'test');chat.send(thread.node_id,'hello',0)
    running=set();replies=ChatReplies(research,chat,running)
    record=await replies.start({'node_id':thread.node_id,'expected_revision':1,'command_id':'stream-1'})
    stream=replies.stream('stream-1',thread.node_id).body_iterator
    event=await anext(stream)
    assert '首字' in event and '"status": "running"' in event
    await stream.aclose()
    assert replies.active and running
    again=replies.stream('stream-1',thread.node_id).body_iterator
    assert '首字' in await anext(again)
    await again.aclose()
    release.set();await asyncio.gather(*running)
    assert len(engine.requests)==1 and not replies.active
    assert '"status": "succeeded"' in await anext(replies.stream('stream-1',thread.node_id).body_iterator)


async def test_stream_revocation_clears_preview(workspace):
    research,_,chat,engine=workspace
    release=asyncio.Event();original=engine.execute
    async def slow(request,*args):
        preview_for(request)(text='private draft',phase='writing')
        await release.wait()
        return await original(request,*args)
    engine.execute=slow
    thread=await chat.start(research.create('test').id,'test');chat.send(thread.node_id,'hello',0)
    running=set();replies=ChatReplies(research,chat,running)
    await replies.start({'node_id':thread.node_id,'expected_revision':1,'command_id':'revoke'})
    events=replies.stream('revoke',thread.node_id).body_iterator
    assert 'private draft' in await anext(events)
    original_read=chat.read
    def denied(_):
        from agent_core.context.scopes import ScopeDenied
        raise ScopeDenied('revoked')
    chat.read=denied;replies.active['revoke'].update(text='must not escape')
    assert await anext(events)=='event: revoked\ndata: {}\n\n'
    chat.read=original_read;release.set();await asyncio.gather(*running)


async def test_core_timeout_keeps_controlled_usage_and_is_not_user_cancellation(tmp_path):
    from test_controlled import controlled_engine
    from agent_core.contracts import AgentSpec, RunLimits
    from agent_core.core import CoreRunner, AgentCatalog
    from agent_core.tasks.models import ExpertAnswer
    stopped=asyncio.Event()
    class Model:
        async def get_response(self, **kwargs):
            try:await asyncio.Event().wait()
            finally:stopped.set()
    engine,ledger,request=controlled_engine(tmp_path,Model())
    request=request.model_copy(update={'limits':RunLimits(timeout_seconds=.1)})
    result=await CoreRunner(engine,AgentCatalog([AgentSpec('expert','',output_type=ExpertAnswer)])).execute(request)
    assert result.status=='failed' and result.error.code=='timeout'
    assert result.usage.requests==1 and not result.usage.complete
    assert stopped.is_set() and ledger.released and not engine.active


async def test_broker_stream_still_uses_audited_budget_and_confirms_only_complete_response():
    from test_controlled import make_broker
    from agent_core.controlled.protocol import Frame
    broker,_,ledger=make_broker();updates=[]
    class Model:
        async def stream_response(self, **kwargs):
            assert len(ledger.reserved)==1 and not ledger.settled
            yield SimpleNamespace(type='response.output_text.delta',delta='{"report":"first')
            assert updates[-1]['text']=='first' and not ledger.settled
            yield SimpleNamespace(type='response.completed',response=completed('{"report":"first and final"}'))
    broker.model=Model();token=reply_preview.set((broker.request.agent_id,lambda **value:updates.append(value)))
    try:
        result=await broker.handle(Frame(sequence=1,operation_id='stream',operation='model',
            payload={'input':'hi','tools':[],'instructions':'frozen'}))
    finally:reply_preview.reset(token)
    assert result['output'][0]['content'][0]['text']=='{"report":"first and final"}'
    assert len(ledger.settled)==1 and ledger.settled[0][-1]['requests']==1
