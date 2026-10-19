from ..execution.ports import Conflict


class MemoryResources:
    def __init__(self):
        self.resources, self.snapshots, self.parses, self.entries = {}, {}, {}, {}

    def register(self, resource, snapshot, ingestion, parse):
        if snapshot.id in self.snapshots or ingestion.id in self.entries or parse.id in self.parses:
            raise Conflict('Duplicate ingestion identity')
        if resource.id in self.resources and self.resources[resource.id] != resource:
            raise Conflict('Resource identity changed')
        self.resources[resource.id] = resource
        self.snapshots[snapshot.id] = snapshot
        self.entries[ingestion.id] = ingestion
        self.parses[parse.id] = parse

    def resource(self, key):
        return self.resources[key]

    def snapshot(self, key):
        return self.snapshots[key]

    def parse(self, key):
        return self.parses[key]

    def save_parse(self, value):
        old = self.parses[value.id]
        if old.snapshot_id != value.snapshot_id or old.parser_key != value.parser_key or old.status in ('ready', 'partial', 'failed'):
            raise Conflict('Parse is immutable once terminal')
        self.parses[value.id] = value

    def cached_parse(self, digest, parser_key):
        return next((p for p in self.parses.values() if p.parser_key == parser_key
                     and p.status in ('ready','partial') and self.snapshots[p.snapshot_id].blob_hash == digest), None)

    def ingestions(self, scope_id):
        return tuple(v for v in self.entries.values() if v.scope_id == scope_id)

    def ingestion(self, snapshot_id):
        entry = next((v for v in self.entries.values() if v.snapshot_id == snapshot_id), None)
        if entry is None: raise KeyError(snapshot_id)
        return entry
