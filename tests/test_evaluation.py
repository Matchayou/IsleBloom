import os
import json
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest

from agent_core.adapters.memory_ledger import MemoryDatabase
from agent_core.adapters.postgres import PostgresDatabase
from agent_core.adapters.scripted import ScriptedEngine
from agent_core.app.api import create_app
from legacy_composition import build_research, build_tasks
from agent_core.composition import build_resources, build_evaluation
from agent_core.execution.ports import Conflict
from agent_core.context.scopes import ScopeDenied
from agent_core.routing.models import ModelCandidate, RouteRecord, RouteStep
from agent_core.methods.defaults import published_experts
from agent_core.contracts import RunResult


def answer(unit):
    return {'report':f'Cash flow 100 {unit}',
        'fields':{'company':'A','period':'2025','operating_cash_flow':'100',
                  'capital_expenditure':'20','unit':unit},
        'sources':{'operating_cash_flow':'line 1','capital_expenditure':'line 1','unit':'line 1'}}


@pytest.fixture(params=['memory','postgres'])
def setup(request,tmp_path):
    if request.param == 'postgres':
        url = os.environ.get('TEST_DATABASE_URL')
        if not url: pytest.skip('Dedicated TEST_DATABASE_URL required')
        db = PostgresDatabase(url)
    else: db = MemoryDatabase()
    scopes = build_resources(db,tmp_path/'blobs')
    research = build_research(db,scopes)
    root = research.create('Stage 7 '+str(uuid4()))
    scope = scopes.current(root.scope_id,'local')
    scope,ref = scopes.ingest(scope,content=b'A 2025 CFO 100 capex 20 thousand USD',
        name='source.txt',media_type='text/plain')
    engine = ScriptedEngine(lambda req:([],answer('million USD' if req.agent_id=='expert_extract' else 'thousand USD')))
    tasks = build_tasks(db,scopes,research,engine)
    evaluation = build_evaluation(db,tasks)
    yield db,scopes,research,root,ref,tasks,evaluation
    db.close()


async def result(setup, *, expert=None):
    _,_,_,root,ref,tasks,_ = setup
    spec = tasks.create(research_id=root.id,kind='extract',user_request='Extract cash flow',
        required_refs=(ref,),expert_id=expert)
    return (await tasks.run(spec.id))['receipt']


async def test_feedback_freeze_compare_and_idempotency(setup):
    _,scopes,_,root,_,tasks,evaluation = setup
    before = await result(setup)
    after = await result(setup,expert='expert_extract_financial')
    fid = 'feedback-'+str(uuid4())
    feedback = evaluation.submit(feedback_id=fid,result_id=before.result_id,
        kind='fact_error',description='Unit says million; original says thousand',
        proposed_correction='thousand USD')
    assert feedback.status == 'pending'
    assert feedback.task_id == before.task_id and feedback.run_id == before.run_id
    assert feedback.method_digest == before.method_digest and feedback.model == before.model
    assert evaluation.submit(feedback_id=fid,result_id=before.result_id,kind='fact_error',
        description=feedback.description,proposed_correction='thousand USD') == feedback
    with pytest.raises(Conflict): evaluation.submit(feedback_id=fid,result_id=after.result_id,
        kind='fact_error',description=feedback.description)
    with pytest.raises(Conflict): evaluation.freeze(fid,split='development',dimension='final_delivery')
    checked = evaluation.adjudicate(fid,status='verified',attribution='checker',
        finding='Source line 1 explicitly uses thousand USD; program presence check passed incorrectly',
        reviewer='independent-review',acceptance_number=4)
    assert checked.status == 'verified'
    case = evaluation.freeze(fid,split='development',dimension='checker')
    assert case.input_digest and case.output_digest and case.label != feedback.proposed_correction
    assert evaluation.freeze(fid,split='development',dimension='checker') == case
    with pytest.raises(Conflict): evaluation.freeze(fid,split='holdout',dimension='checker')
    contradictory = evaluation.submit(feedback_id='contradictory-'+str(uuid4()),
        result_id=before.result_id,kind='verified_example',description='Incorrect positive control')
    evaluation.adjudicate(contradictory.id,status='verified',attribution='reference',
        finding='Contradictory reviewer claim',reviewer='fixture-review')
    with pytest.raises(Conflict): evaluation.freeze(contradictory.id,
        split='development',dimension='final_delivery')
    other_feedback = evaluation.submit(feedback_id='same-source-'+str(uuid4()),
        result_id=after.result_id,kind='fact_error',description='Separate test label')
    evaluation.adjudicate(other_feedback.id,status='verified',attribution='model',
        finding='Control label only',reviewer='judge')
    with pytest.raises(Conflict): evaluation.freeze(other_feedback.id,
        split='holdout',dimension='model_choice')
    scope = scopes.current(root.scope_id,'local')
    _,duplicate_ref = scopes.ingest(scope,content=b'A 2025 CFO 100 capex 20 thousand USD',
        name='duplicate.txt',media_type='text/plain')
    duplicate_spec = tasks.create(research_id=root.id,kind='extract',
        user_request='Extract duplicate',required_refs=(duplicate_ref,))
    duplicate_receipt = (await tasks.run(duplicate_spec.id))['receipt']
    duplicate_feedback = evaluation.submit(feedback_id='duplicate-source-'+str(uuid4()),
        result_id=duplicate_receipt.result_id,kind='fact_error',description='Wrong unit again')
    evaluation.adjudicate(duplicate_feedback.id,status='verified',attribution='model',
        finding='Duplicate source also says thousand USD',reviewer='independent')
    with pytest.raises(Conflict): evaluation.freeze(duplicate_feedback.id,
        split='holdout',dimension='model_choice')
    eid = 'experiment-'+str(uuid4())
    experiment = evaluation.create_experiment(experiment_id=eid,case_ids=(case.id,),
        arms=('before','after'),quality_floor=1,max_total_cost_usd=.02,min_quality_gain=1)
    assert experiment.frozen_digest
    assert evaluation.compare(eid)['verdict'] == 'incomplete'
    evaluation.record_trial(trial_id='trial-'+str(uuid4()),experiment_id=eid,case_id=case.id,
        result_id=before.result_id,arm='before',score='failed',reviewer='judge',
        evidence='Unit differs from source line 1',cost_usd=.002,cost_basis='estimated')
    assert evaluation.compare(eid)['verdict'] == 'incomplete'
    with pytest.raises(ValueError): evaluation.record_trial(trial_id='unsupported-bill',
        experiment_id=eid,case_id=case.id,result_id=after.result_id,arm='after',
        score='passed',reviewer='judge',evidence='Unit matches',
        cost_usd=.003,cost_basis='measured')
    evaluation.record_trial(trial_id='trial-'+str(uuid4()),experiment_id=eid,case_id=case.id,
        result_id=after.result_id,arm='after',score='passed',reviewer='judge',
        evidence='Unit matches source line 1',cost_usd=.003,cost_basis='estimated')
    comparison = evaluation.compare(eid)
    assert comparison['verdict'] == 'provisional_estimated_cost'
    assert comparison['metrics']['before']['quality'] == 0
    assert comparison['metrics']['after']['quality'] == 1
    assert comparison['online_history_enabled'] is False
    positive = evaluation.submit(feedback_id='positive-'+str(uuid4()),result_id=after.result_id,
        kind='verified_example',description='The unit matches source line 1')
    evaluation.adjudicate(positive.id,status='verified',attribution='reference',
        finding='Source line 1 says thousand USD and output matches',reviewer='independent-review')
    assert evaluation.freeze(positive.id,split='development',dimension='final_delivery').original_assessment == 'correct'
    with pytest.raises(Conflict): evaluation.record_trial(trial_id='other',experiment_id=eid,
        case_id=case.id,result_id=after.result_id,arm='after',score='passed',
        reviewer='judge',evidence='duplicate')


async def test_preference_unverified_and_permission(setup):
    _,scopes,research,root,ref,_,evaluation = setup
    receipt = await result(setup)
    preference = evaluation.submit(feedback_id='pref-'+str(uuid4()),result_id=receipt.result_id,
        kind='preference',description='Use a shorter summary')
    evaluation.adjudicate(preference.id,status='verified',attribution=None,
        finding='Style preference recorded',reviewer='local-user')
    with pytest.raises(Conflict): evaluation.freeze(preference.id,split='development',dimension='final_delivery')
    error = evaluation.submit(feedback_id='fact-'+str(uuid4()),result_id=receipt.result_id,
        kind='fact_error',description='Unit wrong')
    with pytest.raises(Conflict): evaluation.freeze(error.id,split='development',dimension='final_delivery')
    scope = scopes.current(root.scope_id,'local')
    scopes.revoke(scope,ref)
    with pytest.raises(ScopeDenied): evaluation.feedback(error.id)
    with pytest.raises(ScopeDenied): evaluation.submit(feedback_id='later',result_id=receipt.result_id,
        kind='fact_error',description='Hidden source')
    assert evaluation.list_feedback(root.id) == []


async def test_api_submission_and_status(setup):
    _,_,research,root,_,tasks,evaluation = setup
    receipt = await result(setup)
    app = create_app(research,task_service=tasks,evaluation=evaluation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        payload={'feedback_id':'api-'+str(uuid4()),'result_id':receipt.result_id,
            'kind':'fact_error','description':'Unit incorrect'}
        created = await client.post('/api/feedback',json=payload)
        assert created.status_code == 201
        assert (await client.get('/api/feedback',params={'id':payload['feedback_id']})).json()['task_id'] == receipt.task_id
        assert len((await client.get('/api/feedbacks',params={'research_id':root.id})).json()) == 1
        assert (await client.post('/api/evaluation_case',json={'feedback_id':payload['feedback_id'],
            'split':'development','dimension':'checker'})).status_code == 409


async def test_postgres_cross_process_feedback_query(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url: pytest.skip('Dedicated TEST_DATABASE_URL required')
    db = PostgresDatabase(url)
    try:
        scopes = build_resources(db,tmp_path/'blobs')
        research = build_research(db,scopes)
        root = research.create('Stage 7 restart '+str(uuid4()))
        scope = scopes.current(root.scope_id,'local')
        _,ref = scopes.ingest(scope,content=b'A 2025 CFO 100 capex 20 thousand USD',
            name='source.txt',media_type='text/plain')
        tasks = build_tasks(db,scopes,research,ScriptedEngine(lambda _: ([],answer('million USD'))))
        evaluation = build_evaluation(db,tasks)
        spec = tasks.create(research_id=root.id,kind='extract',user_request='Extract cash flow',required_refs=(ref,))
        receipt = (await tasks.run(spec.id))['receipt']
        feedback = evaluation.submit(feedback_id='restart-'+str(uuid4()),result_id=receipt.result_id,
            kind='fact_error',description='Wrong unit')
    finally: db.close()
    proc = subprocess.run([sys.executable,'-m','agent_core.evaluation_cli','--blobs',str(tmp_path/'blobs'),
        'feedback',feedback.id],env={**os.environ,'DATABASE_URL':url},capture_output=True,text=True)
    assert proc.returncode == 0, proc.stderr
    restored = json.loads(proc.stdout)
    assert restored['run_id'] == receipt.run_id and restored['status'] == 'pending'


async def test_five_arm_route_evidence_is_paired_and_history_is_separate(setup):
    _,scopes,_,root,ref,tasks,evaluation = setup
    original = await result(setup)
    feedback = evaluation.submit(feedback_id='route-case-'+str(uuid4()),
        result_id=original.result_id,kind='fact_error',description='Wrong unit')
    evaluation.adjudicate(feedback.id,status='verified',attribution='model',
        finding='Source says thousand, result says million',reviewer='independent')
    case = evaluation.freeze(feedback.id,split='holdout',dimension='model_choice')
    scope = scopes.current(root.scope_id,'local')
    _,other_ref = scopes.ingest(scope,content=b'B 2025 CFO 200 capex 30 thousand USD',
        name='other.txt',media_type='text/plain')
    historical_spec = tasks.create(research_id=root.id,kind='extract',user_request='Extract B cash flow',
        required_refs=(other_ref,),model='cheap')
    historical_receipt = (await tasks.run(historical_spec.id))['receipt']
    historical_feedback = evaluation.submit(feedback_id='history-'+str(uuid4()),
        result_id=historical_receipt.result_id,kind='fact_error',description='Wrong unit in B')
    evaluation.adjudicate(historical_feedback.id,status='verified',attribution='model',
        finding='Source B says thousand',reviewer='independent')
    history = evaluation.freeze(historical_feedback.id,split='development',dimension='model_choice')
    class ModelEngine:
        async def execute(self, request, agent, tools, invoke):
            unit = 'thousand USD' if agent.model == 'strong' else 'million USD'
            return RunResult(run_id=request.run_id,status='succeeded',output=answer(unit))
    tasks.engine = ModelEngine()
    strong_history_spec = tasks.create(research_id=root.id,kind='extract',
        user_request='Extract B cash flow',required_refs=(other_ref,),model='strong')
    strong_history_receipt = (await tasks.run(strong_history_spec.id))['receipt']
    good_history = evaluation.submit(feedback_id='strong-history-'+str(uuid4()),
        result_id=strong_history_receipt.result_id,kind='verified_example',
        description='Strong model unit matches the source')
    evaluation.adjudicate(good_history.id,status='verified',attribution='reference',
        finding='Source B and output both say thousand USD',reviewer='independent')
    strong_history = evaluation.freeze(good_history.id,split='development',dimension='model_choice')
    experiment = evaluation.create_experiment(experiment_id='route-'+str(uuid4()),
        case_ids=(case.id,),arms=('fixed','rule','routellm','history','routellm_history'),
        candidate_pool=('cheap','strong'),quality_floor=1,max_total_cost_usd=.02,
        min_quality_gain=1)
    expert = next(x for x in published_experts() if x.id == 'expert_extract')
    model = ModelCandidate(id='cheap',description='Test model',supported_kinds=frozenset({'extract'}),
        capability_rank=1,max_input_chars=100000,input_usd_per_million_tokens=1,
        output_usd_per_million_tokens=1,capability_source='fixture',price_source='fixture',
        tools=frozenset({'read_source'}))
    strong = model.model_copy(update={'id':'strong'})
    import uuid
    for arm in experiment.arms:
        task_id = str(uuid.uuid4())
        uses_routellm = arm in ('routellm','routellm_history')
        chosen = strong if arm in ('history','routellm_history') else model
        history_ids = (history.id,strong_history.id) if arm in ('history','routellm_history') else ()
        summary = 'Approved fixture summary'
        if history_ids:
            summary += '\n' + evaluation.history_context(history_ids,experiment.candidate_pool,
                experiment.dimension)
        route = RouteRecord(task_id=task_id,spec_version=1,research_id=root.id,status='selected',
            summary=summary,policy_version='offline-test',catalog_version='fixture-v1',
            router_version='routellm-bert-0.2.0',router_checkpoint='offline-fixture',
            router_threshold=0.5,input_chars=100,budget_usd=1,
            steps=(RouteStep(stage='expert',eligible=(expert.id,),excluded={},source='only_candidate',selected=expert.id),
                RouteStep(stage='model',eligible=('cheap','strong'),excluded={},
                    source='routellm' if uses_routellm else 'user',selected=chosen.id)),
            selected_expert=expert.id,selected_model=chosen.id,expert_config=expert,
            selected_model_config=chosen)
        spec = tasks.create(research_id=root.id,kind='extract',user_request='Extract cash flow',
            required_refs=(ref,),task_id=task_id,expert_id=expert.id,model=chosen.id,route_record=route)
        receipt = (await tasks.run(spec.id))['receipt']
        if arm == 'history':
            with pytest.raises(Conflict): evaluation.record_trial(trial_id='bad-'+str(uuid4()),
                experiment_id=experiment.id,case_id=case.id,result_id=receipt.result_id,
                arm=arm,score='failed',reviewer='judge',evidence='fixture',
                history_case_ids=(case.id,))
        evaluation.record_trial(trial_id='trial-'+str(uuid4()),experiment_id=experiment.id,
            case_id=case.id,result_id=receipt.result_id,arm=arm,
            score='passed' if chosen.id=='strong' else 'failed',reviewer='judge',
            evidence='Fixture control assessment',cost_usd=.001,cost_basis='estimated',
            history_case_ids=history_ids)
    compared = evaluation.compare(experiment.id)
    assert compared['complete'] and compared['verdict'] == 'provisional_estimated_cost'
    assert compared['history_gain']['routellm_history_vs_routellm'] == 1
    assert compared['online_history_enabled'] is False


async def test_route_llm_five_arm_excludes_expert_selection(setup):
    _,_,_,root,_,_,evaluation = setup
    original = await result(setup)
    feedback = evaluation.submit(feedback_id='expert-case-'+str(uuid4()),
        result_id=original.result_id,kind='fact_error',description='Wrong expert output unit')
    evaluation.adjudicate(feedback.id,status='verified',attribution='route',
        finding='Source uses thousand USD',reviewer='independent')
    case = evaluation.freeze(feedback.id,split='holdout',dimension='expert_choice')
    with pytest.raises(ValueError, match='only for model selection'):
        evaluation.create_experiment(experiment_id='expert-exp-'+str(uuid4()),
            case_ids=(case.id,),arms=('fixed','rule','routellm','history','routellm_history'),
            candidate_pool=('expert_extract','expert_extract_financial'),quality_floor=1,
            max_total_cost_usd=.02,min_quality_gain=1)


async def test_handoff_and_final_delivery_have_distinct_cases(setup):
    _,_,_,root,_,tasks,evaluation = setup
    extraction = await result(setup,expert='expert_extract_financial')
    tasks.review(extraction.result_id,{n:('passed',f'Control fixture line {n}') for n in range(1,6)},
        reviewer='fixture-review')
    tasks.engine = ScriptedEngine(lambda _: ([],{'report':'Cash flow 100, with no unit or premise'}))
    first_spec = tasks.create(research_id=root.id,kind='analyze',user_request='Analyze extracted cash flow',
        required_results=(extraction.result_id,))
    before = (await tasks.run(first_spec.id))['receipt']
    assert before.result_ids == (extraction.result_id,)
    feedback = evaluation.submit(feedback_id='handoff-'+str(uuid4()),result_id=before.result_id,
        kind='fact_error',description='Final answer omitted the transferred unit')
    evaluation.adjudicate(feedback.id,status='verified',attribution='handoff',
        finding='Input extraction says thousand USD; analysis omitted the unit',reviewer='fixture-review')
    case = evaluation.freeze(feedback.id,split='development',dimension='handoff')
    tasks.engine = ScriptedEngine(lambda _: ([],{'report':'Cash flow 100 thousand USD, from fixed extraction'}))
    second_spec = tasks.create(research_id=root.id,kind='analyze',user_request='Analyze extracted cash flow',
        required_results=(extraction.result_id,))
    after = (await tasks.run(second_spec.id))['receipt']
    experiment = evaluation.create_experiment(experiment_id=str(uuid4()),case_ids=(case.id,),
        arms=('before','after'),quality_floor=1,max_total_cost_usd=.02,min_quality_gain=1)
    for arm,receipt,score in (('before',before,'failed'),('after',after,'passed')):
        evaluation.record_trial(trial_id=str(uuid4()),experiment_id=experiment.id,case_id=case.id,
            result_id=receipt.result_id,arm=arm,score=score,reviewer='fixture-review',
            evidence='Compared fixed extraction result and analysis report',cost_usd=0,
            cost_basis='estimated')
    assert evaluation.compare(experiment.id)['metrics']['after']['quality'] == 1
    final = evaluation.submit(feedback_id='final-'+str(uuid4()),result_id=before.result_id,
        kind='fact_error',description='Delivered report omitted the unit')
    evaluation.adjudicate(final.id,status='verified',attribution='final_delivery',
        finding='Final report lacks material unit',reviewer='fixture-review')
    assert evaluation.freeze(final.id,split='development',dimension='final_delivery').dimension == 'final_delivery'
