"""Explicit human confirmation in fixed-plan contract fixtures."""
from uuid import uuid4
from agent_core.research.planning_models import PlanDecision

async def approve_plan(research, plan):
    p = await research.planning.import_plan(plan, 'test:' + str(uuid4()))
    research.planning.confirm(PlanDecision(proposal_id=p.id, expected_revision=p.revision))
    await research.run_plan(p.id)
    return p

async def approve_workflow(methods, key):
    p = await methods.propose(key)
    if p.approved_revision is None:
        methods.research.planning.confirm(PlanDecision(proposal_id=p.id, expected_revision=p.revision))
    await methods.research.run_plan(p.id)
    return p
