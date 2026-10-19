"""Collaborative identities and dispatch intents in the business transaction."""
from sqlalchemy import Table, Column, String, Integer, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB

from ...collaboration.models import AgentMessage, ContextPacket, DispatchIntent, ExpertSession, InformationRequest, ManagerNotice, TaskCancellation, TaskDependency, TaskRevision
from ...execution.ports import Conflict
from ...resources.snapshots import ResourceRef
from ...collaboration.models import ReviewLoop


def define_tables(meta):
    return {
        'review_loops': Table('collaboration_review_loops', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False)),
        'information_requests': Table('collaboration_information_requests', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('task_id', String, nullable=False, index=True),
            Column('status', String, nullable=False), Column('attempts', Integer, nullable=False),
            Column('payload', JSONB, nullable=False)),
        'sessions': Table('collaboration_sessions', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('expert_id', String, nullable=False), Column('revision', Integer, nullable=False),
            Column('payload', JSONB, nullable=False)),
        'packets': Table('collaboration_packets', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('payload', JSONB, nullable=False)),
        'intents': Table('collaboration_intents', meta,
            Column('command_id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('task_id', String, nullable=False, unique=True),
            Column('status', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'messages': Table('collaboration_messages', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('session_id', String, nullable=False, index=True),
            Column('status', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'revisions': Table('collaboration_revisions', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('task_id', String, nullable=False, index=True),
            Column('status', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'cancellations': Table('collaboration_cancellations', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('task_id', String, nullable=False, index=True),
            Column('status', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'dependencies': Table('collaboration_dependencies', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('downstream_task_id', String, nullable=False, index=True),
            Column('payload', JSONB, nullable=False)),
        'notices': Table('collaboration_manager_notices', meta,
            Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('node_id', String, nullable=False, index=True),
            Column('status', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'reads': Table('collaboration_context_reads', meta,
            Column('run_id', String, primary_key=True), Column('resource_id', String, primary_key=True),
            Column('snapshot_id', String, primary_key=True), Column('parse_id', String, primary_key=True),
            Column('payload', JSONB, nullable=False)),
    }


class PostgresCollaboration:
    CLASSES = {'sessions': ExpertSession, 'packets': ContextPacket, 'intents': DispatchIntent,
               'messages': AgentMessage, 'revisions': TaskRevision,
               'cancellations': TaskCancellation, 'dependencies': TaskDependency, 'notices': ManagerNotice,
               'information_requests': InformationRequest, 'review_loops': ReviewLoop}
    KEYS = {'sessions': 'id', 'packets': 'id', 'intents': 'command_id', 'messages': 'id',
            'revisions': 'id', 'cancellations': 'id', 'dependencies': 'id', 'notices': 'id', 'information_requests': 'id', 'review_loops': 'id'}
    def __init__(self, connection, tables): self.c, self.t = connection, tables
    def _one(self, kind, key):
        t = self.t[kind]
        value = self.c.scalar(select(t.c.payload).where(t.c[self.KEYS[kind]] == key))
        return self.CLASSES[kind].model_validate(value) if value else None
    def session(self, key): return self._one('sessions', key)
    def review_loop(self, task_id): return self._one('review_loops', task_id)
    def review_loops_for(self, research_id):
        t = self.t['review_loops']
        return [ReviewLoop.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.research_id == research_id).order_by(t.c.id))]
    def save_review_loop(self, value, expected_revision):
        t = self.t['review_loops']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.revision == expected_revision)
                .values(revision=value.revision, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Automatic review changed')
    def information_request(self, key): return self._one('information_requests', key)
    def information_for(self, task_id):
        t = self.t['information_requests']
        return [InformationRequest.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.task_id == task_id).order_by(t.c.id))]
    def save_information_request(self, value, expected_attempts):
        t = self.t['information_requests']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.status == 'waiting',
                t.c.attempts == expected_attempts).values(status=value.status, attempts=value.attempts,
                payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Information request changed')
    def packet(self, key): return self._one('packets', key)
    def intent(self, key): return self._one('intents', key)
    def message(self, key): return self._one('messages', key)
    def pending_messages(self):
        t = self.t['messages']
        return [AgentMessage.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status == 'queued').order_by(t.c.id))]
    def messages_for(self, session_id):
        t = self.t['messages']
        return [AgentMessage.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.session_id == session_id).order_by(t.c.id))]
    def revision(self, key): return self._one('revisions', key)
    def cancellation(self, key): return self._one('cancellations', key)
    def cancellations_for(self, task_id):
        t = self.t['cancellations']
        return [TaskCancellation.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.task_id == task_id).order_by(t.c.id))]
    def pending_cancellations(self):
        t = self.t['cancellations']
        return [TaskCancellation.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status.in_(['requested', 'stopping'])).order_by(t.c.id))]
    def dependencies_for(self, research_id):
        t = self.t['dependencies']
        return [TaskDependency.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.research_id == research_id).order_by(t.c.id))]
    def notice(self, key): return self._one('notices', key)
    def notices_for(self, research_id):
        t = self.t['notices']
        return [ManagerNotice.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.research_id == research_id).order_by(t.c.id))]
    def pending_notices(self):
        t = self.t['notices']
        return [ManagerNotice.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status == 'pending').order_by(t.c.id))]
    def pending_revisions(self):
        t = self.t['revisions']
        rows = [TaskRevision.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status.in_(['queued', 'saved', 'running'])).order_by(t.c.id))]
        return [v for v in rows if v.status != 'saved' or v.mode != 'save']
    def intent_for_task(self, task_id):
        t = self.t['intents']
        value = self.c.scalar(select(t.c.payload).where(t.c.task_id == task_id))
        return DispatchIntent.model_validate(value) if value else None
    def sessions_for(self, research_id):
        t = self.t['sessions']
        return [ExpertSession.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.research_id == research_id).order_by(t.c.id))]
    def pending(self):
        t = self.t['intents']
        return [DispatchIntent.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status.in_(['accepted', 'queued'])).order_by(t.c.command_id))]
    def intents_for(self, research_id):
        t = self.t['intents']
        return [DispatchIntent.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.research_id == research_id).order_by(t.c.command_id))]
    def intents_for_status(self, status):
        t = self.t['intents']
        return [DispatchIntent.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.status == status).order_by(t.c.command_id))]
    def record_read(self, run_id, ref):
        t = self.t['reads']
        where = (t.c.run_id == run_id, t.c.resource_id == ref.resource_id,
            t.c.snapshot_id == ref.snapshot_id, t.c.parse_id == ref.parse_id)
        if self.c.scalar(select(t.c.run_id).where(*where)) is None:
            self.c.execute(insert(t).values(run_id=run_id, resource_id=ref.resource_id,
                snapshot_id=ref.snapshot_id, parse_id=ref.parse_id,
                payload=ref.model_dump(mode='json')))
    def read_refs(self, run_id):
        t = self.t['reads']
        return [ResourceRef.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.run_id == run_id))]
    def insert(self, kind, key, value):
        fields = {self.KEYS[kind]: key, 'research_id': value.research_id,
                  'payload': value.model_dump(mode='json')}
        if kind == 'sessions': fields.update(expert_id=value.expert_id, revision=value.revision)
        if kind == 'review_loops': fields.update(revision=value.revision)
        if kind == 'intents': fields.update(status=value.status, task_id=value.task_id)
        if kind == 'messages': fields.update(status=value.status, session_id=value.session_id)
        if kind in ('revisions', 'cancellations'): fields.update(status=value.status, task_id=value.task_id)
        if kind == 'dependencies': fields.update(downstream_task_id=value.downstream_task_id)
        if kind == 'notices': fields.update(status=value.status, node_id=value.node_id)
        if kind == 'information_requests': fields.update(status=value.status, task_id=value.task_id, attempts=value.attempts)
        self.c.execute(insert(self.t[kind]).values(**fields))
    def save_session(self, value, expected):
        t = self.t['sessions']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.revision == expected)
                .values(revision=value.revision, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Expert session revision changed')
    def save_intent(self, value, expected_status):
        t = self.t['intents']
        if self.c.execute(update(t).where(t.c.command_id == value.command_id, t.c.status == expected_status)
                .values(status=value.status, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Dispatch state changed')
    def save_message(self, value, expected_status):
        t = self.t['messages']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.status == expected_status)
                .values(status=value.status, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Agent message state changed')
    def save_revision(self, value, expected_status):
        t = self.t['revisions']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.status == expected_status)
                .values(status=value.status, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Task revision state changed')
    def save_cancellation(self, value, expected_status):
        t = self.t['cancellations']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.status == expected_status)
                .values(status=value.status, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Task cancellation state changed')
    def save_notice(self, value, expected_status):
        t = self.t['notices']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.status == expected_status)
                .values(status=value.status, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Manager notice state changed')
