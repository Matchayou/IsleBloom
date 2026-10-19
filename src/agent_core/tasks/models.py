from .artifacts import ReportArtifact
from ..coding.models import CodingContract, CodeCandidate
"""Frozen expert contracts and immutable delivery records."""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import RunLimits, Value
from ..resources.snapshots import ResourceRef, identity
from ..research.models import ChatMessage
from ..evidence.models import ClaimDraft
from ..memories.models import MemoryRef
from ..agent_identity import AgentIdentity


class AcceptanceItem(Value):
    number: int = Field(ge=1)
    requirement: str
    blocking: bool = True


class TaskSpec(Value):
    id: str = Field(default_factory=identity)
    version: int = Field(default=1, ge=1)
    research_id: str
    expert_session_id: str | None = None
    context_packet_id: str | None = None
    node_id: str | None = None
    node_revision: int | None = None
    chat_context: tuple[ChatMessage, ...] = ()
    parent_task_id: str | None = None
    user_request: str
    goal: str
    kind: Literal['search', 'extract', 'analyze', 'synthesize', 'coding']
    expert_id: str
    agent: AgentIdentity
    method_version: str
    method_digest: str
    model: str
    routing_version: str | None = None
    route_id: str | None = None
    required_refs: tuple[ResourceRef, ...] = ()
    optional_refs: tuple[ResourceRef, ...] = ()
    authorized_refs: tuple[ResourceRef, ...] = ()
    required_results: tuple[str, ...] = ()
    pending_dependency_task_ids: tuple[str, ...] = ()
    memory_refs: tuple[MemoryRef, ...] = ()
    allow_partial_results: bool = False
    required_fields: tuple[str, ...] = ()
    acceptance: tuple[AcceptanceItem, ...]
    limits: RunLimits = Field(default_factory=RunLimits)
    evidence_required: bool = False
    coding: CodingContract | None = None

    @model_validator(mode='after')
    def valid_contract(self):
        if self.agent.mode == 'assistant': raise ValueError('An assistant is not a task agent')
        expected_id = self.expert_id if self.agent.mode == 'specialist' else 'temporary:' + self.id
        if self.agent.id != expected_id: raise ValueError('Agent identity does not match its task or specialist')
        if self.agent.mode == 'specialist' and self.agent.instructions:
            raise ValueError('Specialist instructions must come from the method version')
        if (self.kind == 'coding') != (self.coding is not None):
            raise ValueError('Coding tasks require exactly one admitted code contract')
        if self.coding and self.evidence_required:
            raise ValueError('Code snapshots are not research evidence')
        if len({i.number for i in self.acceptance}) != len(self.acceptance):
            raise ValueError('Acceptance numbers must be unique')
        if set(self.required_refs) & set(self.optional_refs):
            raise ValueError('Input cannot be both required and optional')
        if len(set(self.required_refs)) != len(self.required_refs) or len(set(self.optional_refs)) != len(self.optional_refs):
            raise ValueError('Duplicate resource input')
        if len(set(self.required_results)) != len(self.required_results):
            raise ValueError('Duplicate result input')
        if not set((*self.required_refs, *self.optional_refs)) <= set(self.authorized_refs):
            raise ValueError('Task input must belong to its authorized source directory')
        if len(set(self.authorized_refs)) != len(self.authorized_refs): raise ValueError('Duplicate authorized source')
        return self


class TaskRun(Value):
    id: str = Field(default_factory=identity)
    task_id: str
    agent: AgentIdentity
    spec_version: int
    context_snapshot_id: str
    scope_id: str
    scope_version: int
    model: str
    route_id: str | None = None
    method_version: str
    method_digest: str
    result_ids: tuple[str, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    missing_optional_refs: tuple[ResourceRef, ...] = ()
    research_id: str
    node_id: str | None = None
    evidence_required: bool = False


class ExpertAnswer(Value):
    report: str = Field(min_length=1, description='User-facing answer with conclusions and relevant supporting information.')
    fields: dict[str, str] = Field(default_factory=dict, description='Extracted values keyed by the task contract required_fields.')
    sources: dict[str, str] = Field(default_factory=dict, description='Source file and location keyed by the corresponding extracted field.')
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    self_checks: dict[str, str] = Field(default_factory=dict)
    claims: tuple[ClaimDraft, ...] = Field(default=(), description='Source-backed factual claims with citations or evidence gaps; empty for tasks without source claims.')


class CodingAnswer(Value):
    report: str = Field(min_length=1, description='User-facing answer with conclusions and relevant supporting information.')
    candidate_ref: str = Field(description='Fixed candidate reference returned by freeze_code.')
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()


class CodingDelivery(CodingAnswer):
    candidate: CodeCandidate
    code_patch: str


class MarketAnswer(Value):
    report: str = Field(min_length=1, description='User-facing answer with conclusions and relevant supporting information.')
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    claims: tuple[ClaimDraft, ...] = Field(default=(), description='Source-backed factual claims with citations or evidence gaps; empty for tasks without source claims.')


class CheckRecord(Value):
    number: int
    checker: Literal['program', 'semantic']
    status: Literal['passed', 'failed', 'pending']
    evidence: str
    reviewer: str | None = None


class TaskReceipt(Value):
    code_candidate: CodeCandidate | None = None
    artifact: ReportArtifact | None = None
    result_id: str
    task_id: str
    spec_version: int
    run_id: str
    context_snapshot_id: str
    model: str
    route_id: str | None = None
    method_version: str
    method_digest: str
    result_ids: tuple[str, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    missing_optional_refs: tuple[ResourceRef, ...] = ()
    program_checks: tuple[CheckRecord, ...]
    semantic_checks: tuple[CheckRecord, ...]
    assumptions: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    delivery: Literal['pending_review', 'partial_pending_review', 'passed', 'partial', 'rework', 'rejected']
    adoption: Literal['for_discussion', 'adopted'] = 'for_discussion'
    adoption_reason: str | None = None
