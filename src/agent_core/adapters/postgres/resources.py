"""Resources own these four tables, using the application's existing connection."""
from sqlalchemy import MetaData, Table, Column, String, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from ...execution.ports import Conflict
from ...resources.snapshots import Resource, SourceSnapshot, ResourceIngestion, ParseResult


class PostgresResources:
    def __init__(self, database):
        self.database = database
        meta = MetaData()
        self.resources = Table('resources', meta, Column('id', String, primary_key=True), Column('payload', JSONB, nullable=False))
        self.snapshots = Table('source_snapshots', meta, Column('id', String, primary_key=True),
            Column('blob_hash', String, nullable=False, index=True), Column('payload', JSONB, nullable=False))
        self.entries = Table('resource_ingestions', meta, Column('id', String, primary_key=True),
            Column('scope_id', String, nullable=False, index=True), Column('payload', JSONB, nullable=False))
        self.parses = Table('resource_parses', meta, Column('id', String, primary_key=True),
            Column('snapshot_id', String, nullable=False, index=True), Column('parser_key', String, nullable=False),
            Column('payload', JSONB, nullable=False))
        with database.transaction():
            meta.create_all(database.connection)

    def register(self, resource, snapshot, ingestion, parse):
        with self.database.transaction():
            c = self.database.connection
            c.execute(pg_insert(self.resources).values(id=resource.id, payload=resource.model_dump(mode='json')).on_conflict_do_nothing())
            old = c.scalar(select(self.resources.c.payload).where(self.resources.c.id == resource.id))
            if Resource.model_validate(old) != resource:
                raise Conflict('Resource identity changed')
            c.execute(insert(self.snapshots).values(id=snapshot.id, blob_hash=snapshot.blob_hash, payload=snapshot.model_dump(mode='json')))
            c.execute(insert(self.entries).values(id=ingestion.id, scope_id=ingestion.scope_id, payload=ingestion.model_dump(mode='json')))
            c.execute(insert(self.parses).values(id=parse.id, snapshot_id=parse.snapshot_id, parser_key=parse.parser_key, payload=parse.model_dump(mode='json')))

    def _get(self, table, model, key):
        with self.database.transaction():
            data = self.database.connection.scalar(select(table.c.payload).where(table.c.id == key))
        if data is None:
            raise KeyError(key)
        return model.model_validate(data)

    def resource(self, key):
        return self._get(self.resources, Resource, key)

    def snapshot(self, key):
        return self._get(self.snapshots, SourceSnapshot, key)

    def parse(self, key):
        return self._get(self.parses, ParseResult, key)

    def save_parse(self, value):
        with self.database.transaction():
            c = self.database.connection
            old = ParseResult.model_validate(c.scalar(select(self.parses.c.payload).where(self.parses.c.id == value.id).with_for_update()))
            if old.snapshot_id != value.snapshot_id or old.parser_key != value.parser_key or old.status in ('ready', 'partial', 'failed'):
                raise Conflict('Parse is immutable once terminal')
            c.execute(update(self.parses).where(self.parses.c.id == value.id).values(payload=value.model_dump(mode='json')))

    def cached_parse(self, digest, parser_key):
        with self.database.transaction():
            rows = self.database.connection.scalars(select(self.parses.c.payload).join(self.snapshots,
                self.parses.c.snapshot_id == self.snapshots.c.id).where(self.snapshots.c.blob_hash == digest,
                self.parses.c.parser_key == parser_key))
            return next((p for row in rows if (p := ParseResult.model_validate(row)).status in ('ready','partial')), None)

    def ingestions(self, scope_id):
        with self.database.transaction():
            return tuple(ResourceIngestion.model_validate(v) for v in self.database.connection.scalars(
                select(self.entries.c.payload).where(self.entries.c.scope_id == scope_id)))

    def ingestion(self, snapshot_id):
        with self.database.transaction():
            value = self.database.connection.execute(select(self.entries.c.payload).where(
                self.entries.c.payload['snapshot_id'].astext == snapshot_id)).scalar_one_or_none()
        if value is None: raise KeyError(snapshot_id)
        return ResourceIngestion.model_validate(value)
