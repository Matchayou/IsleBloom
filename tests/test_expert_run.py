from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import httpx
import pytest

from agent_core.app.api import create_app
from agent_core.contracts import CoreEvent
from agent_core.controlled.inspection import ToolRecords
from agent_core.controlled.ledger import digest
from agent_core.controlled.workspace import Workspace
from agent_core.context.artifacts import ContextArtifacts
from agent_core.execution.ports import StoredRunEvent
from agent_core.execution.tool_calls import merge_tool_calls
from test_automatic_review import setup_cycle


def test_calls_merge_by_identity_not_name_and_keep_unfinished_calls():
    start = datetime.now(timezone.utc)
    events = [StoredRunEvent(root_run_id='r', sequence=i, occurred_at=start+timedelta(seconds=i),
        event=CoreEvent(run_id=run, parent_run_id=None, phase='tool', name='search', tool_call_id=call, status=status))
        for i,(run,call,status) in enumerate([
            ('r','a','started'),('r','b','started'),('r','b','completed'),
            ('child','a','completed'),('r','a','failed'),('r','c','started')],1)]
    calls = merge_tool_calls('r', events, {})
    assert [(c['call_id'],c['status'],c['duration_seconds']) for c in calls] == [
        ('a','failed',4),('b','completed',1),('c','started',None)]
    assert not any(c['io_recorded'] for c in calls)


def test_saved_tool_io_is_bound_to_operation_hash_and_run_receipt(tmp_path):
    workspace = Workspace(tmp_path/'session')
    artifacts = ContextArtifacts(workspace, lambda:None, lambda _:None)
    binding = {'run_id':'r'}
    payload = {'name':'read_source','call_id':'one','arguments':{'query':'public fact'}}
    workspace.save('transcript_attempt_1_request', json.dumps(dict(operation_id='tool:one',operation='tool',payload=payload)))
    # Other session history and private notebooks must not appear.
    for name,kind,call in [('other','tool','two'),('write_private_note','private','private')]:
        p={'name':name,'call_id':call,'arguments':{'text':'private notebook'}}
        workspace.save(f'transcript_attempt_{call}_request',json.dumps(dict(operation_id='tool:'+call,operation=kind,payload=p)))
    receipt = artifacts.save_text(json.dumps({'binding':digest(binding),'value':{'response':{'value':{'fact':42}}}}))
    operations=[{'id':'tool:one','kind':'tool','request_hash':digest(payload),'status':'confirmed'},
        {'id':'tool:private','kind':'private','request_hash':digest({'name':'write_private_note','call_id':'private','arguments':{'text':'private notebook'}}),'status':'confirmed'}]
    engine=SimpleNamespace(private_root=tmp_path,workspace_id=lambda _: 'session',
        ledger=SimpleNamespace(binding=lambda _:binding,operation_history=lambda _:operations,checkpoint=lambda *args:receipt))
    reader=ToolRecords(engine,'r')
    assert reader.requests()=={'one':{'operation_id':'tool:one','name':'read_source','input':{'query':'public fact'},'confirmation':'confirmed'}}
    assert reader.output('tool:one')==({'fact':42},True)
    # A response from a different run cannot be disclosed even in the same session.
    wrong=artifacts.save_text(json.dumps({'binding':digest({'run_id':'other'}),'value':{'response':{'value':'secret'}}}))
    engine.ledger.checkpoint=lambda *args:wrong
    with pytest.raises(ValueError,match='another run'):reader.output('tool:one')
    operations[0]['request_hash']='wrong'
    assert reader.requests()=={}
    workspace.close()


@pytest.mark.asyncio
async def test_version_run_selection_is_read_only_and_revocation_aware(tmp_path):
    db,scopes,research,tasks,projects,c,intent,ref,calls=await setup_cycle(tmp_path,['needs_revision','passed'])
    await c.execute(intent.command_id)
    runs=tasks.query('list_runs',intent.task_id)
    before=len(calls)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        for binding in runs:
            response=await client.get('/api/expert_run',params={'task_id':intent.task_id,'run_id':binding.id})
            assert response.status_code==200,response.text
            data=response.json()
            assert response.headers['cache-control']=='no-store'
            assert data['spec_version']==binding.spec_version
            assert data['input']==scopes.repository.snapshot(binding.id).input.model_dump(mode='json')
            assert data['output']['report']=='Revenue 100. Source line 1.'
            assert data['result_id']=='task-result:'+binding.id
            assert data['tool_calls']==[]
        params={'task_id':intent.task_id,'run_id':runs[0].id,'call_id':'other'}
        assert (await client.get('/api/expert_run',params=params)).status_code==404
        params={'task_id':c.review.get(intent.task_id).reviewer_task_id,'run_id':runs[0].id}
        assert (await client.get('/api/expert_run',params=params)).status_code==403
        root=research.research(intent.research_id)
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
        params={'task_id':intent.task_id,'run_id':runs[0].id}
        assert (await client.get('/api/expert_run',params=params)).status_code==403
    assert len(calls)==before
    db.close()
