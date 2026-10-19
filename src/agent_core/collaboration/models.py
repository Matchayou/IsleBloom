"""Immutable handoff identity and durable dispatch intent."""
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Usage, Value, RunLimits
from ..research.models import ChatMessage
from ..resources.snapshots import ResourceRef, identity
from ..agent_identity import AgentIdentity


class ExpertSession(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    expert_id: str
    agent: AgentIdentity
    method_version: str
    revision: int = 0
    messages: tuple[ChatMessage, ...] = ()


class ExpertMessageRef(Value):
    session_id: str
    message_id: str


class ContextPacket(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    sender_node_id: str
    sender_chat_revision: int
    user_message_id: str
    user_instruction: str
    agent: AgentIdentity
    authorized_refs: tuple[ResourceRef, ...] = ()
    project: dict
    limits: RunLimits
    manager_instruction: str
    selected_messages: tuple[ChatMessage, ...] = ()
    expert_message_refs: tuple[ExpertMessageRef, ...] = ()
    selected_results: tuple[dict, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    collaborator_session_ids: tuple[str, ...] = ()
    delivery_requirements: str

    @model_validator(mode='after')
    def admitted_handoff(self):
        if self.agent.mode == 'assistant': raise ValueError('Handoff requires a child agent identity')
        if not set(self.resource_refs) <= set(self.authorized_refs):
            raise ValueError('Handoff inputs exceed its authorized directory')
        return self


class DispatchIntent(Value):
    command_id: str
    fingerprint: str
    research_id: str
    session_id: str
    task_id: str
    packet_id: str
    manager_run_id: str | None = None
    waiting_for: tuple[tuple[str, int], ...] = ()
    resume_attempt: int = 0
    resume_status: Literal['pending', 'complete', 'deferred'] = 'pending'
    resume_error: str | None = None
    status: Literal['waiting', 'accepted', 'queued', 'completed', 'failed'] = 'accepted'
    error: str | None = None


class AgentMessage(Value):
    id: str
    fingerprint: str
    research_id: str
    task_id: str
    session_id: str
    spec_version: int
    sender: Literal['manager', 'expert', 'user']
    sender_session_id: str | None = None
    sender_task_id: str | None = None
    sender_run_id: str | None = None
    kind: Literal['question', 'information']
    content: str
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    reply_to: str | None = None
    requires_reply: bool = False
    attempts: int = 0
    status: Literal['queued', 'delivered', 'consumed', 'failed'] = 'queued'
    consumer_turn_id: str | None = None
    response_id: str | None = None
    error: str | None = None


class TaskRevision(Value):
    id: str
    fingerprint: str
    research_id: str
    task_id: str
    expected_version: int
    request: str
    mode: Literal['save', 'after_current', 'replace']
    status: Literal['queued', 'saved', 'running', 'complete', 'failed'] = 'queued'
    new_version: int | None = None
    run_id: str | None = None
    cancellation_id: str | None = None
    error: str | None = None


class TaskCancellation(Value):
    id: str
    fingerprint: str
    research_id: str
    task_id: str
    spec_version: int
    reason: str
    include_children: bool = False
    status: Literal['requested', 'stopping', 'cancelled', 'completed', 'failed'] = 'requested'
    error: str | None = None


class TaskDependency(Value):
    id: str
    research_id: str
    downstream_task_id: str
    downstream_version: int
    upstream_task_id: str
    upstream_version: int
    result_id: str | None = None


class ReviewIssue(Value):
    problem: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    tool_call_ids: tuple[str, ...] = Field(default=(), max_length=8,
        description='Related executor tool call IDs copied from review_execution. Empty for report-only issues. Never invent an ID or reference a reviewer call.')

    @model_validator(mode='after')
    def unique_calls(self):
        if len(set(self.tool_call_ids)) != len(self.tool_call_ids):
            raise ValueError('Duplicate issue tool call')
        return self


class ReadExecutionTool(Value):
    call_id: str = Field(min_length=1)


class ReviewAdvice(Value):
    verdict: Literal['passed', 'needs_revision']
    summary: str = Field(min_length=1)
    issues: tuple[ReviewIssue, ...] = Field(default=(), max_length=20)

    @model_validator(mode='after')
    def consistent_verdict(self):
        if (self.verdict == 'passed') != (not self.issues):
            raise ValueError('Passing advice has no issues; revision advice requires concrete issues')
        return self


class ReviewRound(Value):
    spec_version: int
    result_id: str
    artifact_sha256: str
    message_id: str
    review_run_id: str
    advice: ReviewAdvice
    revision_id: str | None = None


class ReviewLoop(Value):
    id: str  # One automatic cycle per executor task.
    research_id: str
    task_id: str
    reviewer_task_id: str
    original_request: str
    current_version: int
    revision: int = 0
    revisions_completed: int = Field(default=0, ge=0, le=2)
    status: Literal['executing', 'reviewing', 'revising', 'passed', 'exhausted', 'failed', 'cancelled', 'blocked'] = 'executing'
    rounds: tuple[ReviewRound, ...] = ()
    final_result_id: str | None = None
    reason: str | None = None


class ManagerNotice(Value):
    id: str
    research_id: str
    node_id: str
    task_id: str
    kind: Literal['expert_discussion', 'task_revision', 'task_revision_result', 'task_cancellation', 'information_request']
    content: str
    status: Literal['pending', 'complete', 'deferred'] = 'pending'
    resume_attempt: int = 0
    error: str | None = None


class InformationAnswer(Value):
    available: bool
    answer: str = Field(min_length=1, max_length=10000)
    missing: tuple[str, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    message_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()


class ReportGrant(Value):
    result_id: str
    task_id: str
    spec_version: int
    run_id: str


class InformationRequest(Value):
    id: str
    research_id: str
    task_id: str
    spec_version: int
    run_id: str
    question: str
    progress: str
    resource_refs: tuple[ResourceRef, ...] = ()
    requested_result_ids: tuple[str, ...] = ()
    report_grants: tuple[ReportGrant, ...] = ()
    status: Literal['waiting', 'resolved'] = 'waiting'
    attempts: int = 0
    answer: InformationAnswer | None = None
    usage: Usage = Field(default_factory=Usage)
