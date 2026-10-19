"""Read model: selection, run state and dependency updates remain separate."""
from ..contracts import Value
from .models import ResearchNode, NodeResultVersion
from ..context.scopes import ScopeDenied


class NodeProjection(Value):
    node: ResearchNode
    execution_status: str
    stop_reason: str | None
    versions: tuple[NodeResultVersion, ...]
    upstream_updates: tuple[str, ...]


def project(service, research_id):
    research = service.research(research_id)
    nodes = service.query('list_nodes', research_id)
    def updates(key, seen=frozenset()):
        if key in seen: return set()
        values = set()
        own = service.query('result', key)
        for dep in service.query('inputs', key):
            result = service.query('result', dep)
            source = service.node(result.node_id)
            if source.selection == 'excluded' or (source.id != own.node_id and source.current_result_id != dep):
                values.add(source.id)
            values |= updates(dep, seen | {key})
        return values
    projections = []
    for node in nodes:
        runs = service.query('list_runs', node.id)
        last = max(runs, key=lambda r: r.node_revision) if runs else None
        execution = service.ledger.get(last.run_id) if last else None
        last_command = service.query('command', last.command_id) if last else None
        commands = [c for c in service.query('list_commands', research_id) if c.node_id == node.id]
        pending = [c for c in commands if c.status == 'accepted']
        projections.append(NodeProjection(node=node,
            execution_status=execution.status if execution else 'queued' if pending else 'idle',
            stop_reason=execution.result.error.code if execution and execution.result and execution.result.error else
                last_command.reason if last_command else next((c.reason for c in reversed(commands) if c.reason), None),
            versions=tuple(service.query('list_results', node.id)),
            upstream_updates=tuple(sorted(updates(node.current_result_id))) if node.current_result_id else ()))
    return {'research': research, 'nodes': projections,
        'display_edges': [{'source': n.display_parent_id, 'target': n.id} for n in nodes if n.display_parent_id],
        'commands': service.query('list_commands', research_id)}
