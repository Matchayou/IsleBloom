from ..execution.ports import Conflict


class MemoryResearch:
    def __init__(self):
        self.researches, self.nodes, self.commands, self.runs, self.results, self.dependencies = {}, {}, {}, {}, {}, {}
        self.chats, self.transfers, self.proposals = {}, {}, {}

    def research(self, key): return self.researches[key]
    def node(self, key): return self.nodes[key]
    def command(self, key): return self.commands.get(key)
    def run(self, key): return self.runs.get(key)
    def result(self, key): return self.results[key]
    def list_researches(self, owner): return [r for r in self.researches.values() if r.owner == owner]
    def list_nodes(self, key): return [n for n in self.nodes.values() if n.research_id == key]
    def list_runs(self, key): return [r for r in self.runs.values() if r.node_id == key]
    def list_commands(self, key): return [r for r in self.commands.values() if r.command.research_id == key]
    def list_results(self, key): return sorted([r for r in self.results.values() if r.node_id == key], key=lambda r: r.version)
    def inputs(self, key): return self.dependencies.get(key, ())
    def chat(self, node_id): return self.chats.get(node_id)
    def transfer(self, key): return self.transfers.get(key)
    def list_transfers(self, research_id): return [v for v in self.transfers.values() if v.research_id == research_id]
    def create_chat(self, value): self._insert(self.chats, value.node_id, value)
    def save_chat(self, value, expected_revision):
        if self.chats[value.node_id].revision != expected_revision: raise Conflict('Chat revision changed')
        self.chats[value.node_id] = value
    def create_transfer(self, value): self._insert(self.transfers, value.id, value)
    def save_transfer(self, value, expected):
        if self.transfers[value.id] != expected: raise Conflict('Transfer changed')
        self.transfers[value.id] = value

    def create_research(self, value): self._insert(self.researches, value.id, value)
    def admit_plan(self, plan):
        research = self.research(plan.research_id)
        old = next((p for p in research.plans if p.id == plan.id), None)
        if old:
            if old != plan: raise Conflict('Plan ID reused with different content')
            return False
        self.researches[research.id] = research.model_copy(update={'plans': (*research.plans, plan)})
        return True
    def create_node(self, value): self._insert(self.nodes, value.id, value)
    def create_command(self, value): self._insert(self.commands, value.command.command_id, value)
    def create_run(self, value): self._insert(self.runs, value.run_id, value)

    @staticmethod
    def _insert(store, key, value):
        if key in store: raise Conflict('Identity exists')
        store[key] = value

    def save_node(self, value, expected_revision):
        if self.node(value.id).revision != expected_revision: raise Conflict('Node revision changed')
        self.nodes[value.id] = value

    def save_command(self, value):
        old = self.commands[value.command.command_id]
        if old.command != value.command: raise Conflict('Command changed')
        self.commands[value.command.command_id] = value

    def publish(self, value, dependencies):
        self._insert(self.results, value.id, value)
        self.dependencies[value.id] = tuple(dependencies)

    def proposal(self, key): return self.proposals[key]
    def proposal_request(self, research_id, request_id):
        return next((p for p in self.proposals.values() if p.research_id == research_id and p.request_id == request_id), None)
    def list_proposals(self, research_id): return [p for p in self.proposals.values() if p.research_id == research_id]
    def create_proposal(self, value):
        if self.proposal_request(value.research_id, value.request_id): raise Conflict('Plan request already exists')
        self._insert(self.proposals, value.id, value)
    def save_proposal(self, value, expected_revision):
        if self.proposal(value.id).revision != expected_revision: raise Conflict('Plan revision changed')
        self.proposals[value.id] = value
