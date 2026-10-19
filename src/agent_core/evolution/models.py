"""Stage 10 candidate and frozen preview evidence."""
from typing import Literal
from pydantic import Field
from ..contracts import Value


class CandidateDraft(Value):
    instructions: str = Field(min_length=1)
    examples: tuple[str, ...] = ()


class Candidate(Value):
    id: str
    research_id: str
    expert_id: str
    base_version: str
    version: str
    development_case_ids: tuple[str, ...] = Field(min_length=1)
    holdout_case_ids: tuple[str, ...] = Field(min_length=1)
    lesson: str = Field(min_length=1)
    lesson_reviewer: str = Field(min_length=1)
    generator_prompt: str
    generator_model: str
    generator_usage: dict
    quality_floor: float = Field(ge=0, le=1)
    min_quality_gain: float = Field(ge=0, le=1)
    max_total_cost_usd: float = Field(gt=0)
    asset_digest: str


class Preview(Value):
    id: str
    candidate_id: str
    case_id: str
    arm: Literal['before','after','downstream']
    method_version: str
    method_digest: str
    input_digest: str
    upstream_preview_id: str | None = None
    output: dict
    output_digest: str
    usage: dict
    program_passed: bool
    contract_passed: bool


class Assessment(Value):
    preview_id: str
    passed: bool
    scope_passed: bool
    reviewer: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    cost_usd: float = Field(ge=0)
    cost_basis: Literal['measured','estimated']
    cost_evidence: str = Field(min_length=1)
