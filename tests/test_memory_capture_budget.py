"""Automatic draft-only capture, real source identity, whole-entry allocation."""
from uuid import uuid4
import pytest
from agent_core.adapters.scripted import ScriptedEngine, Call
from agent_core.research.chat import ChatService
from agent_core.research.models import NodeCommand
from agent_core.contracts import RunLimits
from agent_core.memories.models import MemoryEntry
from agent_core.context.memory_budget import allocate, bound, MEMORY_TOKEN_BUDGET
from agent_core.context.projection import ContextBudgetExceeded
from agent_core.context.scopes import ScopeDenied
from test_dynamic_context import app
from test_memory_writes import admitted

async def source(app, content='请记住：以后先给结论，再解释原因。'):
    _, _, research, tasks, root, _ = app
    chat_service = ChatService(research,tasks)
    chat = await chat_service.start(root.id,root.goal)
    chat = chat_service.send(chat.node_id,content,chat.revision)
    command = NodeCommand(command_id=uuid4().hex,research_id=root.id,node_id=chat.node_id,
        kind='chat_reply',chat_revision=chat.revision,limits=RunLimits(max_calls=0))
    saved = await research.submit(command)
    assert research.ledger.get(saved.run_id).result.status == 'succeeded'
    jobs = research.memories.capture.records()
    assert len(jobs) == 1 and jobs[0].message_id == chat.messages[-1].id
    return jobs[0],saved


def test_whole_entries_required_and_later_smaller_optional():
    def entry(content):return MemoryEntry(owner='local',kind='preference',content=content,source='user')
    required=entry('明确约束');large=entry('完整正文不能截断'*2000);small=entry('按人民币万元口径')
    selected,report=allocate((required,),(large,small))
    assert selected==(required,small) and selected[1].content==small.content
    assert report['token_upper_bound']==bound(selected)<=MEMORY_TOKEN_BUDGET
    assert report['omitted']==[{'id':large.id,'version':1,'reason':'memory_injection_budget'}]
    with pytest.raises(ContextBudgetExceeded):allocate((large,),())

async def test_capture_source_id_and_repeat_enqueue(app):
    job,saved=await source(app)
    research=app[2];binding=research.query('run',saved.run_id)
    snapshot=research.scopes.repository.snapshot(saved.run_id)
    with research.database.transaction() as tx:research.memories.capture.record_in(tx,binding,snapshot)
    assert research.memories.capture.records()==[job]
    with pytest.raises(ScopeDenied):await research.memories.capture.process('foreign-key')

async def test_automatic_candidate_draft_atomic_once(app,monkeypatch):
    job,_=await source(app);research=app[2]
    args={'scope':'user','kind':'preference','content':'先给结论，再解释原因','basis':'user_statement',
        'quote':job.content,'reason':'Durable user preference'}
    research.engine=ScriptedEngine(lambda request:([Call('write_memory',args,'capture-one')],{'reason':'Saved candidate'}))
    writer=research.memories.writes;original=writer.submit;count=[]
    async def submit(snapshot,research_id,proposal):
        count.append(1)
        with admitted(snapshot,proposal):return await original(snapshot,research_id,proposal)
    monkeypatch.setattr(writer,'submit',submit)
    value=await research.memories.capture.process(job.id)
    assert value.state=='completed'
    assert len(writer.records(value.request.run_id))==1
    memories=research.memories.list(job.research_id)
    assert len(memories)==1 and memories[0].status=='draft'
    assert await research.memories.capture.process(job.id)==value and count==[1]
    assert writer.records(value.request.run_id)[0].state=='published'

async def test_capture_project_movement_and_excluded_source(app):
    job,_=await source(app);research=app[2]
    project=research.collaboration.projects.create('Moved')
    await research.collaboration.projects.create_main_task(project.id,app[4].goal,app[4].id)
    value=await research.memories.capture.process(job.id)
    assert value.state=='failed' and 'project changed' in value.reason
    assert not research.memories.writes.records()

async def test_background_cannot_confirm_correct_or_domain(app):
    job,_=await source(app);research=app[2];ready=research.memories.capture.prepare(job)
    snap=research.scopes.repository.snapshot(ready.request.run_id)
    from agent_core.memories.write_models import WriteMemory
    args=WriteMemory(scope='specialist',kind='experience',content='Reusable',basis='experience',reason='Proposal')
    with admitted(snap,args),pytest.raises(ScopeDenied):await research.memories.writes.submit(snap,job.research_id,args)

async def test_capture_enforces_one_candidate_per_human_message(app):
    job,_=await source(app);research=app[2];ready=research.memories.capture.prepare(job)
    snap=research.scopes.repository.snapshot(ready.request.run_id)
    from agent_core.memories.write_models import WriteMemory
    from agent_core.execution.ports import Conflict
    args=WriteMemory(scope='user',content='先给结论',basis='user_statement',quote=job.content,reason='Candidate')
    with admitted(snap,args):await research.memories.writes.submit(snap,job.research_id,args)
    with admitted(snap,args),pytest.raises(Conflict,match='One background candidate'):
        await research.memories.writes.submit(snap,job.research_id,args)
    assert len(research.memories.writes.records(snap.run_id))==1
