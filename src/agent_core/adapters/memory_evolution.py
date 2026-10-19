from copy import deepcopy
from ..execution.ports import Conflict


class MemoryEvolution:
    def __init__(self): self.records = {}
    def get(self, key): return deepcopy(self.records.get(key))
    def list(self): return deepcopy(list(self.records.values()))
    def add(self, key, value):
        if key in self.records and self.records[key] != value: raise Conflict('Evolution evidence ID reused')
        self.records[key] = deepcopy(value)
