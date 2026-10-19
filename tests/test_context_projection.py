"""CE3 deterministic projections and PostgreSQL/SDK acceptance, not live LLM claims."""
import json
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources, build_controlled_services
from agent_core.context.projection import project_payload, dumps, sha, page
from agent_core.context.artifacts import ContextArtifacts, ReadContextPage, after_complete_read, current_artifacts
from agent_core.controlled.model_projection import ModelProjection
from agent_core.context.scopes import ScopeDenied
from agent_core.controlled.workspace import Workspace
from agent_core.research.chat import ChatService
from agent_core.research.models import ChatMessage, NodeCommand
from test_controlled_collaboration import Provider, answer, call, seed


@pytest.fixture
def controlled(tmp_path):
    url=os.environ.get('CE3_TEST_DATABASE_URL')
    if not url: pytest.skip('CE3 dedicated PostgreSQL required')
    assert make_url(url).database.startswith('ce3_')
    db=PostgresDatabase(url); provider=Provider(); scopes=build_resources(db,tmp_path/'blobs')
    research,tasks,projects=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider)
    yield db,scopes,research,tasks,projects,research.collaboration,provider
    db.close()


def test_projection_before_character_check_preserves_middle_constraint_and_current_instruction():
    history=[{'role':'assistant','content':'older report '+('x'*4000)} for _ in range(30)]
    history.insert(15,{'id':'correction','role':'user','content':'不可把 7 解释为金额；市场规模未知。'})
    payload={'goal':'Current fixed task','history':history,'current_instruction':'只报告可证实的数量',
             'results':[{'id':'fixed-v1','output':'y'*100000}]}
    p=project_payload(payload,6000)
    assert len(p.messages[0].content)<=6000
    selected=json.loads(p.messages[0].content)
    assert selected['current_instruction']==payload['current_instruction']
    assert selected['user_constraints'][0]['content']==history[15]['content']
    assert next(iter(p.external_context.values()))==dumps(payload)
    with pytest.raises(ScopeDenied,match='Required'):
        project_payload({'goal':'x'*6001},6000)


def test_pages_cover_huge_single_item_directory_remainder_and_versions(tmp_path):
    w=Workspace(tmp_path/'private'); reads=[]
    a=ContextArtifacts(w,lambda:None,reads.append)
    try:
        value={'directory':[{'id':'large','text':'中'*25001}]+[{'id':str(i)} for i in range(100)]}
        key=a.save_text(dumps(value))
        result=a.read(ReadContextPage(artifact_id=key,limit=4096));parts=[]
        while True:
            assert result['text'] and result['end']>result['start']
            parts.append(result['text'])
            if result['complete']:break
            assert result['remaining_chars']>0
            result=a.read(ReadContextPage(**result['next_cursor']))
        assert ''.join(parts)==dumps(value) and sha(''.join(parts))==key
        assert result['end']-result['start']<4096
        v2=a.save_text(dumps({**value,'version':2}));assert key!=v2
        row=a.read(ReadContextPage(artifact_id=key,pointer='/directory/0/text',limit=100))
        assert row['text'] and not row['complete']
        other=Workspace(tmp_path/'other')
        try:
            with pytest.raises(ValueError,match='not available'):
                ContextArtifacts(other,lambda:None,lambda _:None).read(ReadContextPage(artifact_id=key))
        finally:other.close()
    finally:w.close()


def test_full_result_over_protocol_frame_persists_before_preview_and_revocation(tmp_path):
    w=Workspace(tmp_path); alive=[True]
    def valid():
        if not alive[0]:raise ScopeDenied('revoked')
    a=ContextArtifacts(w,valid,lambda _:None)
    try:
        value={'huge':'x'*4500000}
        result=a.deliver(value)
        assert not result['complete'] and (a.root/result['artifact_id']).stat().st_size>4194304
        alive[0]=False
        with pytest.raises(ScopeDenied):a.read(ReadContextPage(**result['next_cursor']))
    finally:w.close()


def test_partial_read_cannot_authorize_unseen_whole_report(tmp_path):
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None);done=[]
    token=current_artifacts.set(a)
    try:
        value={'report':'x'*17000}
        after_complete_read(value,lambda:done.append(True))
        result=a.deliver(value);assert not done
        while result['next_cursor']:
            result=a.read(ReadContextPage(**result['next_cursor']))
        assert done==[True]
    finally:current_artifacts.reset(token);w.close()


def test_sdk_projection_preserves_call_pairs_and_versions(tmp_path):
    from agents.run import ModelInputData
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        value=dumps({'version':'v1','text':'x'*15000})
        items=[{'role':'user','content':'Do not estimate missing market size.'}]
        for n,v in enumerate((value,value,value.replace('v1','v2'),value.replace('v1','v3'),value.replace('v1','v4'))):
            items += [{'type':'function_call','name':'read_source','call_id':str(n),'arguments':'{}'},
                {'type':'function_call_output','call_id':str(n),'output':v}]
        hook=ModelProjection(a)
        result=hook(SimpleNamespace(model_data=ModelInputData(input=items,instructions='Keep constraints'),
            agent=SimpleNamespace(tools=[],output_type=None)))
        assert result.input[0]==items[0]
        assert [i['call_id'] for i in result.input if i.get('type')=='function_call']==['0','1','2','3','4']
        assert [i['call_id'] for i in result.input if i.get('type')=='function_call_output']==['0','1','2','3','4']
        assert 'exact_duplicate' in result.input[4]['output']
        assert result.input[6]['output']==items[6]['output']
    finally:w.close()


def test_plan_remaining_budget_pages_optional_packet_and_preserves_approval(tmp_path):
    from agents.run import ModelInputData
    from agent_core.controlled.model_projection import OUTPUT_TOKENS, MARGIN_TOKENS
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        packet={'goal':'综合已批准的研究，未知数字必须标明',
            'handoff':{'phase':'approved_plan_execution','approved_revision':1},
            'results':[{'report':'已保存的报告','evidence':[]}],
            'sources':[{'metadata':'"quoted" \\ text '*4000}]}
        hook=ModelProjection(a,'fixture');hook.window_tokens=OUTPUT_TOKENS+MARGIN_TOKENS+5712
        result=hook(SimpleNamespace(model_data=ModelInputData(input=[{'role':'user','content':[{'type':'input_text','text':dumps(packet)}]}],instructions='Execute'),
            agent=SimpleNamespace(tools=[],output_type=None)))
        selected=json.loads(result.input[0]['content'][0]['text'])
        assert selected['goal']==packet['goal'] and selected['handoff']==packet['handoff']
        assert selected['results']==packet['results'] and 'sources' not in selected
        assert a.load_text(selected['external_context']['artifact_id'])==dumps(packet)
        assert hook.exclusions
    finally:w.close()


def test_remaining_budget_reuses_packet_and_narrows_fresh_page_first(tmp_path):
    from agents.run import ModelInputData
    from agent_core.controlled.model_projection import OUTPUT_TOKENS, MARGIN_TOKENS
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        packet={'goal':'固定综合要求','handoff':{'phase':'approved_plan_execution'},
            'results':[{'report':'正文'*20000}]}
        items=[{'role':'user','content':dumps(packet)}]
        agent=SimpleNamespace(tools=[],output_type=None)
        hook=ModelProjection(a,'fixture');hook.window_tokens=OUTPUT_TOKENS+MARGIN_TOKENS+5712
        first=hook(SimpleNamespace(model_data=ModelInputData(input=items,instructions='Execute'),agent=agent))
        prior={'model':'fixture','instructions':'Execute','input':first.input,'tools':[],
            'output_schema':None,'settings':hook.settings.to_json_dict()}
        hook.input_observation={'public_input':prior,'input_tokens':2000}
        body={'page_type':'context','artifact_id':sha('fixed'),'pointer':'/results/0/report',
            'start':0,'end':10000,'text':'原文'*5000,'total_chars':10000,'complete':True,
            'remaining_chars':0,'next_cursor':None}
        current=items+[{'type':'function_call','name':'read_context_page','call_id':'one','arguments':'{}'},
            {'type':'function_call_output','call_id':'one','output':dumps({'value':body})}]
        result=hook(SimpleNamespace(model_data=ModelInputData(input=current,instructions='Execute'),agent=agent))
        assert result.input[0]==first.input[0]
        latest=json.loads(result.input[-1]['output'])['value']
        assert latest['text'] and len(latest['text'])<len(body['text'])
        assert latest['next_cursor']['offset']==latest['end']
        assert any(e['reason']=='latest_page_narrowed' for e in hook.exclusions)
    finally:w.close()


async def test_long_main_chat_runs_and_manifest_matches_actual_sdk_request(controlled):
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    middle=ChatMessage(role='user',content='MID-CONSTRAINT: market size stays unknown.')
    messages=[]
    for n in range(20):
        messages.append(ChatMessage(role='assistant',content='Old report '+str(n)+' '+('x'*4000)))
        if n==10:messages.append(middle)
    messages.append(ChatMessage(role='user',content='CURRENT: report count 7 only.'))
    with db.transaction() as tx:
        tx.research.save_chat(chat.model_copy(update={'revision':chat.revision+1,'messages':tuple(messages)}),chat.revision)
    from test_compaction_controlled import summary
    async def respond(kwargs):
        return await summary(kwargs) if '当前调用仅整理历史工作记忆' in kwargs['system_instructions'] else answer({'report':'7; market size unknown','claims':[]})
    provider.plans.append([respond]*8)
    outcome=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
        node_id=node.id,kind='chat_reply',chat_revision=chat.revision+1))
    assert research.ledger.get(outcome.run_id).result.status=='succeeded'
    actual=provider.requests[-1]
    assert 'MID-CONSTRAINT' in dumps(actual['input']) and 'CURRENT' in dumps(actual['input'])
    assert len(research.scopes.repository.snapshot(outcome.run_id).input.messages[0].content)<=30000
    with db.transaction():
        rows=db.connection.execute(select(tasks.engine.ledger.manifests).where(
            tasks.engine.ledger.manifests.c.run_id==outcome.run_id)).mappings().all()
    assert rows and all(r['state']=='responded' for r in rows)
    assert sum(r['manifest']['purpose']=='task' for r in rows)==1
    row=next(r for r in rows if r['manifest']['purpose']=='task')
    saved=row['manifest']['public_input']
    assert saved['input']==actual['input'] and saved['instructions']==actual['system_instructions']
    assert row['manifest']['public_input_sha256']==sha(dumps(saved))


async def test_audit_failure_prevents_model_send(controlled,monkeypatch):
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    provider.plans.append([answer({'report':'must not be sent','claims':[]})])
    def unavailable(*args):raise OSError('audit storage unavailable')
    monkeypatch.setattr(tasks.engine.ledger,'save_manifest',unavailable)
    outcome=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
        node_id=node.id,kind='chat_reply',chat_revision=chat.revision))
    assert research.ledger.get(outcome.run_id).result.status=='failed'
    assert provider.requests==[]


async def test_http_manifest_contains_exact_body_and_hash(controlled):
    import httpx2
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    from agent_core.context.audit import audit_http_request
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    sent=[]
    async def transport(request):
        sent.append(request.content.decode())
        return httpx2.Response(200,json={'id':'resp-fixture','created_at':1,'object':'response',
            'model':'fixture','status':'completed', 'output':[i.model_dump(mode='json') for i in answer({'report':'done','claims':[]})],
            'usage':{'input_tokens':20,'output_tokens':4,'total_tokens':24}})
    client=AsyncOpenAI(api_key='synthetic',base_url='https://fixture.invalid',max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(transport),
            event_hooks={'request':[audit_http_request]}))
    tasks.engine.model_provider=OpenAIProvider(openai_client=client,use_responses=True)
    try:
        outcome=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
            node_id=node.id,kind='chat_reply',chat_revision=chat.revision))
        assert research.ledger.get(outcome.run_id).result.status=='succeeded'
        with db.transaction():
            row=db.connection.execute(select(tasks.engine.ledger.manifests).where(
                tasks.engine.ledger.manifests.c.run_id==outcome.run_id)).mappings().one()
        assert sent==[row['manifest']['wire_text']]
        assert sha(sent[0])==row['manifest']['wire_sha256']
        assert json.loads(sent[0])==row['manifest']['wire_body']
        assert row['state']=='responded'
    finally:await client.close()


async def test_bad_pointer_returns_correctable_error_without_fabricated_content(tmp_path):
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        key=a.save_text(dumps({'sources':[{'ref':{'resource_id':'fixed'}}]}))
        bad=await a.binding().handler(ReadContextPage(artifact_id=key,pointer='/sources/0/resource_refs'))
        assert bad.value['status']=='invalid_input' and not bad.value['executed']
        good=await a.binding().handler(ReadContextPage(artifact_id=key,pointer='/sources/0'))
        assert json.loads(good.value['text'])=={'ref':{'resource_id':'fixed'}}
    finally:w.close()


def test_projected_page_keeps_underlying_reference_without_preview_recursion(tmp_path):
    from agents.run import ModelInputData
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        source={'report':'x'*15000};first=a.deliver(source)
        items=[{'role':'user','content':'Current instruction'}]
        for n in range(10):
            items += [{'type':'function_call','name':'read_context','call_id':str(n),'arguments':'{}'},
                {'type':'function_call_output','call_id':str(n),'output':dumps({'value':first})}]
        result=ModelProjection(a)(SimpleNamespace(model_data=ModelInputData(input=items,instructions='Read originals'),
            agent=SimpleNamespace(tools=[],output_type=None)))
        for item in result.input:
            if item.get('type')=='function_call_output':
                value=json.loads(item['output'])
                if 'external_context' in value:
                    assert value['external_context']['artifact_id']==first['artifact_id']
                    assert 'next_cursor' in value
    finally:w.close()


def test_model_settings_and_output_reserve_are_in_projection_budget(tmp_path):
    from agents.run import ModelInputData
    from agent_core.controlled.model_projection import estimated_tokens,model_settings,WINDOW_TOKENS,OUTPUT_TOKENS,MARGIN_TOKENS
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        required_chars=WINDOW_TOKENS-OUTPUT_TOKENS-MARGIN_TOKENS-4000
        items=[{'role':'user','content':'x'*required_chars},{'role':'assistant','content':'old optional '+('x'*4800)}]
        projected=ModelProjection(a,'fixture')(SimpleNamespace(model_data=ModelInputData(input=items,instructions='Frozen'),
            agent=SimpleNamespace(tools=[],output_type=None)))
        final={'model':'fixture','settings':model_settings().to_json_dict(),'instructions':'Frozen',
            'input':projected.input,'tools':[],'output_schema':None}
        assert estimated_tokens(final)+OUTPUT_TOKENS+MARGIN_TOKENS<=WINDOW_TOKENS
        assert projected.input[0]==items[0] and projected.input[1]!=items[1]
    finally:w.close()


async def test_http_audit_commit_failure_prevents_transport(controlled,monkeypatch):
    import httpx2
    from openai import AsyncOpenAI, DefaultAsyncHttpxClient
    from agents.models.openai_provider import OpenAIProvider
    from agent_core.context.audit import audit_http_request
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled);sent=[]
    async def transport(request):sent.append(True);raise AssertionError('Must not send')
    client=AsyncOpenAI(api_key='synthetic',base_url='https://fixture.invalid',max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(transport),event_hooks={'request':[audit_http_request]}))
    original=tasks.engine.ledger.save_manifest
    def fail_sent(run_id,operation,state,manifest):
        if state=='sent':raise OSError('synthetic wire audit commit failure')
        return original(run_id,operation,state,manifest)
    monkeypatch.setattr(tasks.engine.ledger,'save_manifest',fail_sent)
    tasks.engine.model_provider=OpenAIProvider(openai_client=client,use_responses=True)
    try:
        outcome=await research.submit(NodeCommand(command_id=uuid4().hex,research_id=node.research_id,
            node_id=node.id,kind='chat_reply',chat_revision=chat.revision))
        assert research.ledger.get(outcome.run_id).result.status=='failed' and not sent
        with db.transaction():
            row=db.connection.execute(select(tasks.engine.ledger.manifests).where(
                tasks.engine.ledger.manifests.c.run_id==outcome.run_id)).mappings().one()
        assert row['state']=='dispatch_failed' and row['manifest']['wire_body'] is None
    finally:await client.close()


def test_cjk_byte_pressure_externalizes_before_model_window():
    payload={'goal':'保护当前要求','history':[{'role':'assistant','content':'中'*2000} for _ in range(4)]}
    projected=project_payload(payload,10000)
    assert projected.external_context
    assert len(projected.messages[0].content.encode())<=10000
    assert next(iter(projected.external_context.values()))==dumps(payload)

async def test_bad_offset_is_correctable_without_read_receipt_or_auth_fallback(tmp_path):
    w=Workspace(tmp_path);reads=[];a=ContextArtifacts(w,lambda:None,reads.append)
    try:
        key=a.save_text(dumps({'content':'fixed original'}))
        result=await a.binding().handler(ReadContextPage(artifact_id=key,offset=9999))
        assert result.value['status']=='invalid_input' and not result.value['executed'] and not reads
        good=await a.binding().handler(ReadContextPage(**result.value['restart_cursor']))
        assert json.loads(good.value['text'])=={'content':'fixed original'} and len(reads)==1
        unavailable=await a.binding().handler(ReadContextPage(artifact_id='f'*64,offset=9999))
        assert unavailable.value['error']=='context_artifact_unavailable'
        assert not unavailable.value['executed'] and len(reads)==1
    finally:w.close()


def test_validated_memory_survives_pressure_instead_of_being_replaced_by_long_raw_pages(tmp_path):
    from agents.run import ModelInputData
    memory = dumps({'memory': 'Fixed correction and version. '*240})
    # Only exact content published by this run's compactor is protected; a model
    # merely writing a similar JSON object receives no special treatment.
    protected = set()
    w = Workspace(tmp_path)
    a = ContextArtifacts(w, lambda: None, lambda _: None)
    try:
        projection = ModelProjection(a, protected_assistant_contents=protected)
        projection.window_tokens = 65536
        protected.add(memory)
        items = [
            {'role': 'assistant', 'content': memory},
            {'role': 'user', 'content': 'Current task. '+('u'*16000)},
            {'type': 'function_call', 'name': 'read_context_page', 'call_id': 'page', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'page', 'output': dumps({'value': {'text': 'x'*40000}})},
        ]
        data = SimpleNamespace(model_data=ModelInputData(input=items, instructions='Keep current constraints'),
            agent=SimpleNamespace(tools=[], output_type=None))
        result = projection(data)
        assert result.input[0] == items[0]
        assert result.input[1] == items[1]
        assert result.input[2] == items[2]
        assert result.input[3]['call_id'] == 'page'
        narrowed = json.loads(result.input[3]['output'])['value']
        assert narrowed['text'] and narrowed['end'] > narrowed['start']
        assert narrowed['next_cursor']['offset'] == narrowed['end']
        assert 'external_context' not in result.input[3]['output']
        assert all(e['index'] != 0 for e in projection.exclusions)
        # The same content without a trusted producer is ordinary assistant data.
        unprotected_projection = ModelProjection(a)
        unprotected_projection.window_tokens = 65536
        unprotected = unprotected_projection(data)
        assert unprotected.input[0] != items[0]
    finally:
        w.close()


async def test_source_pages_and_projected_references_are_readable(tmp_path):
    from agents.run import ModelInputData
    from agent_core.context.tool_guard import ScopedTool, ReadSource
    from agent_core.resources.snapshots import ResourceRef
    ref = ResourceRef(resource_id=str(uuid4()), snapshot_id=str(uuid4()), parse_id=str(uuid4()))
    fragment = SimpleNamespace(location='body', model_dump=lambda: {'text':'中'*18000})
    scopes = SimpleNamespace(valid=lambda _:None, read=lambda *_:(fragment,),
        resources=SimpleNamespace(source_metadata=lambda _:{}))
    scope = SimpleNamespace(tools={'read_source'}, max_tool_chars=4700)
    reader = ScopedTool(scopes, scope).read_binding()
    first = (await reader.handler(ReadSource(ref=ref))).value
    parts=[]; result=first
    while True:
        assert result['page_type']=='source' and result['read_tool']=='read_source'
        assert 'artifact_id' not in result and 'pointer' not in result
        parts.append(result['text'])
        if result['complete']:break
        result=(await reader.handler(ReadSource(**result['next_cursor']))).value
    assert sha(''.join(parts))==first['sha256']
    assert json.loads(''.join(parts))['fragments']==[fragment.model_dump()]
    w=Workspace(tmp_path);a=ContextArtifacts(w,lambda:None,lambda _:None)
    try:
        # Duplicate source pages exercise the exact projection path from the failure.
        delivered=a.deliver(first)
        items=[]
        for n in range(2):
            items += [{'type':'function_call','name':'fetch_web','call_id':str(n),'arguments':'{}'},
                {'type':'function_call_output','call_id':str(n),'output':dumps({'value':delivered})}]
        projected=ModelProjection(a)(SimpleNamespace(model_data=ModelInputData(input=items,instructions=''),
            agent=SimpleNamespace(tools=[],output_type=None)))
        # A repeated newest page still has to be visible to the next turn.
        assert json.loads(projected.input[-1]['output'])['value'] == delivered
        source_page = json.loads(delivered['text']) if delivered.get('page_type') == 'context' else delivered
        next_page=await reader.handler(ReadSource(**source_page['next_cursor']))
        assert next_page.value['start']==first['end']
    finally:w.close()


async def test_unavailable_context_recovers_but_revocation_and_corruption_still_fail(tmp_path):
    w=Workspace(tmp_path);reads=[];alive=[True]
    def validate():
        if not alive[0]:raise ScopeDenied('revoked')
    a=ContextArtifacts(w,validate,reads.append)
    try:
        key=a.save_text(dumps({'evidence':'available'}))
        bad=await a.binding().handler(ReadContextPage(artifact_id='f'*64))
        assert bad.value['error']=='context_artifact_unavailable' and not reads
        good=await a.binding().handler(ReadContextPage(artifact_id=key))
        assert json.loads(good.value['text'])=={'evidence':'available'}
        (a.root/key).unlink()
        missing=await a.binding().handler(ReadContextPage(artifact_id=key))
        assert missing.value['error']=='context_artifact_unavailable' and len(reads)==1
        a.save_text(dumps({'evidence':'available'}))
        (a.root/key).write_text('corrupted')
        with pytest.raises(ValueError,match='hash mismatch'):
            await a.binding().handler(ReadContextPage(artifact_id=key))
        alive[0]=False
        with pytest.raises(ScopeDenied,match='revoked'):
            await a.binding().handler(ReadContextPage(artifact_id='f'*64))
    finally:w.close()


async def test_sdk_continues_after_unavailable_context_reference(controlled):
    db,scopes,research,tasks,projects,c,provider=controlled
    node,ref,chat=await seed(controlled)
    async def read_valid_source(kwargs):
        outputs=[json.loads(i['output']) for i in kwargs['input'] if i.get('type')=='function_call_output']
        assert outputs[-1]['value']['error']=='context_artifact_unavailable'
        assert outputs[-1]['value']['executed'] is False
        return call('read_source',ref=ref.model_dump())
    async def finish(kwargs):
        outputs=[json.loads(i['output']) for i in kwargs['input'] if i.get('type')=='function_call_output']
        assert 'confirmed count 7' in dumps(outputs[-1])
        return answer({'report':'Confirmed count 7; market size unknown.','missing':['market size']})
    provider.plans.append([call('read_context_page',artifact_id='f'*64),read_valid_source,finish])
    intent=c.delegate_task(command_id=uuid4().hex,node_id=node.id,expert_id='expert_synthesize',
        task_instruction='Read the source and report confirmed facts.',delivery_requirements='Preserve unknowns',
        resource_refs=(ref,))
    status=await c.execute(intent.command_id)
    assert status['intent'].status=='completed', status
    assert status['receipts'][0].delivery=='pending_review'
    assert len(provider.requests)==3
