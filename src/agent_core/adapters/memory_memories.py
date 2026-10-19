from ..execution.ports import Conflict


class MemoryMemories:
    def __init__(self): self.items, self.writes, self.captures = {}, {}, {}
    def get(self, key): return self.items.get(key)
    def list(self, owner): return [v for v in self.items.values() if v.owner == owner]
    def save(self, value, expected_version):
        old = self.get(value.id)
        if (old.version if old else None) != expected_version: raise Conflict('Memory changed')
        self.items[value.id] = value
    def delete(self, key, expected_version):
        old = self.get(key)
        if old is None or old.version != expected_version: raise Conflict('Memory changed')
        del self.items[key]
    def write(self, key): return self.writes.get(key)
    def list_writes(self, owner, run_id=None):
        return [v for v in self.writes.values() if v.owner == owner and (run_id is None or v.run_id == run_id)]
    def save_write(self, value, expected_state=None):
        old = self.write(value.id)
        if (old.state if old else None) != expected_state: raise Conflict('Memory write changed')
        self.writes[value.id] = value

    def capture(self, key): return self.captures.get(key)
    def list_captures(self, owner): return [v for v in self.captures.values() if v.owner == owner]
    def save_capture(self, value, expected_state=None):
        old = self.capture(value.id)
        if (old.state if old else None) != expected_state: raise Conflict('Memory capture changed')
        self.captures[value.id] = value
