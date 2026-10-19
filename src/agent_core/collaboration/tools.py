"""Manager tools with caller identity taken from the active research run."""
import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import ToolBinding, ToolResult, Value
from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from ..research.chat import ChatService
from ..resources.snapshots import ResourceRef
from .models import ExpertMessageRef


class FindExperts(Value):
    query: str = Field(default='', max_length=200, description='Literal text filter on expert ID and description. Empty lists capabilities without a text filter.')
    kind: Literal['search', 'extract', 'analyze', 'synthesize', 'coding'] | None = None
    required_tools: tuple[str, ...] = ()
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=10)


class ListContext(Value):
    category: Literal['sources', 'results', 'messages', 'expert_messages']
    task_id: str | None = None
    session_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8, ge=1, le=20)


class SearchContext(ListContext):
    query: str = Field(min_length=2, max_length=200)


class SelectTaskTools(Value):
    profile: Literal['sources', 'experts', 'manage', 'reports']



class ReadContext(Value):
    message_ids: tuple[str, ...] = Field(default=(), max_length=5)
    expert_message_refs: tuple[ExpertMessageRef, ...] = Field(default=(), max_length=5)
    resource_refs: tuple[ResourceRef, ...] = Field(default=(), max_length=5)
    result_ids: tuple[str, ...] = Field(default=(), max_length=5)


class DelegateTask(Value):
    agent_mode: Literal['temporary', 'specialist'] = Field(default='specialist', description='Specialist uses a published fixed role; temporary uses a task-specific role and fresh identity.')
    role_instructions: str = Field(default='', max_length=6000, description='Required only for temporary agents. Describe this one task role; cannot grant tools or filesystem access.')
    authorized_refs: tuple[ResourceRef, ...] = Field(default=(), description='Additional currently permitted source versions the child may search/read autonomously; not mandatory body inputs.')
    coding_contract_id: str | None = None
    expert_id: str | None = Field(default=None, description='Published capability template ID for a new agent. Required for temporary agents too: agent_mode controls identity, expert_id selects the execution method. Choose exactly one expert_id or session_id.')
    session_id: str | None = Field(default=None, description='Existing specialist session to continue. Mutually exclusive with expert_id; must be null for temporary agents.')
    task_instruction: str = Field(min_length=1, max_length=10000)
    delivery_requirements: str = Field(min_length=1, max_length=3000)
    message_ids: tuple[str, ...] = ()
    expert_message_refs: tuple[ExpertMessageRef, ...] = ()
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    upstream_task_ids: tuple[str, ...] = ()
    collaborator_session_ids: tuple[str, ...] = ()

    @model_validator(mode='after')
    def one_target(self):
        if (self.expert_id is None) == (self.session_id is None):
            raise ValueError('Choose exactly one expert_id or session_id')
        if self.agent_mode == 'temporary' and (not self.role_instructions.strip() or self.session_id):
            raise ValueError('Temporary delegation requires a role and a method template expert_id')
        if self.agent_mode == 'specialist' and self.role_instructions:
            raise ValueError('Specialist role instructions are fixed by the published method')
        return self


class GetTaskStatus(Value):
    task_id: str


class AdoptResult(Value):
    result_id: str
    reason: str = Field(min_length=1, max_length=3000)


class SendAgentMessage(Value):
    task_id: str
    session_id: str
    kind: Literal['question', 'information']
    content: str = Field(min_length=1, max_length=10000)
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()
    reply_to: str | None = None
    requires_reply: bool = False


class ReviseTask(Value):
    task_id: str
    expected_version: int
    request: str = Field(min_length=1, max_length=10000)
    mode: Literal['save', 'after_current', 'replace']


class CancelTask(Value):
    task_id: str
    expected_version: int
    reason: str = Field(min_length=1, max_length=1000)
    include_children: bool = False


class AskCollaborator(Value):
    task_id: str
    session_id: str
    question: str = Field(min_length=1, max_length=10000)


class DelegateCollaborator(Value):
    session_id: str
    task_instruction: str = Field(min_length=1, max_length=10000)
    delivery_requirements: str = Field(min_length=1, max_length=3000)
    resource_refs: tuple[ResourceRef, ...] = ()
    result_ids: tuple[str, ...] = ()


def expert_bindings(collaboration, spec, binding):
    if not spec.context_packet_id: return ()
    information = (collaboration.information.binding(spec, binding),
                   collaboration.information.history.expert_binding(spec, binding))
    packet = collaboration.packet(spec.context_packet_id)
    if not packet.collaborator_session_ids: return information
    calls = 0

    async def ask(args):
        nonlocal calls
        if collaboration.research.scopes.running.get() != binding.id:
            raise ScopeDenied('Collaborator tool belongs to another expert run')
        collaboration.tasks._validate_durable_access(spec, binding)
        if args.session_id not in collaboration.packet(spec.context_packet_id).collaborator_session_ids:
            raise ScopeDenied('Collaborator is outside the fixed handoff')
        if calls >= 1: raise Conflict('One collaborator question is allowed per expert run')
        calls += 1
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'expert-message:' + hashlib.sha256((binding.id + ':' + raw).encode()).hexdigest()
        message = collaboration.send_agent_message(command_id=command_id,
            task_id=args.task_id, session_id=args.session_id, kind='question',
            content=args.question, requires_reply=True, sender='expert',
            sender_session_id=spec.expert_session_id, sender_task_id=spec.id,
            sender_run_id=binding.id)
        delivered = await collaboration.execute_message(message.id)
        if delivered.status != 'consumed' or not delivered.response_id:
            raise Conflict(delivered.error or 'Collaborator did not reply')
        thread = collaboration.query('session', args.session_id)
        answer = next((m.content for m in thread.messages if m.id == delivered.response_id), None)
        if answer is None: raise Conflict('Collaborator reply missing')
        collaboration.tasks._validate_durable_access(spec, binding)
        return ToolResult(value={'message_id': message.id, 'response_id': delivered.response_id,
            'answer': answer})

    async def delegate(args):
        if collaboration.research.scopes.running.get() != binding.id:
            raise ScopeDenied('Collaborator tool belongs to another expert run')
        collaboration.tasks._validate_durable_access(spec, binding)
        packet = collaboration.packet(spec.context_packet_id)
        if args.session_id not in packet.collaborator_session_ids:
            raise ScopeDenied('Collaborator is outside the fixed handoff')
        if not set(args.resource_refs) <= set(spec.required_refs) or not set(args.result_ids) <= set(spec.required_results):
            raise ScopeDenied('Child task input exceeds the parent handoff')
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'expert-child:' + hashlib.sha256((binding.id + ':' + raw).encode()).hexdigest()
        intent = collaboration.delegate_task(command_id=command_id, node_id=packet.sender_node_id,
            session_id=args.session_id, task_instruction=args.task_instruction,
            delivery_requirements=args.delivery_requirements,
            resource_refs=args.resource_refs, result_ids=args.result_ids,
            parent_task_id=spec.id, parent_run_id=binding.id)
        return ToolResult(value={'task_id': intent.task_id, 'status': intent.status,
            'parent_task_id': spec.id, 'instruction': 'Child task accepted for background execution.'})

    return information + (ToolBinding('ask_collaborator',
        'Ask one question of a manager-approved expert session in this main task. The other expert discusses its own fixed task and cannot automatically ask back.',
        AskCollaborator, ask),
        ToolBinding('delegate_collaborator',
        'Assign a bounded background child task to a manager-approved expert session. Inputs must be a subset of this fixed handoff; the child result returns to the main conversation.',
        DelegateCollaborator, delegate))


def manager_bindings(research, scope):
    collaboration = research.collaboration
    def current():
        research.scopes.valid(scope)
        binding = research.query('run', research.scopes.running.get())
        research.validate_binding(binding)
        node = research.node(binding.node_id)
        if node.scope_id != scope.id or node.display_parent_id is not None:
            raise ScopeDenied('Collaboration tools require the main conversation')
        return node

    async def find(args):
        node = current()
        rows = collaboration.find_experts(node.research_id, **args.model_dump())
        return ToolResult(value={'experts': rows, 'offset': args.offset,
            'next_offset': args.offset + args.limit if len(rows) == args.limit else None,
            'instruction': 'Query is a literal filter; use kind or required_tools for capabilities. No matches does not mean no experts exist.'})

    async def read(args):
        node = current()
        if not (args.message_ids or args.expert_message_refs or args.resource_refs or args.result_ids):
            return ToolResult(value={'status': 'invalid_input', 'executed': False,
                'instruction': 'Select explicit refs to read. Use search_context with a category and query, or list_context for an explicitly requested bounded directory. Status questions use get_task_status only.'})
        value = collaboration.read_context(node.id, args.message_ids, args.resource_refs,
            args.result_ids, args.expert_message_refs)
        if args.resource_refs:
            root = research.research(node.research_id)
            root_scope = research.scopes.current(root.scope_id, research.owner)
            run_id = research.scopes.running.get()
            for ref in args.resource_refs:
                research.scopes.acquire_library(scope, root_scope, ref)
                collaboration.record_source_read(run_id, ref)
            current()
        return ToolResult(value=json.loads(json.dumps(value, default=lambda o: o.model_dump(mode='json'), ensure_ascii=False)))

    async def delegate(args):
        node = current()
        chat = ChatService(research, collaboration.tasks).read(node.id)
        original = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
        if original is None: raise ScopeDenied('No user instruction available')
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'manager:' + hashlib.sha256((node.id + ':' + original.id + ':' + raw).encode()).hexdigest()
        intent = collaboration.delegate_task(command_id=command_id, node_id=node.id,
            expert_id=args.expert_id, session_id=args.session_id,
            task_instruction=args.task_instruction,
            delivery_requirements=args.delivery_requirements, message_ids=args.message_ids,
            expert_message_refs=args.expert_message_refs,
            resource_refs=args.resource_refs, result_ids=args.result_ids,
            upstream_task_ids=args.upstream_task_ids,
            collaborator_session_ids=args.collaborator_session_ids,
            manager_run_id=research.scopes.running.get(), coding_contract_id=args.coding_contract_id,
            agent_mode=args.agent_mode, role_instructions=args.role_instructions, authorized_refs=args.authorized_refs)
        return ToolResult(value={'task_id': intent.task_id, 'session_id': intent.session_id,
            'agent': collaboration.tasks.spec(intent.task_id).agent.model_dump(),
            'context_packet_id': intent.packet_id, 'status': intent.status,
            'waiting_for': list(intent.waiting_for),
            'instruction': 'Task accepted for background execution. Dispatch the other requested experts, then tell the user work is pending and end this turn. Do not poll or claim completion. The manager resumes after all tasks from this turn finish. Waiting dependencies require passed review of each named upstream version.'})

    async def status(args):
        node = current()
        return ToolResult(value=collaboration.task_status(node.research_id, args.task_id))

    async def directory(args):
        node = current()
        return ToolResult(value=collaboration.context_directory(node.id, **args.model_dump()))

    async def select_tools(args):
        current()
        return ToolResult(value={'tool_profile': args.profile})

    async def adopt(args):
        node = current()
        receipt = collaboration.tasks.receipt(args.result_id)
        spec = collaboration.tasks.spec(receipt.task_id, receipt.spec_version)
        if spec.research_id != node.research_id: raise ScopeDenied('Foreign result')
        accepted = collaboration.tasks.adopt(args.result_id, args.reason)
        return ToolResult(value={'result_id': accepted.result_id,
            'delivery': accepted.delivery, 'adoption': accepted.adoption,
            'reason': accepted.adoption_reason})

    async def send_message(args):
        node = current()
        spec = collaboration.tasks.spec(args.task_id)
        if spec.research_id != node.research_id: raise ScopeDenied('Foreign task')
        chat = ChatService(research, collaboration.tasks).read(node.id)
        original = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
        if original is None: raise ScopeDenied('No user instruction available')
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'manager-message:' + hashlib.sha256((node.id + ':' + original.id + ':' + raw).encode()).hexdigest()
        message = collaboration.send_agent_message(command_id=command_id,
            task_id=args.task_id, session_id=args.session_id, content=args.content,
            kind=args.kind, requires_reply=args.requires_reply, reply_to=args.reply_to,
            resource_refs=args.resource_refs, result_ids=args.result_ids)
        delivered = await collaboration.execute_message(message.id)
        thread = collaboration.query('session', args.session_id)
        answer = next((m.content for m in thread.messages if m.id == delivered.response_id), None)
        return ToolResult(value={'message_id': delivered.id, 'status': delivered.status,
            'response_id': delivered.response_id, 'answer': answer, 'error': delivered.error})

    async def revise(args):
        node = current()
        spec = collaboration.tasks.spec(args.task_id)
        if spec.research_id != node.research_id: raise ScopeDenied('Foreign task')
        chat = ChatService(research, collaboration.tasks).read(node.id)
        original = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
        if original is None: raise ScopeDenied('No user instruction available')
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'manager-revision:' + hashlib.sha256((node.id + ':' + original.id + ':' + raw).encode()).hexdigest()
        revision = collaboration.revise_task(command_id=command_id,
            task_id=args.task_id, expected_version=args.expected_version,
            request=args.request, mode=args.mode)
        revision = await collaboration.process_revision(revision.id)
        return ToolResult(value={'task_id': revision.task_id, 'status': revision.status,
            'new_version': revision.new_version, 'run_id': revision.run_id,
            'cancellation_id': revision.cancellation_id,
            'error': revision.error})

    async def cancel(args):
        node = current()
        spec = collaboration.tasks.spec(args.task_id)
        if spec.research_id != node.research_id: raise ScopeDenied('Foreign task')
        chat = ChatService(research, collaboration.tasks).read(node.id)
        original = next((m for m in reversed(chat.messages) if m.role == 'user'), None)
        if original is None: raise ScopeDenied('No user instruction available')
        raw = json.dumps(args.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        command_id = 'manager-cancellation:' + hashlib.sha256((node.id + ':' + original.id + ':' + raw).encode()).hexdigest()
        request = collaboration.cancel_task(command_id=command_id, task_id=args.task_id,
            expected_version=args.expected_version, reason=args.reason,
            include_children=args.include_children)
        result = await collaboration.process_cancellation(request.id)
        return ToolResult(value={'task_id': result.task_id, 'spec_version': result.spec_version,
            'status': result.status, 'error': result.error})

    return (
        ToolBinding('select_task_tools', 'Load tools for sources (source lookup/web), experts (bounded background delegation), manage (revise/cancel/discuss/adopt), or reports (compare saved reports). Status or ordinary answers need no selection. Select only the workflow needed for the current request.', SelectTaskTools, select_tools),
        ToolBinding('search_context', 'Search one category of permitted metadata by literal query, optionally within a task/session. Bounded results only, no body search. Use exact refs to read selected bodies.', SearchContext, directory),
        ToolBinding('list_context', 'Browse one category of permitted metadata only when listing is needed. Returns a bounded page and next_offset. Do not traverse all pages unless explicitly requested.', ListContext, directory),
        ToolBinding('find_experts', 'Find available expert capabilities and existing sessions for this main task.', FindExperts, find),
        ToolBinding('read_context', 'Read selected messages, source bodies or saved expert reports with exact refs. Empty input is invalid. Reports are in results[].output with version and review status. Metadata is not a report. read_source only reads mounted sources.', ReadContext, read),
        ToolBinding('delegate_task', 'Accept one bounded background task for exactly one expert_id or existing session_id; returns acceptance, not a report. Dispatch all requested experts and end the turn; the manager resumes when all finish. Use for supplemental investigation with new searches. Reusing a session preserves earlier reports. Verify published web tools with find_experts. Supply unresolved questions and original sources; result_ids require passed independent review. Web experts may start without refs. upstream_task_ids waits for named upstream versions to pass review.', DelegateTask, delegate),
        ToolBinding('get_task_status', 'Read the latest persisted execution and delivery status without running a task.', GetTaskStatus, status),
        ToolBinding('adopt_result', 'Formally adopt a result for this main task only after independent review has passed; explain the reason.', AdoptResult, adopt),
        ToolBinding('send_agent_message', 'Discuss existing material with an assigned expert session. A question with requires_reply gets one answer but has NO search tools and does not start formal analysis. Use delegate_task for new data collection. After investigation, attach its resource_refs or result_ids here so the original expert can reassess; unreviewed results remain preliminary.', SendAgentMessage, send_message),
        ToolBinding('revise_task', 'Create a new immutable expert task version. Save only, run after the current run, or request cancellation and then replace it. Requires the current expected version.', ReviseTask, revise),
        ToolBinding('cancel_task', 'Persist a cancellation request for the current expert task version, then stop its workflow if running. Requires the current expected version.', CancelTask, cancel),
    )
