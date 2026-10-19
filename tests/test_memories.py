import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_tasks import app, create_extract
from agent_core.app.api import create_app
from agent_core.context.scopes import ScopeDenied
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.routing import RouteBlocked
from test_routing import app as route_app, service as routed_service, RouteFixture, task_args


async def test_explicit_preference_and_current_instruction_precedence(app):
    _, _, research, root, _, ref, engine, tasks = app
    memory = tasks.memories.create(kind='preference', content='公司研究先看现金流',
        source='用户明确保存', confirmed=True)
    assert memory in tasks.memories.list(root.id)
    without = tasks.create(
        research_id=root.id, kind='extract', user_request='先看竞争风险', required_refs=(ref,))
    await tasks.run(without.id)
    # Scripted execution freezes the catalog; automatic selection is a controlled
    # LLM operation, exercised in test_memory_side_query rather than this double.
    assert 'approved_memory' not in json.loads(engine.requests[-1].input.messages[0].content)
    snapshot = research.scopes.repository.snapshot(engine.requests[-1].run_id)
    assert memory.id in {r.id for r in snapshot.memory_catalog}
    with_memory = tasks.create(research_id=root.id, kind='extract',
        user_request='本次先看竞争风险，再核对现金流', required_refs=(ref,), memory_ids=(memory.id,))
    await tasks.run(with_memory.id)
    payload = json.loads(engine.requests[-1].input.messages[0].content)
    assert payload['approved_memory'][0]['content'] == '公司研究先看现金流'
    assert payload['approved_memory'][0]['source'] == '用户明确保存'
    assert '本次先看竞争风险' in payload['goal']
    assert 'current goal and user instruction take precedence' in payload['memory_rule']
    assert with_memory.memory_refs[0].version == memory.version


async def test_correction_pause_expiration_scope_and_deletion(app):
    _, _, research, root, _, ref, engine, tasks = app
    from agent_core.composition import build_projects
    projects = build_projects(research.database, research, tasks)
    project = projects.create('Memory project')
    await projects.create_main_task(project.id, root.goal, root.id)
    other = research.create('Other memory scope')
    draft = tasks.memories.create(kind='experience',content='检查来源反证',source='用户认可的复盘',
        scope='project', scope_id=project.id)
    assert draft.status == 'draft'
    with pytest.raises(ScopeDenied): tasks.create(research_id=root.id,kind='extract',
        user_request='检查',required_refs=(ref,),memory_ids=(draft.id,))
    confirmed = tasks.memories.update(draft.id, draft.version, status='confirmed')
    assert confirmed.version == 2
    assert draft.id not in {v.id for v in tasks.memories.list(other.id)}
    with pytest.raises(ScopeDenied): tasks.memories.select(other.id,(draft.id,))
    selected = create_extract(app, memory_ids=(draft.id,))
    corrected = tasks.memories.update(draft.id, confirmed.version, content='先找反证，再检查来源')
    with pytest.raises(ScopeDenied): await tasks.run(selected.id)
    current = create_extract(app, memory_ids=(draft.id,))
    assert current.memory_refs[0].version == corrected.version
    paused = tasks.memories.update(draft.id, corrected.version, status='draft')
    with pytest.raises(ScopeDenied): await tasks.run(current.id)
    with pytest.raises(ScopeDenied): tasks.memories.select(root.id,(draft.id,))
    resumed = tasks.memories.update(draft.id, paused.version, status='confirmed',
        expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    with pytest.raises(ScopeDenied): tasks.memories.select(root.id,(draft.id,))
    active = tasks.memories.update(draft.id, resumed.version,
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    assert tasks.memories.select(root.id,(draft.id,))[0] == active
    tasks.memories.delete(draft.id, active.version)
    assert draft.id not in {v.id for v in tasks.memories.list(root.id)}
    with pytest.raises(ScopeDenied): tasks.memories.select(root.id,(draft.id,))
    no_memory = create_extract(app)
    await tasks.run(no_memory.id)
    assert 'approved_memory' not in json.loads(engine.requests[-1].input.messages[0].content)


async def test_deletion_during_execution_prevents_publication(app):
    _, _, _, root, _, _, engine, tasks = app
    item = tasks.memories.create(kind='preference',content='公司研究先看现金流',source='用户保存',confirmed=True)
    spec = create_extract(app,memory_ids=(item.id,))
    started, release = asyncio.Event(), asyncio.Event()
    original = engine.execute
    async def delayed(*args):
        started.set(); await release.wait(); return await original(*args)
    engine.execute = delayed
    running = asyncio.create_task(tasks.run(spec.id))
    await asyncio.wait_for(started.wait(),5)
    tasks.memories.delete(item.id,item.version)
    release.set()
    outcome = await running
    assert outcome['receipt'].delivery == 'rework'
    assert tasks.ledger.get(outcome['run_id']).result.error.code == 'input_revoked'


async def test_memory_api_create_confirm_update_delete(app):
    *_,root,_,ref,engine,tasks=app
    research=app[2]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(research,task_service=tasks)),base_url='http://test') as client:
        created = await client.post('/api/memory_create',json={'kind':'preference','content':'公司研究先看现金流',
            'source':'用户明确要求','scope':'user','confirmed':True})
        assert created.status_code == 201
        item = created.json()
        listed = await client.get('/api/memories',params={'research_id':root.id})
        assert item['id'] in {v['id'] for v in listed.json()}
        task = await client.post('/api/task_create',json={'research_id':root.id,'kind':'extract','request':'本次先看风险',
            'required_refs':[ref.model_dump(mode='json')],'memory_ids':[item['id']]})
        assert task.status_code == 201
        assert task.json()['memory_refs'] == [{'id':item['id'],'version':1}]
        bad = await client.post('/api/memory_update',json={'id':item['id'],'expected_version':9,'status':'draft'})
        assert bad.status_code == 409
        paused = await client.post('/api/memory_update',json={'id':item['id'],'expected_version':1,'status':'draft'})
        assert paused.status_code == 200
        run = await client.post('/api/task_run',json={'task_id':task.json()['id']})
        assert run.status_code == 403
        deleted = await client.post('/api/memory_delete',json={'id':item['id'],'expected_version':2})
        assert deleted.status_code == 200
        assert item['id'] not in {v['id'] for v in (await client.get('/api/memories',params={'research_id':root.id})).json()}


def test_postgres_memory_recalled_by_fresh_process(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated TEST_DATABASE_URL required')
    db = PostgresDatabase(url)
    blobs = tmp_path / 'blobs'
    try:
        scopes = build_resources(db, blobs)
        research = build_research(db, scopes)
        root = research.create('Stage 9 process recall')
        scope = scopes.current(root.scope_id, 'local')
        _, ref = scopes.ingest(scope, content=b'A 2025 CFO 100 capex 20 million USD',
            name='process-report.txt', media_type='text/plain')
        tasks = build_tasks(db, scopes, research)
        entry = tasks.memories.create(kind='preference', content='公司研究先看现金流',
            source='用户明确保存', confirmed=True)
    finally:
        db.close()
    program = '''import asyncio,json,os,sys
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.composition import build_resources,build_research,build_tasks
from agent_core.resources.snapshots import ResourceRef
db=PostgresDatabase(os.environ['TEST_DATABASE_URL'])
try:
 scopes=build_resources(db,sys.argv[4]);research=build_research(db,scopes)
 engine=ScriptedEngine(lambda request:([],{'report':'cash flow first','missing':[]}))
 tasks=build_tasks(db,scopes,research,engine)
 entry=tasks.memories.select(sys.argv[1],(sys.argv[3],))[0]
 spec=tasks.create(research_id=sys.argv[1],kind='analyze',user_request='本次先看竞争风险',required_refs=(ResourceRef.model_validate_json(sys.argv[2]),),memory_ids=(entry.id,))
 outcome=asyncio.run(tasks.run(spec.id))
 print(json.dumps({'entry':entry.content,'input':json.loads(engine.requests[0].input.messages[0].content),'delivery':outcome['receipt'].delivery},ensure_ascii=False))
finally: db.close()
'''
    result = subprocess.run([sys.executable,'-c',program,root.id,ref.model_dump_json(),entry.id,str(blobs)],
        capture_output=True,text=True,timeout=30,env=dict(os.environ,TEST_DATABASE_URL=url))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload['entry'] == '公司研究先看现金流'
    assert payload['input']['approved_memory'][0]['id'] == entry.id
    assert '本次先看竞争风险' in payload['input']['goal']
    assert payload['delivery'] == 'pending_review'


async def test_routed_task_counts_memory_but_keeps_it_out_of_router_summary(route_app):
    selector = RouteFixture(['model-a'])
    tasks = routed_service(route_app, selector)
    entry = tasks.memories.create(kind='preference',content='公司研究先看现金流',
        source='用户明确保存',confirmed=True)
    spec = await tasks.create_routed(**task_args(route_app,memory_ids=(entry.id,)))
    assert spec.memory_refs[0].id == entry.id
    assert all(entry.content not in prompt for prompt in selector.prompts)
    await tasks.run(spec.id)
    payload = json.loads(route_app[-1].calls[-1][0].input.messages[0].content)
    assert payload['approved_memory'][0]['content'] == entry.content
    long_entry = tasks.memories.create(kind='preference',content='X'*18000,
        source='用户明确保存',confirmed=True)
    from agent_core.context.projection import ContextBudgetExceeded
    with pytest.raises(ContextBudgetExceeded, match='Required complete memories'):
        await tasks.create_routed(**task_args(route_app,memory_ids=(long_entry.id,)))
