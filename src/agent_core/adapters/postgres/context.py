from sqlalchemy import MetaData, Table, Column, String, Integer, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...context.scopes import RunScope
from ...context.mounts import ContextSnapshot


class PostgresContext:
    def __init__(self, database):
        self.database = database
        meta = MetaData()
        self.scopes = Table('context_scopes', meta, Column('id', String, primary_key=True),
            Column('version', Integer, nullable=False), Column('payload', JSONB, nullable=False))
        self.snapshots = Table('context_snapshots', meta, Column('run_id', String, primary_key=True), Column('payload', JSONB, nullable=False))
        self.mounts = Table('context_mounts', meta, Column('run_id', String, primary_key=True),
            Column('position', Integer, primary_key=True), Column('payload', JSONB, nullable=False))
        with database.transaction():
            meta.create_all(database.connection)

    def scope(self, key):
        with self.database.transaction():
            value = self.database.connection.scalar(select(self.scopes.c.payload).where(self.scopes.c.id == key))
        if value is None:
            raise KeyError(key)
        return RunScope.model_validate(value)

    def save_scope(self, scope, expected_version):
        with self.database.transaction():
            c = self.database.connection
            values = dict(version=scope.version, payload=scope.model_dump(mode='json'))
            if expected_version is None:
                c.execute(insert(self.scopes).values(id=scope.id, **values))
            elif c.execute(update(self.scopes).where(self.scopes.c.id == scope.id,
                    self.scopes.c.version == expected_version).values(**values)).rowcount != 1:
                raise Conflict('Scope revision conflict')

    def save_snapshot(self, snapshot):
        with self.database.transaction():
            c = self.database.connection
            old = c.scalar(select(self.snapshots.c.payload).where(self.snapshots.c.run_id == snapshot.run_id))
            if old:
                if ContextSnapshot.model_validate(old) != snapshot:
                    raise Conflict('Run context already bound')
                return
            c.execute(insert(self.snapshots).values(run_id=snapshot.run_id, payload=snapshot.model_dump(mode='json')))
            for index, mount in enumerate(snapshot.mounts):
                c.execute(insert(self.mounts).values(run_id=snapshot.run_id, position=index, payload=mount.model_dump(mode='json')))

    def snapshot(self, run_id):
        with self.database.transaction():
            value = self.database.connection.scalar(select(self.snapshots.c.payload).where(self.snapshots.c.run_id == run_id))
        if value is None:
            raise KeyError(run_id)
        return ContextSnapshot.model_validate(value)
