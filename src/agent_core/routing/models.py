"""Frozen, auditable choices. Catalog entries are operator controlled data."""
from typing import Literal
from pydantic import Field
from ..contracts import Value
from ..resources.snapshots import identity
from ..resources.snapshots import ResourceRef


class ExpertCandidate(Value):
    id: str
    kind: Literal['search', 'extract', 'analyze', 'synthesize', 'coding']
    description: str
    tools: frozenset[str] = frozenset()
    method_version: str
    method_digest: str


class ModelCandidate(Value):
    id: str
    description: str
    available: bool = True
    tools: frozenset[str] = frozenset()
    supported_kinds: frozenset[str]
    capability_rank: int = Field(ge=0)
    max_input_chars: int = Field(gt=0)
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)
    capability_source: str = Field(min_length=1)
    price_source: str = Field(min_length=1)


class RouteStep(Value):
    stage: Literal['expert', 'model']
    eligible: tuple[str, ...]
    excluded: dict[str, str]
    state: dict = Field(default_factory=dict)
    criteria: dict[str, str] = Field(default_factory=dict)
    source: Literal['user', 'policy', 'only_candidate', 'routellm', 'blocked']
    selected: str | None = None
    raw: dict | None = None
    elapsed_ms: int = 0
    usage: dict = Field(default_factory=dict)


class RouteRecord(Value):
    id: str = Field(default_factory=identity)
    task_id: str
    spec_version: int
    research_id: str
    status: Literal['selected', 'blocked']
    reason: str | None = None
    trigger: str | None = None
    summary: str
    policy_version: str
    catalog_version: str
    router_version: str
    router_checkpoint: str | None = None
    router_threshold: float | None = None
    input_chars: int
    required_tools: tuple[str, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    budget_usd: float | None = None
    spent_usd: float = 0
    estimated_execution_usd: float | None = None
    steps: tuple[RouteStep, ...]
    selected_expert: str | None = None
    selected_model: str | None = None
    expert_config: ExpertCandidate | None = None
    selected_model_config: ModelCandidate | None = None
