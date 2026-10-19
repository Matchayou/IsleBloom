from pydantic import Field

from ..contracts import RunResult, Usage, Value


class CollectedResult(Value):
    key: str
    run_id: str
    agent_id: str
    status: str
    result: RunResult | None = None
    missing_reason: str | None = None


class ResultSet(Value):
    group_id: str
    status: str
    items: tuple[CollectedResult, ...]
    usage: Usage = Field(default_factory=Usage)

    @property
    def complete(self):
        return all(item.status == 'completed' for item in self.items)


def collect(repository, group_id: str) -> ResultSet:
    group = repository.get_dispatch(group_id)
    if group is None:
        raise ValueError('Unknown dispatch group')
    items, usage = [], Usage()
    for item in group.plan.items:
        record = repository.get(item.run_id)
        if record is None:
            status, result = 'not_started', None
        else:
            if (record.request.parent_run_id != group_id or record.request.agent_id != item.agent_id or
                    record.request.input != item.input or record.request.limits != item.limits or record.request.depth != 1):
                raise ValueError('Stored run does not match dispatch input binding')
            status = {'succeeded': 'completed', 'saving': 'confirming'}.get(record.status, record.status)
            result = record.result
        reason = group.reasons.get(item.key)
        if result:
            usage = usage.plus(result.usage)
            if result.error:
                reason = result.error.code + ': ' + result.error.message
        if record is not None and result is None:
            usage = usage.model_copy(update={'complete': False})
        if status == 'completed':
            reason = None
        if status != 'completed' and reason is None:
            reason = status if status != 'not_started' else group.status
        items.append(CollectedResult(key=item.key, run_id=item.run_id, agent_id=item.agent_id,
                                     status=status, result=result, missing_reason=reason))
    return ResultSet(group_id=group_id, status=group.status, items=tuple(items), usage=usage)
