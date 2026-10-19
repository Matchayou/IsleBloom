"""Manager-selected context, stable dispatch commands, and expert results."""
import asyncio
import hashlib
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from ..contracts import RunLimits
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from ..research.chat import ChatService
from .models import AgentMessage, ContextPacket, DispatchIntent, ExpertMessageRef, ExpertSession, ManagerNotice, TaskCancellation, TaskDependency, TaskRevision
from ..agent_identity import AgentIdentity
from ..context.assembler import project_context


logger = logging.getLogger(__name__)


class CollaborationService:
    MAX_TASKS_PER_MAIN = 8
    MAX_DELEGATION_DEPTH = 2

    def __init__(self, database, research, tasks, projects):
        self.database, self.research, self.tasks, self.projects = database, research, tasks, projects
        self.locks = {}
        self.durable = None
        from .information import InformationService
        self.information = InformationService(self)
        from .review import AutomaticReview
        self.review = AutomaticReview(self)

    def query(self, method, *args):
        with self.database.transaction() as tx:
            return getattr(tx.collaboration, method)(*args)

    def _research(self, research_id):
        self.projects.main_task(research_id)
        return self.research.research(research_id)

    def find_experts(self, research_id, query='', *, kind=None, required_tools=(), offset=0, limit=5):
        self._research(research_id)
        if offset < 0 or not 1 <= limit <= 10: raise ValueError('Invalid expert page')
        term = query.strip().casefold()
        sessions = [s for s in self.query('sessions_for', research_id) if s.agent.mode == 'specialist']
        specs = self.tasks.query('list_specs', research_id)
        rows = []
        for candidate in self.tasks.methods.experts():
            coding_grants = self.tasks.coding_grants(research_id) if candidate.kind == 'coding' else {}
            if candidate.kind == 'coding' and not coding_grants: continue
            tools = set(candidate.tools)
            from ..controlled.engine import ControlledEngine
            if isinstance(self.tasks.engine, ControlledEngine):
                root = self.research.research(research_id)
                scope = self.research.scopes.current(root.scope_id, self.research.owner)
                tools &= set(scope.tools)
                tools |= {'read_context', 'request_manager_information', 'write_private_note', 'read_private_note'}
            if kind and candidate.kind != kind: continue
            if not set(required_tools) <= tools: continue
            if term and term not in (candidate.id + ' ' + candidate.description).casefold(): continue
            matching = [v for v in sessions if v.expert_id == candidate.id]
            visible_tasks = []
            for spec in specs:
                if not matching or spec.expert_session_id != matching[0].id: continue
                try: self.validate_task_metadata(research_id, spec)
                except (ScopeDenied, Conflict, KeyError): continue
                runs = self.tasks.query('list_runs', spec.id)
                visible_tasks.append({'task_id': spec.id, 'spec_version': spec.version,
                    'goal': spec.goal[:240], 'run_count': len(runs)})
            rows.append({'expert_id': candidate.id, 'description': candidate.description,
                'kind': candidate.kind, 'tools': sorted(tools),
                'coding_contracts': {name: c.model_dump(mode='json') for name,c in coding_grants.items()},
                'method_version': candidate.method_version,
                'session_id': matching[0].id if matching else None,
                'tasks': visible_tasks[-5:],
                'input_requirements': 'operator granted coding_contract_id' if candidate.kind == 'coding' else
                    'passed extraction result' if candidate.kind == 'analyze' else
                    'public web search or selected source' if {'search_web', 'fetch_web'} <= set(candidate.tools) else
                    'selected source or passed result'})
        return sorted(rows, key=lambda row: row['expert_id'])[offset:offset+limit]

    def validate_task_metadata(self, research_id, spec):
        if spec.research_id != research_id: raise ScopeDenied('Foreign task')
        root = self._research(research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        if spec.node_id and self.research.node(spec.node_id).selection == 'excluded':
            raise ScopeDenied('Task node excluded')
        for ref in (*spec.required_refs, *spec.optional_refs): self.research.scopes.check(scope, ref)
        for message in spec.chat_context:
            ChatService(self.research, self.tasks)._validate_message(research_id, message, None if spec.context_packet_id else spec.node_id)
        for key in spec.required_results:
            if key.startswith('task-result:'):
                receipt = self.tasks.receipt(key)
                if self.tasks.spec(receipt.task_id, receipt.spec_version).research_id != research_id:
                    raise ScopeDenied('Foreign result')
                for ref in receipt.resource_refs: self.research.scopes.check(scope, ref)
            else: self.research.check_result(key, scope)
        return scope

    def task_status(self, research_id, task_id):
        """One task only; no source bodies, expert discovery or workspace budget scan."""
        task = self.tasks.spec(task_id)
        scope = self.validate_task_metadata(research_id, task)
        runs = self.tasks.query('list_runs', task_id)
        records = {run.id: self.tasks.ledger.get(run.id) for run in runs}
        latest = max(runs, key=lambda run: (run.spec_version,
            records[run.id].created_at.isoformat() if records[run.id] else ''), default=None)
        record = records[latest.id] if latest else None
        from ..controlled.engine import ControlledEngine
        if latest and isinstance(self.tasks.engine, ControlledEngine):
            self.tasks.engine.validate_run(latest.id)
        receipt = self.tasks.query('receipt', f'task-result:{latest.id}') if latest else None
        if receipt:
            self.tasks.receipt(receipt.result_id)
            for ref in receipt.resource_refs: self.research.scopes.check(scope, ref)
        intent = self.query('intent_for_task', task_id)
        cycle = self.review.visible(task_id)
        return {'task_id': task.id, 'spec_version': task.version,
            'dispatch_status': intent.status if intent else None,
            'dispatch_error': intent.error if intent else None,
            'waiting_for': [{'task_id': key, 'spec_version': version} for key, version in intent.waiting_for]
                if intent and intent.status == 'waiting' else [],
            'run_id': latest.id if latest else None,
            'run_spec_version': latest.spec_version if latest else None,
            'execution_status': record.status if record else 'queued' if latest else 'not_started',
            'error': record.result.error.model_dump(mode='json') if record and record.result and record.result.error else None,
            'result_id': receipt.result_id if receipt else None,
            'delivery': receipt.delivery if receipt else None,
            'automatic_review': cycle.model_dump(mode='json') if cycle else None}

    def context_directory(self, node_id, **args):
        from .directory import directory
        return directory(self, node_id, **args)

    def session_for_task(self, research_id, expert_id, agent):
        if self.projects.query('member', research_id) is None:
            return None
        self._research(research_id)
        existing = [v for v in self.query('sessions_for', research_id) if v.agent == agent]
        if existing: return existing[0]
        asset, _ = self.tasks.methods.method(expert_id)
        return ExpertSession(research_id=research_id, expert_id=expert_id, method_version=asset.version, agent=agent)

    def budget_status(self, research_id):
        self._research(research_id)
        specs = self.tasks.query('list_specs', research_id)
        reservations = [fixed.limits.max_turns
            for spec in specs for version in range(1, spec.version + 1)
            if not (fixed := self.tasks.spec(spec.id, version)).pending_dependency_task_ids]
        reserved_turns = None if None in reservations else sum(reservations)
        actual_requests = input_tokens = output_tokens = 0
        uncertain = []
        for spec in specs:
            for run in self.tasks.query('list_runs', spec.id):
                record = self.tasks.ledger.get(run.id)
                if record is not None and record.status in ('queued', 'running', 'saving'):
                    continue
                if record is None or record.result is None or not record.result.usage.complete:
                    uncertain.append(run.id)
                    continue
                usage = record.result.usage
                actual_requests += usage.requests
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
        for cycle in self.query('review_loops_for', research_id):
            for review_run_id in self.review.run_ids(cycle):
                record = self.tasks.ledger.get(review_run_id)
                if record is None or record.status in ('running', 'saving'): continue
                if record.result is None or not record.result.usage.complete:
                    uncertain.append(review_run_id)
                    continue
                usage = record.result.usage
                actual_requests += usage.requests
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
        return {'task_count': len(specs), 'reserved_model_turns': reserved_turns,
            'actual_model_requests': actual_requests, 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'uncertain_run_ids': uncertain,
            'limits': {'tasks': self.MAX_TASKS_PER_MAIN,
                'delegation_depth': self.MAX_DELEGATION_DEPTH,
                'reserved_model_turns': None,
                'recorded_tokens': None,
                'parallel_expert_runs': 2}}

    def _check_budget(self, research_id, *, new_task, parent_task_id=None):
        budget = self.budget_status(research_id)
        if new_task and budget['task_count'] >= self.MAX_TASKS_PER_MAIN:
            raise Conflict('Main task expert task budget exhausted')
        depth, ancestor = 0, parent_task_id
        while ancestor:
            depth += 1
            ancestor = self.tasks.spec(ancestor).parent_task_id
        if depth > self.MAX_DELEGATION_DEPTH:
            raise Conflict('Main task delegation depth budget exhausted')

    def record_source_read(self, run_id, ref):
        with self.database.transaction() as tx: tx.collaboration.record_read(run_id, ref)

    def read_refs(self, run_id):
        return self.query('read_refs', run_id)

    def read_context(self, node_id, message_ids=(), resource_refs=(), result_ids=(), expert_message_refs=()):
        chat = ChatService(self.research, self.tasks).read(node_id)
        self._research(chat.research_id)
        messages = {m.id: m for m in (*chat.inherited, *chat.messages)}
        if len(set(message_ids)) != len(message_ids): raise Conflict('Duplicate message reference')
        if len(set(result_ids)) != len(result_ids): raise Conflict('Duplicate result reference')
        selected = []
        for key in message_ids:
            if key not in messages: raise ScopeDenied('Message is outside the current conversation')
            selected.append(messages[key])
        if len(set((ref.session_id, ref.message_id) for ref in expert_message_refs)) != len(expert_message_refs):
            raise Conflict('Duplicate expert message reference')
        for ref in expert_message_refs:
            session = self.query('session', ref.session_id)
            if session is None or session.research_id != chat.research_id:
                raise ScopeDenied('Expert session is outside the main task')
            message = next((m for m in session.messages if m.id == ref.message_id), None)
            if message is None or not message.task_id:
                raise ScopeDenied('Expert message is unavailable')
            source_task = self.tasks.spec(message.task_id, message.spec_version)
            if source_task.expert_session_id != session.id or source_task.research_id != chat.research_id:
                raise ScopeDenied('Expert message source task changed')
            self.projects.expert_thread(message.task_id)
            selected_message = message.model_copy(update={'source_session_id': session.id})
            ChatService(self.research, self.tasks)._validate_message(
                chat.research_id, selected_message)
            selected.append(selected_message)
        root = self.research.research(chat.research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        sources = []
        for ref in resource_refs:
            self.research.scopes.check(scope, ref)
            resource, _, parsed = self.research.scopes.resources.describe(ref)
            sources.append({'ref': ref, 'name': resource.name, 'parse_status': parsed.status,
                'metadata': self.research.scopes.resources.source_metadata(ref),
                'fragments': [f.model_dump(mode='json') for f in self.research.scopes.read(scope, ref)]})
        results = []
        for key in result_ids:
            receipt = self.tasks.receipt(key)
            spec = self.tasks.spec(receipt.task_id, receipt.spec_version)
            if spec.research_id != chat.research_id: raise ScopeDenied('Result is outside the main task')
            for ref in receipt.resource_refs: self.research.scopes.check(scope, ref)
            run = self.tasks.ledger.get(receipt.run_id)
            if run is None or run.result is None: raise Conflict('Result output missing')
            results.append({'result_id': key, 'task_id': spec.id, 'expert_id': spec.expert_id,
                'expert_session_id': spec.expert_session_id, 'kind': spec.kind,
                'spec_version': spec.version, 'run_id': receipt.run_id,
                'delivery': receipt.delivery, 'adoption': receipt.adoption,
                'assumptions': receipt.assumptions, 'conflicts': receipt.conflicts,
                'missing': receipt.missing, 'limitations': receipt.limitations,
                'resource_refs': receipt.resource_refs,
                'output': run.result.output})
        value = {'chat_revision': chat.revision, 'messages': selected, 'sources': sources, 'results': results}
        return value

    def _dependencies(self, research_id, downstream_task_id, downstream_version, results):
        graph = {}
        for edge in self.query('dependencies_for', research_id):
            graph.setdefault(edge.downstream_task_id, set()).add(edge.upstream_task_id)
        edges = []
        for row in results:
            upstream = row['task_id']
            pending = [upstream]
            visited = set()
            while pending:
                current = pending.pop()
                if current == downstream_task_id: raise Conflict('Expert result dependency cycle')
                if current in visited: continue
                visited.add(current)
                pending.extend(graph.get(current, ()))
            graph.setdefault(downstream_task_id, set()).add(upstream)
            edges.append(TaskDependency(
                id=str(uuid5(NAMESPACE_URL,
                    f'dependency:{downstream_task_id}:{downstream_version}:{row["result_id"]}')),
                research_id=research_id, downstream_task_id=downstream_task_id,
                downstream_version=downstream_version, upstream_task_id=upstream,
                upstream_version=row['spec_version'], result_id=row['result_id']))
        return tuple(edges)

    def _waiting_dependencies(self, research_id, downstream_task_id, downstream_version, upstream):
        graph = {}
        for edge in self.query('dependencies_for', research_id):
            graph.setdefault(edge.downstream_task_id, set()).add(edge.upstream_task_id)
        edges = []
        for task_id, version in upstream:
            pending, visited = [task_id], set()
            while pending:
                current = pending.pop()
                if current == downstream_task_id: raise Conflict('Expert result dependency cycle')
                if current in visited: continue
                visited.add(current)
                pending.extend(graph.get(current, ()))
            graph.setdefault(downstream_task_id, set()).add(task_id)
            edges.append(TaskDependency(id=str(uuid5(NAMESPACE_URL,
                f'waiting-dependency:{downstream_task_id}:{downstream_version}:{task_id}:v{version}')),
                research_id=research_id, downstream_task_id=downstream_task_id,
                downstream_version=downstream_version, upstream_task_id=task_id,
                upstream_version=version))
        return tuple(edges)

    def delegate_task(self, *, command_id, node_id, task_instruction,
                      delivery_requirements, message_ids=(), expert_message_refs=(),
                      resource_refs=(), result_ids=(), upstream_task_ids=(),
                      collaborator_session_ids=(),
                      manager_run_id=None, expert_id=None, session_id=None,
                      parent_task_id=None, parent_run_id=None, coding_contract_id=None,
                      agent_mode='specialist', role_instructions='', authorized_refs=()):
        chat = ChatService(self.research, self.tasks).read(node_id)
        self._research(chat.research_id)
        if (expert_id is None) == (session_id is None):
            raise ValueError('Choose exactly one expert_id or session_id')
        target = self.query('session', session_id) if session_id else None
        if session_id and (target is None or target.research_id != chat.research_id):
            raise ScopeDenied('Expert session is outside the current main task')
        if target and (target.agent.mode != 'specialist' or agent_mode != 'specialist'):
            raise Conflict('Temporary agents cannot be reused for another task')
        expert_id = target.expert_id if target else expert_id
        parent = None
        if parent_task_id:
            if upstream_task_ids:
                raise Conflict('Expert child task cannot wait for a future result')
            run = self.tasks.query('run', parent_run_id) if parent_run_id else None
            if run is None or run.task_id != parent_task_id:
                raise ScopeDenied('Child task requires an active parent expert run')
            parent = self.tasks.spec(parent_task_id, run.spec_version)
            if parent.research_id != chat.research_id or not parent.context_packet_id:
                raise ScopeDenied('Child task requires an active parent expert run')
            parent_packet = self.packet(parent.context_packet_id)
            if session_id not in parent_packet.collaborator_session_ids or parent_packet.sender_node_id != node_id:
                raise ScopeDenied('Child collaborator was not granted by the manager')
            if not set(resource_refs) <= set(parent.authorized_refs) or not set(result_ids) <= set(parent.required_results):
                raise ScopeDenied('Child input exceeds the parent handoff')
            if not set(authorized_refs) <= set(parent.authorized_refs): raise ScopeDenied('Child directory exceeds parent authorization')
            parent_intent = self.query('intent_for_task', parent.id)
            manager_run_id = parent_intent.manager_run_id if parent_intent else None
            original = next((m for m in chat.messages if m.id == parent_packet.user_message_id), None)
        else:
            original = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
        if original is None: raise Conflict('Delegation requires a user instruction in this conversation')
        if not task_instruction.strip() or not delivery_requirements.strip():
            raise ValueError('Task instruction and delivery requirements are required')
        args = {'node_id': node_id, 'expert_id': expert_id, 'session_id': session_id,
            'task_instruction': task_instruction,
            'delivery_requirements': delivery_requirements, 'message_ids': tuple(message_ids),
            'expert_message_refs': [ref.model_dump(mode='json') for ref in expert_message_refs],
            'resource_refs': [r.model_dump(mode='json') for r in resource_refs],
            'result_ids': tuple(result_ids),
            'upstream_task_ids': tuple(upstream_task_ids),
            'collaborator_session_ids': tuple(collaborator_session_ids),
            'user_message_id': original.id, 'parent_task_id': parent_task_id,
            'parent_run_id': parent_run_id, 'coding_contract_id': coding_contract_id,
            'agent_mode': agent_mode, 'role_instructions': role_instructions,
            'authorized_refs': [r.model_dump(mode='json') for r in authorized_refs]}
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = self.query('intent', command_id)
        if old:
            if old.fingerprint != fingerprint: raise Conflict('Dispatch command ID reused with different input')
            self._research(old.research_id)
            return old
        if parent:
            record = self.tasks.ledger.get(parent_run_id)
            if record is None or record.status != 'running':
                raise Conflict('Parent expert run is no longer active')
            if self.tasks.spec(parent.id).version != parent.version:
                raise Conflict('Parent expert task version changed')
            if self.cancellation_requested(parent.id, parent.version):
                raise Conflict('Parent task is stopping')
            if any(item.parent_task_id == parent.id
                   for item in self.tasks.query('list_specs', chat.research_id)):
                raise Conflict('Parent expert task already delegated a child task')
        if len(set(upstream_task_ids)) != len(upstream_task_ids):
            raise Conflict('Duplicate upstream task')
        upstream = []
        for upstream_id in upstream_task_ids:
            source = self.tasks.spec(upstream_id)
            if source.research_id != chat.research_id:
                raise ScopeDenied('Upstream task is outside the main task')
            self.projects._fixed_context(source)
            upstream.append((source.id, source.version))
        selected = self.read_context(node_id, message_ids, resource_refs, result_ids,
                                     expert_message_refs)
        if selected['chat_revision'] != chat.revision: raise Conflict('Chat changed during handoff')
        selected_refs = tuple(dict.fromkeys(ref for message in selected['messages']
                                            for ref in message.resource_refs))
        effective_refs = tuple(dict.fromkeys((*resource_refs, *selected_refs)))
        authorized_refs = tuple(dict.fromkeys((*effective_refs, *authorized_refs)))
        root_scope = self.research.scopes.current(self.research.research(chat.research_id).scope_id, self.research.owner)
        for ref in authorized_refs: self.research.scopes.check(root_scope, ref)
        if set(authorized_refs) & self.research._excluded_refs(chat.research_id): raise ScopeDenied('Excluded directory material')
        candidate = next((e for e in self.tasks.methods.experts() if e.id == expert_id), None)
        if candidate is None: raise ValueError('Unknown expert')
        coding = None
        if candidate.kind == 'coding':
            coding = self.tasks.coding_grants(chat.research_id).get(coding_contract_id)
            if coding is None or parent is not None:
                raise ScopeDenied('Manager must select an operator granted coding contract')
        elif coding_contract_id is not None: raise ValueError('Coding grant requires a coding expert')
        if candidate.kind == 'analyze' and not result_ids and not upstream:
            raise Conflict('Numeric analysis expert requires a passed extraction result; choose a research-capable expert for source-only work')
        if coding is None and not effective_refs and not authorized_refs and not result_ids and not upstream and not {'search_web', 'fetch_web'} <= set(candidate.tools):
            raise Conflict('Expert task needs a selected source, checked result, or published web research capability')
        if coding is None and not effective_refs and not authorized_refs and not result_ids and not upstream:
            root = self.research.research(chat.research_id)
            root_scope = self.research.scopes.current(root.scope_id, self.research.owner)
            if not {'search_web', 'fetch_web'} <= set(root_scope.tools):
                raise Conflict('Main task has not granted web research capability')
        if result_ids:
            for row in selected['results']:
                if row['delivery'] != 'passed':
                    raise Conflict('Result has not passed independent review; use a discussion message instead')
                if candidate.kind == 'analyze' and row['kind'] != 'extract':
                    raise Conflict('Numeric analysis requires a passed extraction result')
        task_id = str(uuid5(NAMESPACE_URL, command_id + ':task'))
        agent = AgentIdentity(mode=agent_mode,
            id=expert_id if agent_mode == 'specialist' else 'temporary:' + task_id, instructions=role_instructions)
        if agent.mode == 'assistant' or (agent.mode == 'specialist' and role_instructions):
            raise ValueError('Specialist roles are published; temporary roles require task instructions')
        existing = [target] if target else [v for v in self.query('sessions_for', chat.research_id) if v.agent == agent]
        session = existing[0] if existing else ExpertSession(
            research_id=chat.research_id, expert_id=expert_id, method_version=candidate.method_version, agent=agent)
        if len(set(collaborator_session_ids)) != len(collaborator_session_ids):
            raise Conflict('Duplicate collaborator session')
        for collaborator_id in collaborator_session_ids:
            collaborator = self.query('session', collaborator_id)
            if collaborator is None or collaborator.research_id != chat.research_id or collaborator.id == session.id:
                raise ScopeDenied('Collaborator session is unavailable in this main task')
        packet = ContextPacket(id=str(uuid5(NAMESPACE_URL, command_id + ':packet')),
            research_id=chat.research_id, sender_node_id=node_id,
            sender_chat_revision=chat.revision, user_message_id=original.id,
            user_instruction=original.content, manager_instruction=task_instruction,
            agent=agent, authorized_refs=authorized_refs,
            project=project_context(self.research, chat.research_id, coding),
            limits=RunLimits(timeout_seconds=360, max_calls=24),
            selected_messages=tuple(selected['messages']), resource_refs=effective_refs,
            expert_message_refs=tuple(expert_message_refs),
            selected_results=tuple(selected['results']),
            result_ids=tuple(result_ids), delivery_requirements=delivery_requirements)
        packet = packet.model_copy(update={'collaborator_session_ids': tuple(collaborator_session_ids)})
        encoded = json.dumps(packet.model_dump(mode='json'), ensure_ascii=False)
        if len(encoded) > 100_000: raise Conflict('Handoff exceeds input budget; select less context')
        spec = self.tasks.prepare_spec(research_id=chat.research_id, kind=candidate.kind,
            user_request=original.content, goal=task_instruction + '\n交付要求：' + delivery_requirements,
            node_id=node_id, parent_task_id=parent_task_id, task_id=task_id, expert_id=expert_id,
            required_refs=effective_refs, required_results=tuple(result_ids), authorized_refs=authorized_refs,
            agent_mode=agent_mode, role_instructions=role_instructions,
            expert_session_id=session.id, context_packet_id=packet.id,
            selected_messages=tuple(selected['messages']), method_version=candidate.method_version,
            pending_dependency_task_ids=tuple(task_id for task_id, _ in upstream),
            evidence_required=expert_id == 'expert_market_research', coding=coding,
            limits=packet.limits)
        self._check_budget(chat.research_id,
            new_task=True, parent_task_id=parent_task_id)
        intent = DispatchIntent(command_id=command_id, fingerprint=fingerprint,
            research_id=chat.research_id, session_id=session.id, task_id=task_id,
            packet_id=packet.id, manager_run_id=manager_run_id,
            waiting_for=tuple(upstream), status='waiting' if upstream else 'accepted')
        dependencies = self._dependencies(chat.research_id, task_id, spec.version, selected['results'])
        waiting_edges = self._waiting_dependencies(chat.research_id, task_id, spec.version, upstream)
        with self.database.transaction() as tx:
            if not existing: tx.collaboration.insert('sessions', session.id, session)
            tx.collaboration.insert('packets', packet.id, packet)
            tx.tasks.add_spec(spec)
            for edge in dependencies: tx.collaboration.insert('dependencies', edge.id, edge)
            for edge in waiting_edges: tx.collaboration.insert('dependencies', edge.id, edge)
            tx.collaboration.insert('intents', command_id, intent)
        return intent

    def packet(self, packet_id):
        value = self.query('packet', packet_id)
        if value is None: raise KeyError(packet_id)
        self._research(value.research_id)
        root = self.research.research(value.research_id)
        scope = self.research.scopes.current(root.scope_id, self.research.owner)
        for ref in value.authorized_refs: self.research.scopes.check(scope, ref)
        if set(value.authorized_refs) & self.research._excluded_refs(root.id): raise ScopeDenied('Handoff directory excluded')
        self.read_context(value.sender_node_id,
            tuple(m.id for m in value.selected_messages if m.source_session_id is None),
            value.resource_refs, value.result_ids, value.expert_message_refs)
        return value

    def status(self, command_id):
        intent = self.query('intent', command_id)
        if intent is None: raise KeyError(command_id)
        self._research(intent.research_id)
        runs = self.tasks.query('list_runs', intent.task_id)
        receipts = [self.tasks.query('receipt', f'task-result:{v.id}') for v in runs]
        waiting = []
        if intent.status == 'waiting':
            for task_id, version in intent.waiting_for:
                upstream_receipts = [self.tasks.query('receipt', f'task-result:{run.id}')
                    for run in self.tasks.query('list_runs', task_id) if run.spec_version == version]
                waiting.append({'task_id': task_id, 'spec_version': version,
                    'reviewed_result_id': next((receipt.result_id for receipt in upstream_receipts
                        if receipt and receipt.delivery == 'passed'), None),
                    'delivery': next((receipt.delivery for receipt in upstream_receipts if receipt), None)})
        return {'intent': intent, 'task': self.tasks.spec(intent.task_id),
            'waiting_for': waiting,
            'runs': runs, 'receipts': [r for r in receipts if r is not None]}

    def send_agent_message(self, *, command_id, task_id, session_id, content, kind,
                           requires_reply=False, reply_to=None, sender='manager',
                           sender_session_id=None, sender_task_id=None, sender_run_id=None,
                           resource_refs=(), result_ids=()):
        content = content.strip()
        args = {'task_id': task_id, 'session_id': session_id, 'content': content,
            'kind': kind, 'requires_reply': requires_reply,
            'reply_to': reply_to, 'sender': sender,
            'sender_session_id': sender_session_id, 'sender_task_id': sender_task_id,
            'sender_run_id': sender_run_id,
            'resource_refs': [ref.model_dump(mode='json') for ref in resource_refs],
            'result_ids': tuple(result_ids)}
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = self.query('message', command_id)
        if old:
            self._research(old.research_id)
            if old.fingerprint != fingerprint: raise Conflict('Agent message ID reused with different input')
            fixed = self.tasks.spec(old.task_id, old.spec_version)
            self.projects._fixed_context(fixed)
            if old.resource_refs or old.result_ids:
                self.read_context(fixed.node_id, resource_refs=old.resource_refs,
                                  result_ids=old.result_ids)
            return old
        spec = self.tasks.spec(task_id)
        self._research(spec.research_id)
        if not spec.expert_session_id: raise Conflict('Task has no expert session')
        if spec.expert_session_id != session_id: raise ScopeDenied('Task is not assigned to that expert session')
        if kind not in ('question', 'information'): raise ValueError('Unknown agent message kind')
        if not content: raise ValueError('Agent message cannot be empty')
        if requires_reply and kind != 'question': raise ValueError('Only a question requests a reply')
        if resource_refs or result_ids:
            if spec.node_id is None: raise Conflict('Supplemental context requires a main conversation')
            self.read_context(spec.node_id, resource_refs=resource_refs, result_ids=result_ids)
        if sender == 'expert':
            if not sender_session_id or not sender_task_id or not sender_run_id:
                raise ScopeDenied('Expert sender requires a fixed task run')
            source = self.tasks.spec(sender_task_id)
            binding = self.tasks.query('run', sender_run_id)
            if source.research_id != spec.research_id or source.expert_session_id != sender_session_id or \
                    binding is None or binding.task_id != source.id or binding.spec_version != source.version:
                raise ScopeDenied('Expert sender identity does not match its active task')
            if not source.context_packet_id or session_id not in self.packet(source.context_packet_id).collaborator_session_ids:
                raise ScopeDenied('Expert collaboration was not granted in the handoff')
            if source.id == task_id: raise Conflict('Expert cannot message its own task as a collaborator')
        elif sender_session_id or sender_task_id or sender_run_id:
            raise ScopeDenied('Only an expert task run may claim an expert sender')
        session = self.query('session', spec.expert_session_id)
        if session is None or session.research_id != spec.research_id:
            raise ScopeDenied('Expert session unavailable')
        self.projects._fixed_context(spec)
        if reply_to and not any(m.id == reply_to for m in session.messages):
            raise Conflict('Reply target is not in the expert session')
        value = AgentMessage(id=command_id, fingerprint=fingerprint, research_id=spec.research_id,
            task_id=task_id, session_id=session.id, spec_version=spec.version,
            sender=sender, kind=kind, content=content, reply_to=reply_to,
            sender_session_id=sender_session_id, sender_task_id=sender_task_id,
            sender_run_id=sender_run_id,
            resource_refs=tuple(resource_refs), result_ids=tuple(result_ids),
            requires_reply=requires_reply)
        with self.database.transaction() as tx: tx.collaboration.insert('messages', value.id, value)
        return value

    async def execute_message(self, message_id):
        async with self.locks.setdefault('message:' + message_id, asyncio.Lock()):
            value = self.query('message', message_id)
            if value is None: raise KeyError(message_id)
            self._research(value.research_id)
            if value.status != 'queued': return value
            attempt = value.model_copy(update={'attempts': value.attempts + 1})
            with self.database.transaction() as tx: tx.collaboration.save_message(attempt, 'queued')
            try:
                spec = self.tasks.spec(value.task_id)
                if spec.version != value.spec_version or spec.expert_session_id != value.session_id:
                    raise Conflict('Expert task changed before message delivery')
                session = self.query('session', value.session_id)
                if value.requires_reply:
                    thread = await self.projects.discuss(value.task_id, value.content,
                        session.revision, value.id, sender=value.sender, reply_to=value.reply_to,
                        sender_session_id=value.sender_session_id,
                        resource_refs=value.resource_refs, result_ids=value.result_ids)
                    response_id = f'expert-reply:{value.id}'
                    if not any(m.id == response_id for m in thread.messages):
                        raise Conflict('Expert reply was not saved')
                else:
                    self.projects.post_information(value.task_id, value.content, value.id,
                        sender=value.sender, reply_to=value.reply_to,
                        sender_session_id=value.sender_session_id,
                        resource_refs=value.resource_refs, result_ids=value.result_ids)
                    response_id = None
                completed = attempt.model_copy(update={
                    'status': 'consumed' if value.requires_reply else 'delivered',
                    'response_id': response_id,
                    'consumer_turn_id': response_id if value.requires_reply else None})
            except (ScopeDenied, Conflict) as exc:
                completed = attempt.model_copy(update={'status': 'failed', 'error': str(exc)[:500]})
            except Exception as exc:
                logger.exception('Agent message delivery failed for %s', message_id)
                completed = attempt.model_copy(update={
                    'status': 'failed' if attempt.attempts >= 3 else 'queued',
                    'error': str(exc)[:500]})
            with self.database.transaction() as tx: tx.collaboration.save_message(completed, 'queued')
            return completed

    async def recover_pending_messages(self):
        for message in self.query('pending_messages'):
            try:
                await self.execute_message(message.id)
            except Exception:
                logger.exception('Agent message recovery failed for %s', message.id)

    def cancellation_requested(self, task_id, version):
        if any(value.spec_version == version and value.status in ('requested', 'stopping', 'cancelled')
               for value in self.query('cancellations_for', task_id)):
            return True
        spec = self.tasks.spec(task_id, version)
        ancestor = spec.parent_task_id
        while ancestor:
            parent = self.tasks.spec(ancestor)
            if any(value.status in ('requested', 'stopping', 'cancelled')
                   for value in self.query('cancellations_for', ancestor)):
                return True
            ancestor = parent.parent_task_id
        return False

    def cancel_task(self, *, command_id, task_id, expected_version, reason, include_children=False,
                    notify_manager=False):
        reason = reason.strip()
        args = {'task_id': task_id, 'expected_version': expected_version,
            'reason': reason, 'include_children': include_children, 'notify_manager': notify_manager}
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = self.query('cancellation', command_id)
        if old:
            self._research(old.research_id)
            if old.fingerprint != fingerprint: raise Conflict('Cancellation command ID reused')
            return old
        if not reason: raise ValueError('Cancellation reason required')
        spec = self.tasks.spec(task_id)
        self._research(spec.research_id)
        if spec.version != expected_version: raise Conflict('Task spec version changed')
        if spec.expert_session_id is None: raise Conflict('Task has no assigned expert session')
        self.projects._fixed_context(spec)
        if self.cancellation_requested(task_id, expected_version):
            raise Conflict('Task version already has a cancellation request')
        value = TaskCancellation(id=command_id, fingerprint=fingerprint,
            research_id=spec.research_id, task_id=task_id, spec_version=expected_version,
            reason=reason, include_children=include_children)
        children = []
        if include_children:
            specs = self.tasks.query('list_specs', spec.research_id)
            descendants = {task_id}
            while True:
                added = {item.id for item in specs if item.parent_task_id in descendants}
                if added <= descendants: break
                descendants.update(added)
            for child in specs:
                if child.id not in descendants or child.id == task_id or not child.expert_session_id:
                    continue
                if self.cancellation_requested(child.id, child.version): continue
                child_id = str(uuid5(NAMESPACE_URL, f'{command_id}:child:{child.id}:v{child.version}'))
                children.append(TaskCancellation(id=child_id, fingerprint=fingerprint,
                    research_id=spec.research_id, task_id=child.id,
                    spec_version=child.version, reason=f'Parent task stopped: {reason}'))
        with self.database.transaction() as tx:
            tx.collaboration.insert('cancellations', value.id, value)
            for child in children: tx.collaboration.insert('cancellations', child.id, child)
            if notify_manager:
                tx.collaboration.insert('notices', 'notice:' + command_id, ManagerNotice(
                    id='notice:' + command_id, research_id=spec.research_id, node_id=spec.node_id,
                    task_id=task_id, kind='task_cancellation', content=reason))
        return value

    async def process_cancellation(self, command_id):
        value = self.query('cancellation', command_id)
        if value is None: raise KeyError(command_id)
        self._research(value.research_id)
        if value.status not in ('requested', 'stopping'): return value
        runs = [r for r in self.tasks.query('list_runs', value.task_id)
            if r.spec_version == value.spec_version]
        if self.durable:
            intent = self.query('intent_for_task', value.task_id)
            job_ids = [f'collaboration:{intent.command_id}'] if intent else []
            job_ids.append(f'{value.task_id}-v{value.spec_version}')
            for job_id in job_ids:
                try:
                    job = await self.durable.status(job_id)
                except KeyError:
                    continue
                if job['status'] not in ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'):
                    await self.durable.cancel(job_id)
        else:
            self.tasks.stop(value.task_id)
        intent = self.query('intent_for_task', value.task_id)
        if intent and intent.status in ('accepted', 'queued') and not runs:
            failed = intent.model_copy(update={'status': 'failed', 'error': 'Task cancelled: ' + value.reason[:400]})
            with self.database.transaction() as tx: tx.collaboration.save_intent(failed, intent.status)
        runs = [r for r in self.tasks.query('list_runs', value.task_id)
            if r.spec_version == value.spec_version]
        records = [self.tasks.ledger.get(r.id) for r in runs]
        active = any(record is None or record.status in ('queued', 'running', 'saving', 'interrupted')
            for record in records)
        cancelled_result = any(record and (record.status == 'cancelled' or
            (record.result and record.result.error and record.result.error.code == 'task_cancelled'))
            for record in records)
        status = 'stopping' if active else 'cancelled' if not runs or cancelled_result else 'completed'
        settled = value.model_copy(update={'status': status})
        with self.database.transaction() as tx: tx.collaboration.save_cancellation(settled, value.status)
        return settled

    async def recover_pending_cancellations(self):
        for value in self.query('pending_cancellations'):
            try: await self.process_cancellation(value.id)
            except Exception: logger.exception('Task cancellation recovery failed for %s', value.id)

    def revise_task(self, *, command_id, task_id, expected_version, request, mode,
                    notify_manager=False):
        request = request.strip()
        args = {'task_id': task_id, 'expected_version': expected_version,
            'request': request, 'mode': mode, 'notify_manager': notify_manager}
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old_command = self.query('revision', command_id)
        if old_command:
            self._research(old_command.research_id)
            if old_command.fingerprint != fingerprint: raise Conflict('Revision command ID reused')
            return old_command
        if mode not in ('save', 'after_current', 'replace'): raise ValueError('Unknown revision mode')
        if not request: raise ValueError('Revised task request required')
        spec = self.tasks.spec(task_id)
        self._research(spec.research_id)
        if spec.version != expected_version: raise Conflict('Task spec version changed')
        if spec.node_id is None or spec.expert_session_id is None:
            raise Conflict('Collaboration revision requires an assigned expert conversation')
        self.projects._fixed_context(spec)
        value = TaskRevision(id=command_id, fingerprint=fingerprint,
            research_id=spec.research_id, task_id=task_id,
            expected_version=expected_version, request=request, mode=mode)
        with self.database.transaction() as tx:
            tx.collaboration.insert('revisions', value.id, value)
            if notify_manager:
                tx.collaboration.insert('notices', 'notice:' + command_id, ManagerNotice(
                    id='notice:' + command_id, research_id=spec.research_id, node_id=spec.node_id,
                    task_id=task_id, kind='task_revision', content=request))
        return value

    async def process_revision(self, revision_id):
        value = self.query('revision', revision_id)
        if value is None: raise KeyError(revision_id)
        async with self.locks.setdefault('revision:' + value.task_id, asyncio.Lock()):
            try:
                return await self._process_revision_locked(revision_id)
            except (ScopeDenied, Conflict, ValueError) as exc:
                current = self.query('revision', revision_id)
                if current.status not in ('queued', 'saved', 'running'): raise
                failed = current.model_copy(update={'status': 'failed', 'error': str(exc)[:500]})
                with self.database.transaction() as tx:
                    tx.collaboration.save_revision(failed, current.status)
                return failed

    async def _process_revision_locked(self, revision_id):
        value = self.query('revision', revision_id)
        self._research(value.research_id)
        if value.status in ('complete', 'failed') or (value.status == 'saved' and value.mode == 'save'):
            return value
        if value.status == 'queued':
            old = self.tasks.spec(value.task_id)
            if old.version != value.expected_version:
                failed = value.model_copy(update={'status': 'failed', 'error': 'Task spec version changed'})
                with self.database.transaction() as tx: tx.collaboration.save_revision(failed, 'queued')
                return failed
            runs = [r for r in self.tasks.query('list_runs', old.id)
                if r.spec_version == old.version]
            active = [r for r in runs if (record := self.tasks.ledger.get(r.id)) is None
                or record.status in ('queued', 'running', 'saving')]
            if active:
                if value.mode != 'replace': return value
                cancellation_id = 'revision-cancel:' + value.id
                cancellation = self.cancel_task(command_id=cancellation_id,
                    task_id=old.id, expected_version=old.version,
                    reason='Replaced by task revision ' + value.id)
                await self.process_cancellation(cancellation.id)
                if value.cancellation_id is None:
                    updated = value.model_copy(update={'cancellation_id': cancellation.id})
                    with self.database.transaction() as tx: tx.collaboration.save_revision(updated, 'queued')
                    value = updated
                return value
            chat = ChatService(self.research, self.tasks).read(old.node_id)
            latest = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
            if latest is None: raise Conflict('Revision requires a user instruction')
            selected = self.read_context(old.node_id,
                tuple(m.id for m in old.chat_context), old.required_refs, old.required_results)
            loop = self.review.get(old.id)
            automatic = loop and any(round.revision_id == value.id for round in loop.rounds)
            user_instruction = loop.original_request if automatic else value.request
            packet = ContextPacket(id=str(uuid5(NAMESPACE_URL, value.id + ':packet')),
                research_id=old.research_id, sender_node_id=old.node_id,
                sender_chat_revision=chat.revision, user_message_id=latest.id,
                user_instruction=user_instruction, manager_instruction=value.request,
                agent=old.agent, authorized_refs=old.authorized_refs,
                project=project_context(self.research, old.research_id, old.coding), limits=old.limits,
                selected_messages=tuple(selected['messages']),
                selected_results=tuple(selected['results']),
                resource_refs=old.required_refs, result_ids=old.required_results,
                delivery_requirements='Follow the revised task request')
            if old.context_packet_id:
                source_packet = self.packet(old.context_packet_id)
                packet = packet.model_copy(update={
                    'collaborator_session_ids': source_packet.collaborator_session_ids})
            spec = self.tasks.prepare_spec(research_id=old.research_id, kind=old.kind,
                user_request=value.request, goal=user_instruction, node_id=old.node_id,
                parent_task_id=old.parent_task_id, required_refs=old.required_refs,
                optional_refs=old.optional_refs, required_results=old.required_results,
                allow_partial_results=old.allow_partial_results, model=old.model,
                task_id=old.id, expert_id=old.expert_id,
                evidence_required=old.evidence_required, limits=old.limits,
                method_version=None,
                memory_ids=tuple(m.id for m in old.memory_refs),
                expert_session_id=old.expert_session_id, context_packet_id=packet.id,
                selected_messages=old.chat_context, coding=old.coding,
                agent_mode=old.agent.mode, role_instructions=old.agent.instructions, authorized_refs=old.authorized_refs)
            self._check_budget(old.research_id,
                new_task=False, parent_task_id=old.parent_task_id)
            saved = value.model_copy(update={'status': 'saved', 'new_version': spec.version})
            dependencies = self._dependencies(old.research_id, old.id, spec.version, selected['results'])
            with self.database.transaction() as tx:
                tx.collaboration.insert('packets', packet.id, packet)
                tx.tasks.add_spec(spec)
                for edge in dependencies: tx.collaboration.insert('dependencies', edge.id, edge)
                tx.collaboration.save_revision(saved, 'queued')
            value = saved
        if value.mode == 'save': return value
        version = value.new_version
        run_id = self.tasks.prepare_durable(value.task_id, version)
        if value.status == 'saved':
            running = value.model_copy(update={'status': 'running', 'run_id': run_id})
            with self.database.transaction() as tx: tx.collaboration.save_revision(running, 'saved')
            value = running
        receipt = None
        try:
            if self.durable:
                job_id = f'{value.task_id}-v{version}'
                await self.durable.submit(job_id, (value.task_id,))
                while True:
                    job = await self.durable.status(job_id)
                    if job['status'] in ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'):
                        break
                    await asyncio.sleep(0.5)
                if job['status'] != 'SUCCESS': raise Conflict('Revised task workflow ended: ' + job['status'])
            else:
                await self.tasks.run_durable(value.task_id, version, run_id)
            record = self.tasks.ledger.get(run_id)
            receipt = self.tasks.query('receipt', f'task-result:{run_id}')
            if record is None or record.status != 'succeeded' or receipt is None or receipt.delivery == 'rework':
                reason = (record.result.error.message if record and record.result and record.result.error
                          else 'Revised task did not produce a successful result')
                completed = value.model_copy(update={'status': 'failed', 'error': reason[:500]})
            else:
                completed = value.model_copy(update={'status': 'complete'})
        except Exception as exc:
            completed = value.model_copy(update={'status': 'failed', 'error': str(exc)[:500]})
        node_id = self.tasks.spec(value.task_id, version).node_id
        with self.database.transaction() as tx:
            tx.collaboration.save_revision(completed, value.status)
            if tx.collaboration.notice('notice:' + value.id):
                notice_id = 'notice:revision-result:' + value.id
                tx.collaboration.insert('notices', notice_id, ManagerNotice(
                    id=notice_id, research_id=value.research_id,
                    node_id=node_id,
                    task_id=value.task_id, kind='task_revision_result',
                    content=(f'Version {version} {completed.status}; run {run_id}; '
                             f'receipt {receipt.delivery if receipt else "none"}; '
                             f'error {completed.error or "none"}')))
        return completed

    async def recover_pending_revisions(self):
        for revision in self.query('pending_revisions'):
            try:
                await self.process_revision(revision.id)
            except Exception:
                logger.exception('Task revision recovery failed for %s', revision.id)

    def resolve_waiting(self, command_id):
        intent = self.query('intent', command_id)
        if intent is None: raise KeyError(command_id)
        if intent.status != 'waiting': return intent
        self._research(intent.research_id)
        old = self.tasks.spec(intent.task_id)
        packet = self.packet(intent.packet_id)
        results = []
        for upstream_id, version in intent.waiting_for:
            source = self.tasks.spec(upstream_id, version)
            self.projects._fixed_context(source)
            runs = [run for run in self.tasks.query('list_runs', upstream_id)
                    if run.spec_version == version]
            if self.tasks.spec(upstream_id).version != version and not runs:
                return self._fail_waiting(intent, 'Upstream version was superseded before execution')
            receipts = [self.tasks.query('receipt', f'task-result:{run.id}') for run in runs]
            passed = next((receipt for receipt in receipts
                           if receipt and receipt.delivery == 'passed'), None)
            if passed is not None:
                results.append(passed.result_id)
                continue
            upstream_intent = self.query('intent_for_task', upstream_id)
            if upstream_intent and upstream_intent.status == 'failed':
                return self._fail_waiting(intent, 'Upstream task failed: ' + (upstream_intent.error or upstream_id))
            if self.cancellation_requested(upstream_id, version):
                return self._fail_waiting(intent, 'Upstream task was cancelled')
            return intent  # No reviewed result yet; a review or a later run can satisfy it.
        all_results = tuple(dict.fromkeys((*packet.result_ids, *results)))
        selected = self.read_context(packet.sender_node_id,
            tuple(message.id for message in packet.selected_messages if message.source_session_id is None),
            packet.resource_refs, all_results, packet.expert_message_refs)
        if old.kind == 'analyze' and any(row['kind'] != 'extract' for row in selected['results']):
            return self._fail_waiting(intent, 'Numeric analysis requires reviewed extraction results')
        ready_packet = packet.model_copy(update={
            'id': str(uuid5(NAMESPACE_URL, command_id + ':ready-packet')),
            'selected_results': tuple(selected['results']), 'result_ids': all_results})
        ready_spec = self.tasks.prepare_spec(research_id=old.research_id, kind=old.kind,
            user_request=old.user_request, goal=old.goal, node_id=old.node_id,
            parent_task_id=old.parent_task_id, required_refs=old.required_refs,
            required_results=all_results, model=old.model, task_id=old.id,
            expert_id=old.expert_id, evidence_required=old.evidence_required,
            limits=old.limits, method_version=old.method_version,
            expert_session_id=old.expert_session_id, context_packet_id=ready_packet.id,
            selected_messages=old.chat_context, coding=old.coding,
            agent_mode=old.agent.mode, role_instructions=old.agent.instructions, authorized_refs=old.authorized_refs)
        self._check_budget(old.research_id,
            new_task=False, parent_task_id=old.parent_task_id)
        edges = self._dependencies(old.research_id, old.id, ready_spec.version, selected['results'])
        ready = intent.model_copy(update={'packet_id': ready_packet.id, 'status': 'accepted'})
        with self.database.transaction() as tx:
            tx.collaboration.insert('packets', ready_packet.id, ready_packet)
            tx.tasks.add_spec(ready_spec)
            for edge in edges: tx.collaboration.insert('dependencies', edge.id, edge)
            tx.collaboration.save_intent(ready, 'waiting')
        return ready

    def _fail_waiting(self, intent, reason):
        failed = intent.model_copy(update={'status': 'failed', 'error': reason[:500]})
        with self.database.transaction() as tx: tx.collaboration.save_intent(failed, 'waiting')
        return failed

    def recover_waiting(self):
        for intent in self.query('intents_for_status', 'waiting'):
            try:
                self.resolve_waiting(intent.command_id)
            except (ScopeDenied, Conflict) as exc:
                self._fail_waiting(intent, str(exc))
            except Exception:
                logger.exception('Waiting dependency resolution failed for %s', intent.command_id)

    async def execute(self, command_id):
        async with self.locks.setdefault(command_id, asyncio.Lock()):
            return await self._execute_locked(command_id)

    async def _execute_locked(self, command_id):
        intent = self.query('intent', command_id)
        if intent is None: raise KeyError(command_id)
        self._research(intent.research_id)
        if intent.status == 'waiting': return self.status(command_id)
        if intent.status == 'completed': return self.status(command_id)
        if intent.status == 'failed': return self.status(command_id)
        if self.cancellation_requested(intent.task_id, self.tasks.spec(intent.task_id).version):
            cycle = self.review.get(intent.task_id)
            if cycle and cycle.status in ('executing', 'reviewing', 'revising'):
                self.review.change(cycle, status='cancelled', reason='Task cancellation requested')
            failed = intent.model_copy(update={'status': 'failed', 'error': 'Task cancellation requested'})
            with self.database.transaction() as tx: tx.collaboration.save_intent(failed, intent.status)
            return self.status(command_id)
        if intent.status == 'accepted':
            queued = intent.model_copy(update={'status': 'queued'})
            with self.database.transaction() as tx: tx.collaboration.save_intent(queued, 'accepted')
            intent = queued
        try:
            self.packet(intent.packet_id)
            self.review.ensure(intent.task_id)
            if self.durable is None:
                version = self.tasks.spec(intent.task_id).version
                run_id = self.tasks.prepare_durable(intent.task_id, version)
                await self.tasks.run_durable(intent.task_id, version, run_id)
            else:
                job_id = 'collaboration:' + intent.command_id
                await self.durable.submit(job_id, [intent.task_id])
                while True:
                    job = await self.durable.status(job_id)
                    if job['status'] in ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'):
                        break
                    await asyncio.sleep(0.5)
                if job['status'] != 'SUCCESS': raise Conflict('Expert workflow ended: ' + job['status'])
            runs = self.tasks.query('list_runs', intent.task_id)
            if not runs:
                raise Conflict('Expert workflow ended without an admitted run')
            record = self.tasks.ledger.get(runs[-1].id)
            if record is None or record.status != 'succeeded':
                detail = record.result.error.message if record and record.result and record.result.error else 'No successful result'
                raise Conflict('Expert run failed: ' + detail)
            if not self.tasks.query('receipt', f'task-result:{runs[-1].id}'):
                raise Conflict('Expert run ended without a published result')
            cycle = await self.review.run(intent.task_id)
            if cycle.status in ('executing', 'reviewing', 'revising'): return self.status(command_id)
            if cycle.status not in ('passed', 'exhausted'):
                raise Conflict('Automatic collaboration stopped: ' + (cycle.reason or cycle.status))
            completed = intent.model_copy(update={'status': 'completed'})
        except Exception as exc:
            cycle = self.review.get(intent.task_id)
            if cycle and cycle.status in ('executing', 'reviewing', 'revising'):
                self.review.change(cycle, status='failed', reason='Executor or collaboration failed')
            completed = intent.model_copy(update={'status': 'failed', 'error': str(exc)[:500]})
        with self.database.transaction() as tx: tx.collaboration.save_intent(completed, 'queued')
        return self.status(command_id)

    async def execute_assigned_task(self, task_id):
        """UI-created assigned tasks use the same durable admission and review cycle."""
        intent = self.query('intent_for_task', task_id)
        prior = self.review.get(task_id)
        manual_revision = prior and prior.status not in ('executing', 'reviewing', 'revising') and prior.current_version != self.tasks.spec(task_id).version
        if intent and not manual_revision: return await self.execute(intent.command_id)
        async with self.locks.setdefault('assigned:' + task_id, asyncio.Lock()):
            loop = self.review.ensure(task_id)
            if loop.status not in ('executing', 'reviewing', 'revising') and not manual_revision: return loop
            try:
                if loop.status in ('executing', 'reviewing') or manual_revision:
                    spec = self.tasks.spec(task_id) if manual_revision else self.review.validate(loop)
                    run_id = self.tasks.prepare_durable(spec.id, spec.version)
                    if self.durable:
                        job_id = f'{spec.id}-v{spec.version}'
                        await self.durable.submit(job_id, (spec.id,))
                        while True:
                            job = await self.durable.status(job_id)
                            if job['status'] in ('SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'): break
                            await asyncio.sleep(.5)
                        if job['status'] != 'SUCCESS':
                            if manual_revision: raise Conflict('Background executor workflow ended')
                            return self.review.change(loop, status='cancelled' if job['status']=='CANCELLED' else 'failed',
                                reason='Background executor workflow ended')
                    else:
                        await self.tasks.run_durable(spec.id, spec.version, run_id)
                if manual_revision: return loop  # Explicit edits do not inherit a previous verdict or restart its two-round allowance.
                return await self.review.run(task_id)
            except asyncio.CancelledError:
                if manual_revision: raise
                if self.cancellation_requested(task_id, self.tasks.spec(task_id).version):
                    return self.review.change(self.review.get(task_id), status='cancelled', reason='Task cancellation requested')
                raise
            except (ScopeDenied, KeyError, Conflict):
                if manual_revision: raise
                return self.review.change(self.review.get(task_id), status='blocked', reason='Authorization or task changed')
            except Exception as exc:
                if manual_revision: raise
                from ..execution.service import PublicationError
                return self.review.change(self.review.get(task_id),
                    status='blocked' if isinstance(exc, PublicationError) else 'failed',
                    reason='Execution outcome is unconfirmed' if isinstance(exc, PublicationError) else type(exc).__name__)

    async def recover_reviews(self):
        for root in self.research.query('list_researches', self.research.owner):
            for loop in self.query('review_loops_for', root.id):
                if loop.status not in ('executing', 'reviewing', 'revising') or self.query('intent_for_task', loop.task_id): continue
                try: await self.execute_assigned_task(loop.task_id)
                except (ScopeDenied, KeyError, Conflict):
                    current = self.review.get(loop.id)
                    if current.status in ('executing', 'reviewing', 'revising'):
                        self.review.change(current, status='blocked', reason='Authorization or task changed')

    async def recover_pending(self):
        limit = asyncio.Semaphore(2)
        async def recover(intent):
            async with limit:
                try:
                    await self.execute(intent.command_id)
                except Exception:
                    logger.exception('Collaboration dispatch recovery failed for %s', intent.command_id)
        await asyncio.gather(*(recover(intent) for intent in self.query('pending')))

    def completed_for_node(self, node_id, user_message_id):
        node = self.research.node(node_id)
        rows = []
        for intent in self.query('intents_for', node.research_id):
            if intent.status != 'completed': continue
            packet = self.query('packet', intent.packet_id)
            if packet.sender_node_id != node_id or packet.user_message_id != user_message_id: continue
            status = self.status(intent.command_id)
            cycle = self.review.visible(intent.task_id)
            for receipt in status['receipts']:
                if cycle and cycle.current_version == self.tasks.spec(intent.task_id).version and cycle.final_result_id and receipt.result_id != cycle.final_result_id: continue
                record = self.tasks.ledger.get(receipt.run_id)
                if record is None: continue
                output = record.result.output if record.result and isinstance(record.result.output, dict) else {}
                rows.append({'task_id': intent.task_id, 'session_id': intent.session_id,
                    'result_id': receipt.result_id, 'delivery': receipt.delivery,
                    'execution_status': record.status,
                    'error': record.result.error.message if record.result and record.result.error else None,
                    'report': output.get('report'), 'missing': receipt.missing,
                    'automatic_review': cycle.model_dump(mode='json') if cycle and receipt.result_id == cycle.final_result_id else None,
                    'assumptions': receipt.assumptions, 'conflicts': receipt.conflicts,
                    'limitations': receipt.limitations,
                    'resource_refs': [ref.model_dump(mode='json') for ref in receipt.resource_refs]})
        return rows

    def pending_intents_for_node(self, node_id):
        node = self.research.node(node_id)
        intents = self.query('intents_for', node.research_id)
        unfinished_groups = {intent.manager_run_id for intent in intents
            if intent.status not in ('completed', 'failed')}
        return [intent for intent in intents
            if intent.status in ('completed', 'failed') and intent.resume_status in ('pending', 'deferred')
            and intent.manager_run_id not in unfinished_groups
            and intent.manager_run_id and self.query('packet', intent.packet_id).sender_node_id == node_id]

    def pending_notices_for_node(self, node_id):
        node = self.research.node(node_id)
        return [notice for notice in self.query('notices_for', node.research_id)
                if notice.node_id == node_id and notice.status in ('pending', 'deferred')]

    def pending_notice_summaries_for_node(self, node_id):
        summaries = []
        for value in self.pending_notices_for_node(node_id):
            row = {'notice_id': value.id, 'kind': value.kind,
                   'task_id': value.task_id, 'content': value.content}
            if value.kind == 'information_request' and value.id.removesuffix(':notice') not in {
                    item.id for item in self.information.visible_for(value.task_id)}:
                row.update(content='补充信息的访问已撤回。', task_access='unavailable')
                summaries.append(row)
                continue
            spec = self.tasks.spec(value.task_id)
            try:
                self.projects._fixed_context(spec)
            except (ScopeDenied, Conflict, KeyError):
                row['task_access'] = 'unavailable'
            else:
                runs = self.tasks.query('list_runs', value.task_id)
                row['task_version'] = spec.version
                if runs:
                    latest = runs[-1]
                    record = self.tasks.ledger.get(latest.id)
                    receipt = self.tasks.query('receipt', f'task-result:{latest.id}')
                    row['latest_run'] = {'spec_version': latest.spec_version,
                        'execution_status': record.status if record else 'unknown',
                        'delivery': receipt.delivery if receipt else None,
                        'error': record.result.error.message if record and record.result and record.result.error else None}
            summaries.append(row)
        return summaries

    def pending_results_for_node(self, node_id):
        rows = []
        for intent in self.pending_intents_for_node(node_id):
            packet = self.query('packet', intent.packet_id)
            if intent.status == 'failed':
                rows.append({'task_id': intent.task_id, 'session_id': intent.session_id,
                    'result_id': 'failed:' + intent.command_id, 'delivery': None,
                    'execution_status': 'failed', 'error': intent.error,
                    'report': None, 'missing': ('Expert task failed',),
                    'assumptions': (), 'conflicts': (), 'limitations': (), 'resource_refs': []})
            else:
                rows.extend(row for row in self.completed_for_node(node_id, packet.user_message_id)
                            if row['task_id'] == intent.task_id)
        return list({row['result_id']: row for row in rows}.values())

    async def resume_manager(self):
        from ..contracts import RunLimits
        from ..research.models import NodeCommand
        for intent in (*self.query('intents_for_status', 'completed'),
                       *self.query('intents_for_status', 'failed')):
            if not intent.manager_run_id or intent.resume_status != 'pending': continue
            try:
                await self._resume_one_manager(intent, RunLimits, NodeCommand)
            except Exception as exc:
                logger.exception('Collaboration manager resume failed for %s', intent.command_id)
                try:
                    current = self.query('intent', intent.command_id)
                    settled = current.model_copy(update={'resume_status': 'deferred',
                        'resume_error': str(exc)[:500]})
                    with self.database.transaction() as tx:
                        tx.collaboration.save_intent(settled, current.status)
                except Exception:
                    logger.exception('Could not defer failed manager resume for %s', intent.command_id)
        for notice in self.query('pending_notices'):
            try:
                await self._resume_notice(notice, RunLimits, NodeCommand)
            except Exception:
                logger.exception('Manager notice resume failed for %s', notice.id)

    async def _resume_notice(self, notice, RunLimits, NodeCommand):
        notice = self.query('notice', notice.id)
        if notice.status != 'pending': return
        chat = ChatService(self.research, self.tasks).read(notice.node_id)
        latest = chat.messages[-1] if chat.messages else None
        if latest is None or latest.role != 'assistant':
            return  # A pending user turn consumes the notice in its own fixed input.
        node = self.research.node(notice.node_id)
        if node.active_run_id:
            active = self.research.ledger.get(node.active_run_id)
            if active is None or active.status in ('queued', 'running', 'saving'): return
        if notice.resume_attempt >= 2:
            with self.database.transaction() as tx:
                tx.collaboration.save_notice(notice.model_copy(update={
                    'status': 'deferred', 'error': 'Manager continuation failed twice'}), 'pending')
            return
        attempt = notice.resume_attempt + 1
        with self.database.transaction() as tx:
            tx.collaboration.save_notice(notice.model_copy(update={'resume_attempt': attempt}), 'pending')
        command = NodeCommand(command_id=f'resume-notice:{notice.id}:{attempt}',
            research_id=node.research_id, node_id=node.id, kind='chat_followup',
            expected_revision=node.revision, chat_revision=chat.revision,
            instructions=('专家会话或任务状态有新的用户操作。依据本轮保存的通知，简明告知用户当前状态；'
                          '普通讨论不代表任务目标已改变，取消请求不等于实际已停止。'),
            limits=RunLimits(timeout_seconds=180, max_calls=0))
        try:
            await self.research.submit(command)
        except Conflict:
            return  # Another chat turn won the revision race; it will consume the notice.

    async def _resume_one_manager(self, intent, RunLimits, NodeCommand):
        intent = self.query('intent', intent.command_id)
        if intent.resume_status != 'pending': return
        origin = self.research.ledger.get(intent.manager_run_id)
        if origin is None or origin.status in ('running', 'saving'): return
        if intent.resume_attempt >= 2:
            settled = intent.model_copy(update={'resume_status': 'deferred',
                'resume_error': 'Manager continuation failed twice; requires user or operator action'})
            with self.database.transaction() as tx: tx.collaboration.save_intent(settled, intent.status)
            return
        packet = self.packet(intent.packet_id)
        if intent.command_id not in {row.command_id for row in
                self.pending_intents_for_node(packet.sender_node_id)}:
            return  # Aggregate only after every task dispatched by the same turn has settled.
        chat = ChatService(self.research, self.tasks).read(packet.sender_node_id)
        latest = chat.messages[-1] if chat.messages else None
        if latest is None: return
        if latest.role == 'user' and (latest.id != packet.user_message_id or
                                      chat.revision != packet.sender_chat_revision):
            return  # The new user turn will receive pending results in its fixed input.
        if latest.role not in ('user', 'assistant'): return
        node = self.research.node(packet.sender_node_id)
        if node.active_run_id:
            active = self.research.ledger.get(node.active_run_id)
            if active is None or active.status in ('queued', 'running', 'saving'):
                return
        attempt = intent.resume_attempt + 1
        group = [row for row in self.pending_intents_for_node(node.id)
                 if row.manager_run_id == intent.manager_run_id]
        with self.database.transaction() as tx:
            for row in group:
                tx.collaboration.save_intent(row.model_copy(update={'resume_attempt': attempt}), row.status)
        command = NodeCommand(command_id=f'resume:{intent.command_id}:{attempt}',
            research_id=node.research_id, node_id=node.id,
            kind='chat_reply' if latest.role == 'user' else 'chat_followup',
            expected_revision=node.revision, chat_revision=chat.revision,
            instructions='本轮专家任务已结束。根据保存的执行结果和相关报告，继续完成用户目标。',
            limits=RunLimits(timeout_seconds=180, max_calls=8))
        try:
            await self.research.submit(command)
        except Exception:
            logger.exception('Collaboration manager continuation failed for %s', intent.command_id)
            return
        current = self.query('intent', intent.command_id)
        record = self.research.query('command', command.command_id)
        result = self.research.ledger.get(record.run_id) if record and record.run_id else None
        if result and result.status == 'succeeded':
            return  # The chat message and notification acknowledgement commit atomically.
