"""Research values contain identities, never another mutable execution output."""
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value, RunLimits
from ..resources.snapshots import ResourceRef, identity
from ..orchestration.models import DispatchPolicy
from ..evidence.models import ClaimDraft
from ..delivery_files import GeneratedFile


class ResearchAnswer(Value):
    files: tuple[GeneratedFile, ...] = Field(default=(), description='Complete named file deliveries requested by the user; content is saved atomically with this result.')
    report: str = Field(min_length=1, description='User-facing answer with conclusions and relevant supporting information.')
    blocking_missing: tuple[str, ...] = Field(default=(), description='ONLY unfinished mandatory deliverables that prevent this step from being used downstream. Empty when the requested report is delivered with allowed unknowns. Unknown measured cost/performance numbers, allowed uncertainties and source caveats belong to limitations, NEVER here. Nonempty means partial and stops the plan.')
    limitations: tuple[str, ...] = Field(default=(), description='Nonblocking research limitations, unavailable measurements, and source caveats explicitly allowed by the deliverable. Preserve these even when all required deliverables are complete.')
    claims: tuple[ClaimDraft, ...] = Field(default=(), description='Source-backed factual claims with citations or evidence gaps; empty for tasks without source claims.')

    @model_validator(mode='after')
    def valid_files(self):
        from ..delivery_files import files_from_output
        files_from_output(self.model_dump(mode='json'))
        return self


class Research(Value):
    id: str = Field(default_factory=identity)
    owner: str
    goal: str = Field(min_length=1)
    scope_id: str
    plans: tuple['ResearchPlan', ...] = ()


class ResearchNode(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    display_parent_id: str | None = None
    plan_proposal_id: str | None = None
    goal: str
    allowed_tools: tuple[Literal['read_source','search_source','search_web','fetch_web'], ...] = ('read_source','search_source')
    instructions: str = ''
    instruction_version: int = 1
    scope_id: str
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    selection: Literal['selected', 'excluded'] = 'selected'
    revision: int = 0
    active_run_id: str | None = None
    current_result_id: str | None = None
    position: tuple[float, float] = (0, 0)


class ChatMessage(Value):
    id: str = Field(default_factory=identity)
    role: Literal['user', 'assistant', 'expert', 'transfer']
    content: str = Field(min_length=1)
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    sender: Literal['user', 'manager', 'expert'] | None = None
    sender_session_id: str | None = None
    source_session_id: str | None = None
    reply_to: str | None = None
    task_id: str | None = None
    spec_version: int | None = None
    run_id: str | None = None


class ChatThread(Value):
    node_id: str
    research_id: str
    revision: int = 0
    fork_message_id: str | None = None
    fork_source_revision: int | None = None
    inherited: tuple[ChatMessage, ...] = ()
    messages: tuple[ChatMessage, ...] = ()


class ChatTransfer(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    source_node_id: str
    target_node_id: str
    source_message_id: str
    target_revision: int
    target_node_revision: int
    content: str
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    status: Literal['for_discussion', 'adopted'] = 'for_discussion'


class NodeCommand(Value):
    command_id: str = Field(min_length=1)
    research_id: str
    kind: Literal['create', 'branch', 'select', 'exclude', 'move', 'mount', 'run', 'continue', 'retry', 'compare', 'adopt', 'chat_reply', 'chat_followup']
    node_id: str | None = None
    display_parent_id: str | None = None
    expected_revision: int | None = None
    chat_revision: int | None = None
    goal: str = ''
    allowed_tools: tuple[Literal['read_source','search_source','search_web','fetch_web'], ...] = ('read_source','search_source')
    instructions: str = ''
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    position: tuple[float, float] = (0, 0)
    limits: RunLimits = Field(default_factory=RunLimits)
    evidence_required: bool = False


class CommandRecord(Value):
    command: NodeCommand
    node_id: str
    run_id: str | None = None
    status: Literal['accepted', 'completed', 'blocked', 'interrupted'] = 'accepted'
    reason: str | None = None


class NodeRun(Value):
    run_id: str
    node_id: str
    command_id: str
    node_revision: int
    context_snapshot_id: str
    goal: str
    instructions: str
    instruction_version: int
    resource_refs: tuple[ResourceRef, ...]
    result_ids: tuple[str, ...]
    missing: tuple[str, ...] = ()
    limits: RunLimits
    chat_revision: int | None = None
    notification_ids: tuple[str, ...] = ()
    evidence_required: bool = False


class NodeResultVersion(Value):
    id: str
    node_id: str
    version: int
    run_id: str
    context_snapshot_id: str
    instruction_version: int
    completion: Literal['complete', 'partial']
    missing: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()


class NodeResultDependency(Value):
    result_id: str
    input_result_id: str


class PlanStep(Value):
    node_id: str
    depends_on: tuple[str, ...] = ()
    limits: RunLimits = Field(default_factory=RunLimits)


class ResearchPlan(Value):
    id: str = Field(default_factory=identity)
    research_id: str
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    policy: DispatchPolicy = Field(default_factory=DispatchPolicy)

    @model_validator(mode='after')
    def acyclic(self):
        graph = {s.node_id: set(s.depends_on) for s in self.steps}
        if len(graph) != len(self.steps) or any(not v <= graph.keys() for v in graph.values()):
            raise ValueError('Plan needs unique nodes and known dependencies')
        done = set()
        while len(done) < len(graph):
            ready = {k for k, deps in graph.items() if k not in done and deps <= done}
            if not ready:
                raise ValueError('Plan cycle')
            done |= ready
        return self


Research.model_rebuild()
