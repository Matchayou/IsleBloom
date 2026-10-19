"""Immutable domain learning evidence, isolated drafts, and release seals."""
from typing import Literal
from pydantic import Field
from ..contracts import Value, RunRequest
from ..resources.snapshots import ResourceRef


class Trajectory(Value):
    id: str  # completed specialist run id
    owner: str
    expert_id: str
    research_id: str
    task_id: str
    spec_version: int
    method_version: str
    method_digest: str
    original_user: str
    goal: str
    snapshot_id: str
    input_digest: str
    resource_refs: tuple[ResourceRef, ...]
    outcome: dict
    tool_events: tuple[dict, ...]


class SkillDraft(Value):
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1, max_length=8000)
    allowed_tools: tuple[str, ...] = ()


class LearningDraft(Value):
    memory: str | None = Field(default=None, min_length=1, max_length=2000, description='Brief reusable decision rule for this specialist domain. No case facts or personal preferences. Null when no rule is supported.')
    skill: SkillDraft | None = Field(default=None, description='Repeatable procedure supported by the performed workflow, distinct from the brief memory rule. Include when the trace demonstrates reusable steps; null when no procedure is supported. Uses the same published specialist tool contract.')


class DomainCandidate(Value):
    id: str
    trajectory_id: str
    owner: str
    expert_id: str
    draft: LearningDraft
    digest: str
    generation_run_id: str


class ReviewOutput(Value):
    grounded: bool
    generalizable: bool
    privacy_safe: bool
    permission_safe: bool
    evidence_quotes: tuple[str, ...] = Field(min_length=1, max_length=5, description='Each quote must be a contiguous verbatim substring of one original string field in trajectory. No ellipses, paraphrases, joined excerpts, JSON serialization, or invented summaries of structured tool events.')
    reason: str = Field(min_length=1, max_length=2000)


class DomainValidation(Value):
    id: str
    candidate_digest: str
    source_digest: str
    passed: bool
    reason: str
    review: ReviewOutput | None = None
    run_id: str | None = None


class DomainPublication(Value):
    id: str
    candidate_digest: str
    validation_digest: str
    memory_id: str | None = None
    skill_name: str | None = None


class DomainCall(Value):
    id: str
    stage: Literal['generate', 'verify']
    model: str
    request: RunRequest


class DomainFailure(Value):
    id: str
    reason: str


class DomainRevocation(Value):
    id: str
    reason: str
