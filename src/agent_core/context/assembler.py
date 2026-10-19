"""One business assembly pipeline for assistants and delegated executions."""
from .builder import ContextBuilder
from .selection import ContextSelector


class ContextAssembler:
    def __init__(self, selector=None):
        self.learning = None
        self.selector = selector or ContextSelector()

    def assemble(self, scopes, scope, run_id, goal, *, agent, original_message, project,
                 refs=(), results=(), history=(), memories=(), required_memories=(), sources=(),
                 related_results=(), packet=None, lazy_sources=False, evidence_mode=False, persist=True, retrieval_goal=None, mode=None, business=None):
        namespaces = {('user', None)}
        if project.get('project_id'): namespaces.add(('project', project['project_id']))
        if agent.mode == 'specialist': namespaces.add(('specialist', agent.id))
        memories = tuple(m for m in memories if m.owner == scope.owner and m.available() and (m.scope, m.scope_id) in namespaces)
        if any(m.owner != scope.owner or not m.available() or (m.scope, m.scope_id) not in namespaces for m in required_memories):
            from .scopes import ScopeDenied
            raise ScopeDenied('Required memory is outside this agent context')
        search_goal = retrieval_goal if retrieval_goal is not None else (goal if agent.mode != 'assistant' else '')
        query = (search_goal + '\n' if search_goal else '') + original_message.content
        mode = mode or ('assistant' if agent.mode == 'assistant' else 'execution')
        selection = self.selector.select(query, history=history,
            memories=memories, sources=sources, results=related_results,
            skill_entries=self.learning.skills(agent) if self.learning else ())
        # Explicit entries are mandatory; optional semantic selection happens
        # after admission inside the controlled runner and is checkpointed there.
        values = list({m.id: m for m in required_memories}.values())
        selection['memory_catalog'] = list({r['id']: r for r in (*selection['memory_catalog'],
            *({'id': m.id, 'version': m.version} for m in required_memories))}.values())
        from ..memories.service import MemoryService
        selection['manifest']['memory_refs'] = [r.model_dump() for r in MemoryService.refs(values)]
        selection['manifest']['memory_namespaces'] = [
            {'scope': scope, 'scope_id': key} for scope, key in sorted(namespaces)]
        selection['manifest']['mode'] = mode
        selection['manifest']['agent'] = agent.model_dump()
        return ContextBuilder(scopes).build(scope, run_id, goal, refs=refs, results=results,
            history=history, selection=selection, current_message=original_message,
            contributions=MemoryService.contributions(values), packet=packet,
            project=project, agent=agent, tool_query=query, lazy_sources=lazy_sources,
            evidence_mode=evidence_mode, persist=persist, mode=mode, business=business)


def project_context(research, research_id, coding=None):
    root = research.research(research_id)
    value = {'research_id': root.id, 'goal': root.goal}
    with research.database.transaction() as tx:
        member = tx.projects.member(research_id) if 'projects' in research.database.repositories else None
        if member is not None: value['project_id'] = member.project_id
    if coding is not None:
        value['workspace'] = {'repository_id': coding.repository_id, 'baseline': coding.baseline,
            'read_paths': coding.read_paths, 'write_paths': coding.write_paths}
    return value
