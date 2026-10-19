"""Bounded metadata discovery, separate from explicit body reads."""
from itertools import islice
from ..context.scopes import ScopeDenied
from ..research.chat import ChatService


def directory(service, node_id, *, category, query='', task_id=None, session_id=None, offset=0, limit=8):
    if category not in ('sources', 'results', 'messages', 'expert_messages') or not 1 <= limit <= 20 or offset < 0:
        raise ValueError('Invalid directory category or range')
    node = service.research.node(node_id)
    root = service._research(node.research_id)
    scope = service.research.scopes.current(root.scope_id, service.research.owner)
    if task_id and service.tasks.spec(task_id).research_id != root.id:
        raise ScopeDenied('Foreign task')
    if session_id:
        session = service.query('session', session_id)
        if session is None or session.research_id != root.id: raise ScopeDenied('Foreign session')
    if category in ('sources', 'messages') and (task_id or session_id):
        raise ValueError('Task/session filters apply to results and expert_messages only')
    term = query.strip().casefold()
    if query and len(term) < 2: raise ValueError('Search query needs at least two non-space characters')

    def rows():
        if category == 'sources':
            for ref in scope.allowed:
                service.research.scopes.check(scope, ref)
                resource, _, _ = service.research.scopes.resources.describe(ref)
                yield {'ref': ref.model_dump(mode='json'), 'name': resource.name}
        elif category == 'messages':
            chat = ChatService(service.research, service.tasks).read(node_id)
            for message in reversed((*chat.inherited, *chat.messages)):
                yield {'id': message.id, 'role': message.role, 'preview': message.content[:320]}
        elif category == 'results':
            specs = (service.tasks.spec(task_id),) if task_id else service.tasks.query('list_specs', root.id)
            for spec in sorted(specs, key=lambda s: s.id):
                if session_id and spec.expert_session_id != session_id: continue
                for run in sorted(service.tasks.query('list_runs', spec.id), key=lambda r: (r.spec_version, r.id), reverse=True):
                    receipt = service.tasks.query('receipt', f'task-result:{run.id}')
                    if receipt is None: continue
                    service.tasks.receipt(receipt.result_id)
                    fixed = service.tasks.spec(spec.id, receipt.spec_version)
                    service.validate_task_metadata(root.id, fixed)
                    for ref in receipt.resource_refs: service.research.scopes.check(scope, ref)
                    yield {'result_id': receipt.result_id, 'expert_id': spec.expert_id,
                        'task_id': spec.id, 'session_id': spec.expert_session_id,
                        'goal': fixed.goal[:240], 'delivery': receipt.delivery, 'spec_version': receipt.spec_version}
        else:
            sessions = (service.query('session', session_id),) if session_id else service.query('sessions_for', root.id)
            for session in sorted(sessions, key=lambda s: s.id):
                for message in reversed(session.messages):
                    if not message.task_id or (task_id and message.task_id != task_id): continue
                    spec = service.tasks.spec(message.task_id, message.spec_version)
                    service.validate_task_metadata(root.id, spec)
                    ChatService(service.research, service.tasks)._validate_message(root.id, message)
                    yield {'session_id': session.id, 'message_id': message.id,
                        'task_id': message.task_id, 'role': message.role, 'preview': message.content[:320]}
    # Search is deliberately literal metadata search, never a semantic task router.
    import json
    selected = (row for row in rows() if not term or term in json.dumps(row, ensure_ascii=False).casefold())
    batch = list(islice(selected, offset, offset + limit + 1))
    return {'category': category, 'query': query, 'items': batch[:limit],
        'next_offset': offset + limit if len(batch) > limit else None,
        'scope': {'node_id': node_id, 'task_id': task_id, 'session_id': session_id}}
