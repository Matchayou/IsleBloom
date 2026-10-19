import asyncio
import json
from uuid import uuid4

import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from legacy_composition import build_research
from agent_core.composition import build_resources
from agent_core.contracts import RunResult
from agent_core.context.scopes import ScopeDenied
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.resources.ingestion import Discovery, FetchedSource
from agent_core.resources.snapshots import ResourceRef


class Provider:
    async def search(self, query):
        return (Discovery(query=query, title='Official release', url='https://example.com/release',
                          summary='Unverified snippet says 999'),)

    async def fetch(self, url):
        return FetchedSource(url=url, actual_url=url, media_type='text/plain', content=b'Latest release is Borealis 7.')


class WebEngine:
    foreign_ref = None
    snippet = False
    after_fetch = None
    mistaken_read = False
    async def execute(self, request, spec, tools, invoke):
        found = (await invoke('search_web', 'search', {'query': 'latest release'})).value
        discovery = self.foreign_ref or found['results'][0]['ref']
        body = (await invoke('fetch_web', 'fetch', {'ref': discovery})).value
        if self.mistaken_read:
            denied = (await invoke('read_source', 'mistaken-read', {'ref': discovery})).value
            assert denied['status'] == 'unavailable' and denied['fragments'] == []
            assert denied['available_sources'] == [body['ref']]
        if self.after_fetch:
            await self.after_fetch()
        ref = found['results'][0]['ref'] if self.snippet else body['ref']
        read = (await invoke('read_source', 'read', {'ref': body['ref']})).value
        text = read['fragments'][0]['text']
        output = {'report': f'{text} [Official release](https://example.com/release)', 'blocking_missing': [],
                  'claims': [{'statement': text, 'citations': [{'ref': ref, 'location': 'line:1',
                    'quote': text, 'relation': 'supports', 'reasoning': 'The fetched body names the release.'}], 'missing': []}]}
        return RunResult(run_id=request.run_id, status='succeeded', output=output)


@pytest.fixture
def app(tmp_path):
    db = MemoryDatabase()
    scopes = build_resources(db, tmp_path / 'blobs')
    engine = WebEngine()
    service = build_research(db, scopes, engine)
    service.search.provider = Provider()
    root = service.create('Web research')
    return service, root, engine, ChatService(service)


async def reply(app):
    service, root, _, chat = app
    thread = await chat.start(root.id, root.goal)
    thread = chat.send(thread.node_id, 'What is the latest release?', thread.revision)
    command = await service.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
        node_id=thread.node_id, kind='chat_reply', chat_revision=thread.revision))
    return command, chat.read(thread.node_id)


async def test_search_fetch_cite_publish_followup_and_revocation(app):
    service, root, engine, chat = app
    command, thread = await reply(app)
    record = service.ledger.get(command.run_id)
    assert record.status == 'succeeded', record
    version = service.query('result', service.node(thread.node_id).current_result_id)
    assert len(version.resource_refs) == 1
    ref = version.resource_refs[0]
    assert thread.messages[-1].resource_refs == (ref,)
    assert service.node(thread.node_id).resource_refs == (ref,)
    assert service.evidence.read(version.id).evidence[0].ref == ref
    original = service.scopes.repository.snapshot(command.run_id)
    assert original.mounts == ()  # Input remains immutable; fetched data has run provenance.
    assert service.run_resource_refs(service.query('run', command.run_id)) == (ref,)
    branch = await chat.branch(thread.node_id, thread.messages[0].id, 'Separate branch', thread.revision)
    other_scope = service.scopes.current(service.node(branch.node_id).scope_id, service.owner)
    with pytest.raises(ScopeDenied): service.scopes.read(other_scope, ref)

    class Followup:
        async def execute(self, request, spec, tools, invoke):
            assert ref.model_dump() in [v['ref'] for v in json.loads(request.input.messages[0].content)['sources']]
            body = (await invoke('read_source', 'read', {'ref': ref.model_dump()})).value
            return RunResult(run_id=request.run_id, status='succeeded', output={'report': body['fragments'][0]['text'], 'blocking_missing': [], 'claims': []})
    from agent_core.app.research_execution import build_execution
    service.execution_factory = lambda bindings, research: build_execution(service.database, service.scopes, bindings, research, Followup())
    next_command, _ = await reply(app)
    assert service.ledger.get(next_command.run_id).status == 'succeeded'
    scope = service.scopes.current(root.scope_id, service.owner)
    service.scopes.revoke(scope, ref)
    with pytest.raises(ScopeDenied): service.read_result(version.id)


async def test_foreign_discovery_and_snippet_are_not_evidence(app):
    service, root, engine, _ = app
    discovery = (await service.search.discover(root.id, 'earlier run'))[0]['ref']
    engine.foreign_ref = discovery.model_dump()
    command, _ = await reply(app)
    assert service.ledger.get(command.run_id).result.error.code == 'scope_denied'
    engine.foreign_ref = None
    engine.snippet = True
    command, thread = await reply(app)
    assert service.ledger.get(command.run_id).result.error.code == 'evidence_invalid'
    assert all(m.role == 'user' for m in thread.messages)


async def test_revoked_fetched_body_cannot_publish(app):
    service, root, engine, _ = app
    async def revoke():
        binding = service.query('run', service.scopes.running.get())
        ref = service.run_resource_refs(binding)[0]
        scope = service.scopes.current(root.scope_id, service.owner)
        service.scopes.revoke(scope, ref)
    engine.after_fetch = revoke
    command, thread = await reply(app)
    assert service.ledger.get(command.run_id).status != 'succeeded'
    assert all(m.role == 'user' for m in thread.messages)


async def test_unavailable_provider_is_explicit_without_fake_sources(app):
    service, root, _, chat = app
    class Broken(Provider):
        async def search(self, query): raise RuntimeError('TinyFish API returned HTTP 503')
    class Limited:
        async def execute(self, request, spec, tools, invoke):
            value = (await invoke('search_web', 'search', {'query': 'latest release'})).value
            assert value['status'] == 'unavailable' and '503' in value['error']
            return RunResult(run_id=request.run_id, status='succeeded', output={
                'report': 'Search unavailable.', 'blocking_missing': [value['error']], 'claims': []})
    from agent_core.app.research_execution import build_execution
    service.search.provider = Broken()
    service.execution_factory = lambda bindings, research: build_execution(service.database, service.scopes, bindings, research, Limited())
    command, thread = await reply(app)
    assert service.ledger.get(command.run_id).status == 'succeeded'
    version = service.query('result', service.node(thread.node_id).current_result_id)
    assert version.completion == 'partial' and version.resource_refs == ()


async def test_concurrent_branches_keep_acquired_sources_separate(app):
    service, root, _, chat = app
    class ConcurrentProvider(Provider):
        async def search(self, query):
            await asyncio.sleep(0)
            return await super().search(query)
        async def fetch(self, url):
            await asyncio.sleep(0)
            return await super().fetch(url)
    service.search.provider = ConcurrentProvider()
    main = await chat.start(root.id, root.goal)
    main = chat.send(main.node_id, 'Find latest release', main.revision)
    branch = await chat.branch(main.node_id, main.messages[0].id, 'Independent search', main.revision)
    commands = await asyncio.gather(*(service.submit(NodeCommand(command_id=str(uuid4()), research_id=root.id,
        node_id=thread.node_id, kind='chat_reply', chat_revision=thread.revision)) for thread in (main, branch)))
    for command in commands:
        assert service.ledger.get(command.run_id).status == 'succeeded'
    a, b = [chat.read(t.node_id).messages[-1].resource_refs[0] for t in (main, branch)]
    assert a != b
    branch_scope = service.scopes.current(service.node(branch.node_id).scope_id, service.owner)
    with pytest.raises(ScopeDenied): service.scopes.read(branch_scope, a)


async def test_mistaken_discovery_read_returns_no_content_and_can_be_corrected(app):
    service, _, engine, _ = app
    engine.mistaken_read = True
    command, thread = await reply(app)
    assert service.ledger.get(command.run_id).status == 'succeeded'
    assert '999' not in thread.messages[-1].content


async def test_fetch_parse_id_typo_is_correctable_without_fetching_or_expanding_scope(app):
    service,_,_,_=app
    class Correcting:
        async def execute(self,request,spec,tools,invoke):
            found=(await invoke('search_web','search',{'query':'official price'})).value
            ref=found['results'][0]['ref']
            typo={**ref,'parse_id':'wrong-parse-id'}
            denied=(await invoke('fetch_web','typo',{'ref':typo})).value
            assert denied['status']=='invalid_input' and denied['executed'] is False
            assert denied['available_discovery_refs']==[ref]
            body=(await invoke('fetch_web','corrected',{'ref':denied['available_discovery_refs'][0]})).value
            assert body['kind']=='web_body' and body['fragments']
            return RunResult(run_id=request.run_id,status='succeeded',output={
                'report':'Correct reference fetched successfully','blocking_missing':[],'claims':[]})
    from agent_core.app.research_execution import build_execution
    service.execution_factory=lambda bindings,research: build_execution(service.database,service.scopes,bindings,research,Correcting())
    command,_=await reply(app)
    assert service.ledger.get(command.run_id).status=='succeeded'


async def test_fetch_parse_id_typo_does_not_reveal_a_revoked_discovery(app):
    service,root,_,_=app
    class Revoked:
        async def execute(self,request,spec,tools,invoke):
            found=(await invoke('search_web','search',{'query':'official price'})).value
            ref=ResourceRef.model_validate(found['results'][0]['ref'])
            current=service.scopes.current(root.scope_id,service.owner)
            service.scopes.revoke(current,ref)
            await invoke('fetch_web','typo',{'ref':{**ref.model_dump(),'parse_id':'wrong-parse-id'}})
            raise AssertionError('Revoked reference did not stop execution')
    from agent_core.app.research_execution import build_execution
    service.execution_factory=lambda bindings,research: build_execution(service.database,service.scopes,bindings,research,Revoked())
    command,_=await reply(app)
    assert service.ledger.get(command.run_id).status=='failed'


async def test_fulltext_fetch_returns_located_query_and_keeps_entire_body(app):
    service,root,_,_ = app
    class FullText(Provider):
        async def fetch(self,url):
            return FetchedSource(url=url,actual_url=url,media_type='text/plain',
                content=('Header\n'+'Unrelated introduction.\n'*500+
                    'Interleaved reasoning and actions update the plan.\nReferences and appendix.').encode())
    class Located:
        async def execute(self,request,spec,tools,invoke):
            found=(await invoke('search_web','search',{'query':'ReAct full paper'})).value
            body=(await invoke('fetch_web','fetch',{'ref':found['results'][0]['ref'],'query':'Interleaved'})).value
            assert body['kind']=='web_body' and body['fragments']
            f=body['fragments'][0]
            assert 'Interleaved' in f['text']
            assert 'References and appendix.' in service.scopes.original(service.scopes.current(root.scope_id,service.owner),
                ResourceRef.model_validate(body['ref'])).decode()
            return RunResult(run_id=request.run_id,status='succeeded',output={'report':'Mechanism located',
                'blocking_missing':[],'limitations':['Unmeasured costs'],'claims':[{'statement':'Interleaved mechanism',
                'citations':[{'ref':body['ref'],'location':f['location'],'quote':f['text'],'relation':'supports','reasoning':'Exact located excerpt'}],'missing':[]}]})
    from agent_core.app.research_execution import build_execution
    service.search.provider=FullText()
    service.execution_factory=lambda bindings,research: build_execution(service.database,service.scopes,bindings,research,Located())
    command,_=await reply(app)
    assert service.ledger.get(command.run_id).status=='succeeded'
    version=service.query('result',f'result:{command.run_id}')
    assert version.completion=='complete' and version.limitations==('Unmeasured costs',)


async def test_browser_verification_tool_observation_has_code_and_urls(app):
    from agent_core.evidence.search import SourceFetchError
    service,_,_,_=app
    class Blocked(Provider):
        async def fetch(self,url):
            raise SourceFetchError('access_verification_required','Browser verification required',url,
                'https://example.com/challenge')
    class Diagnose:
        async def execute(self,request,spec,tools,invoke):
            found=(await invoke('search_web','search',{'query':'official full paper'})).value
            value=(await invoke('fetch_web','fetch',{'ref':found['results'][0]['ref']})).value
            assert value['code']=='access_verification_required'
            assert value['requested_url']=='https://example.com/release'
            assert value['final_url']=='https://example.com/challenge'
            assert 'arXiv PDF' in value['instruction'] and 'ref' not in value
            return RunResult(run_id=request.run_id,status='succeeded',output={'report':'Full text blocked by verification',
                'blocking_missing':['Required full text not obtained'],'claims':[]})
    from agent_core.app.research_execution import build_execution
    service.search.provider=Blocked()
    service.execution_factory=lambda bindings,research: build_execution(service.database,service.scopes,bindings,research,Diagnose())
    command,_=await reply(app)
    version=service.query('result',f'result:{command.run_id}')
    assert version.completion=='partial' and version.resource_refs==()
