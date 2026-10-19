from sqlalchemy import Table, Column, String, Integer, select, insert, update, delete
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...memories.models import MemoryEntry
from ...memories.write_models import MemoryWrite
from ...memories.capture_models import MemoryCapture


def define_tables(meta):
    entries = Table('user_memories', meta, Column('id', String, primary_key=True),
        Column('owner', String, nullable=False), Column('version', Integer, nullable=False),
        Column('payload', JSONB, nullable=False))
    writes = Table('memory_writes', meta, Column('id', String, primary_key=True),
        Column('owner', String, nullable=False), Column('run_id', String, nullable=False),
        Column('state', String, nullable=False), Column('payload', JSONB, nullable=False))
    captures = Table('memory_captures', meta, Column('id', String, primary_key=True),
        Column('owner', String, nullable=False), Column('state', String, nullable=False),
        Column('payload', JSONB, nullable=False))
    return entries, writes, captures


class PostgresMemories:
    def __init__(self, connection, tables): self.c, (self.t, self.w, self.cap) = connection, tables
    def get(self, key):
        value = self.c.scalar(select(self.t.c.payload).where(self.t.c.id == key))
        return MemoryEntry.model_validate(value) if value else None
    def list(self, owner):
        return [MemoryEntry.model_validate(v) for v in self.c.scalars(select(self.t.c.payload).where(self.t.c.owner == owner))]
    def save(self, value, expected_version):
        if expected_version is None:
            self.c.execute(insert(self.t).values(id=value.id, owner=value.owner,
                version=value.version, payload=value.model_dump(mode='json')))
        else:
            result = self.c.execute(update(self.t).where(self.t.c.id == value.id,
                self.t.c.version == expected_version, self.t.c.owner == value.owner).values(
                version=value.version, payload=value.model_dump(mode='json')))
            if result.rowcount != 1: raise Conflict('Memory changed')
    def delete(self, key, expected_version):
        result = self.c.execute(delete(self.t).where(self.t.c.id == key, self.t.c.version == expected_version))
        if result.rowcount != 1: raise Conflict('Memory changed')
    def write(self, key):
        value = self.c.scalar(select(self.w.c.payload).where(self.w.c.id == key))
        return MemoryWrite.model_validate(value) if value else None
    def list_writes(self, owner, run_id=None):
        query = select(self.w.c.payload).where(self.w.c.owner == owner)
        if run_id is not None: query = query.where(self.w.c.run_id == run_id)
        return [MemoryWrite.model_validate(v) for v in self.c.scalars(query)]
    def save_write(self, value, expected_state=None):
        row = dict(owner=value.owner, run_id=value.run_id, state=value.state, payload=value.model_dump(mode='json'))
        if expected_state is None: self.c.execute(insert(self.w).values(id=value.id, **row))
        else:
            result = self.c.execute(update(self.w).where(self.w.c.id == value.id,
                self.w.c.owner == value.owner, self.w.c.state == expected_state).values(**row))
            if result.rowcount != 1: raise Conflict('Memory write changed')

    def capture(self, key):
        value = self.c.scalar(select(self.cap.c.payload).where(self.cap.c.id == key))
        return MemoryCapture.model_validate(value) if value else None
    def list_captures(self, owner):
        return [MemoryCapture.model_validate(v) for v in self.c.scalars(select(self.cap.c.payload).where(self.cap.c.owner == owner))]
    def save_capture(self, value, expected_state=None):
        row = dict(owner=value.owner, state=value.state, payload=value.model_dump(mode='json'))
        if expected_state is None: self.c.execute(insert(self.cap).values(id=value.id, **row))
        else:
            result = self.c.execute(update(self.cap).where(self.cap.c.id == value.id,
                self.cap.c.owner == value.owner, self.cap.c.state == expected_state).values(**row))
            if result.rowcount != 1: raise Conflict('Memory capture changed')
