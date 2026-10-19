"""Scoped discovery and reading of immutable expert reports and own session messages."""
import json

from pydantic import Field

from ..contracts import ToolBinding, ToolResult, Value
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from .models import ReportGrant


class ReadExpertContext(Value):
    result_ids: tuple[str, ...] = Field(default=(), max_length=8)
    message_ids: tuple[str, ...] = Field(default=(), max_length=20)


class ReportHistory:
    def __init__(self, collaboration):
        self.c = collaboration

    def reference(self, research_id, result_id):
        receipt = self.c.tasks.receipt(result_id)
        spec = self.c.tasks.spec(receipt.task_id, receipt.spec_version)
        if spec.research_id != research_id:
            raise ScopeDenied('Report is outside the current main task')
        research = self.c.research
        root = research.scopes.current(research.research(research_id).scope_id, research.owner)
        if spec.node_id and research.node(spec.node_id).selection != 'selected':
            raise ScopeDenied('Report source node is excluded')
        for ref in receipt.resource_refs:
            research.scopes.check(root, ref)
        run = self.c.tasks.ledger.get(receipt.run_id)
        if run is None or run.status != 'succeeded':
            raise Conflict('Report execution did not succeed')
        return ReportGrant(result_id=result_id, task_id=receipt.task_id,
            spec_version=receipt.spec_version, run_id=receipt.run_id), spec, receipt

    def catalog(self, research_id, *, session_id=None, granted_ids=()):
        rows = []
        for spec in self.c.tasks.query('list_specs', research_id):
            for run in self.c.tasks.query('list_runs', spec.id):
                key = 'task-result:' + run.id
                if session_id and spec.expert_session_id != session_id and key not in granted_ids:
                    continue
                try:
                    pin, fixed, receipt = self.reference(research_id, key)
                except (KeyError, ScopeDenied, Conflict):
                    continue
                rows.append({**pin.model_dump(), 'expert_id': fixed.expert_id,
                    'session_id': fixed.expert_session_id, 'goal': fixed.goal[:300],
                    'delivery': receipt.delivery, 'adoption': receipt.adoption,
                    'resource_refs': [ref.model_dump() for ref in receipt.resource_refs]})
        return rows

    def read(self, node_id, result_ids):
        research_id = self.c.research.node(node_id).research_id
        for key in result_ids:
            self.reference(research_id, key)
        return self.c.read_context(node_id, result_ids=result_ids)['results']

    def expert_binding(self, spec, binding):
        def grants():
            saved = self.c.information.get(binding.id)
            pins = saved.report_grants if saved and saved.answer and saved.answer.available else ()
            for pin in pins:
                current, _, _ = self.reference(spec.research_id, pin.result_id)
                if current != pin:
                    raise ScopeDenied('Authorized report version changed')
            return set(spec.required_results) | {pin.result_id for pin in pins}

        def allowed(key, authorized):
            pin, source, receipt = self.reference(spec.research_id, key)
            if source.expert_session_id != spec.expert_session_id and key not in authorized:
                return False
            scope = self.c.research.scopes.repository.scope(binding.scope_id)
            try:
                for ref in receipt.resource_refs:
                    self.c.research.scopes.check(scope, ref)
            except ScopeDenied:
                return False
            return True

        async def read(args):
            self.c.information._validate(spec, binding)
            authorized = grants()
            session = self.c.query('session', spec.expert_session_id)
            if not args.result_ids and not args.message_ids:
                reports = self.catalog(spec.research_id, session_id=spec.expert_session_id,
                    granted_ids=authorized)
                for row in reports:
                    row['access'] = 'available' if allowed(row['result_id'], authorized) else 'authorization_required'
                return ToolResult(value={'results': reports, 'messages': [
                    {'message_id': m.id, 'role': m.role, 'task_id': m.task_id,
                     'spec_version': m.spec_version} for m in session.messages],
                    'instruction': 'Select result_ids or message_ids to read bodies. Request missing authorization through request_manager_information. Pending review is not formal adoption.'})
            unavailable = []
            readable = []
            for key in args.result_ids:
                try:
                    permitted = allowed(key, authorized)
                except KeyError:
                    unavailable.append({'result_id': key, 'reason': 'not_found'})
                except ScopeDenied:
                    unavailable.append({'result_id': key, 'reason': 'access_denied'})
                except Conflict:
                    unavailable.append({'result_id': key, 'reason': 'report_not_ready'})
                else:
                    if permitted:
                        readable.append(key)
                    else:
                        unavailable.append({'result_id': key, 'reason': 'authorization_required'})
            node_id = self.c.packet(spec.context_packet_id).sender_node_id
            results = self.read(node_id, tuple(readable)) if readable else []
            messages = []
            scope = self.c.research.scopes.repository.scope(binding.scope_id)
            for key in args.message_ids:
                message = next((m for m in session.messages if m.id == key), None)
                if message is None:
                    unavailable.append({'message_id': key, 'reason': 'not_found'})
                    continue
                try:
                    self.c.projects._fixed_context(self.c.tasks.spec(message.task_id, message.spec_version))
                    for ref in message.resource_refs:
                        self.c.research.scopes.check(scope, ref)
                    if any(not allowed(r, authorized) for r in message.result_ids):
                        raise ScopeDenied('Message report authorization missing')
                except (ScopeDenied, KeyError, Conflict):
                    unavailable.append({'message_id': key, 'reason': 'access_denied'})
                    continue
                messages.append(message.model_dump(mode='json'))
            self.c.information._validate(spec, binding)
            return ToolResult(value=json.loads(json.dumps({'results': results,
                'messages': messages, 'unavailable': unavailable},
                default=lambda o: o.model_dump(mode='json'), ensure_ascii=False)))

        return ToolBinding('read_context', 'List and read your own expert session history and immutable reports, plus explicitly authorized reports. Empty arguments list metadata; result_ids or message_ids read bodies. Other experts require manager authorization. Access denial does not mean the report does not exist.', ReadExpertContext, read)
