"""Stage 7 records; PostgreSQL owns uniqueness and transaction boundaries."""
from sqlalchemy import Table, Column, String, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB
from ...execution.ports import Conflict
from ...evaluation.models import Feedback, FrozenCase, Experiment, Trial


def define_tables(meta):
    return {
        name: Table(f'evaluation_{name}', meta,
            Column('id', String, primary_key=True),
            Column('research_id' if name != 'trials' else 'experiment_id', String, nullable=False, index=True),
            Column('payload', JSONB, nullable=False))
        for name in ('feedbacks', 'cases', 'experiments', 'trials')
    }


class PostgresEvaluation:
    models = {'feedbacks': Feedback, 'cases': FrozenCase, 'experiments': Experiment, 'trials': Trial}
    def __init__(self, connection, tables): self.c, self.t = connection, tables

    def _get(self, name, key):
        payload = self.c.scalar(select(self.t[name].c.payload).where(self.t[name].c.id == key))
        return self.models[name].model_validate(payload) if payload else None

    def _list(self, name, field, key):
        table = self.t[name]
        return [self.models[name].model_validate(payload) for payload in self.c.scalars(
            select(table.c.payload).where(getattr(table.c, field) == key).order_by(table.c.id))]

    def _add(self, name, value, field):
        old = self._get(name, value.id)
        if old:
            if old != value: raise Conflict(f'{name} ID reused with different content')
            return
        self.c.execute(insert(self.t[name]).values(id=value.id, **{field:getattr(value, field)},
            payload=value.model_dump(mode='json')))

    def feedback(self, key): return self._get('feedbacks', key)
    def case(self, key): return self._get('cases', key)
    def experiment(self, key): return self._get('experiments', key)
    def trial(self, key): return self._get('trials', key)
    def list_feedback(self, research_id): return self._list('feedbacks','research_id',research_id)
    def list_cases(self, research_id): return self._list('cases','research_id',research_id)
    def list_trials(self, experiment_id): return self._list('trials','experiment_id',experiment_id)
    def add_feedback(self, value): self._add('feedbacks',value,'research_id')
    def add_case(self, value): self._add('cases',value,'research_id')
    def add_experiment(self, value): self._add('experiments',value,'research_id')
    def add_trial(self, value): self._add('trials',value,'experiment_id')
    def save_feedback(self, value, expected):
        table = self.t['feedbacks']
        changed = self.c.execute(update(table).where(table.c.id == value.id,
            table.c.payload == expected.model_dump(mode='json')).values(payload=value.model_dump(mode='json')))
        if changed.rowcount != 1: raise Conflict('Feedback changed')
