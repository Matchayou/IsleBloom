"""Post-commit human-message capture through the existing controlled write path."""
import json
import asyncio
import os
from ..contracts import AgentSpec, Message, RunRequest, RunLimits, RunResult, RunError
from ..agent_identity import AgentIdentity
from ..core import CoreRunner, AgentCatalog
from ..controlled.admission import admit
from ..controlled.ledger import digest
from ..context.assembler import project_context
from ..context.scopes import ScopeDenied
from ..context.memory_tools import write_memory_binding
from ..execution.ports import Conflict
from ..resources.snapshots import identity
from .capture_models import MemoryCapture, CaptureOutput, CAPTURE_INSTRUCTION

class MemoryCapturer:
    def __init__(self, memories):
        self.memories, self.research = memories, memories.research
        self.database, self.owner = memories.database, memories.owner
        self.lock = asyncio.Lock()

    def records(self):
        with self.database.transaction() as tx: return tx.memories.list_captures(self.owner)

    def record_in(self, tx, binding, snapshot):
        if binding.chat_revision is None or binding.command_id.startswith('resume:'): return
        node = tx.research.node(binding.node_id)
        chat = tx.research.chat(node.id)
        if not chat or node.plan_proposal_id: return
        packet = json.loads(snapshot.input.messages[0].content)
        if packet.get('external_context'):
            packet = json.loads(snapshot.input.external_context[packet['external_context']['artifact_id']])
        message_id = packet.get('current_message_id')
        message = next((m for m in (*chat.inherited, *chat.messages) if m.id == message_id and m.role == 'user'
            and m.content == snapshot.input.messages[-1].content), None)
        if message is None or message.sender not in (None, 'user'): return
        key = 'memory-capture:' + digest([self.owner, message.id])
        if tx.memories.capture(key): return
        member = tx.projects.member(node.research_id) if 'projects' in self.database.repositories else None
        tx.memories.save_capture(MemoryCapture(id=key, owner=self.owner, research_id=node.research_id,
            node_id=node.id, source_run_id=binding.run_id, message_id=message.id,
            content=message.content, project_id=member.project_id if member else None))

    def validate(self, job):
        if job.owner != self.owner: raise ScopeDenied('Foreign capture')
        self.research.research(job.research_id)
        node = self.research.node(job.node_id)
        if node.selection != 'selected': raise ScopeDenied('Capture source excluded')
        with self.database.transaction() as tx:
            run = tx.runs.get(job.source_run_id)
            chat = tx.research.chat(job.node_id)
        if not run or not run.commit_id or not run.result or run.result.status != 'succeeded': raise ScopeDenied('Uncommitted capture source')
        if not chat or not any(m.id == job.message_id and m.role == 'user' and m.content == job.content for m in (*chat.inherited, *chat.messages)):
            raise ScopeDenied('Capture human source changed')
        context = project_context(self.research, job.research_id)
        if context.get('project_id') != job.project_id: raise ScopeDenied('Capture project changed')
        engine = self.research.engine
        if hasattr(engine, 'validate_run'): engine.validate_run(job.source_run_id, require_started=True)
        if job.request:
            snapshot = self.research.scopes.repository.snapshot(job.request.run_id)
            self.research.scopes.valid(snapshot.scope)
            self.memories.validate(job.research_id, snapshot.memory_catalog, agent=snapshot.agent)

    def prepare(self, job):
        self.validate(job)
        if job.request: return job
        run_id = identity()
        scope = self.research.scopes.create(self.owner, tools=())
        agent = AgentIdentity(mode='assistant', id='memory_capture:' + job.id)
        with self.database.transaction() as tx:
            chat = tx.research.chat(job.node_id)
        visible = (*chat.inherited, *chat.messages)
        index = next(i for i,m in enumerate(visible) if m.id == job.message_id)
        context = [{'id':m.id, 'role':m.role, 'content':m.content} for m in visible[max(0,index-3):index]]
        snapshot = self.research.context.assemble(self.research.scopes, scope, run_id, 'Propose durable memory from one new human message',
            agent=agent, mode='memory_capture', persist=False, original_message=Message(role='user', content=job.content),
            project=project_context(self.research, job.research_id), refs=(),
            memories=[m for m in self.memories.list(job.research_id) if m.available()],
            business={'new_user': {'id':job.message_id, 'content':job.content}, 'prior_context':context})
        # This internal entrance has one fixed capability, not lexical discovery.
        snapshot = snapshot.model_copy(update={'input':snapshot.input.model_copy(update={'tool_query':None})})
        self.research.scopes.repository.save_snapshot(snapshot)
        ready = job.model_copy(update={'state':'ready', 'model':os.environ.get('OPENAI_MEMORY_MODEL','deepseek-flash'),
            'request':RunRequest(run_id=run_id, agent_id=agent.id, input=snapshot.input,
                limits=RunLimits(max_turns=12, max_calls=2, timeout_seconds=90, max_depth=0))})
        with self.database.transaction() as tx: tx.memories.save_capture(ready, 'pending')
        return ready

    def finish(self, job, result):
        self.validate(job)
        state = 'completed' if result.status == 'succeeded' and result.usage.complete else 'failed'
        with self.database.transaction() as tx:
            current = tx.memories.capture(job.id)
            if current.state in ('completed','failed'): return current
            publication = result if state == 'completed' else RunResult(run_id=job.request.run_id, status='failed',
                usage=result.usage, error=RunError(code='controlled_invalidated', origin='engine', message='Background candidate failed'))
            self.memories.writes.publish_in(tx, job.request.run_id, publication)
            value = job.model_copy(update={'state':state, 'result':result,
                'reason':None if state == 'completed' else 'Capture failed or usage unconfirmed; no automatic retry'})
            tx.memories.save_capture(value, 'ready')
        return value

    async def process(self, key):
        async with self.lock:
            with self.database.transaction() as tx: job = tx.memories.capture(key)
            if job is None or job.owner != self.owner: raise ScopeDenied('Foreign capture')
            if job.state in ('completed','failed'): return job
            try:
                job = self.prepare(job)
                snapshot = self.research.scopes.repository.snapshot(job.request.run_id)
                validate = lambda: self.validate(job)
                tool = write_memory_binding(snapshot, self.memories, job.research_id, validate)
                spec = AgentSpec(job.request.agent_id, CAPTURE_INSTRUCTION, tools=(tool,), output_type=CaptureOutput, model=job.model)
                with admit(job.request, research_id=job.research_id, session_id=job.id,
                        task_id=job.id, spec_version=1, kind='memory_capture', validate=validate):
                    result = await CoreRunner(self.research.engine, AgentCatalog([spec])).execute(job.request)
                return self.finish(job, result)
            except (ScopeDenied, Conflict, ValueError, KeyError) as exc:
                with self.database.transaction() as tx:
                    current = tx.memories.capture(key)
                    if current.state not in ('completed','failed'):
                        if current.request:
                            self.memories.writes.publish_in(tx, current.request.run_id, RunResult(run_id=current.request.run_id, status='failed',
                                error=RunError(code='controlled_invalidated', origin='engine', message='Capture source invalidated')))
                        tx.memories.save_capture(current.model_copy(update={'state':'failed', 'reason':str(exc)}), current.state)
                with self.database.transaction() as tx: return tx.memories.capture(key)

    async def drain(self):
        if self.research.engine is None: return
        for job in self.records():
            if job.state in ('pending','ready'):
                try: await self.process(job.id)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Memory capture job failed: %s', job.id)
