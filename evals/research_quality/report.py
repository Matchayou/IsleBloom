"""All planned trials stay in the denominator; missing usage is never zero cost."""
from collections import Counter
from pathlib import Path
from .grade import grade_batch, verified_evidence
from .storage import read, write


def usage(evidence):
    result = dict(known_input_tokens=0, known_output_tokens=0, model_calls=0,
                  tool_calls=0, known_model_calls=0, known_tool_calls=0,
                  calls_complete=False, usage_complete=False, cost_usd=None)
    if not evidence or not verified_evidence(evidence):
        result['model_calls'] = result['tool_calls'] = None
        return result
    complete = evidence.get('collection_complete') is True
    calls_complete = complete
    seen = set()
    for run in evidence['runs']:
        if run['run_id'] in seen:
            raise ValueError('Duplicate run in evidence')
        seen.add(run['run_id'])
        budget = run.get('budget')
        if budget is None:
            complete = calls_complete = False
            continue
        result['known_model_calls'] += budget['models']
        result['known_tool_calls'] += budget['tools']
        operations = {op['id']: op for op in run['operations'] if op['kind'] == 'model'}
        if len(operations) != budget['models']:
            complete = False
        for operation in operations.values():
            value = operation.get('usage') or {}
            result['known_input_tokens'] += value.get('input_tokens', 0)
            result['known_output_tokens'] += value.get('output_tokens', 0)
            complete = complete and operation['status'] == 'confirmed' and value.get('complete') is True
    result['usage_complete'] = bool(complete)
    result['calls_complete'] = bool(calls_complete)
    result['model_calls'] = result['known_model_calls'] if calls_complete else None
    result['tool_calls'] = result['known_tool_calls'] if calls_complete else None
    return result


def aggregate(rows):
    arms = {}
    for arm in sorted({row['arm'] for row in rows}):
        selected = [r for r in rows if r['arm'] == arm]
        count = len(selected)
        complete = all(r['usage']['usage_complete'] for r in selected)
        known_tokens = sum(r['usage']['known_input_tokens'] + r['usage']['known_output_tokens'] for r in selected)
        per_case = {}
        for case in sorted({row['case_id'] for row in selected}):
            group = [r for r in selected if r['case_id'] == case]
            per_case[case] = dict(planned=len(group), clean_successes=sum(r['clean_success'] is True for r in group),
                                  unresolved=sum(r['clean_success'] is None for r in group))
        arms[arm] = dict(planned=count, execution_counts=dict(Counter(r['execution_status'] for r in selected)),
            confirmed_artifact_correct=sum(r['artifact_correct'] is True for r in selected),
            confirmed_artifact_correct_rate=sum(r['artifact_correct'] is True for r in selected)/count,
            confirmed_clean_success=sum(r['clean_success'] is True for r in selected),
            artifact_unresolved=sum(r['artifact_correct'] is None for r in selected),
            clean_unresolved=sum(r['clean_success'] is None for r in selected),
            confirmed_clean_success_rate=sum(r['clean_success'] is True for r in selected)/count,
            known_total_tokens=known_tokens, usage_complete=complete,
            mean_tokens=known_tokens/count if complete else None,
            known_model_calls=sum(r['usage']['known_model_calls'] for r in selected),
            model_calls=(sum(r['usage']['known_model_calls'] for r in selected)
                         if all(r['usage']['calls_complete'] for r in selected) else None),
            calls_complete=all(r['usage']['calls_complete'] for r in selected),
            mean_seconds=(sum(r['latency_seconds'] for r in selected)/count
                          if all(r['latency_seconds'] is not None for r in selected) else None),
            mean_execution_seconds=(sum(r['execution_seconds'] for r in selected)/count
                          if all(r.get('execution_seconds') is not None for r in selected) else None),
            total_cost_usd=None, per_qualified_delivery_cost_usd=None, per_case=per_case)
    pairs = {}
    for row in rows:
        pair = pairs.setdefault((row['case_id'], row['repeat']), {})
        if row['arm'] in pair:
            raise ValueError('Duplicate case/arm/repeat')
        pair[row['arm']] = row
    comparisons = []
    if set(arms) == {'baseline', 'candidate'}:
        for (case, repeat), pair in sorted(pairs.items()):
            a, b = pair.get('baseline'), pair.get('candidate')
            resolved = a and b and a['clean_success'] is not None and b['clean_success'] is not None
            comparisons.append(dict(case_id=case, repeat=repeat,
                clean_success_delta=int(b['clean_success'])-int(a['clean_success']) if resolved else None,
                status='resolved' if resolved else 'unresolved'))
    return dict(arms=arms, pairs=comparisons,
                interpretation='Fixed development cases; unresolved reviews and failures remain in planned denominators. No generalization claim.')


def report_batch(root):
    root = Path(root)
    grades = grade_batch(root)
    rows = []
    for grade in grades:
        path = root/'trials'/grade['trial_id']
        state = read(path/'trial.json')
        evidence = read(path/'evidence.json') if (path/'evidence.json').exists() else None
        rows.append(dict(grade, usage=usage(evidence), latency_seconds=state.get('latency_seconds'),
                         execution_seconds=state.get('execution_seconds'),
                         failure_type=state.get('failure_type')))
    summary = aggregate(rows)
    summary['grader_matches_run_manifest'] = all(r['grader_matches_run_manifest'] for r in rows)
    summary['grader_sha256'] = sorted({r['grader_sha256'] for r in rows})
    write(root/'report.json', dict(summary, trials=rows))
    lines = ['# 固定研究任务评测', '', '粒度：专家组件。仅代表固定开发案例；完整协作链路另测。', '',
             '| 方案 | 计划任务 | 确认产物正确 | 确认正常完成 | 产物待定 | 已知 token | 用量完整 |',
             '|---|---:|---:|---:|---:|---:|---|']
    for arm, value in summary['arms'].items():
        lines.append(f"| {arm} | {value['planned']} | {value['confirmed_artifact_correct']} | {value['confirmed_clean_success']} | {value['artifact_unresolved']} | {value['known_total_tokens']} | {'是' if value['usage_complete'] else '否'} |")
    lines += ['', '待审不是通过。失败、未运行和中断均保留；缺少价格时费用为未知。', '',
              '| 案例 | 方案 | 重复 | 执行 | 产物正确 | 正常完成 | 记录 |', '|---|---|---:|---|---|---|---|']
    label = {True: '通过', False: '失败', None: '待定'}
    for row in rows:
        lines.append(f"| {row['case_id']} | {row['arm']} | {row['repeat']+1} | {row['execution_status']} | {label[row['artifact_correct']]} | {label[row['clean_success']]} | [评分](trials/{row['trial_id']}/grade.json) |")
    if not summary['grader_matches_run_manifest']:
        lines += ['', '**评分器已变化：这是使用新评分器对原始固定规则的重评，不能与旧评分版本混为同一趋势。**']
    (root/'report.md').write_text('\n'.join(lines)+'\n')
    return summary
