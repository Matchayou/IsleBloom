"""Exact artifact checks and version-bound independent semantic review."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from agent_core.tasks.artifacts import describe
from .storage import digest, implementation, manifest_at, read, write


def matches(value, expected):
    if not isinstance(value, str):
        return False
    if 'number' in expected:
        try:
            number = Decimal(value.strip().replace(',', ''))
            return number.is_finite() and number == Decimal(expected['number'])
        except InvalidOperation:
            return False
    return value.strip().casefold() in {v.casefold() for v in expected['one_of']}


def check(name, passed, detail):
    return dict(id=name, status='pass' if passed else 'fail', evidence=detail)


def verified_evidence(evidence):
    return bool(evidence and evidence.get('evidence_sha256') == digest(
        {k: v for k, v in evidence.items() if k != 'evidence_sha256'}))


def saved_output(evidence):
    runs = evidence.get('runs', [])
    if len(runs) != 1:
        return None
    receipt, result = runs[0].get('receipt'), runs[0].get('result')
    if not receipt or not result or result.get('status') != 'succeeded':
        return None
    output = result.get('output')
    if not isinstance(output, dict) or not isinstance(output.get('report'), str) or not output['report'].strip():
        return None
    if not (runs[0]['run_id'] == result['run_id'] == receipt['run_id'] and
            receipt['result_id'] == f"task-result:{runs[0]['run_id']}"):
        return None
    if receipt.get('artifact') != describe(receipt['result_id'], output).model_dump(mode='json'):
        return None
    return output


def review_packet(case, evidence):
    """No arm, model, cost, or trial identity in the reviewer-facing packet."""
    return dict(evidence_sha256=evidence['evidence_sha256'], rubric_sha256=digest(case['rubric']),
        task=case['request'], history=case['history'], correction=case['correction'],
        sources=evidence['sources'], expected=case['expected'], rubric=case['rubric'],
        output=saved_output(evidence), tool_requests=evidence['tool_requests'])


def review_template(packet):
    return dict(evidence_sha256=packet['evidence_sha256'], rubric_sha256=packet['rubric_sha256'],
        reviewer='', items={item['id']: dict(status='unknown', evidence='') for item in packet['rubric']})


def semantic_checks(case, evidence, review):
    pending = [dict(id=item['id'], status='unknown', evidence='Independent review required') for item in case['rubric']]
    if review is None:
        return pending, 'pending'
    if not isinstance(review, dict):
        return pending, 'invalid_review'
    expected_ids = {item['id'] for item in case['rubric']}
    items = review.get('items', {})
    valid = (review.get('evidence_sha256') == evidence.get('evidence_sha256') and
             review.get('rubric_sha256') == digest(case['rubric']) and isinstance(items, dict) and
             set(items) == expected_ids)
    if not valid:
        return pending, 'invalid_review'
    for item in items.values():
        if not isinstance(item, dict) or set(item) != {'status', 'evidence'} or item.get('status') not in ('pass', 'fail', 'unknown'):
            return pending, 'invalid_review'
        if not isinstance(item['evidence'], str):
            return pending, 'invalid_review'
        if item['status'] != 'unknown' and (not item['evidence'].strip() or
                not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()):
            return pending, 'invalid_review'
    rows = [dict(id=key, **items[key]) for key in sorted(items)]
    return rows, 'pending' if any(row['status'] == 'unknown' for row in rows) else 'reviewed'


def grade(case, state, evidence=None, review=None):
    base = dict(trial_id=state['trial_id'], case_id=state['case_id'], arm=state['arm'], repeat=state['repeat'],
                execution_status=state['execution_status'], artifact_correct=None, clean_success=None,
                grading_status='pending', artifact_checks=[], process_checks=[], semantic_checks=[])
    if evidence is None or not verified_evidence(evidence) or state.get('evidence_sha256') != evidence.get('evidence_sha256') or state['protocol_sha256'] != evidence.get('protocol_sha256'):
        base['grading_status'] = 'evidence_unavailable'
        if state['execution_status'] not in ('planned', 'running'):
            base['clean_success'] = False
        return base
    if not evidence.get('collection_complete'):
        base['grading_status'] = 'evidence_incomplete'
        if state['execution_status'] not in ('planned', 'running', 'completed'):
            base['clean_success'] = False
        return base
    output = saved_output(evidence)
    artifact = [check('saved_final_artifact', output is not None, 'Receipt identity and full saved output hash')]
    if output is not None:
        fields = output.get('fields', {})
        artifact.extend(check('field:' + key, matches(fields.get(key), expected), repr(fields.get(key)))
                        for key, expected in case['expected'].items())
        receipt = evidence['runs'][0]['receipt']
        required = {digest(s['ref']) for s in evidence['sources']}
        artifact.append(check('source_versions', required == {digest(r) for r in receipt['resource_refs']},
                              'Receipt references must match frozen ingested versions'))
        artifact.append(check('unadopted', receipt.get('adoption') == 'for_discussion' and
                              receipt['delivery'] in ('pending_review', 'partial_pending_review'),
                              'Evaluation cannot approve or adopt a product result'))
    else:
        required = {digest(s['ref']) for s in evidence['sources']}
    reads = [r for r in evidence['tool_requests'] if r['name'] in ('read_source', 'search_source')]
    returned = {digest(r['response']['value']['ref']) for r in reads if r['name'] == 'read_source'
                and isinstance(r.get('response'), dict) and isinstance(r['response'].get('value'), dict)
                and 'ref' in r['response']['value']}
    attempted = {digest(r['arguments']['ref']) for r in reads
                 if isinstance(r.get('arguments'), dict) and 'ref' in r['arguments']}
    process = [check('collection_complete', evidence.get('collection_complete') is True, 'Full ledger and transcript collection'),
               check('required_sources_read', bool(required) and required <= returned, 'Confirmed read_source responses'),
               check('authorized_source_requests', not (attempted-required), 'Foreign references requested: ' + str(len(attempted-required))),
               check('no_web_calls', not any(r['name'] in ('search_web', 'fetch_web') for r in evidence['tool_requests']),
                     'Fixed local-source task')]
    for run in evidence['runs']:
        result, budget = run.get('result'), run.get('budget')
        process.append(check('run_completed:' + run['run_id'], bool(result) and result['status'] == 'succeeded' and not result.get('error'),
                             (result or {}).get('error') or 'Normal completion'))
        limits = evidence['spec']['limits']
        process.append(check('budget:' + run['run_id'], bool(budget) and budget['models'] <= limits['max_turns'] and
                             budget['tools'] <= limits['max_calls'], 'Persistent call limits'))
        progress = run.get('progress') or {}
        process.append(check('no_forced_stop:' + run['run_id'], progress.get('phase') == 'delivered' and
                             progress.get('finalization_reason') == 'model_completed',
                             progress.get('finalization_reason', 'No completion evidence')))
    semantic, status = semantic_checks(case, evidence, review)
    if any(item['status'] == 'fail' for item in artifact + semantic):
        correct = False
    elif any(item['status'] == 'unknown' for item in semantic):
        correct = None
    else:
        correct = True
    clean = False if state['execution_status'] != 'completed' or any(item['status'] == 'fail' for item in process) else correct
    return dict(base, artifact_correct=correct, clean_success=clean, grading_status=status,
                artifact_checks=artifact, process_checks=process, semantic_checks=semantic)


def grade_batch(root):
    root = Path(root)
    manifest = manifest_at(root)
    current = implementation()
    grader_files = ['evals/research_quality/grade.py', 'evals/research_quality/report.py',
                    'evals/research_quality/storage.py', 'src/agent_core/tasks/artifacts.py',
                    'src/agent_core/context/projection.py']
    grader_hashes = {name: current[name] for name in grader_files}
    original_grader = {name: manifest['implementation'][name] for name in grader_files}
    cases = {c['id']: c for c in manifest['cases']}
    grades = []
    for trial in manifest['trials']:
        path = root/'trials'/trial['trial_id']
        state = read(path/'trial.json')
        if any(state.get(key) != value for key, value in trial.items()) or state.get('protocol_sha256') != manifest['protocol_sha256']:
            raise ValueError('Trial identity differs from frozen manifest')
        evidence = read(path/'evidence.json') if (path/'evidence.json').exists() else None
        if evidence and verified_evidence(evidence):
            packet = review_packet(cases[trial['case_id']], evidence)
            write(path/'review-packet.json', packet)
            if not (path/'review.json').exists():
                write(path/'review.json', review_template(packet))
        try:
            review = read(path/'review.json') if (path/'review.json').exists() else None
        except (json.JSONDecodeError, UnicodeError):
            review = {'invalid': True}
        result = grade(cases[trial['case_id']], state, evidence, review)
        result.update(grader_sha256=digest(grader_hashes), rubric_sha256=digest(cases[trial['case_id']]['rubric']),
                      grader_matches_run_manifest=grader_hashes == original_grader)
        write(path/'grade.json', result)
        grades.append(result)
    return grades
