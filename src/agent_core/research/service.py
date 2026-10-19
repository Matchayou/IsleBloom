"""Research commands compose public scope, context, execution and dispatch services."""
import asyncio
from ..contracts import RunRequest
from ..contracts import Message
from ..agent_identity import AgentIdentity
from ..context.assembler import project_context
from ..context.mounts import ResultMount
from ..context.scopes import ScopeDenied
from ..context.execution import ScopeExecutionGate
from ..execution.ports import Conflict
from ..execution.service import PublicationError
from ..execution.ledger import Ledger
from ..resources.snapshots import identity
from .models import Research, ResearchNode, NodeCommand, CommandRecord, NodeRun


class ResearchService:
    def __init__(self, database, scopes, execution_factory, owner='local'):
        if scopes is None:
            raise ValueError('Research unavailable without ScopeService')
        self.database, self.scopes, self.execution_factory, self.owner = database, scopes, execution_factory, owner
        self.ledger = Ledger(database)
        self.locks, self.tasks, self.executors = {}, {}, {}
        self.chat_tasks = None

    def query(self, method, *args):
        with self.database.transaction() as tx:
            return getattr(tx.research, method)(*args)

    def research(self, key):
        value = self.query('research', key)
        if value.owner != self.owner: raise ScopeDenied('Research owner mismatch')
        self.scopes.current(value.scope_id, self.owner)
        return value

    def node(self, key):
        value = self.query('node', key)
        self.research(value.research_id)
        return value

    def create(self, goal, research_id=None):
        key = research_id or identity()
        try:
            old = self.research(key)
        except KeyError:
            old = None
        if old:
            if old.goal != goal: raise Conflict('Research ID reused')
            return old
        scope = self.scopes.create(self.owner, tools=('read_source', 'search_source', 'search_web', 'fetch_web'))
        value = Research(id=key, owner=self.owner, goal=goal, scope_id=scope.id)
        with self.database.transaction() as tx: tx.research.create_research(value)
        return value

    def _excluded_refs(self, research_id):
        return {r for n in self.query('list_nodes', research_id) if n.selection == 'excluded' for r in n.resource_refs}

    def check_result(self, key, target_scope=None, seen=frozenset(), allow_excluded=False):
        if key in seen: raise ScopeDenied('Result dependency cycle')
        result = self.query('result', key)
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine):
            self.engine.validate_run(result.run_id, require_started=True)
        source = self.node(result.node_id)
        if source.selection == 'excluded' and not allow_excluded: raise ScopeDenied('Input node excluded')
        source_scope = self.scopes.current(source.scope_id, self.owner)
        root = self.research(source.research_id)
        root_scope = self.scopes.current(root.scope_id, self.owner)
        for ref in result.resource_refs:
            self.scopes.check(source_scope, ref)
            # Original research library revocation also invalidates derived content.
            self.scopes.check(root_scope, ref)
            if target_scope is not None: self.scopes.check(target_scope, ref)
        for dep in self.query('inputs', key):
            self.check_result(dep, target_scope, seen | {key}, allow_excluded=allow_excluded)
        return result

    def read_result(self, key):
        result = self.check_result(key, allow_excluded=True)
        record = self.ledger.get(result.run_id)
        if record is None or record.result is None: raise Conflict('Result publication missing')
        return result, record.result.output['report']

    def result_files(self, key):
        """Authorize each read against the immutable published result and its inputs."""
        self.read_result(key)
        from ..delivery_files import files_from_output
        return files_from_output(self.ledger.get(self.query('result', key).run_id).result.output)

    def _configure_scope(self, node, refs, result_ids):
        root = self.research(node.research_id)
        source = self.scopes.current(root.scope_id, self.owner)
        scope = self.scopes.current(node.scope_id, self.owner)
        resources = list(refs)
        for key in result_ids:
            result = self.check_result(key)
            if self.node(result.node_id).research_id != root.id: raise ScopeDenied('Cross-research result')
            resources.extend(result.resource_refs)
        resources = tuple(dict.fromkeys(resources))
        if set(resources) & self._excluded_refs(root.id): raise ScopeDenied('Excluded company material or mixed summary')
        for ref in resources:
            self.scopes.check(source, ref)
            if ref not in scope.grants:
                scope = self.scopes.grant(scope, source, ref)
        if scope.allowed == resources and not scope.inherited and not scope.web_urls:
            return scope, resources
        return self.scopes.select(scope, resources), resources

    def _validate_command(self, command):
        self.research(command.research_id)
        if len(set(command.result_ids)) != len(command.result_ids): raise ValueError('Duplicate result input')
        if command.display_parent_id:
            if self.node(command.display_parent_id).research_id != command.research_id:
                raise Conflict('Display parent belongs to another research')
        if command.node_id:
            node = self.node(command.node_id)
            if node.research_id != command.research_id: raise Conflict('Node belongs to another research')
            if command.expected_revision is not None and node.revision != command.expected_revision:
                raise Conflict('Node revision changed')
        elif command.kind not in ('create', 'compare'):
            raise ValueError('Command requires node_id')

    def _accept(self, command):
        old = self.query('command', command.command_id)
        if old:
            if old.command != command: raise Conflict('Command ID reused with different content')
            self.research(command.research_id)
            return old, False
        self._validate_command(command)
        creating = command.kind in ('create', 'branch') or command.kind == 'compare' and command.node_id is None
        node = self.node(command.node_id) if command.node_id else None
        if node and node.plan_proposal_id and command.kind not in ('branch','move'):
            self.planning.validate_step(node, command.command_id)
        if creating:
            root_scope = self.scopes.current(self.research(command.research_id).scope_id,self.owner)
            allowed = set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
            scope = self.scopes.create(self.owner,tools=tuple(sorted(set(command.allowed_tools) & allowed)))
            node = ResearchNode(research_id=command.research_id, scope_id=scope.id,
                display_parent_id=node.id if node else command.display_parent_id, goal=command.goal or command.instructions or '比较结果',
                instructions=command.instructions, allowed_tools=command.allowed_tools, resource_refs=command.resource_refs,
                result_ids=command.result_ids, position=command.position)
            self._configure_scope(node, command.resource_refs, command.result_ids)
        record = CommandRecord(command=command, node_id=node.id)
        with self.database.transaction() as tx:
            if creating: tx.research.create_node(node)
            tx.research.create_command(record)
        return record, True

    async def submit(self, command: NodeCommand):
        if command.node_id and self.node(command.node_id).plan_proposal_id and command.kind not in ('branch','move'):
            raise ScopeDenied('Approved plan nodes are managed by the plan executor')
        record, fresh = self._accept(command)
        if not fresh: return record
        kind = command.kind
        if kind in ('create', 'branch', 'select', 'exclude', 'move', 'mount'):
            try:
                return self._mutate(record)
            except Exception as exc:
                self._settle(record, 'blocked', type(exc).__name__)
                raise
        lock = self.locks.setdefault(record.node_id, asyncio.Lock())
        async with lock:
            try:
                node = self.node(record.node_id)
                if node.selection == 'excluded': raise ScopeDenied('Node excluded; no new dispatch')
                binding, snapshot = self._prepare(record)
                executor, _ = self.execution_factory({node.id: self.scopes.current(node.scope_id, self.owner)}, self)
                self.executors[binding.run_id] = executor
                self.tasks[binding.run_id] = asyncio.current_task()
                await executor.execute(RunRequest(run_id=binding.run_id, agent_id=node.id,
                    input=snapshot.input, limits=binding.limits))
                return self.query('command', command.command_id)
            except Exception as exc:
                # A pending C1 publication stays accepted for explicit retry_save.
                current = self.query('command', command.command_id)
                if isinstance(exc, PublicationError):
                    self._settle(current, 'accepted', exc.outcome.status)
                elif current.run_id not in self.executors:
                    self._settle(current, 'blocked', type(exc).__name__)
                raise
            finally:
                if 'binding' in locals(): self.tasks.pop(binding.run_id, None)

    def _settle(self, record, status, reason=None):
        value = record.model_copy(update={'status': status, 'reason': reason})
        with self.database.transaction() as tx: tx.research.save_command(value)
        return value

    def _mutate(self, record):
        c, node = record.command, self.node(record.node_id)
        if node.plan_proposal_id: self.planning.validate_step(node, c.command_id)
        changes = {}
        if c.kind in ('select', 'exclude'):
            changes = {'selection': 'excluded' if c.kind == 'exclude' else 'selected', 'revision': node.revision + 1}
        elif c.kind == 'move': changes = {'position': c.position}  # Layout never invalidates execution.
        elif c.kind == 'mount':
            self._configure_scope(node, c.resource_refs, c.result_ids)
            changes = {'resource_refs': c.resource_refs, 'result_ids': c.result_ids, 'revision': node.revision + 1}
        value = record.model_copy(update={'status': 'completed'})
        with self.database.transaction() as tx:
            if changes: tx.research.save_node(node.model_copy(update=changes), node.revision)
            tx.research.save_command(value)
        if c.kind in ('exclude', 'mount'):
            for run_id, task in tuple(self.tasks.items()):
                binding = self.query('run', run_id)
                try:
                    self.validate_binding(binding)
                except (ScopeDenied, Conflict):
                    task.cancel()
        return value

    def _prepare(self, record):
        import json
        c, node = record.command, self.node(record.node_id)
        if node.active_run_id:
            old = self.ledger.get(node.active_run_id)
            if old is None or old.status in ('running', 'saving'):
                raise Conflict('Node has an unfinished publication')
        goal, instructions = node.goal, node.instructions
        refs, result_ids, missing, limits = node.resource_refs, node.result_ids, c.missing, c.limits
        instruction_version = node.instruction_version
        chat_revision = None
        chat_text = None
        chat_refs = ()
        notification_ids = ()
        if c.kind in ('chat_reply', 'chat_followup'):
            from .chat import ChatService
            chat = ChatService(self, self.chat_tasks).read(node.id)
            if c.chat_revision is None or chat.revision != c.chat_revision:
                raise Conflict('Chat revision changed')
            latest = (*chat.inherited, *chat.messages)[-1] if (*chat.inherited, *chat.messages) else None
            if latest is None or latest.role != ('user' if c.kind == 'chat_reply' else 'assistant'):
                raise Conflict('Reply requires a latest user message' if c.kind == 'chat_reply'
                               else 'Background follow-up requires a latest assistant message')
            chat_revision = chat.revision
            chat_refs = tuple(dict.fromkeys(ref for m in (*chat.inherited, *chat.messages)
                                           for ref in m.resource_refs))
            import json
            chat_text = json.dumps([m.model_dump(mode='json') for m in (*chat.inherited, *chat.messages)], ensure_ascii=False)
            if getattr(self, 'collaboration', None) is not None:
                notification_ids = tuple(v.command_id for v in self.collaboration.pending_intents_for_node(node.id)) + tuple(
                    v.id for v in self.collaboration.pending_notices_for_node(node.id))
        retry_binding = None
        if c.kind == 'retry':
            runs = self.query('list_runs', node.id)
            if not runs: raise Conflict('No failed run to retry')
            # Use last admitted node revision, not UUID ordering.
            retry_binding = max(runs, key=lambda r: r.node_revision)
            previous = self.ledger.get(retry_binding.run_id)
            if previous is None or previous.status not in ('failed', 'cancelled', 'interrupted'):
                raise Conflict('Retry requires a failed/cancelled/interrupted execution')
            goal, instructions, refs, result_ids, missing, limits, instruction_version = (
                retry_binding.goal, retry_binding.instructions, retry_binding.resource_refs,
                retry_binding.result_ids, retry_binding.missing, retry_binding.limits, retry_binding.instruction_version)
        elif c.kind in ('continue', 'compare', 'adopt'):
            if c.kind == 'continue':
                goal = goal + '\n续研：' + (c.goal or c.instructions)
                instructions = instructions + '\n' + c.instructions
            elif c.goal: goal = c.goal
            refs, result_ids = c.resource_refs, c.result_ids
            instruction_version += 1
        if chat_refs:
            refs = tuple(dict.fromkeys((*refs, *chat_refs)))
        # Retry never re-grants/reselects a revoked input; all other executions explicitly assemble.
        if retry_binding:
            scope = self.scopes.current(node.scope_id, self.owner)
            for ref in refs: self.scopes.check(scope, ref)
            if set(refs) & self._excluded_refs(node.research_id): raise ScopeDenied('Retry input excluded')
            if scope.allowed != refs or scope.inherited:
                scope = self.scopes.select(scope, refs)
        else:
            scope, refs = self._configure_scope(node, refs, result_ids)
        from .aggregate import validate_source_comparison
        validate_source_comparison(self.scopes, scope, refs)
        contributions = []
        for key in result_ids:
            mount, output = self.result_input(key, scope)
            contributions.append((mount, output))
            missing += mount.missing
        run_id = identity()
        text = goal + '\n用户指令：' + instructions + '\n缺失项：' + ', '.join(c.missing if not retry_binding else ())
        plan_packet = None
        if node.plan_proposal_id:
            proposal = self.planning.get(node.plan_proposal_id)
            plan_packet = {'phase':'approved_plan_execution', 'proposal_id':proposal.id,
                'approved_revision':proposal.approved_revision, 'approved_by':proposal.approved_by,
                'step_id':node.id, 'instruction':'Execute this approved step. Planning-only restrictions in historical requests have been superseded by approval; retain all research constraints.'}
            # The approved step already contains the compiled research goal and
            # constraints. Keep the original planning request in proposal storage,
            # not in the current model command (it may say "do not execute").
            plan_packet['research_constraints'] = proposal.original_instructions
            plan_packet['planning_history_ref'] = proposal.id
        if chat_text is not None:
            text = '研究目标：' + self.research(node.research_id).goal + '\n当前分支获准对话见 history；全部用户约束保留。'
            if getattr(self, 'collaboration', None) is not None:
                completed = self.collaboration.pending_results_for_node(node.id)
                if completed:
                    catalog = [{key: row[key] for key in ('task_id', 'session_id', 'result_id',
                        'delivery', 'execution_status', 'error', 'resource_refs')} | {
                        'report_preview': (row['report'] or '')[:500]} for row in completed]
                    text += ('\n本轮专家已保存的结果及复核状态（含待通知任务，目录与短预览）：'
                        + json.dumps(catalog, ensure_ascii=False)
                        + '\n仅询问状态或失败原因时直接据此回答；需要汇总或比较报告结论时再读取选定 result_ids 的正文。预览不是完整结论。')
                notices = self.collaboration.pending_notice_summaries_for_node(node.id)
                if notices:
                    text += '\n专家会话与任务变更通知（用户操作，需按真实状态处理）：' + json.dumps(notices, ensure_ascii=False)
            if c.instructions:
                text += '\n本轮续接说明：' + c.instructions
        if retry_binding:
            from ..controlled.engine import ControlledEngine
            if isinstance(self.engine, ControlledEngine): self.engine.validate_run(retry_binding.run_id)
            original = self.scopes.repository.snapshot(retry_binding.run_id)
            if original.scope_id != scope.id or original.scope_version != scope.version:
                raise ScopeDenied('Retry fixed scope changed')
            snapshot = original.model_copy(update={'id':identity(), 'run_id':run_id})
            self.scopes.repository.save_snapshot(snapshot)
        else:
            evidence_mode = bool(node.plan_proposal_id) or c.evidence_required or (chat_text is not None and
                any(self.scopes.resources.describe(ref)[0].kind == 'web' for ref in refs))
            if chat_text is not None:
                current_message = next((m for m in reversed((*chat.inherited, *chat.messages)) if m.role == 'user'), None)
                candidates = self.context.selector.candidates_for_chat(self, node, json.loads(chat_text), refs)
                original_text = current_message.content
            else:
                # A node command has separate goal/instruction fields, not a chat
                # message. Keep each field verbatim and search only its fixed refs;
                # a top-level node does not implicitly gain the main chat library.
                original_text = c.instructions or c.goal or node.instructions or node.goal
                candidates = {
                    'memories': self.memories.list(node.research_id),
                    'sources': self.context.selector.source_candidates(self.scopes, scope, refs),
                    'retrieval_goal': goal + '\n' + instructions,
                    'business': {'node_request': {'goal': node.goal, 'instructions': node.instructions},
                        'node_command': {'kind': c.kind, 'goal': c.goal, 'instructions': c.instructions}},
                }
            snapshot = self.context.assemble(self.scopes, scope, run_id, text,
                agent=AgentIdentity(mode='assistant', id=node.id),
                original_message=Message(role='user', content=original_text),
                project=project_context(self, node.research_id), refs=refs, results=contributions,
                packet=plan_packet, lazy_sources=chat_text is not None and node.display_parent_id is None,
                evidence_mode=evidence_mode, **candidates)
        for mount in snapshot.mounts:
            _, _, parsed = self.scopes.resources.describe(mount.ref)
            missing += tuple(f'{mount.ref.resource_id}: {i.reason}' for i in parsed.issues)
        binding = NodeRun(run_id=run_id, node_id=node.id, command_id=c.command_id,
            node_revision=node.revision + 1, context_snapshot_id=snapshot.id, goal=goal, instructions=instructions,
            instruction_version=instruction_version, resource_refs=refs, result_ids=result_ids,
            missing=tuple(dict.fromkeys(missing)), limits=limits, chat_revision=chat_revision,
            notification_ids=notification_ids,
            evidence_required=retry_binding.evidence_required if retry_binding else c.evidence_required)
        with self.database.transaction() as tx:
            tx.research.create_run(binding)
            tx.research.save_node(node.model_copy(update={'revision': binding.node_revision,
                'active_run_id': run_id, 'goal': goal, 'instructions': instructions,
                'instruction_version': instruction_version, 'resource_refs': refs, 'result_ids': result_ids}), node.revision)
            tx.research.save_command(record.model_copy(update={'run_id': run_id}))
        return binding, snapshot

    def run_resource_refs(self, binding):
        """Fixed inputs plus web bodies obtained by this run, recorded by ingestion provenance."""
        node = self.node(binding.node_id)
        root = self.research(node.research_id)
        root_scope = self.scopes.current(root.scope_id, self.owner)
        node_scope = self.scopes.current(node.scope_id, self.owner)
        obtained = {(e.resource_id, e.snapshot_id) for e in
                    self.scopes.resources.repository.ingestions(root.scope_id)
                    if e.run_id == binding.run_id and e.kind == 'web'}
        refs = tuple(ref for ref in (*root_scope.grants, *node_scope.grants) if (ref.resource_id, ref.snapshot_id) in obtained
                     and self.scopes.resources.describe(ref)[2].status in ('ready', 'partial'))
        library_refs = self.collaboration.read_refs(binding.run_id) if getattr(self, 'collaboration', None) else ()
        return tuple(dict.fromkeys((*binding.resource_refs, *refs, *library_refs)))

    def validate_binding(self, binding):
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine):
            self.engine.validate_run(binding.run_id)
        node = self.node(binding.node_id)
        snapshot = self.scopes.repository.snapshot(binding.run_id)
        if getattr(self, 'learning', None): self.learning.validate_skills(snapshot.skill_catalog, snapshot.agent)
        if snapshot.memory_catalog:
            terminal = self.ledger.get(binding.run_id)
            self.memories.validate(node.research_id, snapshot.memory_catalog, agent=snapshot.agent,
                completed_run_id=binding.run_id if terminal and terminal.commit_id else None)
        if node.plan_proposal_id: self.planning.validate_step(node, binding.command_id)
        if node.selection == 'excluded' or node.active_run_id != binding.run_id or node.revision != binding.node_revision:
            raise ScopeDenied('Node changed; result cannot become current')
        if binding.chat_revision is not None:
            chat = self.query('chat', node.id)
            if chat is None or chat.revision != binding.chat_revision:
                raise ScopeDenied('Chat changed; reply cannot become current')
        scope = self.scopes.current(node.scope_id, self.owner)
        root_scope = self.scopes.current(self.research(node.research_id).scope_id, self.owner)
        allowed = set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
        if not set(scope.tools) <= allowed: raise ScopeDenied('Research tool permission changed')
        for ref in self.run_resource_refs(binding):
            self.scopes.check(scope, ref)
            self.scopes.check(root_scope, ref)
        for key in binding.result_ids: self.check_result(key, scope)

    def validate_commit(self, commit):
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine) and self.engine.ledger.binding(commit.run_id):
            # Execution failure does not imply unknown billing. The operation
            # ledger determines whether provider usage has been confirmed.
            commit = commit.model_copy(update={'result':commit.result.model_copy(
                update={'usage':self.engine.ledger.usage(commit.run_id)})})
        binding = self.query('run', commit.run_id)
        if binding:
            try: self.validate_binding(binding)
            except (ScopeDenied, Conflict):
                return commit.model_copy(update={'result': ScopeExecutionGate.blocked(commit.run_id, commit.result.usage), 'messages': ()})
            if commit.result.status == 'succeeded':
                try:
                    self.evidence.prepare(f'result:{binding.run_id}', binding, commit.result.output)
                except (ScopeDenied, Conflict, ValueError, KeyError) as exc:
                    from ..contracts import RunError, RunResult
                    failed = RunResult(run_id=commit.run_id, status='failed', output=None,
                        usage=commit.result.usage, error=RunError(code='evidence_invalid', origin='app', message=str(exc)))
                    return commit.model_copy(update={'result':failed, 'messages':()})
        return commit

    def result_input(self, key, scope):
        """Public immutable report and its already-validated located evidence."""
        version = self.check_result(key, scope)
        _, report = self.read_result(key)
        return (ResultMount(result_id=key,run_id=version.run_id,resource_refs=version.resource_refs,
            missing=version.missing,limitations=version.limitations),
            {'report':report,'evidence':self.evidence.read(key).model_dump(mode='json')})

    def retry_save(self, run_id):
        binding = self.query('run', run_id)
        if binding is None: raise KeyError(run_id)
        self.node(binding.node_id)
        if run_id in self.executors:
            value = self.executors[run_id].retry_save(run_id)
        else:
            if self.ledger.committed('run:'+run_id) is None:
                raise Conflict('No confirmed publication; generation is never retried by save')
            from ..execution.service import outcome
            value = outcome(self.ledger.get(run_id))
        if value.result is not None and value.result.status == 'succeeded':
            self.check_result(f'result:{run_id}')
        return value

    def reconcile(self):
        """Process restart is queryable interruption, never automatic execution recovery."""
        for research in self.query('list_researches', self.owner):
            for command in self.query('list_commands', research.id):
                if command.status == 'accepted' and not self.node(command.node_id).plan_proposal_id:
                    self._settle(command, 'interrupted', 'process_exited_before_command_completed')
            for node in self.query('list_nodes', research.id):
                if node.active_run_id and not node.plan_proposal_id:
                    with self.database.transaction() as tx:
                        tx.research.save_node(node.model_copy(update={'active_run_id': None, 'revision': node.revision + 1}), node.revision)

    async def run_plan(self, proposal_id):
        if not isinstance(proposal_id, str): raise Conflict('Direct plan execution removed; confirm a proposal first')
        return await self.planning.execute(proposal_id)
