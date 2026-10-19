from ..execution.ports import Conflict


class MemoryProjects:
    def __init__(self):
        self.projects, self.members, self.folders, self.placements = {}, {}, {}, {}

    def project(self, key): return self.projects.get(key)
    def projects_for(self, owner): return [p for p in self.projects.values() if p.owner == owner]
    def member(self, key): return self.members.get(key)
    def members_for(self, project_id): return [v for v in self.members.values() if v.project_id == project_id]
    def folder(self, key): return self.folders.get(key)
    def folders_for(self, project_id): return [v for v in self.folders.values() if v.project_id == project_id]
    def placement(self, kind, key): return self.placements.get((kind, key))
    def placements_for(self, project_id): return [v for v in self.placements.values() if v.project_id == project_id]
    def insert(self, kind, key, value):
        target = getattr(self, kind)
        if kind == 'placements': key = (value.kind, key)
        if key in target: raise Conflict('Identity exists')
        target[key] = value
    def save(self, kind, key, value, expected):
        target = getattr(self, kind)
        if kind == 'placements': key = (value.kind, key)
        old = target.get(key)
        if old is None or old.revision != expected: raise Conflict('Revision changed')
        target[key] = value
