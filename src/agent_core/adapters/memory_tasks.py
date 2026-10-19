from ..execution.ports import Conflict


class MemoryTasks:
    def __init__(self):
        self.specs, self.current, self.runs, self.receipts, self.routes = {}, {}, {}, {}, {}

    def add_route(self, value):
        if value.id in self.routes: raise Conflict('Route ID reused')
        self.routes[value.id] = value

    def route(self, route_id): return self.routes.get(route_id)

    def list_routes(self, task_id):
        return [r for r in self.routes.values() if r.task_id == task_id]

    def spec(self, task_id, version=None):
        version = version or self.current.get(task_id)
        return self.specs.get((task_id, version))

    def add_spec(self, value):
        old = self.spec(value.id)
        if old and value.version != old.version + 1:
            raise Conflict('Task version changed')
        if not old and value.version != 1:
            raise Conflict('New task starts at version 1')
        self.specs[(value.id, value.version)] = value
        self.current[value.id] = value.version

    def run(self, run_id): return self.runs.get(run_id)

    def add_run(self, value):
        if value.id in self.runs: raise Conflict('Task run ID reused')
        if self.spec(value.task_id).version != value.spec_version:
            raise Conflict('Task version changed')
        self.runs[value.id] = value

    def receipt(self, result_id): return self.receipts.get(result_id)

    def save_receipt(self, value, expected=None):
        old = self.receipt(value.result_id)
        if old != expected: raise Conflict('Receipt changed')
        self.receipts[value.result_id] = value

    def list_specs(self, research_id):
        return [self.spec(key) for key in self.current if self.spec(key).research_id == research_id]

    def list_directory(self, research_id):
        fields = ('id', 'research_id', 'goal', 'parent_task_id', 'node_id')
        return [{key: getattr(spec, key) for key in fields}
                for spec in sorted(self.list_specs(research_id), key=lambda spec: spec.id)]

    def list_runs(self, task_id):
        return [r for r in self.runs.values() if r.task_id == task_id]
