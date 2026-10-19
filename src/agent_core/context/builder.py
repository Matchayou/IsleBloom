"""Clean input on every run. Conversation IDs are associations, never history loads."""
import json
from ..contracts import Message, PreparedInput
from .mounts import ContextSnapshot
from .scopes import ScopeDenied
from .projection import project_payload
from ..memories.models import MemoryRef


class ContextBuilder:
    def __init__(self, scopes):
        self.scopes = scopes

    def build(self, scope, run_id, goal, *, refs=None, conversation_id=None, results=(), contributions=(), persist=True, evidence_mode=False, history=(), packet=None, lazy_sources=False, selection=None, current_message=None, project=None, agent=None, tool_query=None, mode='execution', business=None):
        self.scopes.valid(scope)
        refs = tuple(refs) if refs is not None else tuple(ref for ref in scope.allowed + scope.inherited
            if self.scopes.resources.describe(ref)[0].kind != 'discovery')
        mounts = tuple(self.scopes.mount(scope, ref) for ref in refs)
        directory, bodies = [], []
        for mount in mounts:
            resource, _, parsed = self.scopes.resources.describe(mount.ref)
            if not lazy_sources or len(directory) < 8: directory.append({'ref': mount.ref.model_dump(), 'name': resource.name,
                              'kind': resource.kind, 'parse_status': parsed.status,
                              **self.scopes.resources.source_metadata(mount.ref),
                              'missing': [i.model_dump() for i in parsed.issues]})
            if not evidence_mode and not lazy_sources:
                bodies.append({'ref': mount.ref.model_dump(), 'fragments': [f.model_dump() for f in self.scopes.read(scope, mount.ref)]})
        for mount, _ in results:
            for ref in mount.resource_refs:
                self.scopes.check(scope, ref)
        payload = {'context_mode': mode, 'goal': goal, 'sources': directory, 'content': bodies}
        if project is not None: payload['project'] = project
        if agent is not None: payload['agent'] = agent.model_dump()
        if selection is not None:
            payload['dynamic_context'] = True
            history = selection['history']
            payload['skills'] = selection['skills']
            payload['context_selection'] = selection['manifest']
            payload['related_results'] = selection['results']
            if lazy_sources:
                payload['sources'] = selection['sources']
            elif agent is not None and agent.mode != 'assistant':
                payload['authorized_directory'] = selection['sources']
        if lazy_sources:
            payload['source_directory'] = {'shown': len(payload['sources']),
                'total': selection['manifest']['source_total'] if selection else len(mounts),
                'instruction': ('Use read_information with empty arguments to list the frozen directory and with refs to read bodies before authorizing them.' if mode == 'consultation' else
                    'Search bounded source metadata with search_context, or read an already selected ref. Source bodies are loaded only on request.')}
        history = list(history)
        if current_message:
            # The current instruction is already preserved as the final user
            # message. Do not charge its complete text twice against the budget.
            for index in range(len(history)-1, -1, -1):
                if history[index]['role'] == 'user':
                    if history[index]['content'] == current_message.content:
                        payload['current_message_id'] = history[index].get('id')
                        del history[index]
                    break
        if history: payload['history'] = history
        if packet is not None: payload['handoff'] = packet
        if evidence_mode:
            payload['source_reading'] = 'Use search_source on each listed full ref to find located fragments; use read_source for small documents only.'
        if results:
            payload['results'] = [{'mount': m.model_dump(), 'output': body} for m, body in results]
        if contributions:
            from .projection import dumps, ContextBudgetExceeded
            from .memory_budget import MEMORY_TOKEN_BUDGET
            if len(dumps(list(contributions)).encode()) > MEMORY_TOKEN_BUDGET:
                raise ContextBudgetExceeded('Required complete memories exceed memory injection token budget')
            payload['approved_memory'] = list(contributions)
            payload['memory_rule'] = ('These are historical user-approved preferences or experiences, not source facts. '
                'Apply only when relevant. The current goal and user instruction take precedence; '
                'retain contrary evidence and cite source documents for factual claims.')
        if business:
            if business.keys() & payload.keys(): raise ValueError('Business context overwrites assembled context')
            payload.update(business)
        # Reserve the exact final user message in the same input contract. The
        # business packet records its history identity; the last SDK message is
        # the original command, never a rewritten retrieval query.
        reserve = len(current_message.content.encode()) if current_message else 0
        prepared = project_payload(payload, max(0, scope.max_input_chars-reserve))
        if current_message:
            prepared = prepared.model_copy(update={'messages': (*prepared.messages,
                Message(role='user', content=current_message.content))})
        if selection is not None:
            prepared = prepared.model_copy(update={
                'tool_query': tool_query if tool_query is not None else current_message.content if current_message else goal})
        value = ContextSnapshot(run_id=run_id, conversation_id=conversation_id,
            scope_id=scope.id, scope_version=scope.version, scope=scope, mounts=mounts, result_mounts=tuple(m for m, _ in results),
            input=prepared, skill_catalog=tuple(selection['catalog']) if selection else (),
            memory_catalog=tuple(MemoryRef(**v) for v in selection['memory_catalog']) if selection else (),
            memory_refs=tuple(MemoryRef(**v) for v in selection['manifest']['memory_refs']) if selection else (),
            selection=selection['manifest'] if selection else {}, agent=agent, mode=mode)
        if persist: self.scopes.repository.save_snapshot(value)
        return value
