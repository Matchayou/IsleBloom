"""Summary integrity tests with fake summary responses; no live model claims."""
import json
from types import SimpleNamespace
from copy import deepcopy
import pytest
from agent_core.context.compaction import Compactor,complete_groups
from agent_core.context.artifacts import ContextArtifacts
from agent_core.context.projection import dumps,sha,ContextBudgetExceeded
from agent_core.controlled.workspace import Workspace
from agent_core.contracts import RunLimits,RunRequest,PreparedInput,Message

class SummaryLedger:
    def __init__(self):self.heads={};self.attempts={}
    def compaction_head(self,w,s):return deepcopy(self.heads.get((w,s)))
    def compaction_attempt(self,r,a):return self.attempts.get((r,a))
    def save_compaction(self,w,t,e,r,s,a,h):
        self.attempts[r,a['id']]=deepcopy(a)
        if h:self.heads[w,s]=deepcopy(h)
    def run_budget(self,r):return {'models':0}
    def progress(self,r):return {'phase':'researching'}

@pytest.fixture
def compactor(tmp_path):
    work=Workspace(tmp_path/'private');ledger=SummaryLedger()
    artifacts=ContextArtifacts(work,lambda:None,lambda _:None)
    request=RunRequest(agent_id='expert',input=PreparedInput(messages=(Message(role='user',content='current'),)),limits=RunLimits(max_turns=20))
    broker=SimpleNamespace(workspace='authorized-workspace',token='writer',epoch=1,request=request,ledger=ledger,validate=lambda:None,
        policy=SimpleNamespace(reserve_turns=2,reserve_seconds=2,remaining_seconds=lambda:100))
    calls=[]
    async def summarize(value):
        calls.append(value)
        old=value['previous_summary']['items'] if value['previous_summary'] else []
        items=old+[{'kind':'fact','text':s['text'][:60],'quote':s['text'][:40],'source_id':s['id']} for s in value['sources']]
        return {'text':dumps({'covered_ids':value['required_source_ids'],'items':items}),'incomplete':False,'usage':{'requests':1}}
    compact=Compactor(artifacts=artifacts,broker=broker,summarize=summarize)
    yield compact,ledger,calls,work
    work.close()

async def test_incremental_success_has_fixed_sources_and_prior_memory(compactor):
    c,ledger,calls,_=compactor
    groups=['Fixed v1: metric is not revenue. '+(' filler'*500), 'Correction: 7 is a count; size unknown. '+(' filler'*500)]
    sources=c.sources('history',groups)
    first=await c.compact('history',sources,force=True)
    assert first['cursor']==2 and len(first['sources'])==2
    groups+=['Unresolved: no 2025 comparator supplied. '+(' filler'*500)]
    sources=c.sources('history',groups)
    second=await c.compact('history',sources,force=True)
    assert second['cursor']==3 and len(calls)==2
    assert calls[1]['previous_summary'] and len(calls[1]['sources'])==1
    summary=json.loads(c.artifacts.load_text(second['summary_artifact']))
    assert len(summary['items'])==3
    for source in sources:assert c.artifacts.load_text(source['artifact_id'])

@pytest.mark.parametrize('mode',['empty','invalid_json','length','missing_source','no_reduction','missing_coverage'])
async def test_failed_summary_keeps_original_and_old_cursor(compactor,mode):
    c,ledger,calls,_=compactor
    sources=c.sources('history',['original fact '+('data '*1000)])
    prior=await c.compact('history',sources,force=True)
    sources=c.sources('history',['original fact '+('data '*1000),'new correction '+('data '*1000)])
    async def bad(value):
        new=value['sources'][0]
        item={'kind':'correction','text':'new correction','source_id':new['id'],'quote':'new correction'}
        summary={'covered_ids':[new['id']],'items':[item]}
        if mode=='empty':summary['items']=[]
        if mode=='missing_source':item['source_id']='foreign-source'
        if mode=='missing_coverage':summary['covered_ids']=[]
        if mode=='no_reduction':summary['items']=[{**item,'text':'x'*1600} for _ in range(10)]
        return {'text':'{' if mode=='invalid_json' else dumps(summary),'incomplete':mode=='length','usage':{'requests':1}}
    c.summarize=bad
    assert await c.compact('history',sources,force=True)==prior
    assert ledger.compaction_head(c.broker.workspace,'history')==prior
    assert list(ledger.attempts.values())[-1]['state']=='failed'
    assert c.artifacts.load_text(sources[-1]['artifact_id'])
    attempts=len(ledger.attempts)
    await c.compact('history',sources,force=True)
    assert len(ledger.attempts)==attempts

async def test_budget_and_oversized_complete_turn_not_called(compactor):
    c,ledger,calls,_=compactor
    sources=c.sources('history',['x'*25000])
    assert await c.compact('history',sources,force=True) is None and not calls
    assert list(ledger.attempts.values())[-1]['reason']=='oversized_complete_turn'
    ledger.run_budget=lambda _: {'models':18}
    sources=c.sources('history',['y'*5000])
    assert await c.compact('history',sources,force=True) is None and not calls
    assert list(ledger.attempts.values())[-1]['state']=='not_called'

async def test_quote_must_exist_in_fixed_original_and_generation_blocks_reuse(compactor):
    c,ledger,calls,_=compactor;sources=c.sources('history',['original '+('a'*5000)])
    head=await c.compact('history',sources,force=True)
    c.broker.workspace='new-authorization-generation'
    assert c.head('history') is None
    def denied():raise PermissionError('revoked')
    c.artifacts.validate=denied
    with pytest.raises(PermissionError):c.render('history',head)


def test_complete_groups_protect_users_and_pair_parallel_tools():
    items=[{'role':'user','content':'Never treat count as revenue.'},
      {'type':'function_call','call_id':'a'}, {'type':'function_call','call_id':'b'},
      {'type':'function_call_output','call_id':'a'}, {'type':'function_call_output','call_id':'b'},
      {'role':'user','content':'Correction is authoritative.'}, {'role':'assistant','content':'Noted.'}]
    groups=complete_groups(items)
    assert [[i for i,_ in g] for g in groups]==[[1,2,3,4],[6]]
    with pytest.raises(ContextBudgetExceeded):complete_groups([{'type':'function_call','call_id':'pending'}])

async def test_runtime_summary_keeps_recent_groups_and_current_user(compactor):
    from agents.run import ModelInputData
    from dataclasses import dataclass
    c,ledger,calls,_=compactor
    @dataclass
    class Data:
        model_data: object
    items=[{'role':'user','content':'Current correction remains verbatim.'}]
    for i in range(5):
        items += [{'type':'function_call','call_id':str(i),'arguments':'{}','name':'read_source'},
                  {'type':'function_call_output','call_id':str(i),'output':'Fact '+str(i)+' '+('a'*5000)}]
    def project(data):return data.model_data
    project.exclusions=[]
    project.force_compaction=False
    project.context_pressure=True
    result=await c.prepare(Data(ModelInputData(input=items,instructions='fixed')),project)
    assert result.input[1]==items[0] and result.input[-6:]==items[-6:]
    assert len(result.input)==8 and len(calls)==1
    assert ledger.compaction_head(c.broker.workspace,'runtime')['cursor']==2
    assert len(complete_groups(result.input))==4
    assert result.input[0]['role']=='assistant'

async def test_response_audit_preserves_raw_cache_and_length_metadata():
    import httpx
    from agent_core.context.audit import after_receive,audit_http_response
    values=[];token=after_receive.set(values.append)
    try:
        await audit_http_response(httpx.Response(200,json={'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},
            'usage':{'input_tokens':100,'input_tokens_details':{'cached_tokens':80},'output_tokens':10},'output':[]}))
    finally:after_receive.reset(token)
    assert values[-1]['usage']['input_tokens']==100
    assert values[-1]['usage']['input_tokens_details']['cached_tokens']==80
    assert values[-1]['status']=='incomplete'

def test_chat_metadata_is_archived_but_not_flattened_into_business_prose():
    from agent_core.context.compaction import text_of
    assert text_of({'id':'internal-id','role':'assistant','content':'Count 7, not revenue.','extra':None})=='Count 7, not revenue.'
    assert '"call_id"' in text_of({'type':'function_call','call_id':'call-1','name':'read_source'})

async def test_summary_source_id_directly_reads_original_without_alias(compactor):
    c,_,_,_=compactor
    source=c.sources('history',[{'role':'assistant','content':'Fixed v1 '+('x'*4000)}])[0]
    head=await c.compact('history',[source],force=True)
    memory=json.loads(c.artifacts.load_text(head['summary_artifact']))
    original=json.loads(c.artifacts.load_text(memory['items'][0]['source_id']))
    assert original['content'].startswith('Fixed v1')
    assert memory['items'][0]['source_id']==source['artifact_id']

async def test_available_batches_stop_at_original_delivery_reserve(compactor):
    c,ledger,calls,_=compactor
    c.broker.request=c.broker.request.model_copy(update={'limits':RunLimits(max_turns=4)})
    ledger.run_budget=lambda _: {'models':len(calls)}
    sources=c.sources('history',[f'Fact {i} '+('x'*5000) for i in range(12)])
    head=await c.compact_available('history',sources,force=True)
    assert len(calls)==1 and 0<head['cursor']<len(sources)
    assert list(ledger.attempts.values())[-1]['reason']=='original_budget_reserved_for_delivery'
    assert ledger.compaction_head(c.broker.workspace,'history')==head

async def test_finalizing_never_starts_summary_even_with_spare_model_turns(compactor):
    c,ledger,calls,_=compactor
    ledger.progress=lambda _: {'phase':'finalizing'}
    head=await c.compact_available('runtime',c.sources('runtime',['x'*5000]*5),force=True)
    assert head is None and calls==[]
    assert list(ledger.attempts.values())[-1]['reason']=='original_budget_reserved_for_delivery'

async def test_invalid_provider_json_diagnostic_has_no_response_body():
    import httpx,hashlib
    from agent_core.context.audit import after_receive,audit_http_response
    values=[];token=after_receive.set(values.append);raw=b'not-json-\xff'
    try:await audit_http_response(httpx.Response(200,content=raw,headers={'content-type':'text/plain'}))
    finally:after_receive.reset(token)
    assert values[0]=={'http_status':200,'request_id':None}
    assert values[1:]==[{'status_code':200,'body_parse_error':'invalid_json','content_type':'text/plain',
        'body_bytes':len(raw),'body_sha256':hashlib.sha256(raw).hexdigest()}]

async def test_real_failure_history_size_does_not_trigger_summary_without_window_pressure(compactor):
    from types import SimpleNamespace
    from agents.run import ModelInputData
    from agent_core.controlled.model_projection import ModelProjection
    c,ledger,calls,_=compactor
    items=[{'role':'user','content':'approved current task'}]
    for i in range(4):
        items += [{'type':'function_call','call_id':str(i),'name':'read_source','arguments':'{}'},
            {'type':'function_call_output','call_id':str(i),'output':'evidence '+str(i)+' '+('x'*4000)}]
    projection=ModelProjection(c.artifacts,'deepseek-v4-pro')
    from dataclasses import dataclass
    @dataclass
    class Data:
        model_data: object
        agent: object
    data=Data(model_data=ModelInputData(input=items,instructions='fixed'),
        agent=SimpleNamespace(tools=[],output_type=None))
    result=await c.prepare(data,projection)
    assert len(dumps(items))>16031 and not projection.context_pressure
    assert not calls and result.input==items
