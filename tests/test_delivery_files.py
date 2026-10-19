import hashlib
import json
from uuid import uuid4
import httpx
import pytest
from pydantic import ValidationError
from agent_core.delivery_files import GeneratedFile, files_from_output
from agent_core.research.models import ResearchAnswer, NodeCommand
from agent_core.adapters.output_repair import html_documents
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources, build_research, build_tasks, build_projects
from agent_core.app.api import create_app

HTML = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><h1>成都旅行</h1><script>document.body.dataset.ready="yes"</script></body></html>'

@pytest.mark.parametrize('name', ['../handbook.html', '/tmp/a.html', 'a\\b.html', 'a\n.html', 'a.exe'])
def test_invalid_names(name):
    with pytest.raises(ValidationError): GeneratedFile(name=name, content=HTML)


def test_complete_html_and_unique_names():
    with pytest.raises(ValidationError): GeneratedFile(name='handbook.html', content=HTML[:-7])
    with pytest.raises(ValidationError): ResearchAnswer(report='done', files=[{'name':'a.txt','content':'one'},{'name':'a.txt','content':'two'}])
    files=files_from_output({'report':f'```html\n{HTML}\n```'})
    assert files[0].content == HTML
    assert files[0].metadata()['sha256'] == hashlib.sha256(HTML.encode()).hexdigest()
    assert files_from_output({'report':HTML,'files':[{'name':'handbook.html','content':HTML+'\n'}]})[0].content == HTML+'\n'
    assert len(files_from_output({'report':HTML,'files':[{'name':'handbook.html','content':HTML+'\n'}]})) == 1
    assert html_documents(ResearchAnswer(report='done', files=[{'name':'guide.html','content':HTML}]).model_dump_json()) == (HTML,)
    draft = json.dumps({'report':'done','files':[{'name':'guide.html','content':HTML+'\n'}]})
    assert html_documents('```json\n'+draft+'\n```') == html_documents(draft) == (HTML,)


@pytest.mark.asyncio
async def test_new_generation_publishes_preview_download_and_versions(tmp_path):
    db=MemoryDatabase()
    scopes=build_resources(db,tmp_path/'blobs')
    output={'report':'行程与文件已交付', 'files':[{'name':'成都手册.html','content':HTML},{'name':'budget.csv','content':'item,cost\nfood,200\n'}]}
    engine=ScriptedEngine(lambda request:([],output))
    research=build_research(db,scopes,engine)
    tasks=build_tasks(db,scopes,research,engine)
    projects=build_projects(db,research,tasks)
    project=projects.create('Travel')
    foreign=projects.create('Other')
    member=await projects.create_main_task(project.id,'成都旅行')
    node=research.query('list_nodes',member.research_id)[0]
    root=research.research(member.research_id)
    scope=scopes.current(root.scope_id,research.owner)
    scope,ref=scopes.ingest(scope,content=b'Synthetic input',name='input.txt',media_type='text/plain')
    await research.submit(NodeCommand(command_id=str(uuid4()),research_id=root.id,kind='mount',node_id=node.id,resource_refs=(ref,)))
    await research.submit(NodeCommand(command_id=str(uuid4()),research_id=root.id,kind='run',node_id=node.id))
    first=research.node(node.id).current_result_id
    assert first
    # A new result creates another immutable file version, never overwriting the first.
    output['files'][0]['content']=HTML.replace('成都旅行','新行程')
    await research.submit(NodeCommand(command_id=str(uuid4()),research_id=root.id,kind='continue',node_id=node.id,goal='New version',resource_refs=(ref,)))
    app=create_app(research,task_service=tasks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        rows=(await client.get('/api/project_deliveries',params={'project_id':project.id})).json()
        assert len(rows)==4
        file=next(row for row in rows if row['result_id']==first and row['name']=='成都手册.html')
        params={'project_id':project.id,'result_id':first,'file_id':file['file_id']}
        preview=await client.get('/api/generated_file',params=params)
        assert preview.status_code==200 and preview.content==HTML.encode()
        assert preview.headers['content-type'].startswith('text/html')
        assert "sandbox allow-scripts" in preview.headers['content-security-policy']
        assert "allow-same-origin" not in preview.headers['content-security-policy']
        download=await client.get('/api/generated_file',params={**params,'download':'1'})
        assert download.content==preview.content
        assert download.headers['content-disposition'].startswith('attachment; filename*=UTF-8\'\'')
        assert preview.headers['cache-control']=='no-store'
        assert (await client.get('/api/generated_file',params={**params,'project_id':foreign.id})).status_code==403
        assert (await client.get('/api/generated_file',params={**params,'file_id':'missing'})).status_code==404
        assert (await client.get('/api/project_deliveries',params={'project_id':foreign.id,'research_id':root.id})).status_code==403
        scopes.revoke(scopes.current(root.scope_id,research.owner),ref)
        assert (await client.get('/api/generated_file',params=params)).status_code==403
        assert (await client.get('/api/generated_file',params={**params,'download':'1'})).status_code==403
        assert (await client.get('/api/project_deliveries',params={'project_id':project.id})).json()==[]
    db.close()


@pytest.mark.asyncio
async def test_incomplete_file_is_not_published(tmp_path):
    db=MemoryDatabase()
    scopes=build_resources(db,tmp_path/'blobs')
    engine=ScriptedEngine(lambda request:([],{'report':'Generated','files':[{'name':'broken.html','content':'<!DOCTYPE html><html><body>cut off'}]}))
    research=build_research(db,scopes,engine)
    tasks=build_tasks(db,scopes,research,engine)
    projects=build_projects(db,research,tasks)
    project=projects.create('Incomplete')
    member=await projects.create_main_task(project.id,'Broken file')
    node=research.query('list_nodes',member.research_id)[0]
    await research.submit(NodeCommand(command_id=str(uuid4()),research_id=member.research_id,kind='run',node_id=node.id))
    assert research.node(node.id).current_result_id is None
    assert projects.deliveries(project.id)==[]
    db.close()
