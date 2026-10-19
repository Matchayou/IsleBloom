from ..context.scopes import ScopeDenied
from ..execution.ports import Conflict
from .models import MemoryEntry, MemoryRef

_UNCHANGED = object()


class MemoryService:
    def __init__(self, database, owner, research):
        self.database, self.owner, self.research = database, owner, research

    def namespaces(self, research_id, agent=None):
        self.research.research(research_id)
        namespaces = {('user', None)}
        with self.database.transaction() as tx:
            member = tx.projects.member(research_id) if 'projects' in self.database.repositories else None
            if member is not None:
                project = tx.projects.project(member.project_id)
                if project is None or project.owner != self.owner: raise ScopeDenied('Foreign project')
                namespaces.add(('project', project.id))
        if agent is not None and agent.mode == 'specialist':
            namespaces.add(('specialist', agent.id))
        return namespaces

    def usable(self, value):
        if value.scope == 'specialist' and value.id.startswith('agent-memory:'):
            return False  # Proposal IDs never stand in for a validated domain publication.
        if value.publication_id is None: return True
        learning = getattr(self.research, 'learning', None)
        if learning is None: return False
        try:
            candidate, publication = learning.available(value.publication_id)
            return (publication.memory_id == value.id and candidate.draft.memory == value.content
                and value.scope == 'specialist' and candidate.expert_id == value.scope_id)
        except (ScopeDenied, Conflict, KeyError): return False

    def list(self, research_id, *, agent=None):
        namespaces = self.namespaces(research_id, agent)
        with self.database.transaction() as tx:
            values = [v for v in tx.memories.list(self.owner) if (v.scope, v.scope_id) in namespaces]
        return [v for v in values if self.usable(v)]

    def create(self, *, kind, content, source, scope='user', scope_id=None, confirmed=False, expires_at=None):
        if not content.strip() or not source.strip(): raise ValueError('Content and source required')
        value = MemoryEntry(owner=self.owner, scope=scope, scope_id=scope_id, kind=kind,
            content=content.strip(), source=source.strip(), status='confirmed' if confirmed else 'draft',
            expires_at=expires_at)
        if value.scope == 'project':
            if 'projects' not in self.database.repositories: raise ScopeDenied('Project service unavailable')
            with self.database.transaction() as tx: project = tx.projects.project(value.scope_id)
            if project is None or project.owner != self.owner: raise ScopeDenied('Foreign project')
        if value.scope == 'specialist':
            # Domain memory belongs to a published, stable role, never a temporary run.
            if not getattr(self.research, 'methods', None): raise ScopeDenied('Specialist registry unavailable')
            self.research.methods.method(value.scope_id)
        with self.database.transaction() as tx: tx.memories.save(value, None)
        return value

    def update(self, key, expected_version, *, content=None, source=None, status=None, expires_at=_UNCHANGED):
        if status == 'confirmed':
            with self.database.transaction() as tx: before = tx.memories.get(key)
            if before is None or before.owner != self.owner: raise KeyError(key)
            if before.scope == 'specialist' and before.id.startswith('agent-memory:'):
                raise ScopeDenied('Domain candidate requires an independent validated publication')
            if before.publication_id and not self.usable(before):
                raise ScopeDenied('Domain candidate requires an independent validated publication')
        with self.database.transaction() as tx:
            old = tx.memories.get(key)
            if old is None or old.owner != self.owner: raise KeyError(key)
            if old.publication_id and (content is not None or source is not None):
                raise ScopeDenied('Published domain content requires a new validated candidate')
            if old.version != expected_version: raise Conflict('Memory changed; refresh')
            if status is not None and status not in ('draft', 'confirmed'): raise ValueError('Unknown memory status')
            changes = {'version':old.version + 1}
            if content is not None:
                if not content.strip(): raise ValueError('Content required')
                changes['content'] = content.strip()
            if source is not None:
                if not source.strip(): raise ValueError('Source required')
                changes['source'] = source.strip()
            if status is not None: changes['status'] = status
            if expires_at is not _UNCHANGED: changes['expires_at'] = expires_at
            value = MemoryEntry.model_validate({**old.model_dump(mode='json'), **changes})
            tx.memories.save(value, expected_version)
            return value

    def delete(self, key, expected_version):
        with self.database.transaction() as tx:
            old = tx.memories.get(key)
            if old is None or old.owner != self.owner: raise KeyError(key)
            tx.memories.delete(key, expected_version)
        return {'deleted':key}

    def select(self, research_id, ids, *, agent=None):
        namespaces = self.namespaces(research_id, agent)
        if len(set(ids)) != len(ids): raise ValueError('Duplicate memory')
        with self.database.transaction() as tx:
            values = tuple(tx.memories.get(key) for key in ids)
        # Domain publication validation reads its own immutable source records.
        # Run it after this read transaction closes, including for frozen refs.
        for item in values:
            if item is None or item.owner != self.owner or (item.scope, item.scope_id) not in namespaces or not item.available() or not self.usable(item):
                raise ScopeDenied('Memory unavailable in this agent namespace')
        return values

    def validate(self, research_id, refs, *, agent=None, completed_run_id=None):
        if len({r.id for r in refs}) != len(refs): raise ValueError('Duplicate memory')
        if completed_run_id is None:
            values = self.select(research_id, tuple(ref.id for ref in refs), agent=agent)
        else:
            # Only a terminal run's own atomic correction may explain its frozen
            # old version. Other runs and later edits retain normal invalidation.
            namespaces = self.namespaces(research_id, agent)
            with self.database.transaction() as tx:
                run = tx.runs.get(completed_run_id)
                writes = tx.memories.list_writes(self.owner, completed_run_id) if run and run.commit_id else []
                values = []
                for ref in refs:
                    current = tx.memories.get(ref.id)
                    own = next((w for w in writes if w.state == 'published' and w.old_memory and
                        w.old_memory['id'] == ref.id and w.old_memory['version'] == ref.version and
                        w.resolution and w.resolution.get('version') == (current.version if current else None)), None)
                    value = MemoryEntry.model_validate(own.old_memory) if own else current
                    if value is None or value.owner != self.owner or (value.scope, value.scope_id) not in namespaces or not value.available():
                        raise ScopeDenied('Memory unavailable in this completed agent namespace')
                    values.append(value)
            if any(not self.usable(v) for v in values): raise ScopeDenied('Published memory unavailable')
            values = tuple(values)
        if any(value.version != ref.version for value, ref in zip(values, refs)):
            raise ScopeDenied('Memory changed; create a new task with the current version')
        return values

    @staticmethod
    def refs(values):
        return tuple(MemoryRef(id=v.id, version=v.version) for v in values)

    @staticmethod
    def contributions(values):
        return tuple({'id':v.id, 'version':v.version, 'kind':v.kind, 'content':v.content,
            'source':v.source, 'scope':v.scope, 'scope_id':v.scope_id, 'expires_at':v.expires_at.isoformat() if v.expires_at else None}
            for v in values)
