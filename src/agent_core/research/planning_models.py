"""A human-approved envelope around the existing ResearchPlan DAG."""
from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value, RunLimits, RunRequest, RunResult
from ..execution.ports import now
from ..resources.snapshots import ResourceRef, identity
from ..orchestration.models import DispatchPolicy
from .models import ResearchPlan


class StepDraft(Value):
    key: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=4000)
    instructions: str = Field(min_length=1, max_length=8000)
    deliverable: str = Field(min_length=1, max_length=2000)
    depends_on: tuple[str, ...] = ()
    allowed_tools: tuple[Literal['read_source', 'search_source', 'search_web', 'fetch_web'], ...] = ('read_source', 'search_source')
    limits: RunLimits = Field(default_factory=RunLimits)


class PlanningOutput(Value):
    analysis: str = Field(min_length=1, max_length=8000)
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    steps: tuple[StepDraft, ...] = Field(min_length=1, max_length=12)
    policy: DispatchPolicy = Field(default_factory=lambda: DispatchPolicy(max_runs=12, timeout_seconds=600, completion='fail_fast'))

    @model_validator(mode='after')
    def valid_graph(self):
        from .models import PlanStep
        ResearchPlan(research_id='draft', steps=tuple(PlanStep(node_id=s.key, depends_on=s.depends_on, limits=s.limits) for s in self.steps), policy=self.policy)
        if self.policy.max_runs < len(self.steps): raise ValueError('max_runs must cover every step')
        if self.policy.completion != 'fail_fast': raise ValueError('Plan execution requires fail_fast')
        if any(s.limits.max_depth != 1 or s.limits.timeout_seconds > self.policy.timeout_seconds for s in self.steps):
            raise ValueError('Step limits exceed plan limits')
        return self


class PlannedNode(Value):
    node_id: str
    goal: str
    instructions: str
    deliverable: str
    allowed_tools: tuple[str, ...]
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()


class PlanProposal(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    origin_node_id: str
    request_id: str
    request: str
    revision: int = 0
    status: Literal['generating', 'awaiting_confirmation', 'generation_failed', 'rejected', 'queued', 'running', 'succeeded', 'failed', 'blocked', 'interrupted', 'cancelled'] = 'generating'
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    origin_revision: int
    chat_revision: int
    root_scope_version: int
    node_scope_version: int
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    history: tuple[dict, ...] = ()
    original_instructions: str = ''
    planning_request: RunRequest
    planning_result: RunResult | None = None
    model: str
    analysis: str = ''
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    plan: ResearchPlan | None = None
    nodes: tuple[PlannedNode, ...] = ()
    approved_revision: int | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    workflow_id: str | None = None
    execution_started_at: datetime | None = None
    reason: str | None = None
    import_digest: str | None = None
    recovery_of: str | None = None
    reused_steps: dict[str, str] = Field(default_factory=dict)


class GeneratePlan(Value):
    research_id: str
    origin_node_id: str
    expected_chat_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=120)
    request: str = Field(min_length=1, max_length=12000)


class PlanDecision(Value):
    proposal_id: str
    expected_revision: int = Field(ge=0)


class RevisePlan(PlanDecision):
    request_id: str = Field(min_length=1, max_length=120)
    feedback: str = Field(min_length=1, max_length=12000)
