"""Immutable evaluation identities and reviewed labels."""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value
from ..resources.snapshots import ResourceRef


class Feedback(Value):
    id: str
    result_id: str
    research_id: str
    task_id: str
    spec_version: int
    run_id: str
    expert_id: str
    method_version: str
    method_digest: str
    model: str
    route_id: str | None
    resource_refs: tuple[ResourceRef, ...]
    kind: Literal['fact_error', 'verified_example', 'preference']
    description: str = Field(min_length=1)
    proposed_correction: str | None = None
    status: Literal['pending', 'verified', 'rejected'] = 'pending'
    attribution: Literal['source', 'parse', 'task', 'handoff', 'method', 'model', 'route', 'checker', 'final_delivery', 'reference'] | None = None
    finding: str | None = None
    reviewer: str | None = None
    acceptance_number: int | None = None


class FrozenCase(Value):
    id: str
    feedback_id: str
    result_id: str
    research_id: str
    task_id: str
    spec_version: int
    run_id: str
    expert_id: str
    method_version: str
    method_digest: str
    model: str
    route_id: str | None
    resource_refs: tuple[ResourceRef, ...]
    split: Literal['development', 'holdout']
    dimension: Literal['expert_choice', 'model_choice', 'handoff', 'final_delivery', 'checker']
    input_digest: str
    output_digest: str
    label: str = Field(min_length=1)
    original_assessment: Literal['correct', 'incorrect']
    attribution: str
    acceptance_number: int | None = None


class Trial(Value):
    id: str
    experiment_id: str
    case_id: str
    result_id: str
    arm: Literal['before', 'after', 'fixed', 'rule', 'routellm', 'history', 'routellm_history']
    score: Literal['passed', 'failed']
    reviewer: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_basis: Literal['measured', 'estimated', 'unknown'] = 'unknown'
    cost_evidence: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    history_case_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def cost_consistent(self):
        if (self.cost_usd is None) != (self.cost_basis == 'unknown'):
            raise ValueError('Cost amount and basis must agree')
        if self.cost_basis == 'measured' and not self.cost_evidence:
            raise ValueError('Measured total cost needs billing evidence')
        return self


class Experiment(Value):
    id: str
    research_id: str
    split: Literal['development', 'holdout']
    dimension: Literal['expert_choice', 'model_choice', 'handoff', 'final_delivery', 'checker']
    case_ids: tuple[str, ...] = Field(min_length=1)
    arms: tuple[str, ...] = Field(min_length=2)
    candidate_pool: tuple[str, ...] = ()
    task_split_digest: str
    quality_floor: float = Field(ge=0, le=1)
    max_total_cost_usd: float = Field(gt=0)
    min_quality_gain: float = Field(default=0, ge=0, le=1)
    frozen_digest: str

    @model_validator(mode='after')
    def distinct(self):
        if len(set(self.case_ids)) != len(self.case_ids) or len(set(self.arms)) != len(self.arms):
            raise ValueError('Duplicate case or arm')
        if set(self.arms) not in ({'before', 'after'}, {'fixed', 'rule', 'routellm', 'history', 'routellm_history'}):
            raise ValueError('Unsupported experiment arms')
        if len(self.arms) == 5 and not self.candidate_pool:
            raise ValueError('Route experiment needs frozen candidate pool')
        if len(self.arms) == 5 and self.dimension != 'model_choice':
            raise ValueError('RouteLLM five-arm experiment is only for model selection')
        return self
