"""PostgreSQL transactions plus a database-scoped, single-host process lifetime.

Synchronous short DB operations match Core's synchronous event observer. No
connection transaction stays open across an await/model/tool call.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from .conversations import PostgresConversations, define_tables as conversation_tables
from .runs import PostgresRuns, define_tables as run_tables
from ...execution.ports import Conflict, SaveUnavailable, now


class PostgresDatabase:
    ADVISORY_KEY = 724063194112

    def __init__(self, url):
        self.process_id = str(uuid4())
        self.connection = None
        self.engine = None
        self.lock_file = None
        self.closed = False
        self.repositories = {}
        self.metadata = MetaData()
        self.conversation_tables = conversation_tables(self.metadata)
        self.run_tables = run_tables(self.metadata)
        self.database_identity = Table('execution_database_identity', self.metadata,
            Column('singleton', Integer, primary_key=True), Column('identity', String, nullable=False))
        self.instances = Table('execution_instances', self.metadata,
            Column('process_id', String, primary_key=True), Column('hostname', String, nullable=False),
            Column('started_at', DateTime(timezone=True), nullable=False))
        try:
            self.engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=5000 -c lock_timeout=5000'})
            if self.engine.dialect.name != 'postgresql':
                raise ValueError('PostgresDatabase requires PostgreSQL')
            self.connection = self.engine.connect()
            with self.connection.begin():
                if not self.connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': self.ADVISORY_KEY}):
                    raise Conflict('Another execution connection is active for this database')
                self.metadata.create_all(self.connection)
                # Resolve identity in PostgreSQL, so URL aliases and credentials do
                # not change the lock. The advisory lock serializes first creation.
                identity = self.connection.scalar(select(self.database_identity.c.identity)
                    .where(self.database_identity.c.singleton == 1))
                if identity is None:
                    identity = str(uuid4())
                    self.connection.execute(insert(self.database_identity).values(singleton=1, identity=identity))
                oid = self.connection.scalar(text("SELECT oid FROM pg_database WHERE datname = current_database()"))
                # OID distinguishes databases copied from a template with the same
                # persisted identity. Keep flock after connection loss until close.
                lock_path = Path('/tmp') / f'research-agent-execution-{os.getuid()}-{identity}-{oid}.lock'
                self.lock_file = lock_path.open('a')
                try:
                    fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Conflict('Another execution process is still alive for this database') from None
                hosts = set(self.connection.scalars(select(self.instances.c.hostname)))
                hostname = os.uname().nodename
                if hosts - {hostname}:
                    raise Conflict('Single-host lifecycle cannot establish another host has exited')
                self.connection.execute(insert(self.instances).values(process_id=self.process_id,
                                                                      hostname=hostname, started_at=now()))
                groups = PostgresRuns(self.connection, self.run_tables)
                for payload in self.connection.scalars(select(self.run_tables[2].c.payload)):
                    from ...orchestration.models import DispatchGroup
                    group = DispatchGroup.model_validate(payload)
                    if group.status == 'running':
                        groups.finish_dispatch(group.model_copy(update={'status': 'interrupted'}))
                runs = self.run_tables[0]
                self.connection.execute(update(runs).where(runs.c.status.in_(['running', 'saving']))
                                        .values(status='interrupted'))
        except BaseException:
            self.close()
            raise

    @contextmanager
    def transaction(self):
        # Never silently reconnect: a replacement connection would lose ownership.
        if self.closed or self.connection is None or self.connection.invalidated:
            raise SaveUnavailable('Execution database connection is unavailable; reopen the application')
        try:
            with self.connection.begin():
                yield SimpleNamespace(conversations=PostgresConversations(self.connection, self.conversation_tables),
                                      runs=PostgresRuns(self.connection, self.run_tables),
                                      **{k: factory(self.connection) for k, factory in self.repositories.items()})
        except IntegrityError:
            raise Conflict('Repository identity or active conversation constraint violated') from None

    def close(self):
        self.closed = True
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            if self.engine is not None:
                self.engine.dispose()
            if self.lock_file is not None:
                self.lock_file.close()
