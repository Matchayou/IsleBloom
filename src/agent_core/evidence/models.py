"""Claims point to located fragments of existing immutable resource versions."""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value
from ..resources.snapshots import ResourceRef


class SourceFields(Value):
    company: str | None = None
    period: str | None = None
    currency: str | None = None
    unit: str | None = None
    basis: str | None = None


class CitationDraft(Value):
    ref: ResourceRef = Field(description=(
        'Copy the complete immutable ResourceRef returned by a source tool, with its resource_id, snapshot_id and parse_id unchanged. '
        'Context source_id, artifact_id and summary_artifact identify working memory, not source references; never use them in these fields.'))
    location: str = Field(min_length=1, description='Exact fragment location returned by the source tool.')
    quote: str = Field(min_length=1, description='Verbatim excerpt from that located source fragment.')
    relation: Literal['supports', 'opposes']
    reasoning: str = Field(min_length=1)
    fields: SourceFields = Field(default_factory=SourceFields, description=(
        'Optional financial-report metadata. For general web facts, omit this object or leave every field null. '
        'For financial comparisons, values must occur verbatim in the source, without translating or normalizing dates or units. '
        'period means the financial reporting period, not the current date or web publication date.'))


class ClaimDraft(Value):
    statement: str = Field(min_length=1)
    citations: tuple[CitationDraft, ...] = ()
    missing: tuple[str, ...] = Field(default=(), description=(
        'Explicit gaps in document evidence for this claim. For a statement supported only by conversation history, '
        'identify that historical origin and the missing source evidence here; leave citations empty rather than inventing a ResourceRef.'))

    @model_validator(mode='after')
    def needs_evidence_or_gap(self):
        if not self.citations and not self.missing:
            raise ValueError('Claim needs a citation or an explicit evidence gap')
        return self


class Evidence(Value):
    id: str
    result_id: str
    ref: ResourceRef
    location: str
    quote: str
    fields: SourceFields


class Claim(Value):
    id: str
    result_id: str
    statement: str


class ClaimEvidence(Value):
    id: str
    result_id: str
    claim_id: str
    evidence_id: str | None = None
    relation: Literal['supports', 'opposes', 'missing']
    reasoning: str
    assessment: Literal['unreviewed', 'supported', 'unsupported', 'uncertain'] = 'unreviewed'
    review_reason: str | None = None


class EvidenceBundle(Value):
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    relations: tuple[ClaimEvidence, ...] = ()
