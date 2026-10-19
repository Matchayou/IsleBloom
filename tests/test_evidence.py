import json
import os
from uuid import uuid4
import httpx
import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources
from agent_core.context.scopes import ScopeDenied
from agent_core.research.models import NodeCommand
from agent_core.resources.ingestion import Discovery, FetchedSource
from agent_core.evidence.search import TinyFishSearchProvider
from agent_core.evidence.service import _field_present


@pytest.fixture(params=['memory','postgres'])
def app(request,tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('TEST_DATABASE_URL')
        if not url: pytest.skip('TEST_DATABASE_URL required')
        db = PostgresDatabase(url)
    else: db = MemoryDatabase()
    scopes = build_resources(db,tmp_path/'blobs')
    answer = {'report':'青松公司 2025 年经营现金流为 120 百万元。','blocking_missing':[], 'claims':[]}
    engine = ScriptedEngine(lambda _: ([],answer))
    service = build_research(db,scopes,engine)
    research = service.create('证据研究 '+str(uuid4()))
    yield service,research,answer
    db.close()


async def command(service,research,kind,node_id=None,**values):
    return await service.submit(NodeCommand(command_id=str(uuid4()),research_id=research.id,
        kind=kind,node_id=node_id,**values))


async def test_fixed_citation_review_and_revocation(app):
    service,research,answer = app
    scope = service.scopes.current(research.scope_id,service.owner)
    scope,ref = service.scopes.ingest(scope,name='annual.txt',media_type='text/plain',
        content='青松公司 2025 年度 人民币 百万元 合并报表\n经营活动现金流量净额 120 百万元'.encode())
    answer['claims'] = [{'statement':'青松公司 2025 年经营现金流为 1200 百万元。',
        'citations':[{'ref':ref.model_dump(),'location':'line:2','quote':'经营活动现金流量净额 120 百万元',
            'relation':'supports','reasoning':'原文列出金额',
            'fields':{'company':'青松公司','period':'2025 年度','currency':'人民币','unit':'百万元','basis':'合并报表'}}],
        'missing':['竞争风险缺少可核实资料']},
        {'statement':'上年现金流更高', 'citations':[{'ref':ref.model_dump(),'location':'line:2',
            'quote':'经营活动现金流量净额 120 百万元','relation':'opposes',
            'reasoning':'本期金额不能证明上年更高'}]}]
    node = await command(service,research,'create',goal='现金流',resource_refs=(ref,))
    await command(service,research,'run',node.node_id,resource_refs=(ref,),evidence_required=True)
    version = service.node(node.node_id).current_result_id
    bundle = service.evidence.read(version)
    assert (len(bundle.claims),len(bundle.evidence),len(bundle.relations)) == (2,2,3)
    assert {r.relation for r in bundle.relations} == {'supports','opposes','missing'}
    located = service.evidence.locate(version,bundle.evidence[0].id)
    assert located['fragment'].location == 'line:2'
    assert located['snapshot'].id == ref.snapshot_id
    assert bundle.relations[0].assessment == 'unreviewed'
    service.evidence.review(version,bundle.relations[0].id,'unsupported','原文是 120 百万元，不能支持 1200 百万元')
    assert service.evidence.read(version).relations[0].assessment == 'unsupported'
    service.scopes.revoke(scope,ref)
    with pytest.raises(ScopeDenied): service.evidence.locate(version,bundle.evidence[0].id)


async def test_quote_mismatch_returns_exact_authorized_typography_hint(app):
    service,research,_=app
    source='Interleaving improves ReAct’s grounded- ness and trustworthiness. Code: resume={\\"decisions\\": []}'
    _,ref=service.scopes.ingest(service.scopes.current(research.scope_id,service.owner),
        name='exact.txt',media_type='text/plain',content=source.encode())
    node=(await command(service,research,'create',goal='Quote correction',resource_refs=(ref,))).node_id
    record=await command(service,research,'run',node)
    binding=service.query('run',record.run_id)
    issues=service.evidence.validate_output('draft',binding,{'claims':[{'statement':'Interleaving mechanism',
        'citations':[{'ref':ref.model_dump(),'location':'line:1',
            'quote':"Interleaving improves ReAct's groundedness and trustworthiness.",
            'relation':'supports','reasoning':'Draft with altered typography'}],'missing':[]}]})
    hint=issues[0]['source_excerpt']
    assert hint['text']==source and hint['ref']==ref.model_dump()
    assert 'ReAct’s grounded- ness' in hint['text']
    assert '\\"decisions\\"' in hint['text']
async def test_unique_exact_quote_repairs_location_without_changing_source(app):
    service, research, answer = app
    scope = service.scopes.current(research.scope_id, service.owner)
    scope, ref = service.scopes.ingest(scope, name='source.txt', media_type='text/plain',
        content=b'Unrelated first line\nUnique cited fact 42')
    answer['claims'] = [{'statement': 'The cited fact is 42', 'citations': [{
        'ref': ref.model_dump(), 'location': 'line:1', 'quote': 'Unique cited fact 42',
        'relation': 'supports', 'reasoning': 'Exact source text'}]}]
    node = await command(service, research, 'create', goal='Locate fact', resource_refs=(ref,))
    await command(service, research, 'run', node.node_id, resource_refs=(ref,), evidence_required=True)
    result_id = service.node(node.node_id).current_result_id
    evidence = service.evidence.read(result_id).evidence[0]
    assert evidence.ref == ref and evidence.location == 'line:2'


async def test_invalid_quote_and_missing_claim_block_publication(app):
    service,research,answer = app
    scope = service.scopes.current(research.scope_id,service.owner)
    scope,ref = service.scopes.ingest(scope,name='source.txt',media_type='text/plain',content=b'cash flow 120')
    node = await command(service,research,'create',goal='read',resource_refs=(ref,))
    answer['claims'] = [{'statement':'cash flow 999', 'citations':[{'ref':ref.model_dump(),
        'location':'line:1','quote':'cash flow 999','relation':'supports','reasoning':'claimed'}]}]
    await command(service,research,'run',node.node_id,resource_refs=(ref,),evidence_required=True)
    assert service.node(node.node_id).current_result_id is None
    run = service.query('list_runs',node.node_id)[0]
    assert service.ledger.get(run.run_id).result.error.code == 'evidence_invalid'
    answer['claims'] = []
    await command(service,research,'continue',node.node_id,goal='retry',resource_refs=(ref,),evidence_required=True)
    assert service.node(node.node_id).current_result_id is None


async def test_wrong_unit_rejected_when_absent_from_source(app):
    service,research,answer = app
    scope = service.scopes.current(research.scope_id,service.owner)
    scope,ref = service.scopes.ingest(scope,name='report.txt',media_type='text/plain',
        content=b'cash flow 120 million')
    node = await command(service,research,'create',goal='units',resource_refs=(ref,))
    answer['claims'] = [{'statement':'cash flow 120 thousand','citations':[{'ref':ref.model_dump(),
        'location':'line:1','quote':'cash flow 120','relation':'supports','reasoning':'wrong unit',
        'fields':{'unit':'thousand'}}]}]
    await command(service,research,'run',node.node_id,evidence_required=True)
    assert service.node(node.node_id).current_result_id is None
    run = service.query('list_runs',node.node_id)[0]
    assert service.ledger.get(run.run_id).result.error.code == 'evidence_invalid'


async def test_wrong_period_and_foreign_source_cannot_publish(app):
    service,research,answer = app
    root = service.scopes.current(research.scope_id,service.owner)
    root,ref = service.scopes.ingest(root,name='2025.txt',media_type='text/plain',
        content=b'Company X 2025 USD million consolidated\ncash flow 120')
    other = service.scopes.create(service.owner)
    other,foreign = service.scopes.ingest(other,name='foreign.txt',media_type='text/plain',
        content=b'Company X 2025 USD million consolidated\ncash flow 120')
    node = await command(service,research,'create',goal='period and scope',resource_refs=(ref,))
    answer['claims'] = [{'statement':'2024 cash flow was 120 million',
        'citations':[{'ref':ref.model_dump(),'location':'line:2','quote':'cash flow 120',
            'relation':'supports','reasoning':'wrong period','fields':{'period':'2024'}}]}]
    await command(service,research,'run',node.node_id,evidence_required=True)
    assert service.node(node.node_id).current_result_id is None
    answer['claims'][0]['citations'][0].update(ref=foreign.model_dump(),fields={})
    await command(service,research,'continue',node.node_id,goal='foreign',evidence_required=True)
    assert service.node(node.node_id).current_result_id is None
    runs = service.query('list_runs',node.node_id)
    assert all(service.ledger.get(r.run_id).result.error.code == 'evidence_invalid' for r in runs)


async def test_old_version_citation_survives_new_snapshot(app):
    service,research,answer = app
    root = service.scopes.current(research.scope_id,service.owner)
    root,old = service.scopes.ingest(root,name='report.txt',media_type='text/plain',
        content=b'2025 USD million\ncash flow 120')
    answer['claims'] = [{'statement':'2025 cash flow 120 million',
        'citations':[{'ref':old.model_dump(),'location':'line:2','quote':'cash flow 120',
            'relation':'supports','reasoning':'old fixed version'}]}]
    node = await command(service,research,'create',goal='fixed citation',resource_refs=(old,))
    await command(service,research,'run',node.node_id,evidence_required=True)
    version = service.node(node.node_id).current_result_id
    item = service.evidence.read(version).evidence[0]
    root = service.scopes.current(research.scope_id,service.owner)
    root,new = service.scopes.ingest(root,name='report.txt',media_type='text/plain',
        content=b'2025 USD million\ncash flow 999',resource_id=old.resource_id)
    assert new.snapshot_id != old.snapshot_id
    assert service.evidence.locate(version,item.id)['fragment'].text == 'cash flow 120'


async def test_discovery_to_body_requires_actual_fetch(app):
    service,research,_ = app
    class Provider:
        async def search(self,query):
            return (Discovery(query=query,title='财报',url='https://example.com/report.pdf',summary='120 百万元'),)
        async def fetch(self,url):
            return FetchedSource(url=url,actual_url=url,media_type='text/plain',content=b'cash flow 120')
    service.search.provider = Provider()
    found = await service.search.discover(research.id,'中文财报')
    discovery_ref = found[0]['ref']
    assert service.scopes.resources.describe(discovery_ref)[0].kind == 'discovery'
    node = await command(service,research,'create',goal='查来源',resource_refs=(discovery_ref,))
    with pytest.raises(ScopeDenied):
        await command(service,research,'run',node.node_id)
    body = await service.search.fetch(research.id,discovery_ref)
    assert body['parse'].status == 'ready'
    assert service.scopes.resources.describe(body['ref'])[0].kind == 'web'
    assert service.scopes.resources.read(body['ref'])[0].text == 'cash flow 120'


async def test_expert_evidence_saved_with_receipt(app):
    service,research,answer = app
    scope = service.scopes.current(research.scope_id,service.owner)
    scope,ref = service.scopes.ingest(scope,name='cash.txt',media_type='text/plain',content=b'operating cash flow 120 million')
    answer['claims'] = [{'statement':'Operating cash flow was 120 million',
        'citations':[{'ref':ref.model_dump(),'location':'line:1','quote':'operating cash flow 120 million',
                      'relation':'supports','reasoning':'direct report value','fields':{'unit':'million'}}]}]
    tasks = build_tasks(service.database,service.scopes,service)
    # The scripted engine below supplies a fixed expert answer; no model quality is claimed.
    expert_answer = {'report':answer['report'],'claims':answer['claims'],'missing':[]}
    tasks.engine = ScriptedEngine(lambda _: ([],expert_answer))
    spec = tasks.create(research_id=research.id,kind='synthesize',user_request='检查现金流',
        required_refs=(ref,),evidence_required=True)
    run = await tasks.run(spec.id)
    assert run['receipt'].delivery == 'pending_review'
    bundle = service.evidence.read(run['receipt'].result_id)
    assert len(bundle.claims) == len(bundle.evidence) == 1
    assert service.evidence.locate(run['receipt'].result_id,bundle.evidence[0].id)['fragment'].text == 'operating cash flow 120 million'


async def test_tinyfish_search_and_fetch_contract():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['X-API-Key'] == 'key'
        if request.url.host == 'api.search.tinyfish.ai':
            assert request.method == 'GET'
            assert request.url.params['query'] == '公司年报'
            assert request.url.params['language'] == 'zh'
            return httpx.Response(200,json={'results':[
                {'url':'https://example.com/a','title':'中文财报','snippet':'摘要'},
                {'url':'https://example.com/a','title':'重复'},
                {'url':'https://127.0.0.1/private','title':'不安全'},
                {'url':'http://localhost/private','title':'不安全'}]})
        assert request.url.host == 'api.fetch.tinyfish.ai'
        assert request.method == 'POST'
        assert json.loads(request.content) == {'urls':['https://example.com/a'],
            'format':'markdown','ttl':0,'per_url_timeout_ms':45000}
        return httpx.Response(200,json={'results':[{'url':'https://example.com/a',
            'final_url':'https://example.com/a','format':'markdown','text':'# 财报\n经营现金流 120 百万元'}],
            'errors':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TinyFishSearchProvider(api_key='key',client=client)
        values = await provider.search('公司年报')
        assert len(values) == 1 and values[0].summary == '摘要'
        fetched = await provider.fetch(values[0].url)
        assert fetched.media_type == 'text/markdown'
        assert fetched.content.decode() == '# 财报\n经营现金流 120 百万元'
    assert len(calls) == 2


async def test_tinyfish_missing_key_never_calls_network():
    def handler(request): raise AssertionError('No request allowed without a key')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TinyFishSearchProvider(api_key='',client=client)
        for action in (provider.search('公司年报'),provider.fetch('https://example.com/a')):
            with pytest.raises(RuntimeError,match='TINYFISH_API_KEY'):
                await action


async def test_real_openreview_challenge_is_classified_and_not_ingested():
    from agent_core.evidence.search import SourceFetchError
    url = 'https://openreview.net/pdf?id=WE_vluYUL-X'
    final = 'https://openreview.net/challenge?redirect=%2Fpdf%3Fid%3DWE_vluYUL-X'
    payload = {'results':[{'url':url,'final_url':final,
        'title':'Verifying your browser | OpenReview','text':'Complete the verification above.',
        'format':'markdown'}], 'errors':[]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))) as client:
        with pytest.raises(SourceFetchError) as caught:
            await TinyFishSearchProvider(api_key='key',client=client).fetch(url)
    assert caught.value.code == 'access_verification_required'
    assert caught.value.final_url == final
    assert 'arXiv' in str(caught.value)


async def test_arxiv_abstract_discovers_separate_fetchable_fulltext():
    abs_url, pdf_url = 'https://arxiv.org/abs/2210.03629v3', 'https://arxiv.org/pdf/2210.03629v3'
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200,json={'results':[{'url':abs_url,'title':'ReAct','snippet':'Abstract only'}]})
        assert json.loads(request.content)['urls'] == [pdf_url]
        return httpx.Response(200,json={'results':[{'url':pdf_url,'final_url':pdf_url,
            'text':'Published as a conference paper at ICLR 2023\nINTRODUCTION\nREFERENCES', 'format':'markdown'}],'errors':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TinyFishSearchProvider(api_key='key',client=client)
        found = await provider.search('ReAct paper')
        assert [d.url for d in found] == [pdf_url, abs_url]
        assert 'candidate' in found[0].summary
        assert b'REFERENCES' in (await provider.fetch(found[0].url)).content


def test_arxiv_fulltext_candidate_is_publisher_bound():
    from agent_core.evidence.search import arxiv_fulltext_url
    assert arxiv_fulltext_url('https://arxiv.org/abs/hep-th/9901001') == 'https://arxiv.org/pdf/hep-th/9901001'
    assert arxiv_fulltext_url('https://arxiv.org/html/2210.03629v3') == 'https://arxiv.org/pdf/2210.03629v3'
    assert arxiv_fulltext_url('https://evil.example/abs/2210.03629') is None
    assert arxiv_fulltext_url('https://arxiv.org/abs/not-a-paper') is None


@pytest.mark.parametrize('payload,exception,match',[
    ({'results':[],'errors':[{'url':'https://example.com/a','error':'timeout'}]},RuntimeError,'timeout'),
    ({'results':[],'errors':[]},ValueError,'one source'),
    ({'results':[{'url':'https://example.com/a','final_url':'https://example.com/other',
            'format':'markdown','text':'changed'}],'errors':[]},RuntimeError,'changed URL'),
    ({'results':[{'url':'https://example.com/a','final_url':'https://example.com/a',
        'format':'markdown','text':'  '}],'errors':[]},ValueError,'usable Markdown'),
])
async def test_tinyfish_rejects_fetch_failure(payload,exception,match):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))) as client:
        with pytest.raises(exception,match=match):
            await TinyFishSearchProvider(api_key='key',client=client).fetch('https://example.com/a')


async def test_tinyfish_accepts_only_dns_verified_same_path_ip(monkeypatch):
    from agent_core.evidence import search as web_search

    async def resolved(host, port, *, type):
        assert host == 'example.com' and port == 443
        return [(None, None, None, None, ('1.1.1.1', 443))]

    class Loop:
        getaddrinfo = staticmethod(resolved)

    monkeypatch.setattr(web_search.asyncio, 'get_running_loop', lambda: Loop())
    payload = {'results': [{'url': 'https://example.com/a',
        'final_url': 'https://1.1.1.1/a', 'format': 'markdown', 'text': 'official body'}],
        'errors': []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload))) as client:
        provider = TinyFishSearchProvider(api_key='key', client=client)
        fetched = await provider.fetch('https://example.com/a')
        assert fetched.actual_url == 'https://example.com/a'
        payload['results'][0]['final_url'] = 'https://1.1.1.1/other'
        with pytest.raises(RuntimeError, match='changed URL'):
            await provider.fetch('https://example.com/a')
        payload['results'][0]['final_url'] = 'https://8.8.8.8/a'
        with pytest.raises(RuntimeError, match='changed URL'):
            await provider.fetch('https://example.com/a')


def test_period_field_accepts_equivalent_quarter_label_only():
    source = 'The fourth quarter of 2025 followed the 3rd quarter 2025. 4th quarter 2025 sales rose.'
    assert _field_present('period', '2025 Q4', source)
    assert _field_present('period', 'Q4 2025', source)
    assert not _field_present('period', '2024 Q4', source)
    assert not _field_present('period', '2025 Q2', source)
    assert not _field_present('unit', 'billion', source)


def test_us_dollar_unit_equivalence_keeps_scale_check():
    source = 'Sales were $316.1 billion in the fourth quarter.'
    assert _field_present('unit', 'billions of dollars', source)
    assert not _field_present('unit', 'millions of dollars', source)
    assert not _field_present('unit', 'thousands of dollars', source)


@pytest.mark.parametrize('status',[401,402,429,503])
async def test_tinyfish_http_error_is_explicit(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(status))) as client:
        with pytest.raises(RuntimeError,match=f'HTTP {status}'):
            await TinyFishSearchProvider(api_key='key',client=client).search('公司年报')


async def test_tinyfish_bounded_response():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'x'*101))) as client:
        with pytest.raises(ValueError,match='size limit'):
            await TinyFishSearchProvider(api_key='key',client=client,max_bytes=100).search('公司年报')


async def test_tinyfish_markdown_snapshot_is_citable(app):
    service,research,answer = app
    url = 'https://example.com/annual'
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200,json={'results':[{'url':url,'title':'年报','snippet':'发现摘要'}]})
        return httpx.Response(200,json={'results':[{'url':url,'final_url':url,
            'format':'markdown','text':'# 年报\n\n经营现金流 120 百万元。'}],'errors':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service.search.provider = TinyFishSearchProvider(api_key='key',client=client)
        discovery = (await service.search.discover(research.id,'公司年报'))[0]
        fetched = await service.search.fetch(research.id,discovery['ref'])
    ref = fetched['ref']
    assert fetched['parse'].status == 'ready'
    assert service.scopes.resources.describe(ref)[1].media_type == 'text/markdown'
    fragment = next(f for f in fetched['parse'].fragments if '120' in f.text)
    answer['claims'] = [{'statement':'经营现金流为 120 百万元', 'citations':[{
        'ref':ref.model_dump(),'location':fragment.location,'quote':'120 百万元',
        'relation':'supports','reasoning':'固定网页提取内容'}]}]
    node = await command(service,research,'create',goal='网页证据',resource_refs=(ref,))
    await command(service,research,'run',node.node_id,evidence_required=True)
    result = service.node(node.node_id).current_result_id
    item = service.evidence.read(result).evidence[0]
    assert service.evidence.locate(result,item.id)['snapshot'].id == ref.snapshot_id
