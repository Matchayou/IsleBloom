"""DBOS adapter for fixed expert task sequences. Business state stays in task repositories."""
import json
from dbos import DBOS, SetWorkflowID

from .execution.ports import Conflict
from .tasks.service import TransientExecutionError


QUEUE = 'research-experts-v1'
CODE_VERSION = 'plan-v8'
_tasks = None
_plans = None


def _service():
    if _tasks is None:
        raise RuntimeError('Durable task service is not active')
    return _tasks


@DBOS.step(name='research.prepare_expert.v1')
def prepare_expert(task_id, version):
    return _service().prepare_durable(task_id, version)


@DBOS.step(name='research.execute_expert.v1', preemptible=True,
           retries_allowed=True, max_attempts=2,
           should_retry=lambda exc: isinstance(exc, TransientExecutionError))
async def execute_expert(task_id, version, run_id):
    return await _service().run_durable(task_id, version, run_id)


@DBOS.step(name='research.summarize_experts.v1')
def summarize_experts(run_ids):
    results = []
    for run_id in run_ids:
        binding = _service().query('run', run_id)
        spec = _service().spec(binding.task_id)
        _service()._validate_durable_access(spec, binding)
        receipt = _service().query('receipt', f'task-result:{run_id}')
        if receipt is None:
            raise Conflict('Expert result was not committed')
        results.append({'run_id':run_id, 'delivery':receipt.delivery,
            'program_passed':all(check.status == 'passed' for check in receipt.program_checks),
            'semantic_pending':any(check.status == 'pending' for check in receipt.semantic_checks)})
    return {'experts':results}


@DBOS.workflow(name='research.expert_sequence.v1', max_recovery_attempts=3)
async def expert_sequence(version, frozen):
    if version != CODE_VERSION:
        raise Conflict('Durable workflow code version mismatch')
    completed = []
    for task_id, task_version, method_digest, model, route_id in frozen:
        spec = _service().spec(task_id)
        if (spec.version, spec.method_digest, spec.model, spec.route_id) != (
                task_version, method_digest, model, route_id):
            raise Conflict('Frozen task, method, route or model changed')
        run_id = prepare_expert(task_id, task_version)
        completed.append(await execute_expert(task_id, task_version, run_id))
    return summarize_experts(completed)


class DurableResearch:
    def __init__(self, tasks, database_url):
        global _tasks
        from .controlled.engine import ControlledEngine
        if not isinstance(tasks.engine, ControlledEngine):
            raise Conflict('DBOS requires the controlled checkpoint engine')
        if _tasks is not None:
            raise RuntimeError('Only one durable task service may run in this process')
        _tasks = tasks
        global _plans
        _plans = tasks.research.planning
        DBOS(config={'name': 'research-agent-stage6', 'system_database_url': database_url,
                     'application_version': CODE_VERSION})
        DBOS.launch()

    async def start(self):
        await DBOS.register_queue_async(QUEUE, global_concurrency=2, worker_concurrency=2)
        return self

    async def submit(self, job_id, task_ids):
        if not job_id or not job_id.strip() or not task_ids:
            raise ValueError('Job ID and task IDs required')
        if len(set(task_ids)) != len(task_ids):
            raise ValueError('Duplicate task in durable job')
        frozen = tuple((spec.id, spec.version, spec.method_digest, spec.model, spec.route_id)
            for spec in (_service().spec(task_id) for task_id in task_ids))
        workflow_id = f'research:{job_id}'
        old = await DBOS.get_workflow_status_async(workflow_id)
        if old:
            if old.input is None or json.dumps(old.input['args']) != json.dumps((CODE_VERSION, frozen)):
                raise Conflict('Job ID reused with different frozen tasks')
            return await self.status(job_id)
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(QUEUE, expert_sequence, CODE_VERSION, frozen)
        admitted = await DBOS.get_workflow_status_async(workflow_id)
        if admitted is None or admitted.input is None or json.dumps(admitted.input['args']) != json.dumps((CODE_VERSION, frozen)):
            raise Conflict('Concurrent job ID delivery used different frozen tasks')
        return await self.status(job_id)

    async def submit_plan(self, proposal_id, plans):
        global _plans
        _plans = plans
        p = plans.get(proposal_id)
        workflow_id = p.workflow_id or f'planning:{p.id}'
        old = await DBOS.get_workflow_status_async(workflow_id)
        if old:
            if old.app_version != CODE_VERSION: raise Conflict('Plan workflow code version changed')
            if old.input is None or tuple(old.input['args']) != (p.id,p.approved_revision):
                raise Conflict('Plan workflow identity differs from approval')
            return old.status
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(QUEUE, plan_workflow, p.id, p.approved_revision)
        return 'ENQUEUED'

    async def cancel_plan(self, proposal_id):
        p = _plans.research.query('proposal', proposal_id)
        workflow_id = p.workflow_id or f'planning:{p.id}'
        if await DBOS.get_workflow_status_async(workflow_id):
            await DBOS.cancel_workflow_async(workflow_id)

    async def status(self, job_id):
        status = await DBOS.get_workflow_status_async(f'research:{job_id}')
        if status is None: raise KeyError(job_id)
        if status.app_version != CODE_VERSION:
            raise Conflict('Durable checkpoint application version mismatch')
        frozen = status.input['args'][1] if status.input else ()
        tasks = []
        for task_id, version, _, _, _ in frozen:
            spec = _service().spec(task_id)
            if spec.version != version:
                raise Conflict('Task version changed')
            runs = [r for r in _service().query('list_runs', task_id) if r.spec_version == version]
            for run in runs: _service()._validate_durable_access(spec, run)
            tasks.append({'task_id': task_id, 'run_ids': [r.id for r in runs],
                'receipts': [f'task-result:{r.id}' for r in runs
                             if _service().query('receipt', f'task-result:{r.id}')],
                'operations': {r.id: _service().engine.ledger.operation_history(r.id) for r in runs}})
        return {'job_id': job_id, 'workflow_id': status.workflow_id,
            'status': status.status, 'tasks': tasks,
            'summary': status.output if status.status == 'SUCCESS' else None}

    async def cancel(self, job_id):
        await self.status(job_id)
        await DBOS.cancel_workflow_async(f'research:{job_id}')
        return await self.status(job_id)

    def close(self):
        global _tasks, _plans
        DBOS.destroy()
        _tasks = None
        _plans = None


@DBOS.step(name='research.generate_plan.v1')
async def generate_plan_proposal(proposal_id):
    return (await _plans.generate(proposal_id)).status


@DBOS.step(name='research.execute_plan.v1')
async def execute_approved_plan(proposal_id):
    return (await _plans.execute(proposal_id)).status


@DBOS.workflow(name='research.plan.v1', max_recovery_attempts=3)
async def plan_workflow(proposal_id, approved_revision):
    p = _plans.get(proposal_id)
    if approved_revision is None:
        return await generate_plan_proposal(proposal_id)
    if p.approved_revision != approved_revision: raise Conflict('Approved plan version changed')
    return await execute_approved_plan(proposal_id)
