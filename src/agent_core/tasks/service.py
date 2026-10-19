"""R1 task admission, independent execution, checking and handoff."""
import asyncio
import json
import os
from dataclasses import replace
from ..contracts import AgentSpec, CoreFailure, RunResult, RunError, RunLimits
from ..core import AgentCatalog, CoreRunner
from ..context.assembler import project_context
from ..agent_identity import AgentIdentity
from ..contracts import Message
from ..context.mounts import ResultMount
from ..context.execution import ScopeExecutionGate
from ..context.scopes import ScopeDenied
from ..context.tool_guard import ScopedTool
from ..execution.ledger import Ledger, EventSink
from ..execution.service import ExecutionService
from ..execution.ports import Conflict, Commit
from ..orchestration import Dispatcher
from ..orchestration.models import DispatchItem, DispatchPlan, DispatchPolicy
from ..resources.snapshots import identity
from ..routing import RouteBlocked
from ..coding.models import CODING_TOOLS
from .models import AcceptanceItem, CheckRecord, ExpertAnswer, CodingAnswer, CodingDelivery, MarketAnswer, TaskReceipt, TaskRun, TaskSpec


from ..methods.defaults import METHODS

def acceptance_for(kind):
    items = [AcceptanceItem(number=1, requirement='必需资料与依赖均获授权')]
    if kind == 'coding':
        items += [AcceptanceItem(number=2, requirement='代码快照与授权范围一致'),
                  AcceptanceItem(number=3, requirement='全部可信测试对交付快照通过')]
    elif kind == 'extract':
        items += [AcceptanceItem(number=2, requirement='公司和报告期明确'),
            AcceptanceItem(number=3, requirement='经营现金流和资本开支为可解析数值'),
            AcceptanceItem(number=4, requirement='单位明确'),
            AcceptanceItem(number=5, requirement='数值和单位都有来源位置')]
    else:
        items.append(AcceptanceItem(number=2, requirement='按任务目标交付报告'))
    return tuple(items)


def task_goal(task_id, version, goal, acceptance, optional_missing=()):
    text = f'Task {task_id} v{version}: {goal}\nAcceptance: ' + '; '.join(
        f'{i.number}. {i.requirement}' for i in acceptance)
    if optional_missing:
        text += '\nOptional inputs unavailable: ' + ', '.join(r.resource_id for r in optional_missing)
    return text


def prompt_overhead(instruction, tool=None):
    schemas = len(json.dumps(ExpertAnswer.model_json_schema(), ensure_ascii=False))
    if tool: schemas += len(json.dumps(tool.input_type.model_json_schema(), ensure_ascii=False))
    return len(instruction) + schemas
EXTRACT_FIELDS = ('company', 'period', 'operating_cash_flow', 'capital_expenditure', 'unit')
NUMERIC_FIELDS = ('operating_cash_flow', 'capital_expenditure')


def checks(spec, result):
    """Program checks never accept an expert's self_checks as proof."""
    answer = (CodingDelivery if spec.kind == 'coding' else ExpertAnswer).model_validate(result.output) if result.status == 'succeeded' else None
    records, missing = [], []
    for item in spec.acceptance:
        ok, evidence = True, 'Run output and fixed input identity checked'
        if answer is None:
            ok, evidence = False, result.error.code if result.error else result.status
        elif spec.kind == 'coding' and item.number == 3:
            tests = answer.candidate.tests
            ok = ({t['profile'] for t in tests} == set(spec.coding.test_profiles) and
                  all(t['exit_code'] == 0 and t['content_sha256'] == answer.candidate.content_sha256 for t in tests))
            evidence = 'Trusted tests bound to frozen candidate' if ok else 'Missing or stale code tests'
        elif item.number == 2 and spec.kind == 'extract':
            absent = [key for key in ('company', 'period') if not answer.fields.get(key, '').strip()]
            ok, evidence = not absent, 'Missing identity fields: ' + ', '.join(absent) if absent else 'Company and period present'
            missing.extend(absent)
        elif item.number == 3 and spec.kind == 'extract':
            absent = [key for key in NUMERIC_FIELDS if not answer.fields.get(key, '').strip()]
            invalid = [key for key in NUMERIC_FIELDS if key not in absent and not _numeric(answer.fields[key])]
            ok = not absent and not invalid
            evidence = f'Missing: {absent}; nonnumeric: {invalid}' if not ok else 'Required numbers parse as decimals'
            missing.extend((*absent, *invalid))
        elif item.number == 4 and spec.kind == 'extract':
            ok = bool(answer.fields.get('unit', '').strip())
            evidence = 'Unit present' if ok else 'Unit missing'
            if not ok: missing.append('unit')
        elif item.number == 5 and spec.kind == 'extract':
            absent = [key for key in (*NUMERIC_FIELDS, 'unit') if not answer.sources.get(key, '').strip()]
            ok, evidence = not absent, 'Missing source locations: ' + ', '.join(absent) if absent else 'Source locations supplied, meaning unverified'
            missing.extend(f'source:{key}' for key in absent)
        elif item.number == 2 and spec.kind != 'extract':
            ok, evidence = bool(answer.report.strip()), 'Report present' if answer.report.strip() else 'Report missing'
        records.append(CheckRecord(number=item.number, checker='program', status='passed' if ok else 'failed', evidence=evidence))
    if answer:
        missing.extend(answer.missing)
        missing.extend(f'conflict:{item}' for item in answer.conflicts)
    return tuple(records), tuple(dict.fromkeys(missing)), answer


def _numeric(value):
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(value.replace(',', '')).is_finite()
    except (InvalidOperation, AttributeError):
        return False


class TransientExecutionError(Exception):
    """A retryable model/tool failure has not crossed the business commit boundary."""


class TaskLedger(Ledger):
    def __init__(self, database, scopes, research):
        super().__init__(database)
        self.scopes = scopes
        self.research = research

    def commit(self, commit):
        commit = Commit.model_validate(commit.model_dump())
        bundle = None
        with self.database.transaction() as tx:
            admitted = tx.tasks.run(commit.run_id)
        snapshot = self.scopes.repository.snapshot(commit.run_id) if admitted is not None else None
        receipt_refs = (self.research.collaboration.tasks.run_resource_refs(admitted)
            if admitted is not None and getattr(self.research, 'collaboration', None)
            else admitted.resource_refs if admitted is not None else ())
        if commit.result.status == 'succeeded':
            if admitted is not None:
                bundle = self.research.evidence.prepare(f'task-result:{admitted.id}',admitted,commit.result.output)
        with self.database.transaction() as tx:
            previous = tx.runs.committed(commit.commit_id)
            if previous is not None:
                self.commit_in(tx, commit)
            else:
                binding = tx.tasks.run(commit.run_id)
                if binding is None: raise Conflict('Task run binding missing')
                current = tx.tasks.spec(binding.task_id)
                if current.version != binding.spec_version and (commit.result.status != 'failed' or
                        commit.result.error is None or commit.result.error.code != 'task_superseded'):
                    raise Conflict('Task spec changed before publication')
                spec = tx.tasks.spec(binding.task_id, binding.spec_version)
                self.commit_in(tx, commit)
                program, missing, answer = checks(spec, commit.result)
                semantic = tuple(CheckRecord(number=i.number, checker='semantic', status='pending',
                    evidence='Independent review required') for i in spec.acceptance)
                delivery = 'pending_review' if answer and all(c.status == 'passed' for c in program) else (
                    'partial_pending_review' if answer and spec.kind == 'extract' else 'rework')
                from .artifacts import describe
                receipt = TaskReceipt(result_id=f'task-result:{binding.id}', task_id=spec.id,
                    artifact=describe(f'task-result:{binding.id}',commit.result.output) if answer else None,
                    code_candidate=answer.candidate if isinstance(answer, CodingDelivery) else None,
                    spec_version=spec.version, run_id=binding.id, context_snapshot_id=binding.context_snapshot_id,
                    model=binding.model, route_id=binding.route_id, method_version=binding.method_version,
                    method_digest=binding.method_digest, result_ids=binding.result_ids,
                    resource_refs=receipt_refs,
                    missing_optional_refs=binding.missing_optional_refs,
                    program_checks=program, semantic_checks=semantic,
                    assumptions=answer.assumptions if answer else (), conflicts=answer.conflicts if answer else (),
                    missing=missing, limitations=answer.limitations if answer else (),
                    follow_up=answer.follow_up if answer else (), delivery=delivery)
                tx.tasks.save_receipt(receipt)
                self.research.learning.record(tx, spec, binding, receipt, commit.result, snapshot)
                if bundle is not None: tx.evidence.publish(receipt.result_id,bundle)
                self.research.memories.writes.publish_in(tx, commit.run_id, commit.result)
        return self.get(commit.run_id)


class TaskService:
    def __init__(self, database, scopes, research, engine=None, router=None, *, methods, memories, stop_execution=lambda spec: None):
        self.database, self.scopes, self.research, self.engine = database, scopes, research, engine
        self.router = router
        self.coding = None
        self.stop_execution = stop_execution
        self.methods = methods
        self.memories = memories
        self.ledger = TaskLedger(database, scopes, research)
        self.executors = {}
        self.locks = {}
        self.active = {}

    def grant_coding(self, research_id, name, contract):
        """Operator-only admission; never exposed as a model tool."""
        from ..coding.models import CODING_TOOLS
        if self.coding is None: raise ValueError('Coding service is not configured')
        root = self.research.research(research_id)
        scope = self.scopes.current(root.scope_id, self.research.owner)
        if not CODING_TOOLS <= set(scope.tools):
            scope = self.scopes._replace(scope, tools=tuple(sorted(set(scope.tools) | CODING_TOOLS)))
        return self.coding.grant(scope, name, contract)

    def coding_grants(self, research_id):
        if self.coding is None: return {}
        root = self.research.research(research_id)
        scope = self.scopes.current(root.scope_id, self.research.owner)
        return self.coding.grants(scope)

    def query(self, name, *args):
        with self.database.transaction() as tx:
            return getattr(tx.tasks, name)(*args)

    def spec(self, task_id, version=None):
        value = self.query('spec', task_id, version)
        if value is None: raise KeyError(task_id)
        self.research.research(value.research_id)
        return value

    def create(self, *, research_id, kind, user_request, goal=None, node_id=None, parent_task_id=None,
               required_refs=(), optional_refs=(), required_results=(), allow_partial_results=False,
               model=None, task_id=None, expert_id=None, route_record=None, evidence_required=False,
               limits=None, method_version=None, routing_version=None, memory_ids=(),
               expert_session_id=None, context_packet_id=None, selected_messages=None,
               pending_dependency_task_ids=(), coding=None, agent_mode='specialist', role_instructions='', authorized_refs=()):
        spec = self.prepare_spec(research_id=research_id, kind=kind, user_request=user_request,
            goal=goal, node_id=node_id, parent_task_id=parent_task_id,
            required_refs=required_refs, optional_refs=optional_refs, required_results=required_results,
            allow_partial_results=allow_partial_results, model=model, task_id=task_id,
            expert_id=expert_id, route_record=route_record, evidence_required=evidence_required,
            limits=limits, method_version=method_version, routing_version=routing_version,
            memory_ids=memory_ids, expert_session_id=expert_session_id,
            context_packet_id=context_packet_id, selected_messages=selected_messages,
            pending_dependency_task_ids=pending_dependency_task_ids, coding=coding,
            agent_mode=agent_mode, role_instructions=role_instructions, authorized_refs=authorized_refs)
        session = None
        if expert_session_id is None and getattr(self.research, 'collaboration', None):
            session = self.research.collaboration.session_for_task(research_id, spec.expert_id, spec.agent)
            if session is not None: spec = spec.model_copy(update={'expert_session_id': session.id})
        with self.database.transaction() as tx:
            if session is not None and tx.collaboration.session(session.id) is None:
                tx.collaboration.insert('sessions', session.id, session)
            if route_record: tx.tasks.add_route(route_record)
            tx.tasks.add_spec(spec)
        return spec

    def prepare_spec(self, *, research_id, kind, user_request, goal=None, node_id=None, parent_task_id=None,
               required_refs=(), optional_refs=(), required_results=(), allow_partial_results=False,
               model=None, task_id=None, expert_id=None, route_record=None, evidence_required=False,
               limits=None, method_version=None, routing_version=None, memory_ids=(),
               expert_session_id=None, context_packet_id=None, selected_messages=None,
               pending_dependency_task_ids=(), coding=None, agent_mode='specialist', role_instructions='', authorized_refs=()):
        self.research.research(research_id)
        node = self.research.node(node_id) if node_id else None
        if node and node.research_id != research_id: raise Conflict('Foreign node')
        if node and node.selection == 'excluded': raise Conflict('Excluded node cannot receive a task')
        from ..research.chat import ChatService
        chat = ChatService(self.research, self).read(node_id) if node and self.research.query('chat', node_id) else None
        context_messages = tuple(selected_messages) if selected_messages is not None else ((*chat.inherited, *chat.messages) if chat else ())
        chat_refs = tuple(dict.fromkeys(ref for message in context_messages
                                        for ref in message.resource_refs)) if chat else ()
        required_refs = tuple(dict.fromkeys((*required_refs, *chat_refs)))
        if kind not in METHODS: raise ValueError('Unknown expert kind')
        if not user_request.strip(): raise ValueError('User request required')
        if parent_task_id:
            seen = {task_id} if task_id else set()
            ancestor = parent_task_id
            while ancestor:
                if ancestor in seen: raise Conflict('Task parent cycle')
                seen.add(ancestor)
                parent = self.spec(ancestor)
                if parent.research_id != research_id: raise Conflict('Foreign parent task')
                ancestor = parent.parent_task_id
        acceptance = acceptance_for(kind)
        expert_id = expert_id or f'expert_{kind}'
        asset, body = self.methods.method(expert_id, method_version or (route_record.expert_config.method_version if route_record else None))
        if body.kind != kind: raise ValueError('Expert method kind mismatch')
        if kind == 'coding':
            if coding is None or self.coding is None:
                raise ValueError('Coding service and operator admitted contract required')
            coding = self.coding.bind_contract(coding)
        if kind != 'coding' and not required_refs and not authorized_refs and not required_results and not pending_dependency_task_ids and \
                not {'search_web', 'fetch_web'} <= set(body.tools):
            raise ValueError('Task needs a required input or published web research capability')
        method, digest = asset.version, body.method_digest
        fixed_task_id = task_id or identity()
        if agent_mode == 'specialist' and role_instructions:
            raise ValueError('Specialist instructions come from the published method')
        agent = AgentIdentity(mode=agent_mode,
            id=expert_id if agent_mode == 'specialist' else 'temporary:' + fixed_task_id,
            instructions=role_instructions)
        selected_memories = self.memories.select(research_id, memory_ids, agent=agent)
        if agent.mode == 'assistant': raise ValueError('Task agents must be temporary or specialist')
        root_scope = self.scopes.current(self.research.research(research_id).scope_id, self.research.owner)
        for ref in authorized_refs: self.scopes.check(root_scope, ref)
        authorized_refs = tuple(dict.fromkeys((*required_refs, *optional_refs, *authorized_refs)))
        if set(authorized_refs) & self.research._excluded_refs(research_id): raise ScopeDenied('Excluded directory material')
        old = self.query('spec', task_id, None) if task_id else None
        if old and old.research_id != research_id: raise Conflict('Task belongs to another research')
        spec = TaskSpec(id=fixed_task_id, version=old.version + 1 if old else 1,
            research_id=research_id, node_id=node_id, node_revision=node.revision if node else None,
            expert_session_id=expert_session_id, context_packet_id=context_packet_id,
            chat_context=context_messages,
            parent_task_id=parent_task_id,
            user_request=user_request, goal=goal or user_request, kind=kind, expert_id=expert_id, agent=agent,
            method_version=method, method_digest=digest, routing_version=routing_version,
            model=model or os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini'),
            route_id=route_record.id if route_record else None,
            required_refs=tuple(required_refs), optional_refs=tuple(optional_refs), authorized_refs=authorized_refs,
            required_results=tuple(required_results),
            pending_dependency_task_ids=tuple(pending_dependency_task_ids),
            allow_partial_results=allow_partial_results,
            memory_refs=self.memories.refs(selected_memories),
            required_fields=EXTRACT_FIELDS if kind == 'extract' else (), acceptance=tuple(acceptance),
            evidence_required=evidence_required, limits=limits or RunLimits(), coding=coding)
        if route_record and (route_record.task_id != spec.id or route_record.spec_version != spec.version or
                             route_record.research_id != research_id or route_record.status != 'selected' or
                             route_record.selected_expert != expert_id or route_record.selected_model != spec.model):
            raise Conflict('Route does not match frozen task spec')
        return spec

    async def create_routed(self, *, approved_summary,
                            preferred_expert=None, preferred_model=None, budget_usd=None, **task):
        from ..routing.service import Router
        from ..routing.config import router_from_catalog, CatalogFile
        prior_spec = self.query('spec', task.get('task_id'), None) if task.get('task_id') else None
        routing_version = prior_spec.routing_version if prior_spec else self.methods.query('head','routing','model-policy')
        base_router = (router_from_catalog(CatalogFile.model_validate(self.methods.resolve('routing','model-policy',routing_version).body))
                       if routing_version else self.router)
        if base_router is None: raise Conflict('R2 router is not configured')
        experts = tuple(e for e in self.methods.experts() if e.id in base_router.experts)
        if prior_spec:
            frozen = self.methods.candidate(self.methods.resolve('method',prior_spec.expert_id,prior_spec.method_version))
            experts = tuple(frozen if e.id == frozen.id else e for e in experts)
        router = Router(experts,tuple(base_router.models.values()),base_router.model_router,
                        catalog_version=base_router.catalog_version,policy_version=base_router.policy_version)
        from ..adapters.openai import OpenAIEngine
        if isinstance(self.engine, OpenAIEngine) and self.engine.model is not None:
            raise Conflict('Routed tasks require task-level model configuration')
        task_id = task.get('task_id') or identity()
        old = self.query('spec', task_id, None)
        if old and old.research_id != task['research_id']: raise Conflict('Foreign task')
        reroute_reason = task.pop('reroute_reason', None)
        if reroute_reason is not None and not reroute_reason.strip():
            raise ValueError('Model reselection reason required')
        if old and old.route_id:
            preferred_expert = old.expert_id
            if reroute_reason is None: preferred_model = old.model
            elif len(self.query('list_runs', task_id)) >= 2:
                raise Conflict('Task model reselection limit reached')
        minimum_rank = 0
        if old and old.route_id and reroute_reason is not None:
            old_route = self.query('route', old.route_id)
            prior = old_route.selected_model_config if old_route else None
            if prior is None: raise Conflict('Previous model missing from controlled catalog')
            minimum_rank = prior.capability_rank
        version = old.version + 1 if old else 1
        root = self.research.research(task['research_id'])
        scope = self.scopes.current(root.scope_id, self.research.owner)
        refs = tuple(task.get('required_refs', ()))
        routed_refs = list(refs)
        if not refs and not task.get('authorized_refs') and not task.get('required_results') and not task.get('coding'):
            raise ValueError('Task needs an admitted input or source directory')
        mounted_results = []
        optional_missing = []
        for ref in refs:
            self.scopes.check(scope, ref)
        for ref in task.get('optional_refs', ()):
            try: self.scopes.check(scope, ref)
            except (ScopeDenied, KeyError):
                optional_missing.append(ref)
                continue
            routed_refs.append(ref)
        for result_id in task.get('required_results', ()):
            receipt = self.receipt(result_id)
            if receipt.delivery != 'passed': raise Conflict('Unpassed route input')
            source = self.spec(receipt.task_id, receipt.spec_version)
            if source.research_id != task['research_id']: raise ScopeDenied('Foreign task result')
            for ref in receipt.resource_refs:
                self.scopes.check(scope, ref)
                routed_refs.append(ref)
            stored = self.ledger.get(receipt.run_id)
            if stored is None or stored.result is None: raise Conflict('Result output missing')
            mounted_results.append((ResultMount(result_id=result_id, run_id=receipt.run_id,
                resource_refs=receipt.resource_refs, missing=receipt.missing), stored.result.output))
        goal = task_goal(task_id, version, task.get('goal') or task['user_request'],
            acceptance_for(task['kind']), optional_missing)
        preview_agent = AgentIdentity(mode=task.get('agent_mode', 'specialist'),
            id='temporary:' + task_id if task.get('agent_mode') == 'temporary' else task.get('preferred_expert') or f'expert_{task["kind"]}',
            instructions=task.get('role_instructions', ''))
        explicit = self.memories.select(task['research_id'], task.get('memory_ids', ()), agent=preview_agent)
        directory_refs = tuple(dict.fromkeys((*routed_refs, *task.get('authorized_refs', ()))))
        for ref in directory_refs: self.scopes.check(scope, ref)
        preview = self.research.context.assemble(self.scopes, scope, identity(), goal,
            agent=preview_agent,
            project=project_context(self.research, task['research_id'], task.get('coding')),
            retrieval_goal=task.get('goal') or task['user_request'],
            required_memories=explicit, memories=[m for m in self.memories.list(task['research_id'], agent=preview_agent) if m.available()],
            sources=self.research.context.selector.source_candidates(self.scopes, scope, directory_refs, routed_refs),
            refs=tuple(dict.fromkeys(routed_refs)), results=mounted_results,
            original_message=Message(role='user', content=task['user_request']),
            evidence_mode=task.get('evidence_required',False), persist=False)
        read_tool = ScopedTool(self.scopes, scope).read_binding() if routed_refs else None
        search_tool = (ScopedTool(self.scopes, scope).search_binding()
                       if routed_refs and task.get('evidence_required',False) else None)
        input_chars = sum(len(m.content) for m in preview.input.messages) + max(
            prompt_overhead(task['role_instructions'] if task.get('agent_mode') == 'temporary' else self.methods.method(e.id,e.method_version)[1].prompt, read_tool)
            for e in experts if e.kind == task['kind'])
        if search_tool:
            input_chars += len(json.dumps(search_tool.input_type.model_json_schema()))
        previous = self.query('list_routes', task_id)
        if reroute_reason and any(r.trigger for r in previous):
            raise Conflict('Task model reselection limit reached')
        spent_usd = 0.0
        for binding in self.query('list_runs', task_id):
            execution = self.ledger.get(binding.id)
            if execution is None or execution.result is None or not execution.result.usage.complete:
                raise Conflict('Prior run usage is unknown; budget cannot be checked')
            prior_route = self.query('route', binding.route_id) if binding.route_id else None
            model = prior_route.selected_model_config if prior_route else None
            if model is None: raise Conflict('Prior run model price is unknown')
            usage = execution.result.usage
            spent_usd += (usage.input_tokens * model.input_usd_per_million_tokens +
                          usage.output_tokens * model.output_usd_per_million_tokens) / 1_000_000
        try:
            route = await router.route(research_id=task['research_id'], task_id=task_id,
                spec_version=version, approved_summary=approved_summary,
                kind=task['kind'],
                input_chars=input_chars,
                required_tools=(tuple(sorted(CODING_TOOLS))
                    if task['kind'] == 'coding' else tuple(t for t in ('read_source' if routed_refs else None,
                    'search_source' if search_tool else None) if t)),
                preferred_expert=preferred_expert, preferred_model=preferred_model,
                budget_usd=budget_usd, spent_usd=spent_usd,
                minimum_rank=minimum_rank, trigger=reroute_reason,
                resource_refs=tuple(dict.fromkeys(routed_refs)), previous=previous)
        except RouteBlocked as exc:
            with self.database.transaction() as tx: tx.tasks.add_route(exc.record)
            raise
        task.pop('task_id', None)
        return self.create(**task, task_id=task_id, expert_id=route.selected_expert,
                           model=route.selected_model, route_record=route, routing_version=routing_version)

    def _prepare(self, spec):
        if spec.pending_dependency_task_ids:
            raise Conflict('Task is waiting for reviewed upstream results')
        if spec.context_packet_id and self.research.collaboration.cancellation_requested(spec.id, spec.version):
            raise Conflict('Task cancellation requested')
        root = self.research.research(spec.research_id)
        self._check_node(spec)
        from ..research.chat import ChatService
        for message in spec.chat_context:
            ChatService(self.research, self)._validate_message(spec.research_id, message,
                None if spec.context_packet_id else spec.node_id)
        asset, body = self.methods.method(spec.expert_id,spec.method_version)
        method = body.prompt if spec.agent.mode == 'specialist' else spec.agent.instructions
        if body.method_digest != spec.method_digest:
            raise Conflict('Method contents changed; publish a new task version')
        if sum(run.spec_version == spec.version for run in self.query('list_runs', spec.id)) >= 2:
            raise Conflict('Task version attempt limit reached')
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        refs = list(spec.required_refs)
        for ref in refs: self.scopes.check(root_scope, ref)
        results = []
        for result_id in spec.required_results:
            receipt = self.receipt(result_id)
            if receipt.delivery not in ('passed', 'partial' if spec.allow_partial_results else 'passed'):
                raise Conflict('Required result has not passed handoff')
            source = self.spec(receipt.task_id, receipt.spec_version)
            if source.research_id != spec.research_id: raise ScopeDenied('Foreign task result')
            if spec.kind == 'analyze' and (source.kind != 'extract' or receipt.delivery != 'passed'):
                raise Conflict('Numeric analysis requires a complete verified extraction')
            for ref in receipt.resource_refs: self.scopes.check(root_scope, ref)
            refs.extend(receipt.resource_refs)
            stored = self.ledger.get(receipt.run_id)
            if stored is None or stored.result is None: raise Conflict('Result output missing')
            results.append((ResultMount(result_id=result_id, run_id=receipt.run_id,
                resource_refs=receipt.resource_refs, missing=receipt.missing), stored.result.output))
        optional_missing = []
        for ref in spec.optional_refs:
            try: self.scopes.check(root_scope, ref)
            except (ScopeDenied, KeyError):
                optional_missing.append(ref)
                continue
            refs.append(ref)
        refs = tuple(dict.fromkeys(refs))
        directory_refs = tuple(dict.fromkeys((*refs, *(r for r in spec.authorized_refs if r not in optional_missing))))
        for ref in directory_refs: self.scopes.check(root_scope, ref)
        allowed = set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
        scope = self.scopes.create(self.research.owner, tools=tuple(sorted(body.tools & allowed)))
        for ref in directory_refs: scope = self.scopes.grant(scope, root_scope, ref)
        scope = self.scopes.select(scope, directory_refs)
        run_id = identity()
        goal = task_goal(spec.id, spec.version, spec.goal, spec.acceptance, optional_missing)
        goal += '\nDelivery contract: ' + json.dumps({
            'required_fields': spec.required_fields, 'evidence_required': spec.evidence_required})
        packet = None
        if spec.context_packet_id:
            packet = self.research.collaboration.packet(spec.context_packet_id)
            if (packet.research_id != spec.research_id or packet.resource_refs != spec.required_refs or packet.result_ids != spec.required_results
                    or packet.agent != spec.agent or packet.authorized_refs != spec.authorized_refs or packet.limits != spec.limits):
                raise Conflict('Frozen handoff does not match task inputs')
            # The complete frozen handoff is supplied separately to the assembler.
            # Expanding it into goal repeats every instruction and can exhaust the input budget.
        history = [m.model_dump(mode='json') for m in spec.chat_context]
        selected_memories = self.memories.validate(spec.research_id, spec.memory_refs, agent=spec.agent)
        available = [m for m in self.memories.list(spec.research_id, agent=spec.agent) if m.available()]
        snapshot = self.research.context.assemble(self.scopes, scope, run_id, goal, refs=refs, results=results,
            agent=spec.agent, project=project_context(self.research, spec.research_id, spec.coding),
            retrieval_goal=spec.goal,
            history=history, memories=available, required_memories=selected_memories,
            sources=self.research.context.selector.source_candidates(self.scopes, scope, directory_refs, refs),
            packet=packet.model_dump(mode='json') if packet else None,
            original_message=Message(role='user', content=packet.user_instruction if packet else spec.user_request),
            evidence_mode=spec.evidence_required)
        if spec.route_id:
            route = self.query('route', spec.route_id)
            if route is None or route.status != 'selected' or route.selected_model != spec.model:
                raise Conflict('Frozen route missing or changed')
            model = route.selected_model_config
            read_tool = ScopedTool(self.scopes, scope).read_binding() if refs else None
            prompt_chars = sum(len(m.content) for m in snapshot.input.messages) + prompt_overhead(method, read_tool)
            if spec.evidence_required and refs:
                prompt_chars += len(json.dumps(ScopedTool(self.scopes,scope).search_binding().input_type.model_json_schema()))
            if model is None or not model.available or prompt_chars > model.max_input_chars:
                raise Conflict('Assembled input exceeds routed model capacity or model unavailable; reroute required')
            estimated = (prompt_chars / 4 * model.input_usd_per_million_tokens +
                         1000 * model.output_usd_per_million_tokens) / 1_000_000
            if route.budget_usd is None or estimated + route.spent_usd > route.budget_usd:
                raise Conflict('Assembled input exceeds remaining execution budget; reroute required')
        binding = TaskRun(id=run_id, task_id=spec.id, spec_version=spec.version,
            agent=spec.agent,
            context_snapshot_id=snapshot.id, scope_id=scope.id, scope_version=scope.version,
            model=spec.model, route_id=spec.route_id, method_version=spec.method_version, method_digest=spec.method_digest,
            result_ids=spec.required_results, resource_refs=refs,
            missing_optional_refs=tuple(optional_missing), research_id=spec.research_id,
            node_id=spec.node_id, evidence_required=spec.evidence_required)
        with self.database.transaction() as tx: tx.tasks.add_run(binding)
        return binding, snapshot, scope

    def receipt(self, result_id):
        value = self.query('receipt', result_id)
        if value is None: raise KeyError(result_id)
        self.spec(value.task_id, value.spec_version)
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine):
            self.engine.validate_run(value.run_id, require_started=value.delivery != 'rework')
        return value

    def run_resource_refs(self, binding):
        root = self.research.research(binding.research_id)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        task_scope = self.scopes.current(binding.scope_id, self.research.owner)
        obtained = {(e.resource_id, e.snapshot_id) for e in
            self.scopes.resources.repository.ingestions(root_scope.id)
            if e.run_id == binding.id and e.kind == 'web'}
        fetched = tuple(ref for ref in (*root_scope.grants, *task_scope.grants)
            if (ref.resource_id, ref.snapshot_id) in obtained and
            self.scopes.resources.describe(ref)[2].status in ('ready', 'partial'))
        supplied = self.research.collaboration.read_refs(binding.id) if getattr(self.research, 'collaboration', None) else ()
        # Directory grants are frozen input provenance, not a claim that every
        # body was read. Actual reads remain in the tool/operation ledger.
        return tuple(dict.fromkeys((*binding.resource_refs, *task_scope.allowed, *fetched, *supplied)))

    def _check_node(self, spec):
        if spec.node_id:
            node = self.research.node(spec.node_id)
            if node.research_id != spec.research_id or node.selection != 'selected':
                raise Conflict('Task target branch unavailable')
            if spec.context_packet_id is None and node.revision != spec.node_revision:
                raise Conflict('Task target branch revision changed')

    async def run(self, task_id):
        async with self.locks.setdefault(task_id, asyncio.Lock()):
            self.active[task_id] = asyncio.current_task()
            try: return await self._run_locked(task_id)
            finally: self.active.pop(task_id, None)

    def stop(self, task_id):
        spec = self.spec(task_id)
        self.stop_execution(spec)
        task = self.active.get(task_id)
        if task is None: return False
        task.cancel()
        return True

    async def _run_locked(self, task_id):
        spec = self.spec(task_id)
        binding, snapshot, scope = self._prepare(spec)
        return await self._execute_binding(spec, binding, snapshot, scope)

    def prepare_durable(self, task_id, version):
        """Admit one fixed run for a durable task, including after an uncertain prepare."""
        spec = self.spec(task_id)
        if spec.pending_dependency_task_ids:
            raise Conflict('Task is waiting for reviewed upstream results')
        if spec.context_packet_id and self.research.collaboration.cancellation_requested(task_id, version):
            raise Conflict('Task cancellation requested')
        if spec.version != version:
            raise Conflict('Task spec changed before durable admission')
        runs = [run for run in self.query('list_runs', task_id) if run.spec_version == version]
        if runs:
            if len(runs) != 1:
                raise Conflict('Durable task has multiple admitted runs')
            return runs[0].id
        return self._prepare(spec)[0].id

    async def run_durable(self, task_id, version, run_id):
        """Run or confirm the admitted task through the existing business commit."""
        spec = self.spec(task_id)
        binding = self.query('run', run_id)
        if spec.version != version or binding is None or binding.task_id != task_id or binding.spec_version != version:
            raise Conflict('Durable task identity changed')
        receipt = self.query('receipt', f'task-result:{run_id}')
        if receipt is not None:
            self._validate_durable_access(spec, binding)
            return run_id
        snapshot = self.scopes.repository.snapshot(binding.id)
        scope = self.scopes.repository.scope(binding.scope_id)
        await self._execute_binding(spec, binding, snapshot, scope, durable=True)
        return run_id

    def _validate_durable_access(self, spec, binding):
        if binding.agent != spec.agent or self.scopes.repository.snapshot(binding.id).agent != spec.agent:
            raise Conflict('Frozen agent identity changed')
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine):
            self.engine.validate_run(binding.id)
        if spec.context_packet_id and self.research.collaboration.cancellation_requested(spec.id, binding.spec_version):
            raise Conflict('Task cancellation requested')
        self._check_node(spec)
        if spec.context_packet_id:
            self.research.collaboration.packet(spec.context_packet_id)
        terminal = self.ledger.get(binding.id)
        completed_run_id = binding.id if terminal and terminal.commit_id else None
        self.memories.validate(spec.research_id, spec.memory_refs, agent=spec.agent, completed_run_id=completed_run_id)
        self.memories.validate(spec.research_id, self.scopes.repository.snapshot(binding.id).memory_catalog, agent=spec.agent, completed_run_id=completed_run_id)
        self.learning.validate_skills(self.scopes.repository.snapshot(binding.id).skill_catalog, spec.agent)
        if self.spec(spec.id).version != binding.spec_version:
            raise Conflict('Task spec changed')
        scope = self.scopes.repository.scope(binding.scope_id)
        self.scopes.valid(scope)
        if scope.version != binding.scope_version:
            raise ScopeDenied('Task scope changed')
        root = self.research.research(spec.research_id)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        allowed = set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
        if not set(scope.tools) <= allowed: raise ScopeDenied('Task tool permission changed')
        for ref in self.run_resource_refs(binding):
            self.scopes.check(scope, ref)
            self.scopes.check(root_scope, ref)
        for ref in scope.allowed: self.scopes.check(root_scope, ref)

    async def _execute_binding(self, spec, binding, snapshot, scope, *, durable=False):
        task_id = spec.id
        self._validate_durable_access(spec, binding)
        asset, body = self.methods.method(spec.expert_id,binding.method_version)
        if asset.version != spec.method_version or body.method_digest != binding.method_digest:
            raise Conflict('Durable method version mismatch')
        if (binding.model, binding.route_id, binding.context_snapshot_id) != (spec.model, spec.route_id, snapshot.id):
            raise Conflict('Durable model, route or input changed')
        tool = ScopedTool(self.scopes, scope).read_binding() if 'read_source' in scope.tools else None
        search_tool = ScopedTool(self.scopes, scope).search_binding() if {'search_source','read_source'} <= set(scope.tools) else None
        async def record_source(args, original):
            self._validate_durable_access(spec, binding)
            result = await original.handler(args)
            if getattr(self.research, 'collaboration', None):
                self.research.collaboration.record_source_read(binding.id, args.ref)
            return result
        if tool:
            original = tool
            tool = replace(tool, handler=lambda args, original=original: record_source(args, original))
        if search_tool:
            original = search_tool
            search_tool = replace(search_tool, handler=lambda args, original=original: record_source(args, original))
        from ..context.directory_tools import source_directory
        directory_tool = source_directory(self.research.context.selector, self.scopes, scope, scope.allowed,
            lambda: self._validate_durable_access(spec, binding)) if 'read_source' in scope.tools else None
        web_tools = ()
        if {'search_web', 'fetch_web'} <= set(scope.tools):
            from ..app.research_web import source_bindings
            def current_web(name):
                self.scopes.valid(scope)
                if name not in scope.tools: raise ScopeDenied('Web tool not permitted')
                active = self.query('run', self.scopes.running.get())
                if active is None or active.id != binding.id or active.task_id != spec.id:
                    raise ScopeDenied('Web tool belongs to another expert run')
                self._validate_durable_access(spec, active)
                return active.id, spec.research_id
            web_tools = source_bindings(self.research, scope, current_web)
        from ..collaboration.tools import expert_bindings
        collaboration_tools = expert_bindings(self.research.collaboration, spec, binding) if spec.context_packet_id else ()
        from ..prompts import EXPERT
        from ..context.capabilities import context_tools, SKILL_POLICY
        method = body.prompt if spec.agent.mode == 'specialist' else spec.agent.instructions
        market = spec.expert_id == 'expert_market_research'
        def validate_output(output):
            return self.research.evidence.validate_output(f'task-result:{binding.id}', binding, output)
        agent = AgentSpec(spec.agent.id, EXPERT + '\n专业职责：' + method,
            tools=tuple(t for t in (tool,search_tool,directory_tool,*web_tools,*collaboration_tools) if t),
            output_type=MarketAnswer if market else ExpertAnswer,
            strict_output=market, model=spec.model, validate_output=validate_output)
        code_workspace = None
        if spec.kind == 'coding':
            from ..coding.tools import bindings as code_bindings
            from ..coding.models import CODING_TOOLS
            from ..controlled.ledger import digest
            if self.coding is None or not CODING_TOOLS <= set(scope.tools):
                raise ScopeDenied('All coding capabilities must be explicitly granted')
            root_scope = self.scopes.current(self.research.research(spec.research_id).scope_id, self.research.owner)
            generation = digest({'root': root_scope.id, 'version': root_scope.version})
            code_workspace = self.coding.admit(spec, generation,
                lambda: self._validate_durable_access(spec, binding))
            agent = AgentSpec(spec.agent.id, EXPERT + '\n专业职责：' + method,
                tools=code_bindings(code_workspace), output_type=CodingAnswer, model=spec.model)
        agent = replace(agent, instructions=agent.instructions + SKILL_POLICY,
            tools=context_tools(snapshot, self.research.context.selector, agent.tools, {},
                lambda: self._validate_durable_access(spec, binding),
                memories=self.memories, research_id=spec.research_id, enabled=spec.limits.max_calls > 0))
        catalog = AgentCatalog([agent])
        events = EventSink(self.ledger)
        if self.engine is None: raise ValueError('Task execution engine is not configured')
        engine = self.engine
        if spec.context_packet_id:
            engine = self.research.collaboration.information.engine(engine, spec, binding)
        runner = CoreRunner(engine, catalog, events)
        gate = ScopeExecutionGate(runner, self.scopes)
        def validate(commit):
            commit = gate.validate_commit(commit)
            from ..controlled.engine import ControlledEngine
            if isinstance(self.engine, ControlledEngine):
                try:
                    self.engine.validate_run(binding.id)
                except (ScopeDenied, Conflict):
                    return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                        usage=commit.result.usage, error=RunError(code='input_revoked', origin='core',
                            message='Controlled authorization generation was invalidated'))})
            if spec.context_packet_id and self.research.collaboration.cancellation_requested(task_id, binding.spec_version):
                return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                    usage=commit.result.usage,
                    error=RunError(code='task_cancelled', origin='core', message='Task cancellation requested'))})
            try: self._check_node(spec)
            except Conflict:
                return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                    error=RunError(code='branch_changed', origin='core', message='Target branch revision changed'))})
            root = self.research.research(spec.research_id)
            root_scope = self.scopes.current(root.scope_id, self.research.owner)
            try:
                allowed = set(root_scope.tools) | ({'search_source'} if 'read_source' in root_scope.tools else set())
                if not set(scope.tools) <= allowed: raise ScopeDenied('Task tool permission changed')
                for ref in self.run_resource_refs(binding): self.scopes.check(root_scope, ref)
                self.memories.validate(spec.research_id, spec.memory_refs, agent=spec.agent)
                self.memories.validate(spec.research_id, snapshot.memory_catalog, agent=spec.agent)
                self.learning.validate_skills(snapshot.skill_catalog, spec.agent)
            except (ScopeDenied, KeyError):
                return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                    error=RunError(code='input_revoked', origin='core', message='Required source or memory was revoked'))})
            if self.spec(task_id).version != binding.spec_version:
                return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                    error=RunError(code='task_superseded', origin='core', message='Task spec superseded'))})
            if commit.result.status == 'succeeded' and code_workspace is not None:
                try:
                    candidate = code_workspace.resolve_candidate(commit.result.output['candidate_ref'])
                    commit = commit.model_copy(update={'result': commit.result.model_copy(update={
                        'output': {**commit.result.output, 'candidate': candidate.model_dump(mode='json'),
                                   'code_patch': code_workspace.candidate_patch(candidate)}})})
                except (PermissionError, ValueError, KeyError) as exc:
                    return commit.model_copy(update={'result': RunResult(run_id=commit.run_id, status='failed',
                        usage=commit.result.usage, error=RunError(code='code_candidate_invalid', origin='app', message=str(exc)))})
            if commit.result.status == 'succeeded':
                try: self.research.evidence.prepare(f'task-result:{binding.id}',binding,commit.result.output)
                except (ScopeDenied,Conflict,ValueError,KeyError) as exc:
                    return commit.model_copy(update={'result': RunResult(run_id=commit.run_id,status='failed',
                        usage=commit.result.usage,error=RunError(code='evidence_invalid',origin='app',message=str(exc)))})
            return commit
        class ExecutionGate:
            async def execute(self, request):
                from contextlib import nullcontext
                with code_workspace.writer() if code_workspace else nullcontext():
                    result = await gate.execute(request)
                await asyncio.sleep(0)
                if durable and result.status == 'failed' and result.error and result.error.retryable:
                    raise TransientExecutionError(result.error.code)
                return result
        executor = ExecutionService(ExecutionGate() if durable or code_workspace else gate,
            self.ledger, events, validate_commit=validate)
        self.executors[binding.id] = executor
        class Admission:
            async def execute(self, request):
                if self_outer.spec(task_id).version != binding.spec_version: raise Conflict('Task superseded')
                return await executor.execute(request)
        self_outer = self
        if durable:
            from ..contracts import RunRequest
            await executor.execute(RunRequest(run_id=binding.id, agent_id=spec.agent.id,
                input=snapshot.input, limits=spec.limits), resume_interrupted=True)
            receipt = self.query('receipt', f'task-result:{binding.id}')
            return {'group': None, 'run_id': binding.id, 'receipt': receipt}
        group = await Dispatcher(Admission(), catalog, self.ledger).dispatch(DispatchPlan(
            items=(DispatchItem(key=task_id, agent_id=spec.agent.id, run_id=binding.id,
                input=snapshot.input, limits=spec.limits),),
            policy=DispatchPolicy(max_concurrency=1, max_runs=1,
                timeout_seconds=spec.limits.timeout_seconds)))
        receipt = self.query('receipt', f'task-result:{binding.id}')
        return {'group': group, 'run_id': binding.id, 'receipt': receipt}

    def retry_save(self, run_id):
        from ..controlled.engine import ControlledEngine
        if isinstance(self.engine, ControlledEngine):
            self.engine.validate_run(run_id)
        executor = self.executors.get(run_id)
        if executor is not None: return executor.retry_save(run_id)
        binding=self.query('run',run_id)
        if binding is None:raise KeyError(run_id)
        self._validate_durable_access(self.spec(binding.task_id,binding.spec_version),binding)
        if self.ledger.committed('run:'+run_id) is None:
            raise Conflict('No confirmed publication; generation is never retried by save')
        from ..execution.service import outcome
        return outcome(self.ledger.get(run_id))

    def read_artifact(self, result_id):
        receipt=self.receipt(result_id)
        spec=self.spec(receipt.task_id,receipt.spec_version)
        root=self.research.research(spec.research_id)
        scope=self.scopes.current(root.scope_id,self.research.owner)
        for ref in receipt.resource_refs:self.scopes.check(scope,ref)
        from ..research.chat import ChatService
        chat=ChatService(self.research,self)
        for message in spec.chat_context:chat._validate_message(spec.research_id,message,None if spec.context_packet_id else spec.node_id)
        if receipt.artifact is None:raise Conflict('No completed report artifact')
        record=self.ledger.get(receipt.run_id)
        if record is None or record.result is None or record.result.status!='succeeded':
            raise Conflict('Saved report is incomplete')
        from .artifacts import describe
        if describe(result_id,record.result.output)!=receipt.artifact:
            raise Conflict('Saved report artifact hash mismatch')
        return {'artifact':receipt.artifact,'output':record.result.output,'receipt':receipt}


    def review(self, result_id, findings, reviewer='local-user'):
        """Independent human/validator judgement, with evidence per numbered item."""
        old = self.receipt(result_id)
        spec = self.spec(old.task_id, old.spec_version)
        if not reviewer.strip(): raise ValueError('Reviewer identity required')
        if len(findings) != len(spec.acceptance): raise ValueError('Review every acceptance item')
        semantic = []
        for item in spec.acceptance:
            decision = findings.get(item.number)
            if not decision or decision[0] not in ('passed', 'failed') or not decision[1].strip():
                raise ValueError(f'Review item {item.number} needs decision and evidence')
            semantic.append(CheckRecord(number=item.number, checker='semantic', status=decision[0],
                                        evidence=decision[1], reviewer=reviewer))
        if old.delivery not in ('pending_review', 'partial_pending_review'): raise Conflict('Receipt cannot be reviewed again')
        if self.spec(old.task_id).version != old.spec_version: raise Conflict('Old task spec cannot be published')
        self._check_node(spec)
        root = self.research.research(spec.research_id)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        for ref in old.resource_refs: self.scopes.check(root_scope, ref)
        failed_program = {c.number for c in old.program_checks if c.status == 'failed'}
        failed_semantic = {c.number for c in semantic if c.status == 'failed'}
        if not failed_program and not failed_semantic:
            status = 'passed'
        elif old.delivery == 'partial_pending_review' and failed_semantic <= failed_program:
            status = 'partial'
        else:
            status = 'rework'
        value = old.model_copy(update={'semantic_checks': tuple(semantic), 'delivery': status})
        with self.database.transaction() as tx:
            current = tx.tasks.spec(old.task_id)
            if current.version != old.spec_version: raise Conflict('Task spec changed')
            tx.tasks.save_receipt(value, expected=old)
        return value

    def adopt(self, result_id, reason=None):
        old = self.receipt(result_id)
        if old.delivery != 'passed': raise Conflict('Only passed delivery may be adopted')
        spec = self.spec(old.task_id)
        if spec.version != old.spec_version: raise Conflict('Old task spec cannot be adopted')
        self._check_node(spec)
        root = self.research.research(spec.research_id)
        root_scope = self.scopes.current(root.scope_id, self.research.owner)
        for ref in old.resource_refs: self.scopes.check(root_scope, ref)
        if old.adoption == 'adopted':
            if reason is not None and old.adoption_reason != reason: raise Conflict('Result already adopted with another reason')
            return old
        value = old.model_copy(update={'adoption': 'adopted', 'adoption_reason': reason})
        with self.database.transaction() as tx: tx.tasks.save_receipt(value, expected=old)
        return value
