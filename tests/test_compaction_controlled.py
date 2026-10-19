"""Real PostgreSQL + SDK summary accounting; simulated Model responses are labelled."""
import json
import os
from uuid import uuid4
import httpx
from openai import BadRequestError
import pytest
from sqlalchemy import select
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.composition import build_resources,build_controlled_services
from agent_core.context.projection import dumps
from agent_core.contracts import RunLimits
from agent_core.research.models import ChatMessage
from test_controlled_collaboration import Provider,answer,call

@pytest.fixture
def controlled(tmp_path):
    url=os.environ.get('CE6_TEST_DATABASE_URL')
    if not url:pytest.skip('CE6 dedicated PostgreSQL required')
    db=PostgresDatabase(url);provider=Provider();scopes=build_resources(db,tmp_path/'blobs')
    research,tasks,_=build_controlled_services(db,scopes,private_root=tmp_path/'private',model_provider=provider)
    yield db,scopes,research,tasks,provider
    db.close()

def task(controlled,long=True,model="gpt-4.1-mini"):
    _,scopes,research,tasks,_=controlled
    root=research.create('CE6 '+uuid4().hex);scope=scopes.current(root.scope_id,'local')
    _,ref=scopes.ingest(scope,content=b'Fixed source v1: 7 units. Market size unknown.',name='fixed.txt',media_type='text/plain')
    history=[ChatMessage(role='assistant',content=f'Fact {i}: source v1, not revenue. '+('background '*320)) for i in range(10)] if long else []
    if long:history.insert(4,ChatMessage(role='user',content='纠错：7 是数量，不是收入；市场规模未知。'))
    spec=tasks.create(research_id=root.id,kind='synthesize',model=model,user_request='Report only known facts from source v1 background.',required_refs=(ref,),
        selected_messages=history,limits=RunLimits(max_turns=10,max_calls=6,timeout_seconds=90))
    return spec,ref

async def summary(kwargs):
    assert not kwargs['tools']
    assert kwargs['output_schema'].is_strict_json_schema()
    assert kwargs['model_settings'].tool_choice=='none'
    value=json.loads(kwargs['input'][0]['content'])
    items=list(value['previous_summary']['items']) if value['previous_summary'] else []
    for source in value['sources']:
        items.append({'kind':'fact','text':source['text'][:70],'quote':source['text'][:50],'source_id':source['id']})
    return answer({'covered_ids':value['required_source_ids'],'items':items})

@pytest.fixture
def window_pressure(monkeypatch):
    # Inject pressure to test compaction accounting; the real threshold has its own regression.
    from agent_core.controlled.model_projection import ModelProjection
    original=ModelProjection.__call__
    def project(self,data):
        result=original(self,data)
        self.context_pressure=True
        return result
    monkeypatch.setattr(ModelProjection,'__call__',project)

async def test_summary_budget_manifest_and_incremental_cursor(controlled,window_pressure):
    db,_,_,tasks,provider=controlled;spec,ref=task(controlled)
    provider.plans.append([summary,summary,call('read_source',ref=ref.model_dump(mode='json')),
        answer({'report':'7 units, not revenue; market size unknown.'})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert result.status=='succeeded',result
    ledger=tasks.engine.ledger
    with db.transaction():
        attempts=list(db.connection.execute(select(ledger.compactions.c.attempt).where(ledger.compactions.c.run_id==receipt.run_id)).scalars())
        manifests=list(db.connection.execute(select(ledger.manifests.c.manifest).where(ledger.manifests.c.run_id==receipt.run_id)).scalars())
    successes=sorted((a for a in attempts if a['state']=='succeeded'),key=lambda a:a['from_cursor'])
    assert len(successes)==2 and successes[1]['from_cursor']==successes[0]['to_cursor']
    assert successes[1]['previous_summary']==successes[0]['summary_artifact']
    assert sum(m['purpose']=='summary' for m in manifests)==2
    assert ledger.run_budget(receipt.run_id)['models']==4 and result.usage.requests==4
    final=provider.requests[-1]
    assert '纠错：7 是数量' in json.dumps(final['input'],ensure_ascii=False)
    assert '_controlled_summary' in json.dumps(final['input'])

async def test_summary_failure_retains_original_no_repeated_retry(controlled,window_pressure):
    db,_,_,tasks,provider=controlled;spec,_=task(controlled)
    provider.plans.append([answer(''),answer({'report':'Evidence insufficient; original remains available.'})])
    receipt=(await tasks.run(spec.id))['receipt'];ledger=tasks.engine.ledger
    with db.transaction():
        attempts=list(db.connection.execute(select(ledger.compactions.c.attempt).where(ledger.compactions.c.run_id==receipt.run_id)).scalars())
    assert len(attempts)==1 and attempts[0]['state']=='failed'
    workspace=tasks.engine.workspace_id(ledger.binding(receipt.run_id))
    assert ledger.compaction_head(workspace,'business:history') is None
    assert len(provider.requests)==2
    from agent_core.controlled.workspace import Workspace
    from agent_core.context.artifacts import ContextArtifacts
    w=Workspace(tasks.engine.private_root/workspace)
    try:assert ContextArtifacts(w,lambda:None,lambda _:None).load_text(attempts[0]['input_artifact'])
    finally:w.close()

@pytest.mark.parametrize('rejections',[1,2])
async def test_provider_window_gets_only_one_reprojection(controlled,rejections):
    _,_,_,tasks,provider=controlled;spec,_=task(controlled,long=False)
    async def reject(kwargs):
        raise BadRequestError('Window rejected',response=httpx.Response(400,request=httpx.Request('POST','https://fixture.invalid/responses')),
            body={'code':'context_length_exceeded'})
    provider.plans.append([reject]*rejections+[answer({'report':'Confirmed source only.'})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert len(provider.requests)==1
    assert result.status=='failed'
    assert tasks.engine.ledger.run_budget(receipt.run_id)['models']==1
    assert result.error.code=='context_window'

@pytest.mark.parametrize('reject_again',[False,True])
async def test_smaller_projection_retries_once_and_never_loops(controlled,reject_again):
    _,_,_,tasks,provider=controlled;spec,_=task(controlled,long=False)
    async def reject(kwargs):
        raise BadRequestError('Window rejected',response=httpx.Response(400,request=httpx.Request('POST','https://fixture.invalid/responses')),
            body={'code':'context_length_exceeded'})
    # A long malformed draft fits the initial local bound; the provider then rejects
    # the format-correction request. The lower window externalizes that exact draft.
    from agent_core.controlled.model_projection import WINDOW_TOKENS
    provider.plans.append([answer('x'*round(WINDOW_TOKENS*.8)),reject,
        reject if reject_again else answer({'report':'Known fixed facts only.'})])
    receipt=(await tasks.run(spec.id))['receipt'];result=tasks.ledger.get(receipt.run_id).result
    assert len(provider.requests)==3,result
    assert len(dumps(provider.requests[2]['input']))<len(dumps(provider.requests[1]['input']))
    assert tasks.engine.ledger.run_budget(receipt.run_id)['models']==3
    assert result.status==('failed' if reject_again else 'succeeded'),result
    if reject_again:assert result.error.code=='context_window'

async def test_saved_summary_rejected_after_real_scope_revocation(controlled,window_pressure):
    _,scopes,research,tasks,provider=controlled;spec,_=task(controlled)
    provider.plans.append([summary,summary,answer({'report':'Known facts only.'})])
    receipt=(await tasks.run(spec.id))['receipt'];ledger=tasks.engine.ledger
    workspace_id=tasks.engine.workspace_id(ledger.binding(receipt.run_id))
    head=ledger.compaction_head(workspace_id,'business:history');assert head
    from agent_core.context.artifacts import ContextArtifacts
    from agent_core.controlled.workspace import Workspace
    from agent_core.context.scopes import ScopeDenied
    work=Workspace(tasks.engine.private_root/workspace_id)
    artifacts=ContextArtifacts(work,lambda:tasks.engine.validate_run(receipt.run_id,require_started=True),lambda _:None)
    try:
        assert artifacts.load_text(head['summary_artifact'])
        root=scopes.current(research.research(spec.research_id).scope_id,'local')
        scopes._replace(root,tools=())
        with pytest.raises(ScopeDenied):artifacts.load_text(head['summary_artifact'])
    finally:work.close()


async def test_documented_summary_effort_does_not_change_main_model_settings(controlled,window_pressure):
    _,_,_,tasks,provider=controlled;spec,_=task(controlled,model='deepseek-flash')
    provider.plans.append([summary,summary,answer({'report':'Fixed known facts only.'})])
    receipt=(await tasks.run(spec.id))['receipt']
    assert tasks.ledger.get(receipt.run_id).result.status=='succeeded'
    assert all(r['model_settings'].reasoning.effort=='none' for r in provider.requests[:2])
    assert provider.requests[-1]['model_settings'].reasoning.effort=='low'
