"""Writable workspace resource preview stays within the selected research scope."""
import httpx
import pytest
from agent_core.adapters.memory_ledger import MemoryDatabase
from legacy_composition import build_research
from agent_core.composition import build_resources
from agent_core.app.api import create_app


@pytest.mark.asyncio
async def test_resource_original_requires_current_research_grant(tmp_path):
    db=MemoryDatabase()
    scopes=build_resources(db,tmp_path/'blobs')
    service=build_research(db,scopes)
    app=create_app(service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        denied=await client.post('/api/researches',json={'goal':'Cross origin'},headers={'Origin':'http://elsewhere'})
        assert denied.status_code==403
        first=(await client.post('/api/researches',json={'goal':'Workspace scope A'})).json()
        second=(await client.post('/api/researches',json={'goal':'Workspace scope B'})).json()
        content=b'Phase 2 synthetic source bytes\n'
        response=await client.post(f"/api/upload?research_id={first['id']}&name=control.txt",
                                   content=content,headers={'Content-Type':'text/plain'})
        assert response.status_code==201
        ref=response.json()['ref']
        params={**ref,'research_id':first['id']}
        original=await client.get('/api/resource_original',params=params)
        assert original.status_code==200 and original.content==content
        assert original.headers['cache-control']=='no-store'
        assert original.headers['x-content-type-options']=='nosniff'
        unsafe=await client.post(f"/api/upload?research_id={first['id']}&name=control.html",
                                 content=b'<script>1</script>',headers={'Content-Type':'text/html'})
        assert unsafe.status_code==201
        download=await client.get('/api/resource_original',params={**unsafe.json()['ref'],'research_id':first['id']})
        assert download.status_code==200
        assert download.headers['content-disposition'].startswith('attachment;')
        assert (await client.get('/api/resource_original',params={**ref,'research_id':second['id']})).status_code==403
        revoked=await client.post('/api/scope',json={'research_id':first['id'],'operation':'revoke','ref':ref})
        assert revoked.status_code==200
        assert (await client.get('/api/resource_original',params=params)).status_code==403
    db.close()
