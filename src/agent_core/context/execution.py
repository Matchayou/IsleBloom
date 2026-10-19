"""Stage 3 execution gate composed around C0/C1's public ports."""
import asyncio
from ..contracts import RunResult, RunError
from .scopes import ScopeDenied


class ScopeExecutionGate:
    def __init__(self, runner, scopes):
        self.runner, self.scopes = runner, scopes

    def scope_for(self, request):
        snapshot = self.scopes.repository.snapshot(request.run_id)
        scope = self.scopes.repository.scope(snapshot.scope_id)
        if request.input != snapshot.input or snapshot.scope_version != scope.version:
            raise ScopeDenied('Execution input or scope version changed')
        self.scopes.valid(scope)
        for mount in snapshot.mounts:
            self.scopes.check(scope, mount.ref)
        return scope

    async def execute(self, request):
        try:
            scope = self.scope_for(request)
        except (ScopeDenied, KeyError):
            return self.blocked(request.run_id)
        task = asyncio.current_task()
        self.scopes.active[request.run_id] = (scope, task)
        token = self.scopes.running.set(request.run_id)
        try:
            result = await self.runner.execute(request)
            try:
                self.scope_for(request)
            except (ScopeDenied, KeyError):
                return self.blocked(request.run_id, result.usage)
            return result
        finally:
            self.scopes.running.reset(token)
            self.scopes.active.pop(request.run_id, None)

    @staticmethod
    def blocked(run_id, usage=None):
        values = {'usage': usage} if usage is not None else {}
        return RunResult(run_id=run_id, status='failed', output=None,
            error=RunError(code='scope_invalidated', origin='core', message='Scope changed or permission revoked; rebuild clean input'), **values)

    def validate_commit(self, commit):
        snapshot = self.scopes.repository.snapshot(commit.run_id)
        scope = self.scopes.repository.scope(snapshot.scope_id)
        try:
            self.scopes.valid(scope)
            if scope.version != snapshot.scope_version:
                raise ScopeDenied('Scope changed')
            for mount in snapshot.mounts:
                self.scopes.check(scope, mount.ref)
        except ScopeDenied:
            return commit.model_copy(update={'result': self.blocked(commit.run_id, commit.result.usage), 'messages': ()})
        return commit


class ScopedExecutor:
    """Also gate replay reads: a committed run is not a permanent access grant."""
    def __init__(self, execution, gate, bindings):
        self.execution, self.gate, self.bindings = execution, gate, dict(bindings)

    async def execute(self, request):
        scope = self.gate.scope_for(request)
        expected = self.bindings[request.agent_id]
        if scope.id != expected.id or scope.version != expected.version:
            raise ScopeDenied('Agent is bound to a different scope')
        return await self.execution.execute(request)

    def retry_save(self, run_id):
        value = self.execution.retry_save(run_id)
        if value.result is not None and value.result.status == 'succeeded':
            snapshot = self.gate.scopes.repository.snapshot(run_id)
            current = self.gate.scopes.repository.scope(snapshot.scope_id)
            self.gate.scopes.valid(current)
            if current.version != snapshot.scope_version:
                raise ScopeDenied('Saved result is no longer accessible under this scope')
            for mount in snapshot.mounts:
                self.gate.scopes.check(current, mount.ref)
        return value
