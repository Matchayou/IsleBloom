"""Bounded admission, explicit stop policies, and collection of committed runs."""
import asyncio

from ..contracts import ExecutePort, RunRequest
from ..execution.ports import Conflict
from .collect import collect
from .models import DispatchGroup, DispatchPlan


class Dispatcher:
    def __init__(self, executor: ExecutePort, catalog, repository):
        self.executor, self.catalog, self.repository = executor, catalog, repository

    async def dispatch(self, plan: DispatchPlan):
        previous = self.repository.get_dispatch(plan.group_id)
        if previous:
            if previous.plan != plan:
                raise Conflict('Dispatch ID reused with a different plan')
            return collect(self.repository, plan.group_id)  # Query only; never recover computation.
        for item in plan.items:
            self.catalog.get(item.agent_id)
            if self.repository.get(item.run_id) is not None:
                raise Conflict('Child run ID already exists')
            if item.limits.max_depth < 1:
                raise ValueError('Dispatch child requires depth 1')
        group = DispatchGroup(plan=plan)
        self.repository.create_dispatch(group)
        active, reasons = {}, {}
        next_index, admitted, tokens = 0, 0, 0
        stop = None
        deadline = asyncio.get_running_loop().time() + plan.policy.timeout_seconds

        async def execute(item):
            return await self.executor.execute(RunRequest(run_id=item.run_id, parent_run_id=plan.group_id,
                agent_id=item.agent_id, input=item.input, limits=item.limits, depth=1))

        def absorb(task, item):
            nonlocal tokens, stop
            try:
                result = task.result()
                record = self.repository.get(item.run_id)
                if record is None or record.result != result or record.commit_id is None:
                    raise ValueError('ExecutePort returned an uncommitted result')
                tokens += result.usage.input_tokens + result.usage.output_tokens
                if result.status != 'succeeded' and plan.policy.completion == 'fail_fast':
                    stop = stop or 'failed_fast'
                if plan.policy.max_total_tokens is not None and (
                        not result.usage.complete or tokens >= plan.policy.max_total_tokens):
                    stop = stop or 'budget_stopped'
            except asyncio.CancelledError:
                reasons[item.key] = stop or 'cancelled'
            except Exception as exc:
                # Keep the failure visible without persisting potentially sensitive exception text.
                reasons[item.key] = f'execution_or_publication_failed:{type(exc).__name__}'
                if plan.policy.completion == 'fail_fast':
                    stop = stop or 'failed_fast'
                if plan.policy.max_total_tokens is not None:
                    stop = stop or 'budget_stopped'  # Unknown usage cannot safely admit more work.

        try:
            while next_index < len(plan.items) or active:
                if asyncio.get_running_loop().time() >= deadline:
                    stop = 'timed_out'
                while (stop is None and next_index < len(plan.items) and
                       len(active) < plan.policy.max_concurrency and admitted < plan.policy.max_runs):
                    item = plan.items[next_index]
                    next_index += 1
                    admitted += 1
                    active[asyncio.create_task(execute(item))] = item
                if stop is not None or not active:
                    if stop is None and next_index < len(plan.items):
                        stop = 'budget_stopped'
                    break
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                done, _ = await asyncio.wait(active, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    stop = 'timed_out'
                    break
                for task in done:
                    absorb(task, active.pop(task))
        except asyncio.CancelledError:
            stop = 'cancelled'
        finally:
            # Drain children before publishing group state; Core persists cooperative cancellation.
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
                for task, item in active.items():
                    absorb(task, item)
            for item in plan.items[next_index:]:
                reasons[item.key] = stop or 'not_started'
            self.repository.finish_dispatch(group.model_copy(update={'status': stop or 'settled', 'reasons': reasons}))
        return collect(self.repository, plan.group_id)
