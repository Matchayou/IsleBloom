import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from agent_core.adapters.parsing import DocumentParser
from legacy_composition import build_scoped_execution
from agent_core.composition import build_resources
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.context import ContextBuilder, ScopeDenied, ScopedTool
from agent_core.context.tool_guard import ReadSource, NoteInput, note_binding
from agent_core.context.network import NetworkTools, SearchInput, FetchInput
from agent_core.contracts import RunRequest, RunResult, CoreFailure
from agent_core.resources.snapshots import ResourceRef
from agent_core.resources.ingestion import Discovery, FetchedSource
from agent_core.execution.ledger import Ledger


@pytest.fixture(scope='module')
def parser():
    return DocumentParser()


@pytest.fixture(params=['memory', 'postgres'])
def app(request, tmp_path, parser):
    if request.param == 'memory':
        db = MemoryDatabase()
    else:
        if not os.environ.get('TEST_DATABASE_URL'):
            pytest.skip('Real PostgreSQL requires TEST_DATABASE_URL')
        from agent_core.adapters.postgres import PostgresDatabase
        db = PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    scopes = build_resources(db, tmp_path / 'blobs', parser)
    yield db, scopes
    db.close()


def upload(svc, scope, name='A.txt', text='A_ONLY_39 公司A收入100万元', **kw):
    return svc.ingest(scope, content=text.encode(), name=name, media_type='text/plain', **kw)


def prepared(svc, scope, goal='分析A', **kw):
    return ContextBuilder(svc).build(scope, str(uuid4()), goal, **kw)


async def test_web_urls_follow_exact_snapshot_across_scope_and_cache(app):
    from agent_core.context.tool_guard import SearchSource
    _, svc = app
    root = svc.create('owner')
    root, first = upload(svc, root, text='Release seven is available.', kind='web',
        url='https://example.com/old', actual_url='https://example.com/releases/7')
    root, second = upload(svc, root, text='Release seven is available.', kind='web',
        resource_id=first.resource_id, url='https://example.com/new', actual_url='https://example.com/releases/current')
    assert svc.resources.describe(second)[2].cached_from
    branch = svc.create('owner')
    branch = svc.grant(branch, root, first)
    branch = svc.select(branch, (first,))
    assert svc.resources.repository.ingestions(branch.id) == ()
    payload = json.loads(prepared(svc, branch, evidence_mode=True).input.messages[0].content)
    source = payload['sources'][0]
    assert source['url'] == 'https://example.com/old'
    assert source['actual_url'] == 'https://example.com/releases/7'
    assert source['fetched_at'] == svc.resources.repository.ingestion(first.snapshot_id).at.isoformat()
    assert payload['content'] == []
    assert 'https://' not in svc.read(branch, first)[0].text
    guard = ScopedTool(svc, branch)
    read = (await guard.read_binding().handler(ReadSource(ref=first))).value
    search = (await guard.search_binding().handler(SearchSource(ref=first, query='Release'))).value
    assert read['actual_url'] == search['actual_url'] == source['actual_url']
    assert svc.resources.source_metadata(second)['actual_url'] == 'https://example.com/releases/current'
    branch = svc.revoke(branch, first)
    with pytest.raises(ScopeDenied): prepared(svc, branch, refs=(first,))


def test_dedup_name_url_versions_and_parse_cache(app):
    _, svc = app
    a = svc.create('owner'); b = svc.create('owner')
    a, ar = upload(svc, a)
    b, br = upload(svc, b)
    assert ar != br
    assert svc.resources.describe(ar)[1].blob_hash == svc.resources.describe(br)[1].blob_hash
    assert svc.resources.describe(br)[2].cached_from is not None
    with pytest.raises(ScopeDenied): svc.read(a, br)
    a, newer = upload(svc, a, text='A_NEW', resource_id=ar.resource_id)
    assert svc.read(a, ar)[0].text.startswith('A_ONLY')
    assert svc.read(a, newer)[0].text == 'A_NEW'
    assert len(svc.resources.repository.ingestions(a.id)) == 2
    a, w1 = upload(svc, a, kind='web', url='https://a.test', actual_url='https://a.test', text='V1')
    a, w2 = upload(svc, a, kind='web', resource_id=w1.resource_id, url='https://a.test', actual_url='https://a.test', text='V2')
    assert w1.snapshot_id != w2.snapshot_id
    assert svc.read(a, w1)[0].text == 'V1'


def test_scope_a_b_empty_forgery_and_owner(app):
    _, svc = app
    scope = svc.create('owner')
    scope, a = upload(svc, scope)
    scope, b = upload(svc, scope, 'B.txt', 'B_SECRET_92')
    old = scope
    scope = svc.select(scope, (a,))
    snap = prepared(svc, scope)
    assert 'A_ONLY_39' in snap.input.model_dump_json()
    assert 'B_SECRET_92' not in snap.input.model_dump_json()
    assert snap.scope==scope and svc.repository.snapshot(snap.run_id).scope==scope
    for ref in [b, ResourceRef(resource_id='fake', snapshot_id=a.snapshot_id, parse_id=a.parse_id)]:
        with pytest.raises(ScopeDenied): svc.read(scope, ref)
    with pytest.raises(ScopeDenied): svc.read(old, a)
    with pytest.raises(ScopeDenied): svc.current(scope.id, 'other')
    empty = svc.select(scope, ())
    assert prepared(svc, empty).mounts == ()
    with pytest.raises(ScopeDenied): svc.read(empty, a)


def test_mixed_parent_summary_dependencies_and_explicit_branch_grant(app):
    _, svc = app
    scope = svc.create('owner')
    scope, a = upload(svc, scope)
    scope, b = upload(svc, scope, 'B.txt', 'B_SECRET')
    scope, mixed = upload(svc, scope, 'summary', 'A_ONLY + B_SECRET', kind='note', dependencies=(a, b))
    with pytest.raises(ScopeDenied): svc.select(scope, (a, mixed))
    child = svc.create('owner')
    child = svc.grant(child, scope, a)
    with pytest.raises(ScopeDenied): svc.grant(child, scope, mixed)
    child = svc.select(child, (a,))
    assert 'B_SECRET' not in prepared(svc, child).input.model_dump_json()
    assert svc.current(scope.id, 'owner') == scope  # child exclusion is local


def test_revoke_fixed_mount_original_and_disable(app):
    _, svc = app
    scope = svc.create('owner'); scope, ref = upload(svc, scope)
    mount = svc.mount(scope, ref)
    with pytest.raises(ValueError): type(mount)(**{**mount.model_dump(), 'mode': 'write'})
    new = svc.revoke(scope, ref)
    for value in (scope, new):
        with pytest.raises(ScopeDenied): svc.read(value, ref)
        with pytest.raises(ScopeDenied): svc.original(value, ref)
    svc.disable(new)
    with pytest.raises(ScopeDenied): svc.current(new.id, 'owner')
    with pytest.raises(ValueError): build_scoped_execution(app[0], None, {})


async def test_read_tool_and_write_ownership(app):
    _, svc = app
    scope = svc.create('owner', tools=('read_source', 'write_note'))
    scope, a = upload(svc, scope)
    other = svc.create('owner'); other, b = upload(svc, other, text='B_SECRET')
    guard = ScopedTool(svc, scope)
    with pytest.raises(CoreFailure): await guard.read_binding().handler(ReadSource(ref=b))
    result = await note_binding(guard).handler(NoteInput(title='note', text='new'))
    note = ResourceRef.model_validate(result.value['ref'])
    current = svc.current(scope.id, 'owner')
    assert svc.resources.describe(note)[1].dependencies == (a,)
    assert svc.resources.original(a).startswith(b'A_ONLY')
    with pytest.raises(ScopeDenied): svc.read(other, note)
    with pytest.raises(ScopeDenied): upload(svc, other, resource_id=a.resource_id)


async def test_large_source_read_guides_to_bounded_location(app):
    _, svc = app
    scope = svc.create('owner', max_tool_chars=700)
    scope, ref = upload(svc, scope, text='short target line\n' + 'long context line\n' * 80)
    tool = ScopedTool(svc, scope).read_binding()
    whole = await tool.handler(ReadSource(ref=ref))
    assert not whole.value['complete']
    assert whole.value['text'] and whole.value['next_cursor']['offset'] > 0
    one = await tool.handler(ReadSource(ref=ref, location='line:1'))
    assert len(one.value['fragments']) == 1
    assert one.value['fragments'][0]['text'] == 'short target line'


async def test_clean_conversation_and_c0_c1_execution(app):
    db, svc = app
    scope = svc.create('owner'); scope, a = upload(svc, scope); scope, b = upload(svc, scope, text='B_SECRET')
    previous = prepared(svc, scope, conversation_id='same')
    scope = svc.select(scope, (a,))
    fresh = prepared(svc, scope, conversation_id='same')
    assert 'B_SECRET' in previous.input.model_dump_json()
    assert 'B_SECRET' not in fresh.input.model_dump_json()
    seen = []
    def script(request):
        seen.append(request.input)
        return [Call('read_source', {'ref': a.model_dump()}, 'read-a')], 'A_ONLY_39'
    execute, _ = build_scoped_execution(db, svc, {'a': scope}, ScriptedEngine(script))
    request = RunRequest(run_id=fresh.run_id, agent_id='a', input=fresh.input)
    result = await execute.execute(request)
    assert result.status == 'succeeded'
    assert await execute.execute(request) == result
    assert len(seen) == 1
    assert Ledger(db).history('same').messages == ()
    assert Ledger(db).get(request.run_id).commit_id
    with pytest.raises(Exception):
        await execute.execute(request.model_copy(update={'input': previous.input}))


async def test_inflight_revoke_cancels_and_rejects_late_result(app):
    db, svc = app
    scope = svc.create('owner'); scope, ref = upload(svc, scope)
    snap = prepared(svc, scope)
    started = asyncio.Event(); cancelled = asyncio.Event()
    class ResistantEngine:
        async def execute(self, request, *args):
            started.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: cancelled.set()
            return RunResult(run_id=request.run_id, status='succeeded', output='LATE_SECRET')
    execute, _ = build_scoped_execution(db, svc, {'a': scope}, ResistantEngine())
    task = asyncio.create_task(execute.execute(RunRequest(run_id=snap.run_id, agent_id='a', input=snap.input)))
    await started.wait()
    svc.revoke(scope, ref)
    result = await task
    assert cancelled.is_set()
    assert result.status == 'failed' and result.output is None
    assert Ledger(db).get(snap.run_id).result.output is None


async def test_c2_parallel_scopes(app):
    from agent_core.orchestration import Dispatcher
    from agent_core.orchestration.models import DispatchPlan, DispatchItem
    db, svc = app
    a = svc.create('owner'); a, ar = upload(svc, a)
    b = svc.create('owner'); b, br = upload(svc, b, text='B_SECRET')
    sa, sb = prepared(svc, a), prepared(svc, b)
    seen = {}
    ready = asyncio.Event()
    class Engine:
        async def execute(self, request, spec, tools, invoke):
            seen[request.agent_id] = request.input.model_dump_json()
            if len(seen) == 2: ready.set()
            await ready.wait()
            return RunResult(run_id=request.run_id, status='succeeded', output=request.agent_id)
    executor, catalog = build_scoped_execution(db, svc, {'a': a, 'b': b}, Engine())
    plan = DispatchPlan(items=tuple(DispatchItem(key=k, agent_id=k, run_id=s.run_id, input=s.input) for k,s in [('a',sa),('b',sb)]))
    result = await Dispatcher(executor, catalog, Ledger(db)).dispatch(plan)
    assert result.complete
    assert 'B_SECRET' not in seen['a'] and 'A_ONLY' not in seen['b']
    assert svc.current(a.id, 'owner') == a and svc.current(b.id, 'owner') == b


def test_parser_configuration_cache_isolation(app):
    _, svc = app
    scope=svc.create('owner'); content=str(uuid4()); scope, a=upload(svc, scope, text=content)
    svc.resources.parser = DocumentParser(max_pages=99)
    scope,b=upload(svc,scope,text=content)
    assert svc.resources.describe(b)[2].cached_from is None
    assert svc.resources.describe(a)[2].parser_key != svc.resources.describe(b)[2].parser_key


def test_real_corrupt_file_retained_and_partial_pdf(app):
    _, svc = app
    scope=svc.create('owner')
    scope, bad=svc.ingest(scope, name='bad.pdf', content=b'%PDF-broken',media_type='application/pdf')
    assert svc.resources.describe(bad)[2].status == 'failed'
    assert svc.original(scope,bad) == b'%PDF-broken'
    with pytest.raises(ScopeDenied): svc.mount(scope,bad)
    content=(Path(__file__).parent/'fixtures/stage3/partial-scan.pdf').read_bytes()
    states=[];svc.resources.observe_parse=states.append
    scope,partial=svc.ingest(scope,name='scan.pdf',content=content,media_type='application/pdf')
    parsed=svc.resources.describe(partial)[2]
    assert parsed.status == 'partial', parsed
    assert parsed.ocr_pages == (1,2)
    assert any('12345' in f.text for f in svc.read(scope,partial))
    assert any(i.location == 'page:2' for i in parsed.issues)
    assert svc.original(scope,partial) == content
    assert states[0].status=='pending' and states[-1].status=='partial'
    if parsed.cached_from is None:
        assert any(p.phase=='ocr:page:1' and p.status=='processing' for p in states)
    else:
        assert states[-1].phase == 'cache'


def test_docling_real_html_table_and_chinese(parser):
    parsed=parser.parse('<html><body><p>公司甲收入增长。</p><table><tr><th>公司</th><th>收入</th></tr><tr><td>甲</td><td>100万元</td></tr></table></body></html>'.encode(),'text/html',lambda _:None)
    assert not parsed.issues
    assert any(f.kind=='table' and '100' in f.text for f in parsed.fragments)
    assert any('公司甲' in f.text for f in parsed.fragments)


async def test_discovery_filter_before_return_and_note_lineage(app):
    _, svc = app
    scope=svc.create('owner', web_urls=('https://a.test',), tools=('search_sources','fetch_source','write_note','read_source'))
    class Provider:
        async def search(self, query, urls):
            yield Discovery(query=query,title='A_TITLE',url='https://a.test',summary='A_SUMMARY')
            yield Discovery(query=query,title='B_SECRET_TITLE',url='https://b.test',summary='B_SECRET_SUMMARY')
        async def fetch(self,url,urls):
            return FetchedSource(url=url,actual_url=url,media_type='text/plain',content=b'A_BODY')
    network=NetworkTools(svc,scope,Provider())
    result=await network.search(SearchInput(query='company'))
    assert len(result.value)==1 and 'B_SECRET' not in result.model_dump_json()
    discovery=ResourceRef.model_validate(result.value[0]['ref'])
    current=svc.current(scope.id,'owner')
    with pytest.raises(ScopeDenied): svc.mount(current,discovery)
    fetched=await network.fetch(FetchInput(url='https://a.test'))
    assert 'A_BODY' in fetched.model_dump_json()
    with pytest.raises(ScopeDenied): await network.fetch(FetchInput(url='https://b.test'))
    note=await note_binding(ScopedTool(svc,scope)).handler(NoteInput(title='derived',text='A_SUMMARY'))
    note_ref=ResourceRef.model_validate(note.value['ref'])
    assert discovery in svc.resources.describe(note_ref)[1].dependencies
    current=svc.current(scope.id,'owner')
    with pytest.raises(ScopeDenied): svc.select(current,(note_ref,))
    # Explicit selection clears network permissions. Old tool closures immediately fail.
    selected=svc.select(current,())
    assert selected.web_urls == ()
    with pytest.raises(ScopeDenied): await network.search(SearchInput(query='A'))
    entries=svc.resources.repository.ingestions(scope.id)
    assert any(e.summary=='B_SECRET_SUMMARY' for e in entries)  # registered, not exposed


async def test_discovery_scope_change_while_network_pending(app):
    _, svc=app
    scope=svc.create('owner',web_urls=('https://a.test',),tools=('search_sources',))
    started=asyncio.Event(); release=asyncio.Event()
    class Provider:
        async def search(self,*args):
            started.set(); await release.wait()
            yield Discovery(query='a',title='OLD',url='https://a.test',summary='STALE')
    tool=NetworkTools(svc,scope,Provider())
    task=asyncio.create_task(tool.search(SearchInput(query='a')))
    await started.wait();svc.select(scope,());release.set()
    with pytest.raises(ScopeDenied): await task


async def test_sdk_real_tool_loop_scope_contract(app):
    from test_sdk_contract import QueueModel, call, text
    from agent_core.adapters.openai import OpenAIEngine
    db,svc=app
    scope=svc.create('owner');scope,a=upload(svc,scope)
    other=svc.create('owner');other,b=upload(svc,other,text='B_SECRET')
    snap=prepared(svc,scope)
    model=QueueModel([[call('read_source',args={'ref':a.model_dump()})],[text('A_ONLY_39')]])
    execution,_=build_scoped_execution(db,svc,{'a':scope},OpenAIEngine(model))
    result=await execution.execute(RunRequest(run_id=snap.run_id,agent_id='a',input=snap.input))
    assert result.status=='succeeded' and 'B_SECRET' not in json.dumps(model.inputs)
    bad=prepared(svc,scope)
    model=QueueModel([[call('read_source',args={'ref':b.model_dump()})],[text('should not run')]])
    execution,_=build_scoped_execution(db,svc,{'a':scope},OpenAIEngine(model))
    result=await execution.execute(RunRequest(run_id=bad.run_id,agent_id='a',input=bad.input))
    assert result.error.code=='scope_denied' and len(model.inputs)==1


def test_chinese_scan_ocr_status_and_locations():
    tessdata=Path(__file__).resolve().parents[1]/'.stage3/tessdata'
    if not (tessdata/'chi_sim.traineddata').exists():
        pytest.skip('Chinese OCR acceptance requires RESEARCH_TESSDATA files; not verified')
    parser=DocumentParser(tessdata=tessdata,languages='eng+chi_sim')
    content=(Path(__file__).parent/'fixtures/stage3/chinese-scan.png').read_bytes()
    states=[]
    parsed=parser.parse(content,'image/png',states.append)
    assert states==['ocr:page:1']
    assert parsed.ocr_pages==(1,)
    assert '12345' in parsed.fragments[0].text and '收入' in parsed.fragments[0].text


async def test_save_retry_revalidates_revocation_without_model_rerun(app):
    from test_execution import FaultDatabase
    from agent_core.execution.service import PublicationError
    db,svc=app
    scope=svc.create('owner');scope,ref=upload(svc,scope)
    snap=prepared(svc,scope); calls=[]
    wrapped=FaultDatabase(db,'before')
    execution,_=build_scoped_execution(wrapped,svc,{'a':scope},ScriptedEngine(lambda r:(calls.append(r) or [],'PRIVATE')))
    with pytest.raises(PublicationError):
        await execution.execute(RunRequest(run_id=snap.run_id,agent_id='a',input=snap.input))
    assert Ledger(db).get(snap.run_id).result is None
    svc.revoke(scope,ref)
    result=execution.retry_save(snap.run_id)
    assert result.result.status=='failed' and result.result.output is None
    assert len(calls)==1


def test_budget_and_parse_terminal_immutability(app):
    _,svc=app
    scope=svc.create('owner',max_input_chars=10);scope,ref=upload(svc,scope)
    with pytest.raises(ScopeDenied): prepared(svc,scope)
    parsed=svc.resources.describe(ref)[2]
    from agent_core.execution.ports import Conflict
    with pytest.raises(Conflict): svc.resources.repository.save_parse(parsed)


async def test_http_real_socket_url_update_and_redirect_guard(app):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from agent_core.adapters.web_sources import HttpSources
    class Handler(BaseHTTPRequestHandler):
        value=b'<html><title>Company A</title><p>A_WEB_V1</p></html>'
        def do_GET(self):
            if self.path=='/redirect':
                self.send_response(302);self.send_header('Location','/b');self.end_headers();return
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(self.value)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        _,svc=app
        url=f'http://127.0.0.1:{server.server_port}/a'
        scope=svc.create('owner',web_urls=(url,),tools=('search_sources','fetch_source','read_source'))
        network=NetworkTools(svc,scope,HttpSources())
        found=await network.search(SearchInput(query='A_WEB'))
        assert found.value[0]['kind']=='discovery_only'
        one=await network.fetch(FetchInput(url=url))
        Handler.value=b'<html><p>A_WEB_V2</p></html>'
        two=await network.fetch(FetchInput(url=url))
        r1,r2=(ResourceRef.model_validate(x.value['ref']) for x in (one,two))
        assert r1.snapshot_id!=r2.snapshot_id
        assert 'V1' in str(svc.read(svc.current(scope.id,'owner'),r1))
        redirect=url.replace('/a','/redirect')
        with pytest.raises(Exception): await HttpSources().fetch(redirect,(redirect,))
    finally:
        server.shutdown();server.server_close();thread.join()


def test_forged_scope_cannot_expand_tool_authority(app):
    _,svc=app
    scope=svc.create('owner',tools=())
    forged=scope.model_copy(update={'tools':('fetch_source',),'web_urls':('https://evil.test',)})
    with pytest.raises(ScopeDenied): ScopedTool(svc,forged).require('fetch_source')
    with pytest.raises(ScopeDenied): svc.select(forged,())


async def test_saved_result_replay_after_revoke_is_denied(app):
    db,svc=app
    scope=svc.create('owner');scope,ref=upload(svc,scope)
    snap=prepared(svc,scope)
    execution,_=build_scoped_execution(db,svc,{'a':scope})
    request=RunRequest(run_id=snap.run_id,agent_id='a',input=snap.input)
    assert (await execution.execute(request)).status=='succeeded'
    svc.revoke(scope,ref)
    with pytest.raises(ScopeDenied): await execution.execute(request)
    with pytest.raises(ScopeDenied): execution.retry_save(request.run_id)


async def test_sdk_tool_child_task_keeps_ingestion_run_attribution(app):
    from test_sdk_contract import QueueModel, call, text
    from agent_core.adapters.openai import OpenAIEngine
    db,svc=app
    scope=svc.create('owner',tools=('write_note',));scope,ref=upload(svc,scope)
    snap=prepared(svc,scope)
    model=QueueModel([[call('write_note',args={'title':'own-note','text':'A_ONLY'})],[text('done')]])
    execution,_=build_scoped_execution(db,svc,{'a':scope},OpenAIEngine(model))
    result=await execution.execute(RunRequest(run_id=snap.run_id,agent_id='a',input=snap.input))
    assert result.status=='succeeded'
    notes=[e for e in svc.resources.repository.ingestions(scope.id) if e.kind=='note']
    assert len(notes)==1 and notes[0].run_id==snap.run_id
