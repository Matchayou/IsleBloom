import httpx
import pytest
from agent_core.app.api import create_app
from test_automatic_review import setup_cycle


@pytest.mark.asyncio
async def test_execution_review_and_old_version_contexts_are_frozen_and_authorized(tmp_path):
    db,scopes,research,tasks,projects,c,intent,ref,calls=await setup_cycle(tmp_path,['needs_revision','passed'])
    app=create_app(research,task_service=tasks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        url='/api/expert_context'
        pending=await client.get(url,params={'task_id':intent.task_id})
        assert pending.status_code==200
        assert pending.json()['state']=='not_started'
        assert pending.json()['handoff']['user_instruction']=='Summarize the source and explain limitations.'
        await c.execute(intent.command_id)
        response=await client.get(url,params={'task_id':intent.task_id})
        assert response.status_code==200,response.text
        data=response.json()
        assert response.headers['cache-control']=='no-store'
        assert data['state']=='recorded' and data['snapshot']['input']['messages']
        assert data['spec_version']==2
        old=next(run for run in data['runs'] if run['spec_version']==1)
        earlier=await client.get(url,params={'task_id':intent.task_id,'run_id':old['run_id']})
        assert earlier.status_code==200,earlier.text
        assert earlier.json()['spec_version']==1
        snapshot=scopes.repository.snapshot(old['run_id'])
        assert earlier.json()['snapshot']['input']==snapshot.input.model_dump(mode='json')
        reviewer=c.review.get(intent.task_id).reviewer_task_id
        review=await client.get(url,params={'task_id':reviewer})
        assert review.status_code==200,review.text
        assert review.json()['state']=='recorded'
        assert review.json()['snapshot']['mode']=='discussion'
        assert all(run['kind']=='review' for run in review.json()['runs'])
        # Cross-task run selection cannot disclose either frozen input.
        assert (await client.get(url,params={'task_id':reviewer,'run_id':old['run_id']})).status_code==403
        assert (await client.get(url,params={'task_id':'missing'})).status_code==404
        root=research.research(intent.research_id)
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
        assert (await client.get(url,params={'task_id':intent.task_id})).status_code==403
        assert (await client.get(url,params={'task_id':reviewer})).status_code==403
    db.close()
