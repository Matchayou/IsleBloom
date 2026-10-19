import json
import httpx
import pytest
from pydantic import ValidationError

from agent_core.adapters.scripted import Call, ScriptedEngine
from agent_core.app.api import create_app
from agent_core.collaboration.models import ReviewAdvice, ReviewIssue
from agent_core.collaboration.review import validate_issue_calls
from agent_core.execution.ports import Conflict
from test_automatic_review import setup_cycle


def test_issue_calls_must_be_unique_and_belong_to_candidate():
    with pytest.raises(ValidationError):
        ReviewIssue(problem='p',evidence='e',instruction='i',tool_call_ids=('a','a'))
    advice=ReviewAdvice(verdict='needs_revision',summary='s',issues=(
        ReviewIssue(problem='p',evidence='e',instruction='i',tool_call_ids=('foreign',)),))
    with pytest.raises(Conflict,match='fixed execution'):
        validate_issue_calls(advice,[{'call_id':'actual'}])


async def linked_cycle(tmp_path, *, invalid=False, foreign_read=False):
    values=await setup_cycle(tmp_path,['needs_revision','passed'])
    db,scopes,research,tasks,projects,c,intent,ref,_=values
    counts={'executor':0,'reviewer':0};inspections=[]
    def script(request):
        if request.agent_id.startswith('temporary:'):
            counts['reviewer']+=1
            payload=json.loads(request.input.messages[0].content)
            catalog=payload['review_execution']
            call=catalog['tool_calls'][0]['call_id']
            assert catalog['task_id']==intent.task_id
            assert call==f"source-{counts['reviewer']}"
            def answer(_,results):
                inspections.extend(results)
                return {'verdict':'passed' if counts['reviewer']>1 else 'needs_revision','summary':'Checked the fixed report.',
                    'issues':[] if counts['reviewer']>1 else [{'problem':'Missing source limitation',
                    'evidence':'The recorded source does not justify a market conclusion.',
                    'instruction':'State the evidence limit.', 'tool_call_ids':['foreign' if invalid else call]}]}
            return [Call('read_execution_tool',{'call_id':'foreign' if foreign_read else call},'inspect-'+str(counts['reviewer']))],answer
        counts['executor']+=1
        return [Call('read_source',{'ref':ref.model_dump()},'source-'+str(counts['executor']))],{
            'report':'Synthetic source revenue 100. Version '+str(counts['executor'])}
    tasks.engine=ScriptedEngine(script)
    await c.execute(intent.command_id)
    return values,counts,inspections


@pytest.mark.asyncio
async def test_issue_pins_old_report_and_executor_calls_after_revision(tmp_path,monkeypatch):
    values,counts,inspections=await linked_cycle(tmp_path)
    db,scopes,research,tasks,projects,c,intent,ref,_=values
    loop=c.review.get(intent.task_id)
    assert loop.status=='passed' and loop.current_version==2
    assert len(inspections)==2 and all(v['call_id'].startswith('source-') for v in inspections)
    before=dict(counts)
    params={'task_id':intent.task_id,'spec_version':1,'issue_index':0}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        response=await client.get('/api/review_issue',params=params)
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        data=response.json()
        assert data['result_id']==loop.rounds[0].result_id
        assert data['execution']['run_id']==tasks.receipt(data['result_id']).run_id
        assert data['execution']['spec_version']==1
        assert data['issue']['tool_call_ids']==['source-1']
        assert [v['call_id'] for v in data['execution']['tool_calls']]==['source-1']
        assert data['execution']['output']['report'].endswith('Version 1')
        for wrong in [dict(params,issue_index=-1),dict(params,issue_index=5),dict(params,spec_version=2),dict(params,task_id=loop.reviewer_task_id)]:
            assert (await client.get('/api/review_issue',params=wrong)).status_code==404
        original=tasks.read_artifact
        def changed(key):
            value=original(key)
            return {**value,'artifact':value['artifact'].model_copy(update={'sha256':'changed'})}
        monkeypatch.setattr(tasks,'read_artifact',changed)
        assert (await client.get('/api/review_issue',params=params)).status_code==409
        monkeypatch.setattr(tasks,'read_artifact',original)
        root=research.research(intent.research_id)
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
        assert (await client.get('/api/review_issue',params=params)).status_code==403
    assert counts==before
    db.close()


@pytest.mark.asyncio
async def test_foreign_call_prevents_publishing_review_round(tmp_path):
    values,_,_=await linked_cycle(tmp_path,invalid=True)
    db,_,_,_,_,c,intent,_,_=values
    loop=c.review.get(intent.task_id)
    assert loop.status=='blocked'
    assert 'outside the fixed execution' in loop.reason
    assert not loop.rounds
    db.close()


@pytest.mark.asyncio
async def test_report_only_issue_has_no_invented_tool_links(tmp_path):
    db,_,research,tasks,_,c,intent,_,_=await setup_cycle(tmp_path,['needs_revision','passed'])
    await c.execute(intent.command_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        response=await client.get('/api/review_issue',params={'task_id':intent.task_id,'spec_version':1,'issue_index':0})
        assert response.status_code==200,response.text
        assert response.json()['issue']['tool_call_ids']==[]
    db.close()


@pytest.mark.asyncio
async def test_reviewer_cannot_read_a_tool_outside_candidate(tmp_path):
    values,_,inspections=await linked_cycle(tmp_path,foreign_read=True)
    db,_,_,_,_,c,intent,_,_=values
    loop=c.review.get(intent.task_id)
    assert loop.status=='failed' and not loop.rounds
    assert inspections==[]
    db.close()
