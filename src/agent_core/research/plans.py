"""Approved ResearchPlan execution; Dispatcher still owns each DAG wave."""
import asyncio
from ..contracts import RunRequest
from ..execution.ports import Conflict, now
from ..orchestration import Dispatcher
from ..orchestration.models import DispatchPlan, DispatchItem
from .models import NodeCommand


async def execute_plan(plans, proposal_id):
    s = plans.research
    p = plans.get(proposal_id)
    if p.approved_revision is None: raise Conflict('Plan requires human confirmation')
    if p.status not in ('queued', 'running'): return p
    if proposal_id in plans.active: return p
    plans.active[proposal_id] = asyncio.current_task()
    try:
        if p.status == 'queued':
            p = plans.change(p, status='running', execution_started_at=now())
        plan = p.plan
        def saved_command(node_id):
            if node_id in p.reused_steps:
                version = s.check_result(p.reused_steps[node_id])
                binding = s.query('run',version.run_id)
                return s.query('command',binding.command_id)
            return s.query('command',f'plan:{plan.id}:{node_id}')
        done, wave = set(), 0
        reason, final_status = None, 'succeeded'
        while len(done) < len(plan.steps):
            current = plans.get(p.id)
            if current.status != 'running': return current
            elapsed = (now() - current.execution_started_at).total_seconds()
            if elapsed >= plan.policy.timeout_seconds:
                reason, final_status = 'Plan time budget exhausted', 'blocked'
                break
            ready = [step for step in plan.steps if step.node_id not in done and set(step.depends_on) <= done]
            items, scopes, bindings = [], {}, []
            tokens, unknown = 0, False
            for step in plan.steps:
                record = saved_command(step.node_id)
                run = s.ledger.get(record.run_id) if record and record.run_id else None
                if run and run.result:
                    tokens += run.result.usage.input_tokens + run.result.usage.output_tokens
                    unknown |= not run.result.usage.complete
            if plan.policy.max_total_tokens and (unknown or tokens >= plan.policy.max_total_tokens):
                reason, final_status = 'Plan token budget exhausted or unknown', 'blocked'
                break
            for step in sorted(ready, key=lambda step: step.node_id):
                if step.node_id in p.reused_steps:
                    if s.check_result(p.reused_steps[step.node_id]).completion != 'complete':
                        reason, final_status = 'Reused step is not complete', 'blocked'
                        break
                    continue
                node = s.node(step.node_id)
                plans.validate_step(node)
                definition = next(n for n in p.nodes if n.node_id == node.id)
                result_ids = list(definition.result_ids)
                for upstream in step.depends_on:
                    record = saved_command(upstream)
                    try: result = s.check_result(f'result:{record.run_id}') if record and record.run_id else None
                    except (KeyError, PermissionError, Conflict): result = None
                    if result is None or result.completion != 'complete':
                        reason, final_status = 'Required upstream did not deliver a complete result', 'blocked'
                        break
                    result_ids.append(result.id)
                if reason: break
                c = NodeCommand(command_id=f'plan:{plan.id}:{step.node_id}', research_id=plan.research_id,
                    node_id=step.node_id, kind='adopt', resource_refs=definition.resource_refs,
                    result_ids=tuple(dict.fromkeys(result_ids)), limits=step.limits)
                record, fresh = s._accept(c)
                if record.run_id:
                    run = s.ledger.get(record.run_id)
                    if run and run.result and run.result.status == 'succeeded':
                        version = s.check_result(f'result:{record.run_id}')
                        if version.completion != 'complete': reason, final_status = 'Step delivered a partial result', 'blocked'
                        continue
                    # An uncertain step is never blindly re-issued with a new run ID.
                    reason, final_status = 'Step interrupted or failed; inspect its saved run before replanning', 'interrupted' if not run or run.status in ('running','saving','interrupted') else 'failed'
                    break
                binding, snapshot = s._prepare(record)
                bindings.append(binding)
                scopes[node.id] = s.scopes.current(node.scope_id, s.owner)
                items.append(DispatchItem(key=node.id, agent_id=node.id, run_id=binding.run_id,
                    input=snapshot.input, limits=step.limits))
            if reason: break
            if items:
                executor, catalog = s.execution_factory(scopes, s)
                class Admission:
                    async def execute(self, request):
                        binding = s.query('run', request.run_id)
                        s.validate_binding(binding)
                        s.tasks[request.run_id] = asyncio.current_task()
                        s.executors[request.run_id] = executor
                        try: return await executor.execute(request)
                        finally: s.tasks.pop(request.run_id, None)
                policy = plan.policy.model_copy(update={
                    'max_runs':len(items), 'timeout_seconds':max(.001,plan.policy.timeout_seconds - elapsed),
                    'max_total_tokens':max(1,plan.policy.max_total_tokens - tokens) if plan.policy.max_total_tokens else None})
                await Dispatcher(Admission(), catalog, s.ledger).dispatch(DispatchPlan(
                    group_id=f'{p.workflow_id}:wave:{wave}', items=tuple(items), policy=policy))
            for step in ready:
                record = saved_command(step.node_id)
                run = s.ledger.get(record.run_id) if record and record.run_id else None
                try: result = s.check_result(f'result:{record.run_id}') if run and run.result and run.result.status == 'succeeded' else None
                except (KeyError, PermissionError, Conflict): result = None
                if not result or result.completion != 'complete':
                    reason, final_status = 'Step failed, stopped or delivered a partial result', 'blocked'
                    break
            if reason: break
            done.update(step.node_id for step in ready)
            wave += 1
        current = plans.get(p.id)
        if current.status == 'running': return plans.change(current, status=final_status, reason=reason)
        return current
    except asyncio.CancelledError:
        current = s.query('proposal', p.id)
        if current.status == 'running': plans.change(current, status='interrupted', reason='Plan worker interrupted')
        raise
    except Exception as exc:
        current = s.query('proposal', p.id)
        if current.status == 'running': plans.change(current, status='blocked', reason=type(exc).__name__ + ': ' + str(exc)[:500])
        return s.query('proposal', p.id)
    finally:
        plans.active.pop(proposal_id, None)
