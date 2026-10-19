"""Evidence rows participate in the caller's result publication transaction."""
from sqlalchemy import Table, Column, String, MetaData, select, insert, update
from sqlalchemy.dialects.postgresql import JSONB
from ...evidence.models import Evidence, Claim, ClaimEvidence, EvidenceBundle
from ...execution.ports import Conflict


def define_tables(meta: MetaData):
    return {
        name: Table(name, meta, Column('id', String, primary_key=True),
            Column('result_id', String, nullable=False, index=True), Column('payload', JSONB, nullable=False))
        for name in ('evidence', 'claims', 'claim_evidence')
    }


class PostgresEvidence:
    def __init__(self, connection, tables):
        self.c, self.tables = connection, tables

    def publish(self, result_id, bundle):
        for name, values in (('claims', bundle.claims), ('evidence', bundle.evidence),
                             ('claim_evidence', bundle.relations)):
            table = self.tables[name]
            for value in values:
                if value.result_id != result_id: raise Conflict('Evidence result mismatch')
                self.c.execute(insert(table).values(id=value.id, result_id=result_id,
                    payload=value.model_dump(mode='json')))

    def read(self, result_id):
        def rows(name, model):
            table = self.tables[name]
            return tuple(model.model_validate(row) for row in self.c.scalars(
                select(table.c.payload).where(table.c.result_id == result_id).order_by(table.c.id)))
        return EvidenceBundle(claims=rows('claims',Claim), evidence=rows('evidence',Evidence),
                              relations=rows('claim_evidence',ClaimEvidence))

    def review(self, relation_id, assessment, reason):
        table = self.tables['claim_evidence']
        old = self.c.scalar(select(table.c.payload).where(table.c.id == relation_id).with_for_update())
        if old is None: raise KeyError(relation_id)
        value = ClaimEvidence.model_validate(old).model_copy(update={'assessment':assessment,'review_reason':reason})
        self.c.execute(update(table).where(table.c.id == relation_id).values(payload=value.model_dump(mode='json')))
        return value
