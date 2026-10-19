from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Table, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert

from ...execution.ports import Commit, Conflict, RunRecord, StoredRunEvent


def define_tables(metadata):
    runs = Table('runs', metadata,
        Column('run_id', String, primary_key=True), Column('conversation_id', String),
        Column('process_id', String, nullable=False), Column('request', JSONB, nullable=False),
        Column('expected_revision', Integer, nullable=False), Column('status', String, nullable=False),
        Column('result', JSONB(none_as_null=True)), Column('commit_id', String, unique=True),
        Column('commit_payload', JSONB(none_as_null=True)), Column('created_at', DateTime(timezone=True), nullable=False),
        CheckConstraint("status IN ('running','saving','succeeded','failed','cancelled','interrupted')"),
        CheckConstraint("(status IN ('succeeded','failed','cancelled') AND result IS NOT NULL AND commit_id IS NOT NULL AND commit_payload IS NOT NULL) OR (status IN ('running','saving','interrupted') AND result IS NULL AND commit_id IS NULL AND commit_payload IS NULL)"))
    Index('one_active_run_per_conversation', runs.c.conversation_id, unique=True,
          postgresql_where=runs.c.status.in_(['running', 'saving']))
    events = Table('run_events', metadata,
        Column('root_run_id', String, ForeignKey('runs.run_id'), primary_key=True),
        Column('sequence', Integer, primary_key=True), Column('event', JSONB, nullable=False),
        Column('occurred_at', DateTime(timezone=True), nullable=False))
    groups = Table('dispatch_groups', metadata,
        Column('group_id', String, primary_key=True), Column('payload', JSONB, nullable=False))
    return runs, events, groups


class PostgresRuns:
    def __init__(self, connection, tables):
        self.connection, (self.runs, self.timeline, self.groups) = connection, tables

    def create_dispatch(self, group):
        self.connection.execute(insert(self.groups).values(group_id=group.plan.group_id,
                                                          payload=group.model_dump(mode='json')))

    def get_dispatch(self, group_id):
        from ...orchestration.models import DispatchGroup
        payload = self.connection.scalar(select(self.groups.c.payload).where(self.groups.c.group_id == group_id))
        return DispatchGroup.model_validate(payload) if payload else None

    def finish_dispatch(self, group):
        old = self.get_dispatch(group.plan.group_id)
        if old is None or old.plan != group.plan or old.status != 'running':
            if old == group:
                return
            raise Conflict('Dispatch cannot finish')
        self.connection.execute(update(self.groups).where(self.groups.c.group_id == group.plan.group_id)
                                .values(payload=group.model_dump(mode='json')))

    @staticmethod
    def decode(row):
        if row is None:
            return None
        return RunRecord.model_validate({k: v for k, v in row.items() if k != 'commit_payload'})

    def get(self, run_id):
        return self.decode(self.connection.execute(select(self.runs).where(self.runs.c.run_id == run_id)).mappings().one_or_none())

    def list(self, conversation_id):
        return [self.decode(r) for r in self.connection.execute(select(self.runs).where(
            self.runs.c.conversation_id == conversation_id).order_by(self.runs.c.created_at, self.runs.c.run_id)).mappings()]

    def start(self, record):
        values = record.model_dump(mode='json')
        values['created_at'] = record.created_at
        self.connection.execute(insert(self.runs).values(**values))

    def saving(self, run_id):
        changed = self.connection.execute(update(self.runs).where(self.runs.c.run_id == run_id,
            self.runs.c.status.in_(['running', 'saving', 'interrupted'])).values(status='saving')).rowcount
        if not changed:
            raise Conflict('Run cannot enter saving')

    def committed(self, commit_id):
        payload = self.connection.scalar(select(self.runs.c.commit_payload).where(self.runs.c.commit_id == commit_id))
        return Commit.model_validate(payload) if payload else None

    def finish(self, commit):
        self.connection.execute(update(self.runs).where(self.runs.c.run_id == commit.run_id).values(
            status=commit.result.status, result=commit.result.model_dump(mode='json'), commit_id=commit.commit_id,
            commit_payload=commit.model_dump(mode='json')))

    def append_event(self, event):
        t = self.timeline
        existing = self.connection.execute(select(t).where(t.c.root_run_id == event.root_run_id,
                                         t.c.sequence == event.sequence)).mappings().one_or_none()
        if existing:
            if StoredRunEvent.model_validate(existing) != event:
                raise Conflict('Event sequence reused with different content')
            return
        previous = self.connection.scalar(select(t.c.sequence).where(t.c.root_run_id == event.root_run_id)
                                          .order_by(t.c.sequence.desc()).limit(1)) or 0
        if event.sequence != previous + 1:
            raise Conflict('Event sequence gap')
        self.connection.execute(insert(t).values(root_run_id=event.root_run_id, sequence=event.sequence,
            event=event.event.model_dump(mode='json'), occurred_at=event.occurred_at))

    def events(self, run_id):
        return [StoredRunEvent.model_validate(r) for r in self.connection.execute(select(self.timeline).where(
            self.timeline.c.root_run_id == run_id).order_by(self.timeline.c.sequence)).mappings()]
