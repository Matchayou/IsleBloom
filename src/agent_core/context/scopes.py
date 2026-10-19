"""Program-owned permissions. Immutable scopes, explicit grants, no global defaults."""
import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
from pydantic import Field
from ..contracts import Value
from ..resources.snapshots import ResourceRef, identity


class ScopeDenied(PermissionError):
    pass


class RunScope(Value):
    id: str = Field(default_factory=identity)
    owner: str
    version: int = 1
    allowed: tuple[ResourceRef, ...] = ()
    grants: tuple[ResourceRef, ...] = ()
    inherited: tuple[ResourceRef, ...] = ()
    tools: tuple[str, ...] = ('read_source',)
    max_input_chars: int = Field(default=30000, ge=1)
    max_tool_chars: int = Field(default=20000, ge=1)
    web_urls: tuple[str, ...] = ()
    enabled: bool = True


class ScopeService:
    def __init__(self, repository, resources):
        self.repository, self.resources = repository, resources
        self.running = ContextVar('scoped_run_id', default=None)
        self.active = {}  # run_id -> (immutable scope, task); single event loop

    @contextmanager
    def track(self, key, scope):
        """Public lifecycle registration for application-owned dependent executions."""
        self.valid(scope)
        self.active[key] = (scope, asyncio.current_task())
        try:
            yield
        finally:
            self.active.pop(key, None)

    def create(self, owner, **options):
        scope = RunScope(owner=owner, **options)
        if scope.allowed or scope.grants or scope.inherited:
            raise ScopeDenied('New scopes start without resource grants')
        self.repository.save_scope(scope, expected_version=None)
        return scope

    def current(self, scope_id, owner):
        scope = self.repository.scope(scope_id)
        if scope.owner != owner or not scope.enabled:
            raise ScopeDenied('Scope unavailable')
        return scope

    def valid(self, scope):
        current = self.current(scope.id, scope.owner)
        if current.version != scope.version or any(getattr(current, key) != getattr(scope, key)
                for key in ('tools', 'max_input_chars', 'max_tool_chars', 'web_urls', 'enabled')):
            raise ScopeDenied('Scope changed; rebuild clean input')
        return current

    def check(self, scope, ref, *, selected=True, seen=frozenset()):
        current = self.valid(scope)
        if ref not in current.grants or (selected and ref not in current.allowed + current.inherited):
            raise ScopeDenied('Resource version not permitted')
        if ref in seen:
            raise ScopeDenied('Cyclic dependency')
        resource, snapshot, _ = self.resources.describe(ref)
        if resource.owner != current.owner:
            raise ScopeDenied('Resource owner mismatch')
        for dependency in snapshot.dependencies:
            self.check(scope, dependency, selected=selected, seen=seen | {ref})

    def _replace(self, scope, *, invalidate=True, **changes):
        if self.valid(scope) != scope:
            raise ScopeDenied('Scope changed during automatic ingestion; reload before mutation')
        new = scope.model_copy(update={**changes, 'version': scope.version + int(invalidate)})
        self.repository.save_scope(new, expected_version=scope.version)
        # Every old run loses publication eligibility, even if it swallows cancel.
        for old, task in tuple(self.active.values()):
            if invalidate and old.id == scope.id and task is not asyncio.current_task():
                task.cancel()
        return new

    def select(self, scope, refs, inherited=(), *, web_urls=()):
        if not set(web_urls) <= set(scope.web_urls):
            raise ScopeDenied("Selection cannot expand web authorization")
        for ref in (*refs, *inherited):
            self.check(scope, ref, selected=False)
        candidate = scope.model_copy(update={'allowed': tuple(refs), 'inherited': tuple(inherited)})
        # Check dependency closure against the candidate selection before changing state.
        selected = set(refs) | set(inherited)
        for ref in selected:
            _, snapshot, _ = self.resources.describe(ref)
            if not set(snapshot.dependencies) <= selected:
                raise ScopeDenied('Mixed summary depends on excluded material')
        return self._replace(scope, allowed=candidate.allowed, inherited=candidate.inherited, web_urls=tuple(web_urls))

    def grant(self, target, source, ref):
        self.check(source, ref)
        if source.owner != target.owner:
            raise ScopeDenied('Cross-owner grant forbidden')
        _, snap, _ = self.resources.describe(ref)
        for dep in snap.dependencies:
            self.check(target, dep, selected=False)
        return self._replace(target, grants=tuple(dict.fromkeys((*target.grants, ref))))

    def revoke(self, scope, ref):
        return self._replace(scope, grants=tuple(r for r in scope.grants if r != ref),
            allowed=tuple(r for r in scope.allowed if r != ref), inherited=tuple(r for r in scope.inherited if r != ref))

    def acquire(self, target, source, ref):
        """Select a web body obtained by this active run without changing its input snapshot."""
        self.check(source, ref)
        run_id = self.running.get()
        active = self.active.get(run_id)
        entries = self.resources.repository.ingestions(source.id)
        if (not active or active[0].id != target.id or source.owner != target.owner or
                not any(e.run_id == run_id and e.kind == 'web' and
                        e.resource_id == ref.resource_id and e.snapshot_id == ref.snapshot_id for e in entries)):
            raise ScopeDenied('Only this run can acquire its fetched web body')
        self.mount(source, ref)
        return self._replace(target, invalidate=False,
            grants=tuple(dict.fromkeys((*target.grants, ref))),
            allowed=tuple(dict.fromkeys((*target.allowed, ref))))

    def acquire_library(self, target, source, ref):
        """Permit one active run to cite a body explicitly read from its parent library."""
        self.check(source, ref)
        run_id = self.running.get()
        active = self.active.get(run_id)
        if not active or active[0].id != target.id or source.owner != target.owner:
            raise ScopeDenied('Library acquisition requires the active node run')
        self.mount(source, ref)
        current = self.current(target.id, target.owner)
        if ref in current.allowed: return current
        return self._replace(current, invalidate=False,
            grants=tuple(dict.fromkeys((*current.grants, ref))),
            allowed=tuple(dict.fromkeys((*current.allowed, ref))))

    def disable(self, scope):
        return self._replace(scope, enabled=False)

    def ingest(self, scope, *, content, name, media_type, dependencies=(), automatic=False, **metadata):
        self.valid(scope)
        for ref in dependencies:
            self.check(scope, ref)
        resource_id = metadata.get('resource_id')
        if resource_id and not any(r.resource_id == resource_id for r in scope.grants):
            raise ScopeDenied('Cannot write another scope resource')
        run_id = self.running.get()
        ref = self.resources.ingest(run_id=run_id, owner=scope.owner, scope_id=scope.id, content=content,
            name=name, media_type=media_type, dependencies=dependencies, **metadata)
        # Ingestion creates a separate access relation even when blobs/parses are reused.
        scope = self._replace(scope, invalidate=not automatic, grants=(*scope.grants, ref), allowed=(*scope.allowed, ref))
        return scope, ref

    def mount(self, scope, ref):
        from .mounts import ContextMount
        self.check(scope, ref)
        resource, _, parsed = self.resources.describe(ref)
        if resource.kind == 'discovery':
            raise ScopeDenied('Discovery summaries are not source bodies')
        if parsed.status not in ('ready', 'partial'):
            raise ScopeDenied('Source is not parsed')
        return ContextMount(scope_id=scope.id, scope_version=scope.version, ref=ref)

    def read(self, scope, ref):
        self.mount(scope, ref)
        return self.resources.read(ref)

    def original(self, scope, ref):
        self.check(scope, ref)
        return self.resources.original(ref)
