from ..execution.ports import Conflict


class MemoryContext:
    def __init__(self):
        self.scopes, self.snapshots, self.mounts = {}, {}, {}

    def scope(self, key):
        return self.scopes[key]

    def save_scope(self, scope, expected_version):
        old = self.scopes.get(scope.id)
        if (old.version if old else None) != expected_version:
            raise Conflict('Scope revision conflict')
        self.scopes[scope.id] = scope

    def save_snapshot(self, snapshot):
        old = self.snapshots.get(snapshot.run_id)
        if old is not None and old != snapshot:
            raise Conflict('Run context already bound')
        self.snapshots[snapshot.run_id] = snapshot
        self.mounts[snapshot.run_id] = snapshot.mounts

    def snapshot(self, run_id):
        return self.snapshots[run_id]
