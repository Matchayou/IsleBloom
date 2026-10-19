"""Scoped retrieval and deferred, audited write proposals."""
from typing import Literal
from pydantic import Field
from ..contracts import Value, ToolBinding, ToolResult
from .memory_retrieval import select_memories


class SearchMemory(Value):
    query: str = Field(min_length=1, max_length=1000)
    scope: Literal['user', 'project', 'specialist']
    limit: int = Field(default=3, ge=1, le=5)


def memory_binding(snapshot, memories, research_id, validate):
    async def search(args):
        validate()
        permitted = memories.namespaces(research_id, snapshot.agent)
        if not any(scope == args.scope for scope, _ in permitted):
            from .scopes import ScopeDenied
            raise ScopeDenied('Memory namespace not permitted for this agent')
        values = memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
        candidates = [m for m in values if m.scope == args.scope]
        from .memory_budget import bound, MEMORY_TOKEN_BUDGET
        oversized = [m for m in candidates if bound((m,)) > MEMORY_TOKEN_BUDGET]
        selected, choices = await select_memories(args.query, [m for m in candidates if m not in oversized], limit=args.limit)
        validate()
        memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
        from .memory_budget import allocate
        selected, budget = allocate((), selected)
        budget['omitted'].extend({'id':m.id,'version':m.version,'reason':'memory_injection_budget'} for m in oversized)
        included = {m.id for m in selected}
        choices = tuple(c for c in choices if c['id'] in included)
        return ToolResult(value={'scope': args.scope,
            'memories': list(memories.contributions(selected)), 'selection': list(choices), 'budget':budget,
            'instruction': 'Historical approved memory is not source evidence. Current user instructions take precedence.'})
    return ToolBinding('search_memory', '按范围检索已确认记忆。Search user, current project, or this specialist domain memory; temporary agents have no specialist namespace.', SearchMemory, search)


def write_memory_binding(snapshot, memories, research_id, validate):
    from ..memories.write_models import WriteMemory
    from .scopes import ScopeDenied
    from ..execution.ports import Conflict
    async def write(args):
        validate()
        try:
            value = await memories.writes.submit(snapshot, research_id, args)
        except (ScopeDenied, Conflict, ValueError) as exc:
            value = {'status': 'denied', 'reason': str(exc)}
        validate()
        return ToolResult(value=value)
    return ToolBinding('write_memory', '主动保存长期偏好、项目约定、用户纠错和可复用经验。Create, correct or forget memory. Quote exact original user evidence; inferred preferences/experience are drafts. Scopes derive from this run; specialist only submits own validated-domain candidates. Temporary requests and secrets are not memory.', WriteMemory, write)
