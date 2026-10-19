"""Research owns these six tables. Every method uses its caller's transaction."""
from sqlalchemy import Table, Column, String, Integer, select, insert, update, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...research.models import Research, ResearchNode, CommandRecord, NodeRun, NodeResultVersion, ChatThread, ChatTransfer


def define_tables(meta):
    tables = {}
    for name, key, parent in [('researches', 'id', 'owner'), ('research_nodes', 'id', 'research_id'),
        ('research_commands', 'id', 'research_id'), ('research_node_runs', 'id', 'node_id'),
        ('node_result_versions', 'id', 'node_id')]:
        tables[name] = Table(name, meta, Column(key, String, primary_key=True),
            Column(parent, String, nullable=False, index=True), Column('revision', Integer, nullable=False),
            Column('payload', JSONB, nullable=False))
    tables['node_result_dependencies'] = Table('node_result_dependencies', meta,
        Column('result_id', String, primary_key=True), Column('input_result_id', String, primary_key=True),
        Column('position', Integer, nullable=False))
    tables['research_chats'] = Table('research_chats', meta,
        Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
        Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False))
    tables['research_transfers'] = Table('research_transfers', meta,
        Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
        Column('revision', Integer, nullable=False), Column('payload', JSONB, nullable=False))
    tables['research_plan_proposals'] = Table('research_plan_proposals', meta,
        Column('id', String, primary_key=True), Column('research_id', String, nullable=False, index=True),
        Column('request_id', String, nullable=False), Column('revision', Integer, nullable=False),
        Column('payload', JSONB, nullable=False), UniqueConstraint('research_id', 'request_id'))
    return tables


class PostgresResearch:
    def __init__(self, connection, tables): self.c, self.tables = connection, tables
    def _get(self, name, key, cls):
        t = self.tables[name]
        v = self.c.scalar(select(t.c.payload).where(t.c.id == key))
        return cls.model_validate(v) if v else None
    def _required(self, name, key, cls):
        value = self._get(name, key, cls)
        if value is None: raise KeyError(key)
        return value
    def _list(self, name, parent, key, cls):
        t = self.tables[name]
        return [cls.model_validate(v) for v in self.c.scalars(select(t.c.payload).where(t.c[parent] == key).order_by(t.c.id))]
    def _insert(self, name, key, parent, value, revision=0):
        self.c.execute(insert(self.tables[name]).values(id=key, **parent, revision=revision, payload=value.model_dump(mode='json')))
    def research(self, key): return self._required('researches', key, Research)
    def node(self, key): return self._required('research_nodes', key, ResearchNode)
    def command(self, key): return self._get('research_commands', key, CommandRecord)
    def run(self, key): return self._get('research_node_runs', key, NodeRun)
    def result(self, key): return self._required('node_result_versions', key, NodeResultVersion)
    def list_researches(self, owner): return self._list('researches', 'owner', owner, Research)
    def list_nodes(self, key): return self._list('research_nodes', 'research_id', key, ResearchNode)
    def list_runs(self, key): return self._list('research_node_runs', 'node_id', key, NodeRun)
    def list_commands(self, key): return self._list('research_commands', 'research_id', key, CommandRecord)
    def list_results(self, key): return sorted(self._list('node_result_versions', 'node_id', key, NodeResultVersion), key=lambda v: v.version)
    def create_research(self, v): self._insert('researches', v.id, {'owner': v.owner}, v)
    def admit_plan(self, plan):
        table = self.tables['researches']
        self.c.execute(select(table.c.id).where(table.c.id == plan.research_id).with_for_update())
        research = self.research(plan.research_id)
        old = next((p for p in research.plans if p.id == plan.id), None)
        if old:
            if old != plan: raise Conflict('Plan ID reused with different content')
            return False
        value = research.model_copy(update={'plans': (*research.plans, plan)})
        table = self.tables['researches']
        self.c.execute(update(table).where(table.c.id == value.id).values(payload=value.model_dump(mode='json')))
        return True
    def create_node(self, v): self._insert('research_nodes', v.id, {'research_id': v.research_id}, v, v.revision)
    def create_command(self, v): self._insert('research_commands', v.command.command_id, {'research_id': v.command.research_id}, v)
    def create_run(self, v): self._insert('research_node_runs', v.run_id, {'node_id': v.node_id}, v)
    def save_node(self, v, expected_revision):
        t = self.tables['research_nodes']
        if self.c.execute(update(t).where(t.c.id == v.id, t.c.revision == expected_revision)
            .values(revision=v.revision, payload=v.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Node revision changed')
    def save_command(self, v):
        old = self.command(v.command.command_id)
        if old is None or old.command != v.command: raise Conflict('Command changed')
        t = self.tables['research_commands']
        self.c.execute(update(t).where(t.c.id == v.command.command_id).values(payload=v.model_dump(mode='json')))
    def publish(self, v, dependencies):
        self._insert('node_result_versions', v.id, {'node_id': v.node_id}, v, v.version)
        t = self.tables['node_result_dependencies']
        for i, key in enumerate(dependencies):
            self.c.execute(insert(t).values(result_id=v.id, input_result_id=key, position=i))
    def inputs(self, key):
        t = self.tables['node_result_dependencies']
        return tuple(self.c.scalars(select(t.c.input_result_id).where(t.c.result_id == key).order_by(t.c.position)))
    def chat(self, node_id): return self._get('research_chats', node_id, ChatThread)
    def transfer(self, key): return self._get('research_transfers', key, ChatTransfer)
    def list_transfers(self, research_id): return self._list('research_transfers', 'research_id', research_id, ChatTransfer)
    def create_chat(self, value): self._insert('research_chats', value.node_id, {'research_id':value.research_id}, value)
    def save_chat(self, value, expected_revision):
        t = self.tables['research_chats']
        if self.c.execute(update(t).where(t.c.id == value.node_id, t.c.revision == expected_revision)
            .values(revision=value.revision, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Chat revision changed')
    def create_transfer(self, value): self._insert('research_transfers', value.id, {'research_id':value.research_id}, value)
    def save_transfer(self, value, expected):
        t = self.tables['research_transfers']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.payload == expected.model_dump(mode='json'))
            .values(payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Transfer changed')

    def proposal(self, key):
        from ...research.planning_models import PlanProposal
        return self._required('research_plan_proposals', key, PlanProposal)
    def proposal_request(self, research_id, request_id):
        from ...research.planning_models import PlanProposal
        t = self.tables['research_plan_proposals']
        value = self.c.scalar(select(t.c.payload).where(t.c.research_id == research_id, t.c.request_id == request_id))
        return PlanProposal.model_validate(value) if value else None
    def list_proposals(self, research_id):
        from ...research.planning_models import PlanProposal
        return self._list('research_plan_proposals', 'research_id', research_id, PlanProposal)
    def create_proposal(self, value):
        self._insert('research_plan_proposals', value.id, {'research_id': value.research_id, 'request_id': value.request_id}, value, value.revision)
    def save_proposal(self, value, expected_revision):
        t = self.tables['research_plan_proposals']
        if self.c.execute(update(t).where(t.c.id == value.id, t.c.revision == expected_revision)
            .values(revision=value.revision, payload=value.model_dump(mode='json'))).rowcount != 1:
            raise Conflict('Plan revision changed')
