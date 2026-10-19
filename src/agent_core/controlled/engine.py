"""Shared EnginePort for admitted research, expert, consultation and discussion runs."""
import asyncio
import os
from dataclasses import replace
from pathlib import Path
from ..contracts import RunResult, RunError
from .broker import Broker
from .ledger import digest
from .workspace import Workspace
from .runner import run
from .admission import current_admission


SOURCE_TOOLS = frozenset(('read_source', 'search_source', 'search_web', 'fetch_web'))
CONTEXT_TOOLS = frozenset(('search_tools', 'search_skills', 'load_skill', 'read_skill_resource', 'search_memory'))
WRITE_TOOLS = frozenset(('write_memory',))
MANAGER_TOOLS = SOURCE_TOOLS | CONTEXT_TOOLS | WRITE_TOOLS | frozenset(('select_task_tools', 'search_context', 'list_context', 'find_experts', 'read_context', 'delegate_task',
    'get_task_status', 'adopt_result', 'send_agent_message', 'revise_task', 'cancel_task'))
EXPERT_TOOLS = SOURCE_TOOLS | CONTEXT_TOOLS | WRITE_TOOLS | frozenset(('request_manager_information', 'read_context', 'search_context',
    'ask_collaborator', 'delegate_collaborator'))


class ControlledEngine:
    def __init__(self, tasks, private_root, ledger, model_provider):
        self.tasks, self.ledger, self.model_provider = tasks, ledger, model_provider
        self.private_root = Path(private_root).absolute()
        self.active = set()

    def binding(self, request):
        research = self.tasks.research
        auxiliary = current_admission.get()
        if auxiliary is not None and auxiliary.request.run_id == request.run_id:
            if auxiliary.request != request:
                raise ValueError('Auxiliary request changed after admission')
            auxiliary.validate()
            if auxiliary.kind in ('planning', 'discussion', 'consultation', 'learning', 'memory_capture'):
                snapshot = research.scopes.repository.snapshot(request.run_id)
                if request.input != snapshot.input or snapshot.mode != auxiliary.kind or request.agent_id != snapshot.agent.id:
                    raise ValueError('Auxiliary request differs from assembled admission')
                research.scopes.valid(snapshot.scope)
                if getattr(research, 'learning', None): research.learning.validate_skills(snapshot.skill_catalog, snapshot.agent)
            self.ledger.check_cancelled(auxiliary.task_id, auxiliary.spec_version)
            root = research.research(auxiliary.research_id)
            scope = research.scopes.current(root.scope_id, research.owner)
            identity = dict(research_id=root.id, session_id=auxiliary.session_id,
                root_scope_id=scope.id, root_scope_version=scope.version,
                task_id=auxiliary.task_id, spec_version=auxiliary.spec_version,
                run_id=request.run_id, entrance=auxiliary.kind,
                authorization_generation=digest({'root': scope.id, 'version': scope.version,
                    'task': auxiliary.task_id, 'spec': auxiliary.spec_version}))
            if auxiliary.kind in ('planning', 'discussion', 'consultation', 'learning', 'memory_capture'):
                identity.update(scope_id=snapshot.scope_id, scope_version=snapshot.scope_version, agent=snapshot.agent.model_dump())
            return None, identity, self.workspace_id(identity)
        binding = self.tasks.query('run', request.run_id)
        if binding is None:
            binding = research.query('run', request.run_id)
            if binding is None: raise ValueError('Controlled requires server admission')
            research.validate_binding(binding)
            node = research.node(binding.node_id)
            snapshot = research.scopes.repository.snapshot(request.run_id)
            scope = research.scopes.current(node.scope_id, research.owner)
            if (request.input != snapshot.input or request.agent_id != node.id
                    or request.limits != binding.limits or scope.version != snapshot.scope_version):
                raise ValueError('Research request differs from frozen admission')
            root_scope = research.scopes.current(research.research(node.research_id).scope_id, research.owner)
            identity = dict(research_id=node.research_id, session_id=node.id,
                root_scope_id=root_scope.id, root_scope_version=root_scope.version,
                run_id=request.run_id, entrance='plan_step' if node.plan_proposal_id else 'manager', scope_id=scope.id,
                scope_version=scope.version, authorization_generation=digest({
                    'root': root_scope.id, 'version': root_scope.version,
                    'scope': scope.id, 'scope_version': scope.version}))
            return None, identity, self.workspace_id(identity)
        spec = self.tasks.spec(binding.task_id, binding.spec_version)
        snapshot = self.tasks.scopes.repository.snapshot(request.run_id)
        if (request.input != snapshot.input or request.agent_id != spec.agent.id or snapshot.agent != spec.agent
                or any(getattr(spec.limits, field) is not None and
                       (getattr(request.limits, field) is None or
                        getattr(request.limits, field) > getattr(spec.limits, field))
                       for field in ('max_turns', 'max_calls', 'timeout_seconds', 'max_depth'))):
            raise ValueError('Controlled request does not match frozen admission')
        self.tasks._validate_durable_access(spec, binding)
        self.ledger.check_cancelled(spec.id, spec.version)
        root = self.tasks.research.research(spec.research_id)
        root_scope = self.tasks.scopes.current(root.scope_id, self.tasks.research.owner)
        identity = dict(research_id=spec.research_id, session_id=spec.expert_session_id or spec.id,
            agent=spec.agent.model_dump(),
            root_scope_id=root_scope.id, root_scope_version=root_scope.version,
            task_id=spec.id, spec_version=spec.version, run_id=binding.id,
            scope_id=binding.scope_id, scope_version=binding.scope_version,
            authorization_generation=digest({'root': root_scope.id, 'version': root_scope.version,
                'scope': binding.scope_id, 'scope_version': binding.scope_version}),
            model=binding.model, method_digest=binding.method_digest, entrance='expert')
        return spec, identity, self.workspace_id(identity)

    @staticmethod
    def workspace_id(identity):
        return digest({k: identity[k] for k in ('research_id', 'session_id', 'authorization_generation')})

    def validate_run(self, run_id, *, require_started=False):
        """Saved results and retry_save remain subject to the admitted generation."""
        from ..context.scopes import ScopeDenied
        saved = self.ledger.binding(run_id)
        if saved is None:
            if require_started:
                raise ScopeDenied('Result has no controlled admission')
            return  # Admission has not entered the runner yet.
        root = self.tasks.scopes.current(saved['root_scope_id'], self.tasks.research.owner)
        if root.version != saved['root_scope_version']:
            raise ScopeDenied('Controlled authorization generation was revoked; rebuild clean input')
        if 'task_id' in saved:
            self.ledger.check_cancelled(saved['task_id'], saved['spec_version'])

    async def execute(self, request, spec, tools, invoke):
        task_spec, identity, workspace_id = self.binding(request)
        kind = identity['entrance']
        allowed = {'expert': EXPERT_TOOLS, 'manager': MANAGER_TOOLS,
                   'learning': frozenset(), 'memory_capture': WRITE_TOOLS,
                   'consultation': CONTEXT_TOOLS | frozenset(('read_information',)),
                   'discussion': CONTEXT_TOOLS | frozenset(('read_source', 'search_source', 'search_context', 'read_context', 'read_execution_tool')),
                   'preview': frozenset(('read_source', 'search_source')),
                   'planning': CONTEXT_TOOLS | frozenset(('read_source', 'search_source', 'search_context')), 'plan_step': SOURCE_TOOLS | CONTEXT_TOOLS | WRITE_TOOLS}[kind]
        if task_spec is not None and task_spec.kind == 'coding':
            from ..coding.models import CODING_TOOLS
            allowed = CODING_TOOLS | CONTEXT_TOOLS | WRITE_TOOLS
        tools = tuple(t for t in tools if t.name in allowed)
        if identity.get('model', spec.model) != spec.model:
            raise ValueError('Model differs from frozen admission')
        spec = replace(spec, tools=tools)

        def validate():
            _, current, current_workspace = self.binding(request)
            if current != identity or current_workspace != workspace_id:
                raise ValueError('Controlled execution binding changed')

        frozen = {**identity, 'request_hash': digest(request.model_dump(mode='json')),
                  'agent_hash': digest({'instructions': spec.instructions, 'tools': [t.name for t in tools],
                    'tool_profiles': {k: {'tools': v.tools, 'instructions': v.instructions} for k,v in spec.tool_profiles.items()},
                    'model': spec.model, 'output': spec.output_type.model_json_schema() if spec.output_type else None,
                    'output_validation': spec.validate_output is not None}),
                  'execution': 'controlled_tools', 'checkpoint_protocol': 'ce7-v4-memory-capture-budget',
                  'code_contract': task_spec.coding.model_dump(mode='json') if task_spec and task_spec.coding else None}
        memory_model_name = os.environ.get('OPENAI_MEMORY_MODEL', 'deepseek-flash')
        if not memory_model_name.strip(): raise ValueError('OPENAI_MEMORY_MODEL must name a model')
        frozen['memory_model'] = memory_model_name
        from ..memories.capture_models import CAPTURE_INSTRUCTION
        from ..context.memory_budget import MEMORY_TOKEN_BUDGET
        frozen['memory_capture_contract'] = digest(CAPTURE_INSTRUCTION)
        frozen['memory_injection_budget'] = MEMORY_TOKEN_BUDGET
        from ..context.memory_retrieval import MEMORY_INSTRUCTION, MemorySelection, MAX_INPUT_BYTES, MAX_BATCH_ENTRIES
        frozen['memory_query_contract'] = digest({'instructions': MEMORY_INSTRUCTION,
            'schema': MemorySelection.model_json_schema(), 'input_bytes': MAX_INPUT_BYTES,
            'batch_entries': MAX_BATCH_ENTRIES})
        from ..memories.write_models import WRITE_REVIEW_INSTRUCTION, WriteReview, WriteMemory
        frozen['memory_write_contract'] = digest({'instructions': WRITE_REVIEW_INSTRUCTION,
            'review': WriteReview.model_json_schema(), 'tool': WriteMemory.model_json_schema()})
        model = self.model_provider.get_model(spec.model)
        token, epoch = self.ledger.acquire(workspace_id, frozen, request.limits,
                                           self.ledger.database.process_id)
        self.active.add(workspace_id)
        workspace = None
        work = None
        broker = None
        try:
            workspace = Workspace(self.private_root / workspace_id)
            from ..context.artifacts import ContextArtifacts
            artifacts = ContextArtifacts(workspace, validate,
                lambda page: self.ledger.record_read(request.run_id, page))
            for key, text in request.input.external_context.items():
                if artifacts.save_text(text) != key: raise ValueError('Frozen context hash mismatch')
            proposal = self.tasks.research.planning.get(self.tasks.research.node(request.agent_id).plan_proposal_id) if kind=='plan_step' else None
            plan_budget = ({'node_ids':tuple(step.node_id for step in proposal.plan.steps),'max_tokens':proposal.plan.policy.max_total_tokens}
                if proposal and proposal.plan.policy.max_total_tokens else None)
            broker = Broker(request=request, spec=spec, tools=tools, invoke=invoke,
                model=model, ledger=self.ledger, workspace=workspace_id, token=token,
                epoch=epoch, validate=validate,
                private_tools=((() if kind in ('discussion', 'planning', 'consultation', 'learning', 'memory_capture') else workspace.bindings()) + (artifacts.binding(),))
                    if request.limits.max_calls else (), artifacts=artifacts, plan_budget=plan_budget,
                memory_model_name=memory_model_name,
                memory_context=(self.tasks.research.memories, self.tasks.scopes.repository.snapshot(request.run_id),
                    identity['research_id']) if self.tasks is not None else None)
            # Resolve the lightweight provider only when this run has candidates.
            if broker.memory_context and (broker.memory_context[1].memory_catalog or any(t.name == 'write_memory' for t in tools)):
                broker.memory_model = self.model_provider.get_model(memory_model_name)
            from .checkpoints import Checkpoints
            code_snapshot = lambda: None
            if task_spec and task_spec.coding:
                generation = digest({'root': identity['root_scope_id'], 'version': identity['root_scope_version']})
                code_workspace = self.tasks.coding.admit(task_spec, generation, validate)
                code_snapshot = code_workspace.execution_snapshot
            checkpoints = Checkpoints(broker, code_snapshot)
            work = asyncio.create_task(run(request, spec, tools, workspace, broker, checkpoints))
            async with asyncio.timeout(request.limits.timeout_seconds):
                while not work.done():
                    await asyncio.wait((work,), timeout=0.1)
                    validate()
                result = await work
            validate()
        except asyncio.CancelledError:
            from ..core import execution_deadline
            deadline = execution_deadline.get()
            timed_out = deadline is not None and deadline.expired()
            result = RunResult(run_id=request.run_id, status='failed' if timed_out else 'cancelled',
                usage=self.ledger.usage(request.run_id), error=RunError(
                    code='timeout' if timed_out else 'cancelled', origin='engine',
                    message='Run deadline exceeded' if timed_out else 'Controlled execution cancelled',
                    retryable=timed_out))
        except TimeoutError:
            result = RunResult(run_id=request.run_id, status='failed', usage=self.ledger.usage(request.run_id),
                error=RunError(code='timeout', origin='engine', message='Run deadline exceeded', retryable=True))
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            detail = type(exc).__name__ + (f'; HTTP {status}' if isinstance(status, int) else '')
            result = RunResult(run_id=request.run_id, status='failed', usage=self.ledger.usage(request.run_id),
                error=RunError(code='controlled_failed', origin='engine',
                    message=f'Controlled execution failed ({detail})'))
        finally:
            if work is not None:
                if not work.done():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            try:
                if workspace is not None: workspace.close()
            finally:
                self.active.discard(workspace_id)
                self.ledger.release(workspace_id, token, epoch)
        if result.status == 'succeeded':
            try:
                validate()
            except Exception:
                result = RunResult(run_id=request.run_id, status='failed', usage=self.ledger.usage(request.run_id),
                    error=RunError(code='controlled_invalidated', origin='engine',
                        message='Authorization or task changed before publication'))
        if broker is not None: broker.policy.finish(result)
        return result
