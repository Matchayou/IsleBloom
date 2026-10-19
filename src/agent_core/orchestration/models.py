from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ..contracts import PreparedInput, RunLimits, Value


class DispatchPolicy(Value):
    max_concurrency: int = Field(default=3, ge=1, le=32)
    max_runs: int = Field(default=3, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float = Field(default=120, gt=0)
    completion: Literal['all_settled', 'fail_fast'] = 'all_settled'


class DispatchItem(Value):
    key: str = Field(min_length=1)
    agent_id: str
    input: PreparedInput
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    limits: RunLimits = Field(default_factory=RunLimits)


class DispatchPlan(Value):
    group_id: str = Field(default_factory=lambda: str(uuid4()))
    items: tuple[DispatchItem, ...] = Field(min_length=1, max_length=100)
    policy: DispatchPolicy = Field(default_factory=DispatchPolicy)
    depth: int = Field(default=0, ge=0, le=0)

    @model_validator(mode='after')
    def unique_bindings(self):
        if len({i.key for i in self.items}) != len(self.items) or len({i.run_id for i in self.items}) != len(self.items):
            raise ValueError('Task keys and child run IDs must be unique')
        if self.group_id in {i.run_id for i in self.items}:
            raise ValueError('Group ID cannot be a child run ID')
        return self


class DispatchGroup(Value):
    plan: DispatchPlan
    status: Literal['running', 'settled', 'cancelled', 'budget_stopped', 'timed_out', 'failed_fast', 'interrupted'] = 'running'
    # Explicit reasons also cover items never admitted to the execution application.
    reasons: dict[str, str] = Field(default_factory=dict)


class RouteDecision(Value):
    mode: Literal['direct', 'compare']
    reason: str
    plan: DispatchPlan | None = None
