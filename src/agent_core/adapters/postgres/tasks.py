"""Task-owned tables; writes use the caller's application transaction."""
from sqlalchemy import Table, Column, String, Integer, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...tasks.models import TaskSpec, TaskRun, TaskReceipt
from ...routing.models import RouteRecord


def define_tables(meta):
    return {
        'specs': Table('task_specs', meta, Column('task_id', String, primary_key=True),
            Column('version', Integer, primary_key=True), Column('research_id', String, nullable=False, index=True),
            Column('payload', JSONB, nullable=False)),
        'current': Table('task_current', meta, Column('task_id', String, primary_key=True),
            Column('version', Integer, nullable=False)),
        'runs': Table('task_runs', meta, Column('id', String, primary_key=True),
            Column('task_id', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'receipts': Table('task_receipts', meta, Column('result_id', String, primary_key=True),
            Column('task_id', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
        'routes': Table('task_routes', meta, Column('id', String, primary_key=True),
            Column('task_id', String, nullable=False, index=True), Column('payload', JSONB, nullable=False)),
    }


class PostgresTasks:
    def __init__(self, connection, tables): self.c, self.t = connection, tables

    def add_route(self, value):
        self.c.execute(insert(self.t['routes']).values(id=value.id, task_id=value.task_id,
            payload=value.model_dump(mode='json')))

    def route(self, route_id):
        payload = self.c.scalar(select(self.t['routes'].c.payload).where(self.t['routes'].c.id == route_id))
        return RouteRecord.model_validate(payload) if payload else None

    def list_routes(self, task_id):
        t = self.t['routes']
        return [RouteRecord.model_validate(v) for v in self.c.scalars(
            select(t.c.payload).where(t.c.task_id == task_id))]

    def spec(self, task_id, version=None):
        if version is None:
            version = self.c.scalar(select(self.t['current'].c.version).where(self.t['current'].c.task_id == task_id))
        if version is None: return None
        t = self.t['specs']
        payload = self.c.scalar(select(t.c.payload).where(t.c.task_id == task_id, t.c.version == version))
        return TaskSpec.model_validate(payload) if payload else None

    def add_spec(self, value):
        current = self.spec(value.id)
        if value.version != (current.version + 1 if current else 1): raise Conflict('Task version changed')
        self.c.execute(insert(self.t['specs']).values(task_id=value.id, version=value.version,
            research_id=value.research_id, payload=value.model_dump(mode='json')))
        if current:
            if self.c.execute(update(self.t['current']).where(self.t['current'].c.task_id == value.id,
                self.t['current'].c.version == current.version).values(version=value.version)).rowcount != 1:
                raise Conflict('Task version changed')
        else:
            self.c.execute(insert(self.t['current']).values(task_id=value.id, version=1))

    def run(self, run_id):
        payload = self.c.scalar(select(self.t['runs'].c.payload).where(self.t['runs'].c.id == run_id))
        return TaskRun.model_validate(payload) if payload else None

    def add_run(self, value):
        if self.spec(value.task_id).version != value.spec_version: raise Conflict('Task version changed')
        self.c.execute(insert(self.t['runs']).values(id=value.id, task_id=value.task_id,
            payload=value.model_dump(mode='json')))

    def receipt(self, result_id):
        payload = self.c.scalar(select(self.t['receipts'].c.payload).where(self.t['receipts'].c.result_id == result_id))
        return TaskReceipt.model_validate(payload) if payload else None

    def save_receipt(self, value, expected=None):
        old = self.receipt(value.result_id)
        if old != expected: raise Conflict('Receipt changed')
        payload = value.model_dump(mode='json')
        if old is None:
            self.c.execute(insert(self.t['receipts']).values(result_id=value.result_id,
                task_id=value.task_id, payload=payload))
        else:
            self.c.execute(update(self.t['receipts']).where(self.t['receipts'].c.result_id == value.result_id)
                .values(payload=payload))

    def list_specs(self, research_id):
        t = self.t['specs']
        ids = self.c.scalars(select(t.c.task_id).where(t.c.research_id == research_id).distinct())
        return [self.spec(key) for key in ids]

    def list_directory(self, research_id):
        """Read conversation metadata without loading an execution contract."""
        specs, current = self.t['specs'], self.t['current']
        fields = ('id', 'research_id', 'goal', 'parent_task_id', 'node_id')
        rows = self.c.execute(select(*(specs.c.payload[key].astext.label(key) for key in fields))
            .join(current, (specs.c.task_id == current.c.task_id) &
                  (specs.c.version == current.c.version))
            .where(specs.c.research_id == research_id).order_by(specs.c.task_id))
        return [dict(row._mapping) for row in rows]

    def list_runs(self, task_id):
        t = self.t['runs']
        return [TaskRun.model_validate(v) for v in self.c.scalars(select(t.c.payload).where(t.c.task_id == task_id))]
