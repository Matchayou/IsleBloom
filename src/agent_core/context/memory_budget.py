"""Whole-entry injection budget using the existing conservative UTF-8 token bound."""
from .projection import dumps, ContextBudgetExceeded
from ..memories.service import MemoryService

MEMORY_TOKEN_BUDGET = 4000


def bound(values):
    return len(dumps(list(MemoryService.contributions(values))).encode())


def allocate(required, optional, *, budget=MEMORY_TOKEN_BUDGET):
    selected = list(required)
    if bound(selected) > budget:
        raise ContextBudgetExceeded('Required complete memories exceed memory injection token budget')
    included = {m.id for m in selected}
    omitted = []
    # A large optional entry does not prevent a later smaller complete entry.
    for entry in optional:
        if entry.id in included: continue
        if bound([*selected, entry]) <= budget:
            selected.append(entry); included.add(entry.id)
        else: omitted.append({'id':entry.id, 'version':entry.version, 'reason':'memory_injection_budget'})
    return tuple(selected), {'max_tokens':budget, 'token_upper_bound':bound(selected),
        'estimation':'utf8_upper_bound', 'omitted':omitted}
