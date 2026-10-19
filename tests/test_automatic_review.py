import asyncio
import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources, build_projects
from agent_core.research.chat import ChatService
from legacy_composition import build_research, build_tasks


async def setup_cycle(tmp_path, verdicts):
    db=MemoryDatabase();scopes=build_resources(db,tmp_path/'blobs')
    research=build_research(db,scopes);tasks=build_tasks(db,scopes,research)
    projects=build_projects(db,research,tasks);collaboration=research.collaboration
    project=projects.create('Automatic cooperation')
    member=await projects.create_main_task(project.id,'Read and summarize')
    node=research.query('list_nodes',member.research_id)[0]
    root=research.research(member.research_id)
    _,ref=scopes.ingest(scopes.current(root.scope_id,research.owner),content=b'Fact: revenue is 100.',name='source.txt',media_type='text/plain')
    ChatService(research,tasks).send(node.id,'Summarize the source and explain limitations.',0,'request')
    calls=[]
    def script(request):
        calls.append(request)
        if request.agent_id.startswith('temporary:'):
            index=sum(r.agent_id.startswith('temporary:') for r in calls)-1
            verdict=verdicts[min(index,len(verdicts)-1)]
            return [],{'verdict':verdict,'summary':'Checked this fixed candidate.',
                'issues':[] if verdict=='passed' else [{'problem':'Missing source location','evidence':'The report does not give the line.','instruction':'Add source line 1.'}]}
        return [],{'report':'Revenue 100. Source line 1.','limitations':['Synthetic fixture only.']}
    tasks.engine=ScriptedEngine(script)
    intent=collaboration.delegate_task(command_id='cooperation',node_id=node.id,expert_id='expert_synthesize',
        task_instruction='Summarize the source and explain limitations.',delivery_requirements='Cite the source.',resource_refs=(ref,))
    return db,scopes,research,tasks,projects,collaboration,intent,ref,calls


@pytest.mark.parametrize('verdicts,status,revisions',[
    (['passed'],'passed',0),(['needs_revision','passed'],'passed',1),
    (['needs_revision','needs_revision','passed'],'passed',2),
    (['needs_revision'],'exhausted',2)])
async def test_automatic_cycle_is_bounded_versioned_and_idempotent(tmp_path,verdicts,status,revisions):
    db,_,_,tasks,projects,c,intent,_,calls=await setup_cycle(tmp_path,verdicts)
    value=await c.execute(intent.command_id)
    assert value['intent'].status=='completed',value
    loop=c.review.get(intent.task_id)
    assert (loop.status,loop.revisions_completed)==(status,revisions),loop
    assert len(loop.rounds)==revisions+1
    assert len(calls)==2*(revisions+1)
    assert tasks.spec(intent.task_id).version==revisions+1
    for round in loop.rounds:
        assert tasks.read_artifact(round.result_id)['artifact'].sha256==round.artifact_sha256
        assert tasks.ledger.get(round.review_run_id).status=='succeeded'
        assert tasks.receipt(round.result_id).delivery=='pending_review'
    assert tasks.receipt(loop.final_result_id).adoption!='adopted'
    from agent_core.contracts import ToolResult
    status_result=ToolResult(value=c.task_status(loop.research_id,loop.task_id))
    assert status_result.value['automatic_review']['status']==status
    assert 'Agent' in projects.expert_thread(loop.reviewer_task_id).messages[-1].content
    count=len(calls)
    await c.execute(intent.command_id)
    await c.review.run(intent.task_id)
    assert len(calls)==count
    if revisions:
        spec=tasks.spec(intent.task_id)
        assert loop.original_request in spec.user_request and '固定版本评审建议' in spec.user_request
        assert loop.rounds[-2].result_id in spec.user_request
        assert loop.rounds[-2].artifact_sha256 in spec.user_request
        assert 'read_context' in spec.user_request
    db.close()


async def test_failed_executor_never_calls_reviewer(tmp_path):
    from agent_core.contracts import fail
    db,_,_,tasks,_,c,intent,_,_=await setup_cycle(tmp_path,['passed'])
    def script(request): fail('fixture_failure','engine','Controlled test failure')
    tasks.engine=ScriptedEngine(script)
    assert (await c.execute(intent.command_id))['intent'].status=='failed'
    assert not c.review.get(intent.task_id).rounds
    db.close()


@pytest.mark.parametrize('action,expected',[('revoke','blocked'),('cancel','cancelled')])
async def test_revocation_and_cancellation_stop_review_without_revision(tmp_path,action,expected):
    db,scopes,research,tasks,_,c,intent,ref,calls=await setup_cycle(tmp_path,['needs_revision'])
    base=tasks.engine;entered=asyncio.Event();release=asyncio.Event()
    class Engine:
        async def execute(self,request,spec,tools,invoke):
            if request.agent_id.startswith('temporary:'):
                entered.set();await release.wait()
            return await base.execute(request,spec,tools,invoke)
    tasks.engine=Engine()
    work=asyncio.create_task(c.execute(intent.command_id))
    await asyncio.wait_for(entered.wait(),5)
    if action=='revoke':
        root=research.research(tasks.spec(intent.task_id).research_id)
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
    else:
        c.cancel_task(command_id='stop',task_id=intent.task_id,expected_version=1,reason='Stop during review')
    release.set();await work
    loop=c.review.get(intent.task_id)
    assert loop.status==expected and loop.revisions_completed==0,loop
    assert tasks.spec(intent.task_id).version==1
    db.close()


async def test_recover_saved_review_and_saved_revision_does_not_repeat_model(tmp_path,monkeypatch):
    db,_,research,tasks,projects,c,intent,_,calls=await setup_cycle(tmp_path,['needs_revision','passed'])
    c.review.ensure(intent.task_id)
    version=tasks.spec(intent.task_id).version
    await tasks.run_durable(intent.task_id,version,tasks.prepare_durable(intent.task_id,version))
    original=c.review.change
    class Crash(BaseException):pass
    crashed=False
    def crash_after_save(loop,**changes):
        nonlocal crashed
        result=original(loop,**changes)
        if not crashed and changes.get('status')=='revising':
            crashed=True;raise Crash()
        return result
    monkeypatch.setattr(c.review,'change',crash_after_save)
    with pytest.raises(Crash):await c.review.run(intent.task_id)
    assert len(calls)==2
    # Existing revision processing can finish before the review worker returns.
    loop=c.review.get(intent.task_id)
    round=loop.rounds[-1]
    c.revise_task(command_id=round.revision_id,task_id=intent.task_id,expected_version=1,
        request=loop.original_request+'\nAdd source line 1.',mode='after_current')
    await c.process_revision(round.revision_id)
    assert len(calls)==3
    from agent_core.composition import build_collaboration
    recovered=build_collaboration(db,research,tasks,projects)
    result=await recovered.review.run(intent.task_id)
    assert result.status=='passed' and result.revisions_completed==1
    assert len(calls)==4
    await recovered.review.run(intent.task_id)
    assert len(calls)==4
    db.close()


async def test_malformed_reviewer_output_fails_without_auto_approval(tmp_path):
    db,_,_,tasks,_,c,intent,_,calls=await setup_cycle(tmp_path,['passed'])
    base=tasks.engine
    class Engine:
        async def execute(self,request,spec,tools,invoke):
            if request.agent_id.startswith('temporary:'):
                from agent_core.contracts import RunResult
                return RunResult(run_id=request.run_id,status='succeeded',output={'verdict':'passed','summary':'Invalid','issues':[
                    {'problem':'Conflict','evidence':'An issue remains','instruction':'Fix it'}]})
            return await base.execute(request,spec,tools,invoke)
    tasks.engine=Engine()
    await c.execute(intent.command_id)
    loop=c.review.get(intent.task_id)
    assert loop.status=='failed' and loop.revisions_completed==0
    assert not loop.rounds
    db.close()


async def test_reviewer_can_read_current_frozen_handoff_and_candidate(tmp_path):
    db,_,_,tasks,_,c,intent,_,_=await setup_cycle(tmp_path,['passed'])
    base=tasks.engine
    class Engine:
        async def execute(self,request,spec,tools,invoke):
            if request.agent_id.startswith('temporary:'):
                message_id=f'automatic-review:{intent.task_id}:v1'
                source_run=tasks.query('list_runs',intent.task_id)[-1]
                result_id='task-result:'+source_run.id
                listing=(await invoke('read_context','review-directory',{})).value
                assert message_id in {m['message_id'] for m in listing['messages']}
                body=(await invoke('read_context','review-handoff',{
                    'message_ids':[message_id],'result_ids':[result_id]})).value
                assert body['messages'][0]['id']==message_id
                assert body['results'][0]['output']['report']
            return await base.execute(request,spec,tools,invoke)
    tasks.engine=Engine()
    await c.execute(intent.command_id)
    assert c.review.get(intent.task_id).status=='passed'
    db.close()


async def test_long_review_advice_uses_fixed_candidate_without_duplicating_user_input(tmp_path):
    db,_,_,tasks,_,c,intent,_,_=await setup_cycle(tmp_path,['needs_revision','passed'])
    reviews=0
    def script(request):
        nonlocal reviews
        if request.agent_id.startswith('temporary:'):
            reviews+=1
            return [],{'verdict':'needs_revision' if reviews==1 else 'passed','summary':'检查固定版本。',
                'issues':[{'problem':'需补充核查说明','evidence':'报告未完成终验内容。',
                    'instruction':'请核对收入计算、列明资料局限、说明下一步核查方式。'*80}] if reviews==1 else []}
        return [],{'report':'Revenue 100. '+('Synthetic source evidence. '*500),'limitations':['Synthetic fixture.']}
    tasks.engine=ScriptedEngine(script)
    await c.execute(intent.command_id)
    loop=c.review.get(intent.task_id)
    assert loop.status=='passed' and loop.revisions_completed==1,loop
    revised=tasks.spec(intent.task_id)
    packet=c.packet(revised.context_packet_id)
    assert packet.user_instruction==loop.original_request
    assert revised.goal==loop.original_request
    assert loop.rounds[0].result_id in packet.manager_instruction
    assert len(revised.user_request)<4000
    db.close()


async def test_ui_assigned_task_runs_automatically_and_projects_results(tmp_path):
    import httpx
    from agent_core.app.api import create_app
    db,_,research,tasks,_,c,intent,ref,calls=await setup_cycle(tmp_path,['needs_revision','passed'])
    source=tasks.spec(intent.task_id)
    direct=tasks.create(research_id=source.research_id,node_id=source.node_id,kind='synthesize',
        user_request='Direct assigned task.',required_refs=(ref,))
    app=create_app(research,task_service=tasks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/task_run',json={'task_id':direct.id})
        assert response.status_code==202,response.text
        for _ in range(100):
            if c.review.get(direct.id).status in ('passed','failed','blocked','exhausted'):break
            await asyncio.sleep(.01)
        assert c.review.get(direct.id).status=='passed'
        data=(await client.get('/api/task_activity',params={'research_id':source.research_id})).json()
        rows=[row for row in data if row['automatic_review'] and row['automatic_review']['task_id']==direct.id]
        assert {row['collaboration_role'] for row in rows}=={'executor','reviewer'}
        assert all(row['automatic_review']['rounds'] for row in rows)
        count=len(calls)
        assert (await client.post('/api/task_run',json={'task_id':direct.id})).status_code==202
        await asyncio.sleep(.02)
        assert len(calls)==count
    db.close()


async def test_explicit_saved_revision_remains_runnable_without_inheriting_old_verdict(tmp_path):
    db,_,_,tasks,_,c,intent,_,calls=await setup_cycle(tmp_path,['passed'])
    await c.execute(intent.command_id)
    before=c.review.get(intent.task_id)
    revision=c.revise_task(command_id='explicit-edit',task_id=intent.task_id,expected_version=1,
        request='New explicit request; inspect another aspect of the same source.',mode='save')
    await c.process_revision(revision.id)
    await c.execute_assigned_task(intent.task_id)
    latest=tasks.query('list_runs',intent.task_id)[-1]
    assert latest.spec_version==2 and tasks.ledger.get(latest.id).status=='succeeded'
    assert len(calls)==3
    assert c.review.get(intent.task_id)==before  # The v1 verdict is history, not approval of v2.
    failed_revision=c.revise_task(command_id='explicit-edit-again',task_id=intent.task_id,expected_version=2,
        request='Another explicit requirement.',mode='save')
    await c.process_revision(failed_revision.id)
    class FailedQueue:
        async def submit(self,*args): pass
        async def status(self,*args): return {'status':'ERROR'}
    c.durable=FailedQueue()
    from agent_core.execution.ports import Conflict
    with pytest.raises(Conflict,match='Background executor workflow ended'):
        await c.execute_assigned_task(intent.task_id)
    assert c.review.get(intent.task_id)==before
    db.close()
