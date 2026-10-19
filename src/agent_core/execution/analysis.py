"""Read-only, content-free analysis of saved execution evidence."""
from ..contracts import Usage


def analyze_run(record, events, *, operations=None, measurements=()):
    events = sorted(events, key=lambda item: item.sequence)
    starts = {}
    timeline = []
    for item in events:
        event = item.event
        key = (event.run_id, event.phase, event.tool_call_id, event.child_run_id, event.name)
        duration = None
        if event.status == 'started':
            starts[key] = item.occurred_at
        elif key in starts:
            duration = max(0, (item.occurred_at - starts.pop(key)).total_seconds())
        timeline.append(dict(sequence=item.sequence, occurred_at=item.occurred_at,
            run_id=event.run_id, parent_run_id=event.parent_run_id, phase=event.phase,
            name=event.name, status=event.status, duration_seconds=duration,
            error_code=event.error.code if event.error else None,
            error_origin=event.error.origin if event.error else None))
    usage = record.result.usage if record.result else Usage(complete=False)
    if operations is not None:
        usage = Usage()
        for operation in operations:
            if operation['kind'] == 'model':
                usage = usage.plus(Usage.model_validate(operation['usage']) if operation['usage']
                    else Usage(requests=1, complete=False))
    failed = next((row for row in timeline if row['status'] == 'failed'), None)
    error = record.result.error if record.result else None
    # Failed tools can be recovered. Do not report them as a failed run's root cause.
    failure = None
    if record.status in ('failed', 'cancelled', 'interrupted'):
        failure = dict(sequence=failed['sequence'] if failed else None,
            phase=failed['phase'] if failed else None,
            error_code=error.code if error else (failed['error_code'] if failed else None),
            origin=error.origin if error else (failed['error_origin'] if failed else None),
            conclusion='recorded_failure' if failed or error else 'unknown')
    terminal = next((row for row in reversed(timeline) if row['phase'] == 'run'
        and row['run_id'] == record.run_id and row['status'] != 'started'), None)
    elapsed = max(0, (terminal['occurred_at'] - record.created_at).total_seconds()) if terminal else None
    models = [dict(operation_id=m['operation_id'], state=m['state'],
        purpose=m.get('purpose'), duration_seconds=m.get('duration_seconds'),
        usage={key:m['usage'].get(key) for key in ('input_tokens','output_tokens','cached_input_tokens')}
            if m.get('usage') else None) for m in measurements]
    return dict(run_id=record.run_id, agent_id=record.request.agent_id, status=record.status,
        created_at=record.created_at, elapsed_seconds=elapsed, usage=usage.model_dump(),
        cost=None, cost_reason='No recorded billing price', timeline=timeline,
        models=models, failure=failure,
        unconfirmed_operations=sum(o['status'] != 'confirmed' for o in operations) if operations is not None else None)
