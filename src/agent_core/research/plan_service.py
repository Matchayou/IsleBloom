"""Durable proposals and explicit, version-bound human decisions."""
import asyncio
import os
from ..contracts import RunRequest, RunLimits
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict, now
from ..resources.snapshots import identity
from .chat import ChatService
from .models import ResearchNode
from .planning_models import PlanProposal, GeneratePlan, PlanDecision, RevisePlan
from .planning import planning_input, generate, compile_plan


class PlanService:
    def __init__(self, research):
        self.research = research
        self.active = {}
        self.durable = None

    def get(self, key):
        p = self.research.query('proposal', key)
        self.research.research(p.research_id)
        self.validate_inputs(p)
        return p

    def list(self, research_id, node_id=None):
        self.research.research(research_id)
        return [self.get(p.id) for p in self.research.query('list_proposals', research_id)
                if node_id is None or p.origin_node_id == node_id]

    def change(self, p, **updates):
        value = p.model_copy(update={**updates, 'revision': p.revision + 1, 'updated_at': now()})
        with self.research.database.transaction() as tx: tx.research.save_proposal(value, p.revision)
        return value

    def validate_inputs(self, p, *, unchanged=False):
        s = self.research
        root = s.research(p.research_id)
        node = s.node(p.origin_node_id)
        if node.research_id != root.id or node.selection != 'selected': raise ScopeDenied('Plan origin unavailable')
        root_scope = s.scopes.current(root.scope_id, s.owner)
        scope = s.scopes.current(node.scope_id, s.owner)
        if root_scope.version != p.root_scope_version or scope.version != p.node_scope_version:
            raise ScopeDenied('Plan authorization changed; generate a new proposal')
        if set(p.resource_refs) & s._excluded_refs(p.research_id): raise ScopeDenied('Plan input was excluded')
        snapshot = s.scopes.repository.snapshot(p.planning_request.run_id)
        s.memories.validate(p.research_id, snapshot.memory_catalog, agent=snapshot.agent)
        planning_scope = snapshot.scope
        s.scopes.valid(planning_scope)
        for ref in p.resource_refs:
            s.scopes.check(root_scope, ref)
            s.scopes.check(planning_scope, ref)
        for key in p.result_ids: s.check_result(key, planning_scope)
        if unchanged:
            chat = ChatService(s, s.chat_tasks).read(node.id)
            if node.revision != p.origin_revision or chat.revision != p.chat_revision:
                raise Conflict('Source conversation changed; revise and confirm the new plan')

    def validate_generation(self, key, revision):
        p = self.get(key)
        if p.status != 'generating' or p.revision != revision: raise Conflict('Planning request cancelled or replaced')
        self.validate_inputs(p, unchanged=True)

    def prepare(self, data):
        data = GeneratePlan.model_validate(data)
        s = self.research
        s.research(data.research_id)
        old = s.query('proposal_request', data.research_id, data.request_id)
        if old:
            if old.origin_node_id != data.origin_node_id or old.request != data.request:
                raise Conflict('Plan request ID reused with different input')
            return self.get(old.id)
        node = s.node(data.origin_node_id)
        if node.plan_proposal_id: raise Conflict('Plan steps cannot generate nested plans')
        if node.research_id != data.research_id: raise ScopeDenied('Foreign origin node')
        if node.active_run_id: raise Conflict('Origin has an active run')
        chat = ChatService(s, s.chat_tasks).read(node.id)
        if chat.revision != data.expected_chat_revision: raise Conflict('Chat revision changed')
        scope = s.scopes.current(node.scope_id, s.owner)
        root_scope = s.scopes.current(s.research(data.research_id).scope_id, s.owner)
        refs = tuple(dict.fromkeys((*node.resource_refs, *scope.allowed, *scope.inherited,
            *((*root_scope.allowed,*root_scope.inherited) if node.display_parent_id is None else ()),
            *(r for m in (*chat.inherited, *chat.messages) for r in m.resource_refs))))
        refs = tuple(ref for ref in refs if ref not in s._excluded_refs(data.research_id)
            and s.scopes.resources.describe(ref)[0].kind != 'discovery')
        results = tuple(dict.fromkeys((*node.result_ids, *(r for m in (*chat.inherited, *chat.messages) for r in m.result_ids if not r.startswith('task-result:')))))
        run_id, key = identity(), identity()
        planning_scope = s.scopes.create(s.owner, tools=('read_source','search_source') if 'read_source' in scope.tools else ())
        for ref in refs:
            planning_scope = s.scopes.grant(planning_scope,root_scope,ref)
        planning_scope = s.scopes.select(planning_scope,refs)
        prepared = planning_input(s, node, chat, data.request, run_id, refs, results, planning_scope, 'planner:' + key)
        p = PlanProposal(id=key, research_id=data.research_id, origin_node_id=node.id, request_id=data.request_id,
            request=data.request, origin_revision=node.revision, chat_revision=chat.revision,
            root_scope_version=root_scope.version, node_scope_version=scope.version, resource_refs=refs, result_ids=results,
            history=tuple(m.model_dump(mode='json') for m in (*chat.inherited, *chat.messages)), original_instructions=node.instructions,
            planning_request=RunRequest(run_id=run_id, agent_id='planner:' + key, input=prepared,
                limits=RunLimits(max_calls=8, timeout_seconds=180, max_depth=0)),
            model=os.environ.get('OPENAI_MANAGER_MODEL', 'deepseek-v4-pro'))
        self.validate_inputs(p, unchanged=True)
        return p

    def create(self, data):
        p = self.prepare(data)
        if self.research.query('proposal_request', p.research_id, p.request_id): return p
        with self.research.database.transaction() as tx: tx.research.create_proposal(p)
        return p

    async def generate(self, key):
        p = self.get(key)
        if p.status != 'generating': return p
        if key in self.active: return p
        self.active[key] = asyncio.current_task()
        try:
            result = await generate(self, p)
            current = self.get(key)
            if current.status != 'generating' or current.revision != p.revision: return current
            if result.status != 'succeeded':
                return self.change(current, status='generation_failed', planning_result=result,
                    reason=result.error.message if result.error else result.status)
            changes = compile_plan(current, result.output)
            self.validate_definition(current, changes['plan'], changes['nodes'])
            return self.change(current, status='awaiting_confirmation', planning_result=result, **changes)
        except asyncio.CancelledError:
            current = self.research.query('proposal', key)
            if current.status == 'generating': self.change(current, status='interrupted', reason='Planning interrupted')
            raise
        except Exception as exc:
            current = self.research.query('proposal', key)
            if current.status == 'generating' and current.revision == p.revision:
                self.change(current, status='generation_failed', reason=type(exc).__name__ + ': ' + str(exc)[:500])
            return self.research.query('proposal', key)
        finally: self.active.pop(key, None)

    def validate_definition(self, p, plan, nodes):
        if plan is None or not nodes: raise Conflict('Plan is incomplete')
        allowed = set(self.research.scopes.current(self.research.research(p.research_id).scope_id, self.research.owner).tools) & set(self.research.node(p.origin_node_id).allowed_tools)
        if 'read_source' in allowed: allowed.add('search_source')
        if any(not set(n.allowed_tools) <= allowed for n in nodes): raise ScopeDenied('Plan requests unavailable tools')
        if {s.node_id for s in plan.steps} != {n.node_id for n in nodes}: raise Conflict('Plan definitions differ from DAG')
        if plan.policy.max_runs < len(plan.steps): raise Conflict('Plan run budget is insufficient')
        if any(step.limits.max_depth != 1 or step.limits.timeout_seconds > plan.policy.timeout_seconds for step in plan.steps):
            raise Conflict('Step limits exceed plan limits')

    def confirm(self, data):
        data = PlanDecision.model_validate(data)
        p = self.get(data.proposal_id)
        if p.approved_revision == data.expected_revision: return p
        if p.revision != data.expected_revision or p.status != 'awaiting_confirmation': raise Conflict('Plan revision or status changed')
        if p.questions: raise Conflict('Resolve required questions before confirming')
        self.validate_inputs(p, unchanged=True)
        self.validate_definition(p, p.plan, p.nodes)
        s = self.research
        # Scope grants are program-owned; unused scopes on a failed CAS cannot execute.
        values = []
        for definition in p.nodes:
            if definition.node_id in p.reused_steps:
                version = s.check_result(p.reused_steps[definition.node_id])
                previous = s.query('proposal',p.recovery_of)
                if (version.node_id != definition.node_id or version.completion != 'complete' or
                        previous is None or definition not in previous.nodes):
                    raise Conflict('Reused step differs from its completed frozen definition')
                continue
            scope = s.scopes.create(s.owner, tools=definition.allowed_tools)
            node = ResearchNode(id=definition.node_id, research_id=p.research_id, display_parent_id=p.origin_node_id,
                plan_proposal_id=p.id, goal=definition.goal, instructions=definition.instructions,
                allowed_tools=definition.allowed_tools, scope_id=scope.id, resource_refs=definition.resource_refs, result_ids=definition.result_ids)
            s._configure_scope(node, definition.resource_refs, definition.result_ids)
            values.append(node)
        approved = p.model_copy(update={'status':'queued', 'revision':p.revision + 1, 'updated_at':now(),
            'approved_revision':p.revision, 'approved_by':s.owner, 'approved_at':now(),
            'workflow_id':f'plan:{p.id}:{p.revision}'})
        with s.database.transaction() as tx:
            tx.research.save_proposal(approved, p.revision)
            for node in values: tx.research.create_node(node)
            tx.research.admit_plan(p.plan)
        return approved

    def revise(self, data):
        data = RevisePlan.model_validate(data)
        p = self.get(data.proposal_id)
        old = self.research.query('proposal_request', p.research_id, data.request_id)
        request = p.request + '\n计划修改要求：' + data.feedback
        if old:
            if old.request != request: raise Conflict('Revision request ID reused')
            return self.get(old.id)
        if p.revision != data.expected_revision or p.status not in ('awaiting_confirmation', 'generation_failed', 'interrupted', 'rejected'):
            raise Conflict('Only an unapproved plan can be revised')
        chat = ChatService(self.research, self.research.chat_tasks).read(p.origin_node_id)
        # A fresh proposal is immutable history; old confirm loses its CAS.
        new = self.prepare(GeneratePlan(research_id=p.research_id, origin_node_id=p.origin_node_id,
            expected_chat_revision=chat.revision, request_id=data.request_id, request=request))
        replaced = p.model_copy(update={'status':'rejected','reason':'Replaced by proposal ' + new.id,
            'revision':p.revision + 1,'updated_at':now()})
        with self.research.database.transaction() as tx:
            tx.research.save_proposal(replaced,p.revision)
            tx.research.create_proposal(new)
        return new

    def recovery_proposal(self, data, *, max_total_tokens=None):
        """Prepare an explicit fresh approval; never replay a committed run."""
        from .models import ResearchPlan, PlanStep
        data = PlanDecision.model_validate(data)
        p = self.get(data.proposal_id)
        if p.approved_revision is None or p.status not in ('blocked','failed'):
            raise Conflict('Recovery requires a terminal failed approved plan')
        if p.revision != data.expected_revision: raise Conflict('Plan revision changed')
        policy = p.plan.policy if max_total_tokens is None else type(p.plan.policy).model_validate(
            {**p.plan.policy.model_dump(),'max_total_tokens':max_total_tokens})
        request_id = f'recovery:{p.id}:{p.revision}:tokens:{policy.max_total_tokens}'
        old = self.research.query('proposal_request',p.research_id,request_id)
        if old: return self.get(old.id)
        completed, reused = [], {}
        for step in p.plan.steps:
            pinned = p.reused_steps.get(step.node_id)
            binding = self.research.query('run',self.research.check_result(pinned).run_id) if pinned else None
            command = self.research.query('command',binding.command_id if binding else f'plan:{p.plan.id}:{step.node_id}')
            if command and command.run_id:
                run = self.research.ledger.get(command.run_id)
                from ..controlled.engine import ControlledEngine
                audited = (self.research.engine.ledger.usage(command.run_id)
                    if isinstance(self.research.engine, ControlledEngine) and
                    self.research.engine.ledger.binding(command.run_id) else None)
                if not run or not run.result or not (audited.complete if audited else run.result.usage.complete):
                    raise Conflict('Execution or usage is unconfirmed; inspect before creating recovery')
                if run.result.status == 'succeeded':
                    version = self.research.check_result(f'result:{command.run_id}')
                    if version.completion == 'complete':
                        completed.append(version.id)
                        reused[step.node_id] = version.id
        self.validate_inputs(p,unchanged=True)
        # Freeze a fresh read-only input over the current authorized library.
        # Old planning scopes and runs remain immutable; acquired bodies survive
        # failure and need not be searched or fetched again in a recovery.
        prepared = self.prepare(GeneratePlan(research_id=p.research_id, origin_node_id=p.origin_node_id,
            expected_chat_revision=p.chat_revision, request_id=request_id, request=p.request))
        covered, pending = set(), list(completed)
        while pending:
            for key in self.research.query('inputs',pending.pop()):
                if key not in covered:
                    covered.add(key)
                    pending.append(key)
        completed = tuple(key for key in completed if key not in covered)
        mapping = {step.node_id:step.node_id if step.node_id in reused else identity() for step in p.plan.steps}
        plan = ResearchPlan(research_id=p.research_id,policy=policy,
            steps=tuple(PlanStep(node_id=mapping[step.node_id],depends_on=tuple(mapping[key] for key in step.depends_on),limits=step.limits) for step in p.plan.steps))
        value = p.model_copy(update={'id':identity(),'request_id':request_id,'revision':1,
            'status':'awaiting_confirmation','created_at':now(),'updated_at':now(),'recovery_of':p.id,
            'analysis':p.analysis.split('\n恢复方案：',1)[0] + '\n恢复方案：保持原研究目标、步骤、权限与时限。已完成步骤复用，未完成步骤续跑；完成步骤用量计入总预算，失败记录与用量另存。',
            'plan':plan,'nodes':tuple(n.model_copy(update={'node_id':mapping[n.node_id]}) for n in p.nodes),
            'approved_revision':None,'approved_by':None,'approved_at':None,'workflow_id':None,
            'execution_started_at':None,'planning_result':None,'reason':None,'import_digest':None})
        value = value.model_copy(update={'reused_steps':reused})
        if policy.max_total_tokens != p.plan.policy.max_total_tokens:
            value = value.model_copy(update={'analysis':value.analysis +
                f'\n待确认总token预算由{p.plan.policy.max_total_tokens}调整为{policy.max_total_tokens}；仅在确认后生效。'})
        value = value.model_copy(update={'planning_request':prepared.planning_request,
            'resource_refs':prepared.resource_refs,
            'result_ids':tuple(dict.fromkeys((*prepared.result_ids,*completed))),
            'nodes':tuple(n if n.node_id in reused else n.model_copy(update={'resource_refs':tuple(dict.fromkeys((*n.resource_refs,*prepared.resource_refs))),
                'result_ids':tuple(dict.fromkeys((*prepared.result_ids,
                    *(key for key in n.result_ids if key not in covered),
                    *(completed if not next(step for step in plan.steps if step.node_id==n.node_id).depends_on else ()))))}) for n in value.nodes),
            'analysis':value.analysis + '\n复用已完成步骤的保存结果，不重跑其模型；仅续跑未完成步骤，原成功与失败记录不变。已有步骤用量计入三步总预算。'})
        self.validate_definition(value,plan,value.nodes)
        with self.research.database.transaction() as tx:
            if tx.research.proposal(p.id).revision != p.revision: raise Conflict('Plan revision changed')
            tx.research.create_proposal(value)
        return value

    async def decide(self, data, action):
        data = PlanDecision.model_validate(data)
        p = self.get(data.proposal_id)
        status = 'rejected' if action == 'reject' else 'cancelled'
        if p.status == status: return p
        allowed = ('awaiting_confirmation',) if action == 'reject' else ('generating','queued','running','interrupted')
        if p.revision != data.expected_revision or p.status not in allowed: raise Conflict('Plan revision or status changed')
        value = self.change(p, status=status, reason='User ' + status)
        active = self.active.get(p.id)
        if active and active is not asyncio.current_task(): active.get_loop().call_soon_threadsafe(active.cancel)
        if p.plan:
            for step in p.plan.steps:
                node = self.research.node(step.node_id) if p.approved_revision is not None else None
                if node and node.active_run_id:
                    task = self.research.tasks.get(node.active_run_id)
                    if task: task.get_loop().call_soon_threadsafe(task.cancel)
        if self.durable: await self.durable.cancel_plan(p.id)
        return value

    def validate_step(self, node, command_id=None):
        p = self.get(node.plan_proposal_id)
        if p.approved_revision is None or p.status != 'running': raise ScopeDenied('Plan is not approved and running')
        if command_id is not None and command_id != f'plan:{p.plan.id}:{node.id}': raise ScopeDenied('Plan step requires approved execution')
        definition = next(n for n in p.nodes if n.node_id == node.id)
        if (node.goal, node.instructions, node.allowed_tools) != (definition.goal, definition.instructions, definition.allowed_tools):
            raise ScopeDenied('Approved step definition changed')
        return p

    async def execute(self, key):
        from .plans import execute_plan
        return await execute_plan(self, key)

    def recover(self):
        # Model/tool operations in flight require inspection; committed wave results remain usable.
        for research in self.research.query('list_researches', self.research.owner):
            for p in self.research.query('list_proposals', research.id):
                if p.status == 'generating' and self.research.ledger.get(p.planning_request.run_id):
                    run = self.research.ledger.get(p.planning_request.run_id)
                    if not run.result:
                        self.change(p, status='interrupted', reason='Planning run completion is unconfirmed')
                elif p.status == 'running':
                    uncertain = False
                    for step in p.plan.steps:
                        command = self.research.query('command', f'plan:{p.plan.id}:{step.node_id}')
                        if command and command.run_id:
                            run = self.research.ledger.get(command.run_id)
                            if run is None or run.result is None: uncertain = True
                    if uncertain:
                        self.change(p, status='interrupted', reason='Step completion is unconfirmed; inspect saved run')

    async def import_plan(self, plan, request_id, origin_node_id=None):
        """Manual/workflow plans become proposals; this never starts their steps."""
        from .models import ResearchPlan, PlanStep
        from .planning_models import PlannedNode
        plan = ResearchPlan.model_validate(plan)
        s = self.research
        s.research(plan.research_id)
        from ..controlled.ledger import digest
        original = [s.node(step.node_id) for step in plan.steps]
        fingerprint = digest({'plan':plan.model_dump(mode='json'), 'nodes':[
            {field:getattr(n,field) if field not in ('resource_refs',) else [ref.model_dump() for ref in n.resource_refs]
             for field in ('goal','instructions','allowed_tools','resource_refs','result_ids')} for n in original]})
        old = s.query('proposal_request', plan.research_id, request_id)
        if old:
            if old.import_digest != fingerprint: raise Conflict('Plan import request reused with different content')
            return self.get(old.id)
        if plan.policy.max_runs < len(plan.steps): raise Conflict('Plan run budget is insufficient')
        if any(n.research_id != plan.research_id or n.plan_proposal_id for n in original): raise ScopeDenied('Foreign or already-owned plan node')
        chat_service = ChatService(s, s.chat_tasks)
        chat = chat_service.read(origin_node_id) if origin_node_id else await chat_service.start(plan.research_id, '研究计划')
        refs = tuple(dict.fromkeys(ref for n in original for ref in n.resource_refs))
        chat_service._select_message_refs(chat.node_id, refs)
        p = self.prepare(GeneratePlan(research_id=plan.research_id, origin_node_id=chat.node_id,
            expected_chat_revision=chat.revision, request_id=request_id, request='审核固定研究计划'))
        mapping = {n.id: identity() for n in original}
        nodes = tuple(PlannedNode(node_id=mapping[n.id], goal=n.goal, instructions=n.instructions,
            deliverable='按步骤指令提交完整研究报告并说明证据与局限性', allowed_tools=n.allowed_tools,
            resource_refs=n.resource_refs, result_ids=n.result_ids) for n in original)
        frozen = ResearchPlan(research_id=plan.research_id,
            steps=tuple(PlanStep(node_id=mapping[step.node_id], depends_on=tuple(mapping[key] for key in step.depends_on),limits=step.limits) for step in plan.steps),
            policy=plan.policy.model_copy(update={'completion':'fail_fast'}))
        self.validate_definition(p, frozen, nodes)
        p = p.model_copy(update={'status':'awaiting_confirmation', 'analysis':'固定工作流已编译；请核对步骤、依赖、权限和预算后确认。',
            'resource_refs':refs, 'result_ids':tuple(dict.fromkeys(key for n in original for key in n.result_ids)), 'plan':frozen, 'nodes':nodes, 'import_digest':fingerprint})
        self.validate_inputs(p, unchanged=True)
        with s.database.transaction() as tx: tx.research.create_proposal(p)
        return p

    def view(self, key):
        p = self.get(key)
        steps = []
        if p.approved_revision is not None:
            for step in p.plan.steps:
                reused = p.reused_steps.get(step.node_id)
                binding = self.research.query('run',self.research.check_result(reused).run_id) if reused else None
                command = self.research.query('command',binding.command_id if binding else f'plan:{p.plan.id}:{step.node_id}')
                run = self.research.ledger.get(command.run_id) if command and command.run_id else None
                report = None
                result_id = None
                completion = None
                missing, limitations = (), ()
                if run and run.result and run.result.status == 'succeeded':
                    result_id = f'result:{run.run_id}'
                    version, report = self.research.read_result(result_id)
                    completion = version.completion
                    missing, limitations = version.missing, version.limitations
                steps.append(dict(node_id=step.node_id, status=run.status if run else 'waiting',
                    reused=bool(reused),
                    result_id=result_id, report=report, completion=completion,
                    missing=missing, limitations=limitations,
                    error=run.result.error if run and run.result else None,
                    usage=run.result.usage if run and run.result else None))
        public = p.model_dump(mode='json', exclude={'planning_request','history','original_instructions','import_digest'})
        if p.planning_result:
            public['planning_result'] = p.planning_result.model_dump(mode='json',exclude={'output','output_repairs'})
        sources = [{'ref':ref.model_dump(), 'name':self.research.scopes.resources.describe(ref)[0].name} for ref in p.resource_refs]
        return {**public, 'sources':sources, 'execution_steps': steps}
