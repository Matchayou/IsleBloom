from copy import deepcopy
from ..execution.ports import Conflict


class MemoryMethods:
    def __init__(self): self.versions, self.heads, self.releases, self.instances = {}, {}, [], {}
    def get(self, category, asset_id, version): return deepcopy(self.versions.get((category,asset_id,version)))
    def list(self): return deepcopy(list(self.versions.values()))
    def head(self, category, asset_id): return self.heads.get((category,asset_id))
    def add(self, value):
        key = (value.category,value.asset_id,value.version)
        if key in self.versions and self.versions[key] != value: raise Conflict('Immutable version already exists')
        self.versions[key] = deepcopy(value)
    def activate(self, category, asset_id, version, expected, actor, reason):
        if self.head(category,asset_id) != expected: raise Conflict('Active version changed; refresh before publishing')
        self.heads[category,asset_id] = version
        self.releases.append(dict(category=category,asset_id=asset_id,version=version,previous=expected,actor=actor,reason=reason))
    def history(self): return deepcopy(self.releases)
    def instance(self, key): return deepcopy(self.instances.get(key))
    def add_instance(self, value):
        if value['id'] in self.instances and self.instances[value['id']] != value: raise Conflict('Instance ID reused')
        self.instances[value['id']] = deepcopy(value)
