"""Project records with indexed ownership and compare-and-swap updates."""
from sqlalchemy import Table, Column, String, Integer, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...projects.models import Project, MainTask, Folder, Placement


def define_tables(meta):
    return {
        'projects': Table('workspace_projects', meta, Column('id', String, primary_key=True), Column('owner', String, nullable=False, index=True), Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False)),
        'members': Table('workspace_main_tasks', meta, Column('id', String, primary_key=True), Column('project_id', String, nullable=False, index=True), Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False)),
        'folders': Table('workspace_folders', meta, Column('id', String, primary_key=True), Column('project_id', String, nullable=False, index=True), Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False)),
        'placements': Table('workspace_placements', meta, Column('kind', String, primary_key=True), Column('id', String, primary_key=True), Column('project_id', String, nullable=False, index=True), Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False)),
    }


class PostgresProjects:
    CLASSES = {'projects':Project,'members':MainTask,'folders':Folder,'placements':Placement}
    def __init__(self, connection, tables): self.c, self.t = connection, tables
    def _get(self, kind, key, subkind=None):
        t = self.t[kind]
        where = (t.c.kind == subkind) & (t.c.id == key) if kind == 'placements' else t.c.id == key
        value = self.c.scalar(select(t.c.payload).where(where))
        return self.CLASSES[kind].model_validate(value) if value else None
    def _list(self, kind, field, value):
        t = self.t[kind]
        return [self.CLASSES[kind].model_validate(v) for v in self.c.scalars(select(t.c.payload).where(t.c[field] == value).order_by(t.c.id))]
    def project(self, key): return self._get('projects',key)
    def projects_for(self, owner): return self._list('projects','owner',owner)
    def member(self, key): return self._get('members',key)
    def members_for(self, key): return self._list('members','project_id',key)
    def folder(self, key): return self._get('folders',key)
    def folders_for(self, key): return self._list('folders','project_id',key)
    def placement(self, kind, key): return self._get('placements',key,kind)
    def placements_for(self, key): return self._list('placements','project_id',key)
    def insert(self, kind, key, value):
        if kind == 'projects': fields = {'owner':value.owner}
        else:
            fields = {'project_id':value.project_id}
            if kind == 'placements': fields['kind'] = value.kind
        self.c.execute(insert(self.t[kind]).values(id=key, revision=value.revision, payload=value.model_dump(mode='json'), **fields))
    def save(self, kind, key, value, expected):
        t=self.t[kind]
        where = (t.c.kind == value.kind) & (t.c.id == key) if kind == 'placements' else t.c.id == key
        if self.c.execute(update(t).where(where, t.c.revision == expected).values(revision=value.revision,payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Revision changed')
