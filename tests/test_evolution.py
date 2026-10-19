"""Stage 10 control fixtures. Reviewer names and costs here are synthetic contract inputs."""
import json
from uuid import uuid4
import os
import subprocess
import sys
import pytest
import httpx
from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_evaluation, build_evolution
from agent_core.contracts import RunResult, Usage
from agent_core.execution.ports import Conflict
from agent_core.methods.defaults import METHODS
from agent_core.app.api import create_app


class EvolutionFixtureEngine:
    def __init__(self): self.calls=[];self.bad_candidate=False;self.extra_generation_field=False
    async def execute(self, request, spec, tools, invoke):
        self.calls.append((request,spec))
        if request.agent_id == 'method_generator':
            draft={
                'instructions': METHODS['extract'][1] + (' BAD_UNIT_RULE: always say million USD.' if self.bad_candidate else
                    ' STAGE10_UNIT_CHECK: match unit to the exact source line before emitting.'),
                'examples': []}
            if self.extra_generation_field: draft['tools']=['unrestricted_tool']
            return RunResult(run_id=request.run_id,status='succeeded',output=draft,
                usage=Usage(requests=1,input_tokens=20,output_tokens=20))
        payload=json.loads(request.input.messages[0].content)
        if request.agent_id == 'expert_analyze':
            unit=payload['results'][0]['output']['fields']['unit']
            output={'report':f'Downstream calculation uses {unit}', 'fields':{},'sources':{}}
        else:
            unit='thousand USD' if 'STAGE10_UNIT_CHECK' in spec.instructions else 'million USD'
            output={'report':f'Cash flow 100 {unit}',
                'fields':{'company':'A','period':'2025','operating_cash_flow':'100',
                          'capital_expenditure':'20','unit':unit},
                'sources':{'operating_cash_flow':'line 1','capital_expenditure':'line 1','unit':'line 1'}}
        return RunResult(run_id=request.run_id,status='succeeded',output=output,
            usage=Usage(requests=1,input_tokens=30,output_tokens=20))


@pytest.fixture(params=['memory','postgres'])
async def system(request,tmp_path):
    if request.param=='postgres':
        if not os.environ.get('TEST_DATABASE_URL'): pytest.skip('Dedicated TEST_DATABASE_URL required')
        db=PostgresDatabase(os.environ['TEST_DATABASE_URL'])
    else: db=MemoryDatabase()
    scopes=build_resources(db,tmp_path/'blobs')
    research=build_research(db,scopes); root=research.create('Stage 10 fixture')
    engine=EvolutionFixtureEngine(); tasks=build_tasks(db,scopes,research,engine)
    evaluation=build_evaluation(db,tasks); evolution=build_evolution(db,tasks,evaluation)
    yield db,scopes,research,root,engine,tasks,evaluation,evolution
    db.close()


async def frozen_error(system, name, split):
    _,scopes,_,root,_,tasks,evaluation,_=system
    scope=scopes.current(root.scope_id,'local')
    _,ref=scopes.ingest(scope,content=f'{name} 2025 CFO 100 capex 20 thousand USD'.encode(),
        name=f'{name}.txt',media_type='text/plain')
    spec=tasks.create(research_id=root.id,kind='extract',user_request=f'Extract {name}',required_refs=(ref,))
    receipt=(await tasks.run(spec.id))['receipt']
    feedback=evaluation.submit(feedback_id=str(uuid4()),result_id=receipt.result_id,
        kind='fact_error',description='Model output says million; source says thousand')
    evaluation.adjudicate(feedback.id,status='verified',attribution='method',
        finding='The source line says thousand USD; output incorrectly says million USD',reviewer='fixture-review')
    case=evaluation.freeze(feedback.id,split=split,dimension='final_delivery')
    return case,receipt


async def test_isolated_unit_candidate_fixed_holdout_handoff_and_rollback(system):
    _,_,_,root,engine,tasks,evaluation,evolution=system
    dev,_=await frozen_error(system,'Development A','development')
    holdout,old_receipt=await frozen_error(system,'Holdout B','holdout')
    candidate=await evolution.propose(candidate_id='unit-'+str(uuid4()),expert_id='expert_extract',
        development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),approved_lesson='Check the unit at its source location before output.',
        lesson_reviewer='fixture-review',generator_model='fixture-model',quality_floor=1,
        min_quality_gain=1,max_total_cost_usd=.1)
    assert tasks.methods.query('head','method','expert_extract')==candidate.base_version
    assert candidate.version not in tasks.methods.method('expert_extract')[0].version
    assert holdout.id not in candidate.generator_prompt
    assert 'Holdout B' not in candidate.generator_prompt
    with pytest.raises(Conflict): tasks.methods.publish('method','expert_extract',candidate.version,
        candidate.base_version,'fixture','bypass')
    with pytest.raises(Conflict): evolution.publish(candidate.id,expected=candidate.base_version,
        user_actor='fixture-user',confirmation=f'Publish expert_extract/{candidate.version}')
    # Prepare a frozen downstream task that consumes the exact holdout extraction result.
    tasks.review(old_receipt.result_id,{n:('passed','Fixture handoff only') for n in range(1,6)},reviewer='fixture-review')
    downstream=tasks.create(research_id=root.id,kind='analyze',user_request='Analyze cash flow',
        required_results=(old_receipt.result_id,))
    down_receipt=(await tasks.run(downstream.id))['receipt']
    fb=evaluation.submit(feedback_id=str(uuid4()),result_id=down_receipt.result_id,
        kind='verified_example',description='Frozen downstream contract fixture')
    evaluation.adjudicate(fb.id,status='verified',attribution='reference',
        finding='Downstream output is parseable',reviewer='fixture-review')
    down_case=evaluation.freeze(fb.id,split='holdout',dimension='handoff')
    for case in (dev,holdout):
        for arm in ('before','after'):
            preview=await evolution.run_preview(preview_id=f'{case.id}-{arm}',candidate_id=candidate.id,
                case_id=case.id,arm=arm)
            assert preview.input_digest==case.input_digest
            evolution.assess(preview_id=preview.id,passed=arm=='after',scope_passed=True,
                reviewer='fixture-review',evidence='Compared unit with the source line',
                cost_usd=.001,cost_basis='measured',cost_evidence='Synthetic fixture usage')
    after=evolution.preview(f'{holdout.id}-after')
    connected=await evolution.run_preview(preview_id='down-'+str(uuid4()),candidate_id=candidate.id,
        case_id=down_case.id,arm='downstream',upstream_preview_id=after.id)
    assert 'thousand USD' in connected.output['report']
    evolution.assess(preview_id=connected.id,passed=True,scope_passed=True,
        reviewer='fixture-review',evidence='Downstream accepted new unit',
        cost_usd=.001,cost_basis='measured',cost_evidence='Synthetic fixture usage')
    report=evolution.compare(candidate.id)
    assert report['verdict']=='passed' and report['metrics']['holdout']['gain']==1
    with pytest.raises(Conflict): evolution.publish(candidate.id,expected='stale',
        user_actor='fixture-user',confirmation=f'Publish expert_extract/{candidate.version}')
    with pytest.raises(Conflict): evolution.publish(candidate.id,expected=candidate.base_version,
        user_actor='fixture-user',confirmation='wrong version')
    evolution.publish(candidate.id,expected=candidate.base_version,user_actor='fixture-user',
        confirmation=f'Publish expert_extract/{candidate.version}')
    assert tasks.methods.query('head','method','expert_extract')==candidate.version
    assert tasks.spec(dev.task_id,dev.spec_version).method_version==candidate.base_version
    evolution.rollback(candidate.id,expected=candidate.version,user_actor='fixture-user',reason='Fixture rollback')
    assert tasks.methods.query('head','method','expert_extract')==candidate.base_version
    assert tasks.methods.resolve('method','expert_extract',candidate.version)


async def test_failed_candidate_is_retained_and_holdout_never_prompts_generator(system):
    _,_,_,root,engine,tasks,evaluation,evolution=system
    dev,_=await frozen_error(system,'Development C','development')
    holdout,old_receipt=await frozen_error(system,'Holdout D','holdout')
    engine.bad_candidate=True
    candidate=await evolution.propose(candidate_id='failed-'+str(uuid4()),expert_id='expert_extract',
        development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),approved_lesson='Locate the unit.',lesson_reviewer='fixture-review',
        generator_model='fixture-model',quality_floor=1,min_quality_gain=1,max_total_cost_usd=.01)
    generator_call=next(req for req,spec in engine.calls if req.agent_id=='method_generator')
    assert 'Holdout D' not in generator_call.input.messages[0].content
    assert holdout.id not in generator_call.input.messages[0].content
    tasks.review(old_receipt.result_id,{n:('passed','Fixture handoff only') for n in range(1,6)},reviewer='fixture-review')
    downstream=tasks.create(research_id=root.id,kind='analyze',user_request='Analyze cash flow',
        required_results=(old_receipt.result_id,))
    down_receipt=(await tasks.run(downstream.id))['receipt']
    fb=evaluation.submit(feedback_id=str(uuid4()),result_id=down_receipt.result_id,
        kind='verified_example',description='Frozen downstream fixture')
    evaluation.adjudicate(fb.id,status='verified',attribution='reference',
        finding='Parseable downstream fixture',reviewer='fixture-review')
    down_case=evaluation.freeze(fb.id,split='holdout',dimension='handoff')
    for case in (dev,holdout):
        for arm in ('before','after'):
            preview=await evolution.run_preview(preview_id=f'{arm}-{uuid4()}',candidate_id=candidate.id,
                case_id=case.id,arm=arm)
            evolution.assess(preview_id=preview.id,passed=False,scope_passed=True,
                reviewer='fixture-review',evidence='Unit remains wrong',cost_usd=.001,cost_basis='measured',
                cost_evidence='Synthetic fixture usage')
            if case.id==holdout.id and arm=='after': after=preview
    connected=await evolution.run_preview(preview_id='bad-down-'+str(uuid4()),candidate_id=candidate.id,
        case_id=down_case.id,arm='downstream',upstream_preview_id=after.id)
    evolution.assess(preview_id=connected.id,passed=False,scope_passed=True,
        reviewer='fixture-review',evidence='Wrong unit reaches analysis',cost_usd=.001,
        cost_basis='measured',cost_evidence='Synthetic fixture usage')
    assert evolution.compare(candidate.id)['verdict']=='failed'
    with pytest.raises(Conflict): evolution.publish(candidate.id,expected=candidate.base_version,
        user_actor='fixture-user',confirmation=f'Publish expert_extract/{candidate.version}')
    assert evolution.candidate(candidate.id)==candidate
    assert tasks.methods.query('head','method','expert_extract')==candidate.base_version


@pytest.mark.parametrize('system', ['postgres'], indirect=True)
async def test_postgres_candidate_survives_process_restart(system):
    db,_,_,_,_,_,_,evolution=system
    dev,_=await frozen_error(system,'Development E','development')
    holdout,_=await frozen_error(system,'Holdout F','holdout')
    candidate=await evolution.propose(candidate_id='persist-'+str(uuid4()),expert_id='expert_extract',
        development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),
        approved_lesson='Verify the source unit.',lesson_reviewer='fixture-review',
        generator_model='fixture-model',quality_floor=1,min_quality_gain=0,max_total_cost_usd=.1)
    db.close()
    code="""import os,sys,psycopg
with psycopg.connect(os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')) as c:
    with c.cursor() as cur:
        cur.execute('select payload from evolution_records where id=%s',('Candidate:'+sys.argv[1],))
        row=cur.fetchone()
        assert row and row[0]['value']['version']==sys.argv[2]
        cur.execute('select version from method_asset_heads where category=%s and asset_id=%s',('method','expert_extract'))
        assert cur.fetchone()[0]!=sys.argv[2]
"""
    result=subprocess.run([sys.executable,'-c',code,candidate.id,candidate.version],
        capture_output=True,text=True,env=os.environ.copy(),check=False)
    assert result.returncode==0,result.stderr


async def test_evolution_http_visibility_and_mutation_guard(system):
    _,_,research,root,_,tasks,evaluation,evolution=system
    dev,_=await frozen_error(system,'Development G','development')
    holdout,_=await frozen_error(system,'Holdout H','holdout')
    candidate=await evolution.propose(candidate_id='http-'+str(uuid4()),expert_id='expert_extract',
        development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),
        approved_lesson='Check each unit.',lesson_reviewer='fixture-review',
        generator_model='fixture-model',quality_floor=1,min_quality_gain=1,max_total_cost_usd=.1)
    app=create_app(research,task_service=tasks,evaluation=evaluation,evolution=evolution)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        listed=await client.get('/api/evolution_candidates',params={'research_id':root.id})
        assert listed.status_code==200 and any(c['id']==candidate.id for c in listed.json())
        compared=await client.get('/api/evolution_compare',params={'candidate_id':candidate.id})
        assert compared.json()['verdict']=='incomplete'
        direct=await client.post('/api/asset_publish',json=dict(category='method',asset_id='expert_extract',
            version=candidate.version,expected=candidate.base_version,actor='fixture',reason='bypass',
            reviewed_candidate_id=candidate.id))
        assert direct.status_code==409
        foreign=await client.post('/api/evolution_publish',json=dict(candidate_id=candidate.id,
            expected=candidate.base_version,user_actor='fixture-user',
            confirmation=f'Publish expert_extract/{candidate.version}'),headers={'origin':'http://foreign'})
        assert foreign.status_code==403
        assert (await client.get('/api/evolution_publish')).status_code==405


async def test_estimated_cost_blocks_otherwise_passing_candidate(system):
    _,_,_,root,_,tasks,evaluation,evolution=system
    dev,_=await frozen_error(system,'Development I','development')
    holdout,old_receipt=await frozen_error(system,'Holdout J','holdout')
    candidate=await evolution.propose(candidate_id='cost-'+str(uuid4()),expert_id='expert_extract',
        development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),
        approved_lesson='Verify the exact unit.',lesson_reviewer='fixture-review',
        generator_model='fixture-model',quality_floor=1,min_quality_gain=1,max_total_cost_usd=.1)
    tasks.review(old_receipt.result_id,{n:('passed','Fixture handoff only') for n in range(1,6)},reviewer='fixture-review')
    downstream=tasks.create(research_id=root.id,kind='analyze',user_request='Analyze cash flow',
        required_results=(old_receipt.result_id,))
    down_receipt=(await tasks.run(downstream.id))['receipt']
    fb=evaluation.submit(feedback_id=str(uuid4()),result_id=down_receipt.result_id,
        kind='verified_example',description='Frozen downstream fixture')
    evaluation.adjudicate(fb.id,status='verified',attribution='reference',
        finding='Parseable downstream fixture',reviewer='fixture-review')
    down_case=evaluation.freeze(fb.id,split='holdout',dimension='handoff')
    for case in (dev,holdout):
        for arm in ('before','after'):
            preview=await evolution.run_preview(preview_id=str(uuid4()),candidate_id=candidate.id,
                case_id=case.id,arm=arm)
            evolution.assess(preview_id=preview.id,passed=arm=='after',scope_passed=True,
                reviewer='fixture-review',evidence='Unit compared with source',cost_usd=.001,
                cost_basis='estimated' if case.id==holdout.id and arm=='after' else 'measured',
                cost_evidence='Synthetic fixture pricing')
            if case.id==holdout.id and arm=='after': after=preview
    connected=await evolution.run_preview(preview_id=str(uuid4()),candidate_id=candidate.id,
        case_id=down_case.id,arm='downstream',upstream_preview_id=after.id)
    evolution.assess(preview_id=connected.id,passed=True,scope_passed=True,
        reviewer='fixture-review',evidence='Downstream accepts unit',cost_usd=.001,
        cost_basis='measured',cost_evidence='Synthetic fixture pricing')
    report=evolution.compare(candidate.id)
    assert report['verdict']=='failed'
    assert report['metrics']['integration_passed'] and report['metrics']['scope_passed']
    assert not report['metrics']['cost_passed']
    with pytest.raises(Conflict): evolution.publish(candidate.id,expected=candidate.base_version,
        user_actor='fixture-user',confirmation=f'Publish expert_extract/{candidate.version}')


async def test_generator_cannot_expand_tool_or_code_surface(system):
    _,_,_,_,engine,tasks,_,evolution=system
    dev,_=await frozen_error(system,'Development K','development')
    holdout,_=await frozen_error(system,'Holdout L','holdout')
    engine.extra_generation_field=True
    with pytest.raises(Conflict,match='output_contract'):
        await evolution.propose(candidate_id='invalid-'+str(uuid4()),expert_id='expert_extract',
            development_case_ids=(dev.id,),holdout_case_ids=(holdout.id,),
            approved_lesson='Check units.',lesson_reviewer='fixture-review',generator_model='fixture-model',
            quality_floor=1,min_quality_gain=0,max_total_cost_usd=.1)
    assert tasks.methods.query('head','method','expert_extract')=='extract-v3'
