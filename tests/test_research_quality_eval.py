"""Evaluator calibration and failure accounting; synthetic answers are not agent scores."""
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from evals.research_quality.cases import load_cases, public_request
from evals.research_quality.grade import grade, matches, review_packet, review_template, semantic_checks
from evals.research_quality.report import aggregate, report_batch, usage
from evals.research_quality.run import execute_trial, plan, validate_manifest
from evals.research_quality.storage import digest, read, write
from agent_core.tasks.artifacts import describe


def reference(case):
    return dict(report=case['reference_report'], fields={key: rule.get('number', rule.get('one_of', [None])[0])
                for key, rule in case['expected'].items()}, sources={key: case['sources'][0]['name'] + ': text:1'
                                                                    for key in case['fields']})


def sample(case_id='Q01'):
    case = load_cases()[case_id]
    run_id = 'test-run'
    refs = [dict(resource_id=f'r{i}', snapshot_id=f's{i}', parse_id=f'p{i}') for i, _ in enumerate(case['sources'])]
    output = reference(case)
    receipt = dict(run_id=run_id, result_id=f'task-result:{run_id}', resource_refs=deepcopy(refs),
                   artifact=describe(f'task-result:{run_id}', output).model_dump(mode='json'),
                   adoption='for_discussion', delivery='pending_review')
    evidence = dict(protocol_sha256='protocol', collection_complete=True,
        spec=dict(limits=dict(max_turns=14, max_calls=12)),
        sources=[dict(s, ref=ref) for s, ref in zip(case['sources'], refs)],
        tool_requests=[dict(name='read_source', arguments=dict(ref=ref), response=dict(value=dict(ref=ref))) for ref in refs],
        runs=[dict(run_id=run_id, receipt=receipt, result=dict(run_id=run_id, status='succeeded', output=output),
                   budget=dict(models=2, tools=len(refs)), progress=dict(phase='delivered', finalization_reason='model_completed'),
                   operations=[dict(id=f'm{i}', kind='model', status='confirmed', usage=dict(input_tokens=10, output_tokens=5, complete=True)) for i in range(2)])])
    state = dict(trial_id='t', case_id=case_id, arm='baseline', repeat=0, execution_status='completed', protocol_sha256='protocol')
    seal(state, evidence)
    return case, state, evidence


def seal(state, evidence):
    evidence.pop('evidence_sha256', None)
    evidence['evidence_sha256'] = digest(evidence)
    state['evidence_sha256'] = evidence['evidence_sha256']


def human_review(case, evidence):
    review = review_template(review_packet(case, evidence))
    review['reviewer'] = 'synthetic-test-annotation'
    for item in review['items'].values():
        item.update(status='pass', evidence='Synthetic calibration annotation; not a live semantic judgment')
    return review


@pytest.mark.parametrize('case_id', list(load_cases()))
def test_reference_and_review_gate(case_id):
    case, state, evidence = sample(case_id)
    pending = grade(case, state, evidence)
    assert all(c['status'] == 'pass' for c in pending['artifact_checks'])
    assert pending['artifact_correct'] is None and pending['clean_success'] is None
    assert grade(case, state, evidence, human_review(case, evidence))['clean_success'] is True


@pytest.mark.parametrize('case_id,key,bad', [('Q01', 'quantity', '11'), ('Q01', 'codename', '北斗'),
    ('Q01', 'revenue', '0'), ('Q02', 'numeric_change_pct', '10'), ('Q02', 'a_currency', '美元'),
    ('Q03', 'accepted', '90'), ('Q04', 'difference_pp', '0'), ('Q05', 'quantity', '11'),
    ('Q06', 'inventory', '9'), ('Q07', 'difference', 'NaN'), ('Q08', 'quantity', '999')])
def test_wrong_fields_fail_even_when_execution_and_review_pass(case_id, key, bad):
    case, state, evidence = sample(case_id)
    run = evidence['runs'][0]
    run['result']['output']['fields'][key] = bad
    run['receipt']['artifact'] = describe(run['receipt']['result_id'], run['result']['output']).model_dump(mode='json')
    seal(state, evidence)
    result = grade(case, state, evidence, human_review(case, evidence))
    assert result['artifact_correct'] is False and result['clean_success'] is False


def test_text_contradiction_is_semantic_failure_despite_correct_fields():
    case, state, evidence = sample()
    run = evidence['runs'][0]
    run['result']['output']['report'] = '收入为 7 元，设备 11 台。已审核采用。'
    run['receipt']['artifact'] = describe(run['receipt']['result_id'], run['result']['output']).model_dump(mode='json')
    seal(state, evidence)
    review = human_review(case, evidence)
    review['items']['S01'] = dict(status='fail', evidence='正文第 1 句把 7 当收入；固定登记 v1 明确它是设备数量。')
    result = grade(case, state, evidence, review)
    assert all(c['status'] == 'pass' for c in result['artifact_checks'])
    assert result['artifact_correct'] is False


@pytest.mark.parametrize('change', ['empty', 'no_receipt', 'stale_artifact', 'wrong_version'])
def test_missing_or_stale_saved_result_fails(change):
    case, state, evidence = sample()
    run = evidence['runs'][0]
    if change == 'empty':
        run['result']['output']['report'] = ''
    elif change == 'no_receipt':
        run['receipt'] = None
    elif change == 'wrong_version':
        run['receipt']['resource_refs'][0]['snapshot_id'] = 'other'
    else:
        run['result']['output']['report'] += 'changed after save'
    seal(state, evidence)
    assert grade(case, state, evidence)['artifact_correct'] is False


def test_review_bound_to_exact_evidence_and_rubric():
    case, state, evidence = sample()
    review = human_review(case, evidence)
    review['evidence_sha256'] = 'stale'
    assert grade(case, state, evidence, review)['grading_status'] == 'invalid_review'
    review = human_review(case, evidence)
    review['items']['S01']['evidence'] = ''
    assert semantic_checks(case, evidence, review)[1] == 'invalid_review'
    packet = review_packet(case, evidence)
    assert not {'arm', 'model', 'cost', 'trial_id'} & packet.keys()


def test_failed_stop_can_have_correct_artifact():
    case, state, evidence = sample()
    evidence['runs'][0]['progress']['finalization_reason'] = 'tool_budget'
    seal(state, evidence)
    result = grade(case, state, evidence, human_review(case, evidence))
    assert result['artifact_correct'] is True and result['clean_success'] is False


def test_foreign_read_attempt_and_no_positive_task_cannot_pass():
    case, state, evidence = sample('Q08')
    evidence['tool_requests'].append(dict(name='read_source', arguments=dict(ref={'resource_id': 'B'}), response=None))
    seal(state, evidence)
    assert grade(case, state, evidence, human_review(case, evidence))['clean_success'] is False
    evidence['runs'][0]['result']['output']['fields'] = {}
    run = evidence['runs'][0]
    run['receipt']['artifact'] = describe(run['receipt']['result_id'], run['result']['output']).model_dump(mode='json')
    seal(state, evidence)
    assert grade(case, state, evidence)['artifact_correct'] is False


def test_unknown_usage_and_failed_runs_are_retained():
    case, state, evidence = sample()
    evidence['runs'][0]['operations'][1].update(status='unconfirmed', usage=None)
    seal(state, evidence)
    measured = usage(evidence)
    assert measured['model_calls'] == 2 and measured['known_input_tokens'] == 10
    assert measured['usage_complete'] is False and measured['cost_usd'] is None
    rows = [dict(state, clean_success=True, artifact_correct=True, usage=measured, latency_seconds=2),
            dict(state, repeat=1, execution_status='failed', clean_success=False, artifact_correct=False,
                 usage=usage(None), latency_seconds=3)]
    result = aggregate(rows)['arms']['baseline']
    assert result['planned'] == 2 and result['confirmed_clean_success_rate'] == .5
    assert result['mean_tokens'] is None and result['known_total_tokens'] == 15


def test_plan_is_frozen_balanced_and_contains_no_model_key(tmp_path):
    candidate = tmp_path/'candidate.txt'
    candidate.write_text('A concrete test-only method candidate')
    manifest = plan(tmp_path/'batch', ['Q01', 'Q02'], 3, 'gpt-4.1-mini', 'https://example.invalid/v1', candidate=candidate)
    assert len(manifest['trials']) == 12
    assert manifest['candidate_instructions'] == candidate.read_text()
    assert 'API_KEY' not in json.dumps(manifest)
    validate_manifest(manifest)
    manifest['limits']['max_turns'] += 1
    with pytest.raises(ValueError, match='manifest changed'):
        validate_manifest(manifest)


def test_planned_trials_and_failed_setup_report_without_receipts(tmp_path):
    root = tmp_path/'batch'
    manifest = plan(root, ['Q01'], 2, 'gpt-4.1-mini', 'https://example.invalid/v1')
    summary = report_batch(root)
    assert summary['arms']['baseline']['planned'] == 2
    assert summary['arms']['baseline']['artifact_unresolved'] == 2
    assert (root/'report.md').exists()


async def test_database_setup_failure_is_persisted(tmp_path, monkeypatch):
    root = tmp_path/'batch'
    manifest = plan(root, ['Q01'], 1, 'gpt-4.1-mini', 'https://example.invalid/v1')
    def fail(*args):
        raise RuntimeError('sensitive detail must not be logged')
    monkeypatch.setattr('evals.research_quality.run.create_database', fail)
    state = await execute_trial(root, manifest, manifest['trials'][0], 'unused')
    assert state['execution_status'] == 'failed' and state['failure_type'] == 'RuntimeError'
    assert 'sensitive' not in json.dumps(state)
    summary = report_batch(root)
    assert summary['arms']['baseline']['confirmed_clean_success'] == 0


def test_public_input_excludes_gold():
    for case in load_cases().values():
        text = public_request(case)
        assert case['reference_report'] not in text
        assert 'expected' not in text and 'rubric' not in text


def test_equivalent_entity_labels_and_wrong_entities():
    expected = load_cases()['Q02']['expected']
    assert matches('合并集团口径', expected['a_entity'])
    assert matches('仅海外子公司', expected['b_entity'])
    assert not matches('海外子公司', expected['a_entity'])
    assert not matches('集团合并口径', expected['b_entity'])


def test_changed_manifest_and_swapped_trial_are_rejected_on_report(tmp_path):
    root = tmp_path/'batch'
    manifest = plan(root, ['Q01'], 1, 'gpt-4.1-mini', 'https://example.invalid/v1')
    changed = deepcopy(manifest)
    changed['cases'][0]['expected']['quantity']['number'] = '11'
    write(root/'manifest.json', changed)
    with pytest.raises(ValueError, match='manifest changed'):
        report_batch(root)
    write(root/'manifest.json', manifest)
    path = root/'trials'/manifest['trials'][0]['trial_id']/'trial.json'
    state = read(path)
    state['arm'] = 'candidate'
    write(path, state)
    with pytest.raises(ValueError, match='Trial identity'):
        report_batch(root)


@pytest.mark.parametrize('case_id,arm', [('Q01', 'baseline'), ('Q02', 'baseline'), ('Q08', 'baseline'), ('Q01', 'candidate')])
async def test_real_postgres_controlled_artifact_collection(tmp_path, case_id, arm):
    """Real DB/SDK/tool execution, synthetic Model responses; no network LLM."""
    admin = os.environ.get('QUALITY_TEST_DATABASE_ADMIN_URL')
    if not admin:
        pytest.skip('Dedicated PostgreSQL admin URL required')
    from agents import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText, ResponseFunctionToolCall
    root = tmp_path/'batch'
    candidate = tmp_path/'candidate.txt'
    candidate.write_text('测试候选：核对固定材料，保留所有未知和限制。')
    manifest = plan(root, [case_id], 1, 'gpt-4.1-mini', 'https://example.invalid/v1',
                    candidate=candidate if arm == 'candidate' else None)
    trial = next(t for t in manifest['trials'] if t['arm'] == arm)
    path = root/'trials'/trial['trial_id']
    case = manifest['cases'][0]
    def message(value):
        return [ResponseOutputMessage(id=uuid4().hex, type='message', role='assistant', status='completed',
                content=[ResponseOutputText(type='output_text', text=json.dumps(value), annotations=[])])]
    class Provider:
        def get_model(self, name):
            class Model:
                reads = 0
                async def get_response(self, **kwargs):
                    first = kwargs['input'][0]
                    try:
                        payload = json.loads(first.get('content', ''))
                    except (TypeError, ValueError):
                        payload = {}
                    if isinstance(payload, dict) and 'required_source_ids' in payload:
                        items = list(payload['previous_summary']['items']) if payload['previous_summary'] else []
                        items += [dict(kind='fact', text=s['text'][:70], quote=s['text'][:50], source_id=s['id']) for s in payload['sources']]
                        output = message(dict(covered_ids=payload['required_source_ids'], items=items))
                    else:
                        sources = read(path/'evidence.json')['sources']
                        if self.reads < len(sources):
                            ref = sources[self.reads]['ref']
                            self.reads += 1
                            output = [ResponseFunctionToolCall(type='function_call', name='read_source', call_id=uuid4().hex,
                                                              arguments=json.dumps(dict(ref=ref)))]
                        else:
                            output = message(reference(case))
                    return ModelResponse(output=output, usage=Usage(requests=1, input_tokens=3, output_tokens=4), response_id=None)
            return Model()
    state = await execute_trial(root, manifest, trial, admin, provider=Provider())
    evidence = read(path/'evidence.json')
    assert state['execution_status'] == 'completed', (state, evidence)
    assert evidence['collection_complete'] is True
    assert evidence['spec']['method_version'] == ('eval-candidate' if arm == 'candidate' else manifest['baseline_method_version'])
    assert usage(evidence)['usage_complete'] is True
    result = grade(case, state, evidence, human_review(case, evidence))
    assert result['clean_success'] is True, result
    with pytest.raises(ValueError, match='cannot be run twice'):
        await execute_trial(root, manifest, trial, admin, provider=Provider())


def test_collection_failure_and_malformed_review_stay_unknown():
    case, state, evidence = sample()
    for review in ([], {'items': None}, {'items': {'S01': None}}):
        assert grade(case, state, evidence, review)['grading_status'] == 'invalid_review'
    evidence['collection_complete'] = False
    seal(state, evidence)
    result = grade(case, state, evidence)
    assert result['artifact_correct'] is None and result['clean_success'] is None
    assert result['grading_status'] == 'evidence_incomplete'


async def test_cancelled_attempt_is_not_lost(tmp_path, monkeypatch):
    import asyncio
    root = tmp_path/'batch'
    manifest = plan(root, ['Q01'], 1, 'gpt-4.1-mini', 'https://example.invalid/v1')
    def cancel(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr('evals.research_quality.run.create_database', cancel)
    with pytest.raises(asyncio.CancelledError):
        await execute_trial(root, manifest, manifest['trials'][0], 'unused')
    summary = report_batch(root)
    assert summary['arms']['baseline']['execution_counts'] == {'interrupted': 1}
    assert summary['arms']['baseline']['confirmed_clean_success'] == 0


async def test_model_failure_keeps_unconfirmed_calls(tmp_path):
    admin = os.environ.get('QUALITY_TEST_DATABASE_ADMIN_URL')
    if not admin:
        pytest.skip('Dedicated PostgreSQL admin URL required')
    root = tmp_path/'batch'
    manifest = plan(root, ['Q05'], 1, 'gpt-4.1-mini', 'https://example.invalid/v1')
    trial = manifest['trials'][0]
    class Provider:
        def get_model(self, name):
            class Model:
                async def get_response(self, **kwargs):
                    raise ConnectionError('synthetic provider failure')
            return Model()
    state = await execute_trial(root, manifest, trial, admin, provider=Provider())
    assert state['execution_status'] == 'failed'
    evidence = read(root/'trials'/trial['trial_id']/'evidence.json')
    assert evidence['collection_complete'] is True
    measured = usage(evidence)
    assert measured['model_calls'] == 1 and measured['usage_complete'] is False
    result = report_batch(root)['arms']['baseline']
    assert result['planned'] == 1 and result['confirmed_clean_success'] == 0 and result['mean_tokens'] is None
