"""Project hierarchy and expert conversation behavior on real services."""
import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_projects
from agent_core.execution.ports import Conflict
from agent_core.context.scopes import ScopeDenied
from agent_core.research.chat import ChatService


@pytest.fixture
def workspace(tmp_path):
    db=MemoryDatabase()
    engine=ScriptedEngine(lambda request:([], 'A fixed-context explanation'))
    scopes=build_resources(db,tmp_path/'blobs')
    research=build_research(db,scopes,engine)
    tasks=build_tasks(db,scopes,research,engine)
    projects=build_projects(db,research,tasks)
    yield db,scopes,research,tasks,projects
    db.close()


@pytest.mark.asyncio
async def test_main_tasks_folders_and_semantic_parent(workspace):
    _,_,research,tasks,projects=workspace
    a=projects.create('A','project-a'); b=projects.create('B','project-b')
    first=await projects.create_main_task(a.id,'First','main-1')
    second=await projects.create_main_task(a.id,'Second','main-2')
    third=await projects.create_main_task(b.id,'Third','main-3')
    assert [t.research_id for t in projects.contents(a.id)['tasks']]==['main-1','main-2']
    assert research.query('list_nodes',first.research_id)[0].id != research.query('list_nodes',second.research_id)[0].id
    root=research.query('list_nodes',first.research_id)[0]
    chat=ChatService(research,tasks)
    sent=chat.send(root.id,'Test message',0)
    branch=await chat.branch(root.id,sent.messages[0].id,'Child',sent.revision)
    assert research.node(branch.node_id).display_parent_id==root.id
    assert branch.research_id==first.research_id
    folder=projects.create_folder(a.id,'Collection',key='folder-1')
    placement=projects.arrange(a.id,'node',branch.node_id,0,folder_id=folder.id,title='Named branch')
    assert projects.query('placement','node',branch.node_id).folder_id==folder.id
    assert research.node(branch.node_id).display_parent_id==root.id
    moved=projects.arrange(a.id,'node',branch.node_id,placement.revision,folder_id=None,title='Named branch')
    assert moved.folder_id is None
    with pytest.raises(Conflict): projects.arrange(b.id,'node',branch.node_id,0)
    with pytest.raises(Conflict): projects.arrange(a.id,'node',branch.node_id,0)
    with pytest.raises(Conflict): projects.create_folder(b.id,'Wrong parent',parent_id=folder.id)
    child=projects.create_folder(a.id,'Nested',parent_id=folder.id)
    with pytest.raises(Conflict): projects.update_folder(folder.id,0,parent_id=child.id,change_parent=True)
    with pytest.raises(Conflict): projects.arrange(a.id,'research',third.research_id,0)


@pytest.mark.asyncio
async def test_project_directory_reads_only_conversation_metadata(workspace, monkeypatch):
    _, _, _, tasks, projects = workspace
    project = projects.create('Directory')
    main = await projects.create_main_task(project.id, 'Main')
    query = tasks.query

    def directory_query(name, *args):
        if name == 'list_specs':
            raise AssertionError('Directory must not load execution contracts')
        if name == 'list_directory':
            return [{'id': 'entry', 'research_id': main.research_id,
                     'goal': 'Conversation', 'parent_task_id': None, 'node_id': 'parent'}]
        return query(name, *args)

    monkeypatch.setattr(tasks, 'query', directory_query)
    entries = projects.contents(project.id)['conversations']
    assert entries[-1] == {'kind': 'task', 'id': 'entry', 'research_id': main.research_id,
                           'title': 'Conversation', 'parent_id': 'parent'}


@pytest.mark.asyncio
async def test_expert_discussion_preserves_spec_and_results(workspace):
    _,scopes,research,tasks,projects=workspace
    p=projects.create('A')
    main=await projects.create_main_task(p.id,'Main')
    root=research.research(main.research_id)
    scope=scopes.current(root.scope_id,research.owner)
    scope,ref=scopes.ingest(scope,content=b'Synthetic revenue source',name='source.txt',media_type='text/plain')
    node=research.query('list_nodes',root.id)[0]
    spec=tasks.create(research_id=root.id,node_id=node.id,kind='synthesize',user_request='Summarize source',required_refs=(ref,))
    before=tasks.spec(spec.id)
    discussion=await projects.discuss(spec.id,'What is the source?',0,message_id='question-1')
    assert discussion.revision==1 and len(discussion.messages)==2
    assert projects.expert_thread(spec.id)==discussion
    assert tasks.spec(spec.id)==before
    with pytest.raises(Conflict): await projects.discuss(spec.id,'Changed',0,message_id='question-2')
    assert (await projects.discuss(spec.id,'What is the source?',0,message_id='question-1'))==discussion
    updated=projects.rerun_spec(spec.id,1,'Summarize source with caveats')
    assert updated.version==2 and before.version==1
    assert tasks.spec(spec.id,1)==before
    with pytest.raises(Conflict): projects.rerun_spec(spec.id,1,'Again')
    assert projects.expert_thread(spec.id).messages==discussion.messages
    scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
    with pytest.raises(ScopeDenied): projects.expert_thread(spec.id)

@pytest.mark.asyncio
async def test_project_api_isolation_and_file_relations(workspace):
    import httpx
    from agent_core.app.api import create_app
    _,scopes,research,tasks,projects=workspace
    app=create_app(research,task_service=tasks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        a=(await client.post('/api/projects',json={'name':'A','id':'api-a'})).json()
        b=(await client.post('/api/projects',json={'name':'B','id':'api-b'})).json()
        one=(await client.post('/api/project_main_task',json={'project_id':a['id'],'title':'One','research_id':'api-one'})).json()
        two=(await client.post('/api/project_main_task',json={'project_id':a['id'],'title':'Two','research_id':'api-two'})).json()
        other=(await client.post('/api/project_main_task',json={'project_id':b['id'],'title':'Other','research_id':'api-other'})).json()
        assert len((await client.get('/api/project_contents',params={'project_id':a['id']})).json()['tasks'])==2
        upload=await client.post('/api/upload?research_id=api-one&name=fixture.txt',content=b'synthetic fixture',headers={'Content-Type':'text/plain'})
        assert upload.status_code==201
        ref=upload.json()['ref']
        spec=tasks.create(research_id=one['research_id'],kind='synthesize',user_request='Read fixture',required_refs=(__import__('agent_core.resources.snapshots',fromlist=['ResourceRef']).ResourceRef.model_validate(ref),))
        root_links=(await client.get('/api/project_files',params={'project_id':a['id']})).json()
        assert any(r['relation']=='upload' and r['ref']==ref for r in root_links)
        task_links=(await client.get('/api/project_files',params={'project_id':a['id'],'task_id':spec.id})).json()
        assert task_links and all(r['task_id']==spec.id for r in task_links)
        assert (await client.get('/api/project_files',params={'project_id':b['id'],'task_id':spec.id})).status_code==403
        assert (await client.post('/api/project_arrange',json={'project_id':b['id'],'kind':'research','object_id':one['research_id'],'expected_revision':0})).status_code==409
        folder=(await client.post('/api/project_folder',json={'project_id':a['id'],'name':'Filed','id':'api-folder'})).json()
        assert folder['name']=='Filed'
        moved=await client.post('/api/project_arrange',json={'project_id':a['id'],'kind':'research','object_id':one['research_id'],'expected_revision':0,'folder_id':folder['id'],'title':'Renamed'})
        assert moved.status_code==200
        assert (await client.post('/api/project_arrange',json={'project_id':a['id'],'kind':'research','object_id':one['research_id'],'expected_revision':0})).status_code==409
        assert (await client.post('/api/project_folder',json={'project_id':a['id'],'name':'Filed'})).status_code==409
        revoked=await client.post('/api/scope',json={'research_id':one['research_id'],'operation':'revoke','ref':ref})
        assert revoked.status_code==200
        assert (await client.get('/api/project_files',params={'project_id':a['id'],'task_id':spec.id})).json()==[]

@pytest.mark.asyncio
async def test_running_task_cannot_be_rewritten(workspace):
    _,scopes,research,tasks,projects=workspace
    p=projects.create('A')
    main=await projects.create_main_task(p.id,'Main')
    root=research.research(main.research_id)
    scope=scopes.current(root.scope_id,research.owner)
    _,ref=scopes.ingest(scope,content=b'input',name='input.txt',media_type='text/plain')
    spec=tasks.create(research_id=root.id,kind='synthesize',user_request='Original',required_refs=(ref,))
    tasks.active[spec.id]=object()
    with pytest.raises(Conflict): projects.rerun_spec(spec.id,1,'Changed')
    assert tasks.spec(spec.id).version==1
    tasks.active.pop(spec.id)
    with pytest.raises(Conflict): tasks.create(research_id=root.id,kind='synthesize',user_request='Cycle',required_refs=(ref,),task_id=spec.id,parent_task_id=spec.id)

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['revoke','task_version'])
async def test_expert_reply_rechecks_access_and_task_after_model_call(workspace,change):
    from agent_core.contracts import RunResult
    _,scopes,research,tasks,projects=workspace
    project=projects.create('Conversation guard')
    member=await projects.create_main_task(project.id,'Fixed task')
    root=research.research(member.research_id)
    scope=scopes.current(root.scope_id,research.owner)
    scope,ref=scopes.ingest(scope,content=b'Fictional sample only',name='sample.txt',media_type='text/plain')
    spec=tasks.create(research_id=root.id,kind='analyze',user_request='Analyze sample risk',required_refs=(ref,))
    class ChangingEngine:
        async def execute(self,request,agent,tools,invoke):
            assert agent.output_type is None
            assert 'Markdown' in agent.instructions
            if change=='revoke':scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
            else:projects.rerun_spec(spec.id,spec.version,'Changed task')
            return RunResult(run_id=request.run_id,status='succeeded',output='A reply that must not be saved')
    tasks.engine=ChangingEngine()
    with pytest.raises(ScopeDenied if change=='revoke' else Conflict):
        await projects.discuss(spec.id,'What are the risks?',0,message_id='guard-question')
    assert research.collaboration.query('session',spec.expert_session_id).messages == ()
    assert not tasks.query('list_runs',spec.id)
