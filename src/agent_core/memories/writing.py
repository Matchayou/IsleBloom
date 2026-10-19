"""Deferred writes: frozen execution first, atomic memory publication second."""
from datetime import datetime, timezone
import json
from ..context.scopes import ScopeDenied
from ..context.memory_retrieval import select_memories
from ..controlled.ledger import digest
from ..execution.ports import Conflict
from .models import MemoryEntry
from .write_models import MemoryWrite, WriteReview, current_write_operation, current_write_review


class MemoryWriter:
    def __init__(self, memories):
        self.memories = memories
        self.database, self.owner, self.research = memories.database, memories.owner, memories.research

    def records(self, run_id=None):
        with self.database.transaction() as tx: return tx.memories.list_writes(self.owner, run_id)

    def replay(self, run_id, call_id, payload):
        with self.database.transaction() as tx: value = tx.memories.write(self.key(run_id, call_id))
        if value is None: return None
        if value.owner != self.owner or value.operation_hash != digest(payload): raise Conflict('Memory operation changed')
        return {'value': value.receipt}

    @staticmethod
    def key(run_id, call_id): return 'memory-write:' + digest([run_id, call_id])

    def original_user(self, snapshot, research_id):
        payload = json.loads(snapshot.input.messages[0].content)
        if payload.get('external_context'):
            payload = json.loads(snapshot.input.external_context[payload['external_context']['artifact_id']])
        handoff = payload.get('handoff')
        if handoff and 'user_message_id' in handoff:
            from ..research.chat import ChatService
            packet = self.research.collaboration.packet(handoff['id'])
            if packet.research_id != research_id: raise ScopeDenied('Foreign memory evidence packet')
            chat = ChatService(self.research, self.research.chat_tasks).read(packet.sender_node_id)
            original = next((m for m in (*chat.inherited, *chat.messages)
                if m.id == packet.user_message_id and m.role == 'user'), None)
            if original is None: raise ScopeDenied('Original human message unavailable')
            return original.content
        if handoff and handoff.get('phase') == 'approved_plan_execution':
            proposal = self.research.planning.get(handoff['proposal_id'])
            if proposal.research_id != research_id: raise ScopeDenied('Foreign memory evidence proposal')
            return proposal.request
        if handoff: raise ScopeDenied('Unrecognized human evidence packet')
        return snapshot.input.messages[-1].content

    async def submit(self, snapshot, research_id, args):
        operation = current_write_operation.get()
        if operation is None or operation['run_id'] != snapshot.run_id or operation.get('name') != 'write_memory':
            raise ScopeDenied('Memory writing requires an admitted broker operation')
        if snapshot.mode not in ('assistant', 'execution', 'memory_capture'): raise ScopeDenied('This entrance is read-only')
        if snapshot.mode == 'memory_capture' and (args.action != 'create' or args.scope == 'specialist'):
            raise ScopeDenied('Background capture only creates user/project drafts')
        namespaces = self.memories.namespaces(research_id, snapshot.agent)
        namespace = next(((s, k) for s, k in namespaces if s == args.scope), None)
        if namespace is None: raise ScopeDenied('Memory namespace not permitted')
        if args.scope == 'specialist' and (args.action != 'create' or args.kind != 'experience'):
            raise ScopeDenied('Specialists submit new domain experience candidates; published content requires independent validation')
        values = self.memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
        old = None
        if args.action != 'create':
            old = next((m for m in values if m.id == args.target_id), None)
            if old is None:
                with self.database.transaction() as tx: draft = tx.memories.get(args.target_id)
                if draft and draft.owner == self.owner and not draft.available() and (draft.scope, draft.scope_id) == namespace:
                    old = draft
            if old is None or (old.scope, old.scope_id) != namespace: raise ScopeDenied('Target is outside this frozen namespace')
            if old.version != args.expected_version: raise Conflict('Memory version changed; refresh')
            if old.publication_id: raise ScopeDenied('Published domain memory requires validated replacement or publication revocation')
            if args.action == 'correct' and args.kind != old.kind: raise ValueError('Correction cannot change memory kind')
        original = self.original_user(snapshot, research_id)
        if args.basis == 'user_statement' and args.quote not in original:
            raise ScopeDenied('Quote does not match the frozen original user request')
        if args.expires_at and args.expires_at <= datetime.now(timezone.utc): raise ValueError('Expiration is in the past')
        content = args.content.strip() + ('\n适用条件：' + args.applicability.strip() if args.applicability.strip() else '')
        if args.scope == 'specialist' and len(content) > 2000: raise ValueError('Complete domain candidate exceeds 2000 characters')
        candidates = []
        if args.action == 'create':
            candidates, _ = await select_memories(content, [m for m in values if (m.scope, m.scope_id) == namespace])
        review_call = current_write_review.get()
        if review_call is None: raise ScopeDenied('Independent memory write reviewer unavailable')
        review = WriteReview.model_validate(await review_call({'original_user': original,
            'proposal': args.model_dump(mode='json'), 'namespace': args.scope,
            'old_memory': old.model_dump(mode='json') if old else None,
            'existing': [m.model_dump(mode='json') for m in candidates]}))
        if not review.safe or not review.supported or not review.persistent:
            return {'status': 'not_saved', 'reason': review.reason,
                'related_memories': [{'id': m.id, 'version': m.version} for m in candidates] if review.safe else []}
        confirmed = snapshot.mode != 'memory_capture' and args.basis == 'user_statement' and review.explicit_user and args.scope != 'specialist'
        if args.action != 'create' and not confirmed: raise ScopeDenied('Changing existing memory requires an explicit durable user instruction')
        expiry = args.expires_at if args.expires_at is not None or old is None else old.expires_at
        if args.action == 'correct' and old.status == 'confirmed' and old.content == content and old.expires_at == expiry:
            return {'status': 'duplicate', 'memory_id': old.id, 'version': old.version}
        duplicate = next((m for m in candidates if m.id == review.duplicate_id), None)
        if review.duplicate_id and duplicate is None: raise ValueError('Reviewer selected an unknown duplicate')
        if duplicate and duplicate.kind == args.kind and duplicate.available():
            return {'status': 'duplicate', 'memory_id': duplicate.id, 'version': duplicate.version}
        self.memories.validate(research_id, snapshot.memory_catalog, agent=snapshot.agent)
        key = self.key(snapshot.run_id, operation['call_id'])
        memory_id = old.id if old else 'agent-memory:' + digest(key)
        receipt = {'status': 'queued', 'write_id': key, 'memory_id': memory_id,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'intended_status': 'forgotten' if args.action == 'forget' else 'confirmed' if confirmed else 'draft',
            'instruction': 'Durably queued. Current run keeps its frozen memory; publication follows business result validation. Not yet active.'}
        value = MemoryWrite(id=key, owner=self.owner, run_id=snapshot.run_id, research_id=research_id,
            snapshot_id=snapshot.id, agent_id=snapshot.agent.id, operation_hash=operation['hash'],
            request=args, scope_id=namespace[1], memory_id=memory_id,
            target_version=old.version if old else None, old_memory=old.model_dump(mode='json') if old else None,
            original_user_hash=digest(original), review=review, confirmed=confirmed, receipt=receipt)
        with self.database.transaction() as tx:
            pending = tx.memories.list_writes(self.owner, snapshot.run_id)
            if snapshot.mode == 'memory_capture' and pending:
                raise Conflict('One background candidate per human message')
            if args.scope == 'specialist' and any(w.request.scope == 'specialist' for w in pending):
                raise Conflict('One independently validated domain candidate per trajectory')
            tx.memories.save_write(value)
        return receipt

    def publish_in(self, tx, run_id, result):
        """Called after business dependency checks and result writes, in their transaction."""
        records = tx.memories.list_writes(self.owner, run_id)
        for value in records:
            if value.state != 'pending': continue
            args = value.request
            status, resolution = 'published', None
            blocked = result.error and result.error.code in ('input_revoked', 'scope_denied', 'controlled_invalidated', 'task_superseded', 'task_cancelled', 'branch_changed')
            if args.scope == 'project':
                member = tx.projects.member(value.research_id)
                project = tx.projects.project(value.scope_id)
                blocked = blocked or member is None or member.project_id != value.scope_id or project is None or project.owner != self.owner
            old = tx.memories.get(value.memory_id) if value.target_version else None
            if blocked or (value.target_version and (old is None or old.owner != self.owner or old.version != value.target_version)):
                status, resolution = 'rejected', {'status': 'conflict_or_revoked', 'memory_id': value.memory_id}
            elif args.action == 'forget':
                # Pause instead of destroying provenance; user deletion API remains available.
                tx.memories.save(old.model_copy(update={'status': 'draft', 'version': old.version + 1}), old.version)
                resolution = {'status': 'forgotten', 'memory_id': old.id, 'version': old.version + 1}
            else:
                content = args.content.strip() + ('\n适用条件：' + args.applicability.strip() if args.applicability.strip() else '')
                duplicate = next((m for m in tx.memories.list(self.owner) if args.scope != 'specialist' and value.target_version is None and
                    (m.scope, m.scope_id, m.kind, m.content, m.status, m.expires_at) ==
                    (args.scope, value.scope_id, args.kind, content, 'confirmed' if value.confirmed else 'draft', args.expires_at)), None)
                if duplicate is not None:
                    tx.memories.save_write(value.model_copy(update={'state': 'published', 'resolution': {
                        'status': 'duplicate', 'memory_id': duplicate.id, 'version': duplicate.version,
                        'resolved_at': datetime.now(timezone.utc).isoformat()}}), 'pending')
                    continue
                entry = MemoryEntry(id=value.memory_id, owner=self.owner, version=old.version + 1 if old else 1,
                    scope=args.scope, scope_id=value.scope_id, kind=args.kind, content=content,
                    publication_id=run_id if args.scope == 'specialist' else None,
                    source='Agent write ' + value.id + '; user source ' + value.original_user_hash,
                    status='confirmed' if value.confirmed else 'draft',
                    expires_at=args.expires_at if args.expires_at is not None or old is None else old.expires_at)
                tx.memories.save(entry, value.target_version)
                resolution = {'status': entry.status, 'memory_id': entry.id, 'version': entry.version}
                if args.scope == 'specialist':
                    from ..learning.models import DomainCandidate, LearningDraft, Trajectory
                    learning = self.research.learning
                    trajectory = tx.evolution.get(learning.key(Trajectory, run_id))
                    if trajectory is not None and tx.evolution.get(learning.key(DomainCandidate, run_id)) is None:
                        draft = LearningDraft(memory=content)
                        learning.add(DomainCandidate(id=run_id, trajectory_id=run_id, owner=self.owner,
                            expert_id=value.scope_id, draft=draft, digest=digest(draft.model_dump(mode='json')),
                            generation_run_id=run_id), tx)
                        resolution['status'] = 'awaiting_independent_validation'
                        resolution['trajectory_id'] = run_id
            resolution['resolved_at'] = datetime.now(timezone.utc).isoformat()
            tx.memories.save_write(value.model_copy(update={'state': status, 'resolution': resolution}), 'pending')

    def drain(self):
        """Reconcile terminal business runs only; never publish while a run can resume."""
        run_ids = {w.run_id for w in self.records() if w.state == 'pending'}
        for run_id in run_ids:
            with self.database.transaction() as tx:
                record = tx.runs.get(run_id)
                if record and record.commit_id and record.result:
                    self.publish_in(tx, run_id, record.result)
