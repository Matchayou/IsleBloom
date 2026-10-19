"""One row per tool invocation, rather than one row per notification."""


def merge_tool_calls(run_id, events, requests):
    calls = {}
    for item in sorted(events, key=lambda value: value.sequence):
        event = item.event
        if event.run_id != run_id or event.phase != 'tool' or not event.tool_call_id:
            continue
        row = calls.setdefault(event.tool_call_id, dict(call_id=event.tool_call_id,
            name=event.name, status='unknown', started_at=None, ended_at=None,
            duration_seconds=None, error=None))
        if event.status == 'started':
            if row['started_at'] is None: row['started_at'] = item.occurred_at
        else:
            row.update(ended_at=item.occurred_at, error=event.error)
        row['status'] = event.status
    for call_id, request in requests.items():
        row = calls.setdefault(call_id, dict(call_id=call_id, name=request['name'],
            status='completed' if request['confirmation'] == 'confirmed' else 'unknown',
            started_at=None, ended_at=None, duration_seconds=None, error=None))
        row['confirmation'] = request['confirmation']
    for row in calls.values():
        row['io_recorded'] = row['call_id'] in requests
        if row['started_at'] is not None and row['ended_at'] is not None:
            row['duration_seconds'] = max(0, (row['ended_at'] - row['started_at']).total_seconds())
    return list(calls.values())
